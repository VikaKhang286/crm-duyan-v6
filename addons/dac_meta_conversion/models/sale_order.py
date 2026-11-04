from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError
from .meta_conversion_event import EVENT_STATES


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    # dac_erp declares this relation before CRM_DAC is loaded. Redeclare it in
    # a module that depends on CRM_DAC so combined installation resolves it.
    conversation_id = fields.Many2one('page.fm.conversation', string='Conversation', index=True)

    meta_conversion_psid = fields.Char(
        string='PSID khách hàng Messenger', copy=False,
        help='ID khách trong Facebook Page đã cấu hình. Tự lấy từ hội thoại Pancake nếu có; không nhập ID khách hàng Pancake.',
    )
    meta_conversion_event_ids = fields.One2many('dac.meta.conversion.event', 'order_id', readonly=True, copy=False)
    meta_conversion_state = fields.Selection(EVENT_STATES, compute='_compute_meta_conversion_message',
                                             compute_sudo=True, string='Chuyển đổi Meta')
    meta_conversion_message = fields.Text(
        compute='_compute_meta_conversion_message', compute_sudo=True, string='Kết quả gửi Meta',
    )

    @api.depends('meta_conversion_event_ids.message', 'meta_conversion_event_ids.state')
    def _compute_meta_conversion_message(self):
        for order in self:
            order.meta_conversion_state = order.meta_conversion_event_ids[:1].state
            order.meta_conversion_message = order.meta_conversion_event_ids[:1].message

    @api.model_create_multi
    def create(self, vals_list):
        orders = super().create(vals_list)
        Event = self.env['dac.meta.conversion.event'].sudo()
        events = Event.browse()
        for order in orders:
            company = order.company_id.sudo()
            if company.meta_conversion_enabled:
                events |= Event.create({
                    'order_id': order.id,
                    'company_id': company.id,
                    'dataset_id': company.meta_conversion_dataset_id,
                    'page_id': company.meta_conversion_page_id,
                    'channel': company.meta_conversion_channel,
                    'test_code': company.meta_conversion_test_code,
                    'event_time': fields.Datetime.now(),
                })
        events._schedule_dispatch()
        return orders

    def write(self, vals):
        result = super().write(vals)
        if {'partner_id', 'conversation_id', 'meta_conversion_psid', 'order_line'} & vals.keys():
            events = self.env['dac.meta.conversion.event'].sudo().search([
                ('order_id', 'in', self.ids), ('state', '=', 'waiting'),
            ])
            events.write({'state': 'pending', 'next_attempt_at': fields.Datetime.now()})
            events._schedule_dispatch()
        return result

    def action_meta_retry(self):
        self.check_access('write')
        events = self.env['dac.meta.conversion.event'].sudo().search([('order_id', 'in', self.ids)])
        if not events:
            raise UserError(_('Đơn này không có sự kiện Meta. Chỉ đơn được tạo sau khi bật tính năng mới được gửi.'))
        if any(event.state not in ('waiting', 'error', 'retry', 'pending') for event in events):
            raise UserError(_('Không gửi lại sự kiện đã nhận, đang gửi hoặc chưa rõ kết quả để tránh tính trùng chuyển đổi.'))
        events.write({'state': 'pending', 'payload': False, 'next_attempt_at': fields.Datetime.now()})
        events._schedule_dispatch()
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_meta_open_api_logs(self):
        self.check_access('read')
        if not (self.env.user.has_group('base.group_system') or self.env.user.has_group('dac_erp.group_dac_erp_manager')):
            raise AccessError(_('Chỉ quản trị viên và quản lý được xem nhật ký API Meta.'))
        action = self.env.ref('dac_meta_conversion.action_meta_api_logs').read()[0]
        action['domain'] = [('order_id', 'in', self.ids)]
        return action
