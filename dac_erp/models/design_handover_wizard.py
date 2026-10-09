# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class DacShippingCarrier(models.Model):
    _name = 'dac.shipping.carrier'
    _description = 'Đơn vị vận chuyển tùy chỉnh'
    _order = 'name, id'

    name = fields.Char(string='Tên đơn vị vận chuyển', required=True, index=True)
    active = fields.Boolean(default=True)


class SaleOrderDesignPanel(models.Model):
    _inherit = 'sale.order'

    design_revision_display = fields.Char(
        string='Phiên bản bản vẽ',
        compute='_compute_design_panel_display',
    )
    design_status_display = fields.Char(
        string='Trạng thái thiết kế',
        compute='_compute_design_panel_display',
    )
    design_summary_display = fields.Char(
        string='Thiết kế',
        compute='_compute_design_panel_display',
    )
    quotation_tax_display = fields.Char(
        string='Thuế',
        compute='_compute_design_panel_display',
    )
    quotation_appointment_date = fields.Date(
        string='Ngày hẹn',
        compute='_compute_design_panel_display',
    )
    delivery_status_display = fields.Char(
        string='Trạng thái giao hàng',
        compute='_compute_delivery_flow_display',
    )
    delivery_method_display = fields.Char(
        string='Hình thức giao hàng',
        compute='_compute_delivery_flow_display',
    )
    delivery_shipping_summary = fields.Char(
        string='Đơn vị vận chuyển và mã vận đơn',
        compute='_compute_delivery_flow_display',
    )
    delivery_next_message = fields.Char(
        string='Điều kiện chuyển bước',
        compute='_compute_delivery_flow_display',
    )
    delivery_can_complete = fields.Boolean(
        string='Có thể hoàn thành giao hàng',
        compute='_compute_delivery_flow_display',
    )
    design_task_progress_percent = fields.Integer(
        string='Tiến độ task thiết kế',
        compute='_compute_design_task_transition',
    )
    design_tasks_complete = fields.Boolean(
        string='Task thiết kế đã hoàn tất',
        compute='_compute_design_task_transition',
    )
    production_task_progress_percent = fields.Integer(
        string='Tiến độ task sản xuất',
        compute='_compute_production_task_transition',
    )
    production_tasks_complete = fields.Boolean(
        string='Task sản xuất đã hoàn tất',
        compute='_compute_production_task_transition',
    )

    @api.depends(
        'design_done', 'amount_untaxed_original', 'amount_tax',
        'commitment_date', 'date_order',
    )
    def _compute_design_panel_display(self):
        revisions = self.env['dac.design.revision'].search(
            [('order_id', 'in', self.ids)],
            order='order_id, revision_number desc, id desc',
        ) if self.ids else self.env['dac.design.revision']
        latest_by_order = {}
        for revision in revisions:
            latest_by_order.setdefault(revision.order_id.id, revision)
        for order in self:
            revision = latest_by_order.get(order.id)
            order.design_revision_display = revision.name if revision else 'V1'
            order.design_status_display = 'Đã duyệt' if order.design_done else 'Chờ duyệt'
            order.design_summary_display = '%s · %s' % (
                order.design_revision_display,
                order.design_status_display,
            )
            untaxed = order.amount_untaxed_original or 0.0
            tax_percent = round((order.amount_tax / untaxed) * 100) if untaxed else 0
            order.quotation_tax_display = '%s%%' % tax_percent
            order.quotation_appointment_date = (
                fields.Date.to_date(order.commitment_date)
                if order.commitment_date
                else fields.Date.to_date(order.date_order)
            )

    @api.depends(
        'shipping_method', 'shipping_status', 'bus_shipping_status',
        'shipping_carrier', 'shipping_tracking_code',
        'bus_carrier_name', 'bus_shipping_info',
    )
    def _compute_delivery_flow_display(self):
        carrier_labels = dict(self._fields['shipping_status'].selection)
        bus_labels = dict(self._fields['bus_shipping_status'].selection)
        shipping_carrier_labels = dict(self._selection_shipping_carrier())
        for order in self:
            is_bus = order.shipping_method == 'bus'
            status = order.bus_shipping_status if is_bus else order.shipping_status
            labels = bus_labels if is_bus else carrier_labels
            order.delivery_status_display = labels.get(status, 'Chờ lấy hàng')
            order.delivery_method_display = 'Chành xe' if is_bus else 'Đơn vị vận chuyển'
            if is_bus:
                carrier_name = order.bus_carrier_name or 'Chành xe'
                tracking_reference = order.bus_shipping_info or 'Chưa có thông tin gửi hàng'
            else:
                carrier_name = (
                    shipping_carrier_labels.get(order.shipping_carrier)
                    if order.shipping_carrier not in (False, 'choose-option')
                    else 'Đơn vị vận chuyển'
                )
                tracking_reference = order.shipping_tracking_code or 'Chưa có mã vận đơn'
            order.delivery_shipping_summary = '%s · %s' % (
                carrier_name,
                tracking_reference,
            )
            order.delivery_can_complete = status == 'delivered'
            if status == 'returned':
                order.delivery_next_message = (
                    'Chưa thể chuyển: Đơn bị trả hàng. Cần xử lý và giao lại.'
                )
            elif status != 'delivered':
                order.delivery_next_message = (
                    'Chưa thể chuyển: Chưa giao thành công. Kiểm tra trạng thái giao hàng.'
                )
            else:
                order.delivery_next_message = 'Bấm tiếp tục để chuyển đơn sang bước kế tiếp.'

    @api.depends('design_task_done_count', 'design_task_count')
    def _compute_design_task_transition(self):
        for order in self:
            total = order.design_task_count or 0
            done = order.design_task_done_count or 0
            order.design_task_progress_percent = round((done / total) * 100) if total else 100
            order.design_tasks_complete = not total or done >= total

    @api.depends('production_task_done_count', 'production_task_count')
    def _compute_production_task_transition(self):
        for order in self:
            total = order.production_task_count or 0
            done = order.production_task_done_count or 0
            order.production_task_progress_percent = round((done / total) * 100) if total else 100
            order.production_tasks_complete = not total or done >= total

    def action_open_design_handover(self):
        self.ensure_one()
        if self.order_state_custom != 'deposit':
            raise UserError(_('Chỉ mở bàn giao thiết kế ở bước Thiết kế & Đặt cọc.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Thiết kế & bàn giao'),
            'res_model': 'dac.design.handover.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'active_id': self.id, 'active_model': 'sale.order'},
        }

    def action_open_deposit_payment(self):
        self.ensure_one()
        if self.order_state_custom != 'deposit':
            raise UserError(_('Chỉ ghi nhận cọc ở bước Thiết kế & Đặt cọc.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Ghi nhận thanh toán'),
            'res_model': 'deposit.confirm.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'active_id': self.id, 'active_model': 'sale.order'},
        }

    def action_open_shipping_carrier_create_wizard(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Tạo đơn vị vận chuyển'),
            'res_model': 'dac.shipping.carrier.create.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_order_id': self.id,
            },
        }

    def action_start_delivery_tracking(self):
        self.ensure_one()
        if self.order_state_custom != 'delivery':
            raise UserError(_('Chỉ bắt đầu giao hàng tại bước Giao hàng.'))
        if self.shipping_method == 'carrier':
            if self.shipping_carrier in (False, 'choose-option', 'option-0'):
                raise UserError(_('Vui lòng chọn đơn vị vận chuyển.'))
            if not self.shipping_tracking_code:
                raise UserError(_('Vui lòng nhập mã vận đơn để bắt đầu theo dõi giao hàng.'))
            values = {'started_delivery': True, 'shipping_status': 'shipping'}
        else:
            if not self.bus_carrier_name:
                raise UserError(_('Vui lòng nhập tên chành xe / nhà xe.'))
            if not self.bus_shipping_info:
                raise UserError(_('Vui lòng nhập thông tin gửi hàng.'))
            values = {'started_delivery': True, 'bus_shipping_status': 'shipping'}
        self.write(values)
        return {'type': 'ir.actions.client', 'tag': 'reload'}


class DacShippingCarrierCreateWizard(models.TransientModel):
    _name = 'dac.shipping.carrier.create.wizard'
    _description = 'Tạo đơn vị vận chuyển'

    order_id = fields.Many2one('sale.order', required=True, readonly=True)
    name = fields.Char(string='Tên đơn vị vận chuyển', required=True)

    def action_create_carrier(self):
        self.ensure_one()
        name = (self.name or '').strip()
        if not name:
            raise UserError(_('Vui lòng nhập tên đơn vị vận chuyển.'))

        built_in = {
            'viettel post': 'viettel-post',
            'ghn': 'ghn',
            'j&t express': 'j&t-express',
            'đối tác vận chuyển khác': 'other',
        }
        selection_value = built_in.get(name.casefold())
        if not selection_value:
            Carrier = self.env['dac.shipping.carrier'].sudo()
            carrier = Carrier.search([('name', '=ilike', name)], limit=1)
            if carrier:
                if not carrier.active:
                    carrier.active = True
            else:
                carrier = Carrier.create({'name': name})
            selection_value = 'custom-%s' % carrier.id

        self.order_id.shipping_carrier = selection_value
        return {'type': 'ir.actions.client', 'tag': 'reload'}


class DesignHandoverWizard(models.TransientModel):
    _name = 'dac.design.handover.wizard'
    _description = 'Thiết kế và bàn giao'

    order_label = fields.Char(string='Đơn hàng', readonly=True)
    design_version = fields.Char(string='Phiên bản bản vẽ', required=True)
    designer_id = fields.Many2one('res.users', string='Người thiết kế', readonly=True)

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        order = self.env['sale.order'].browse(self.env.context.get('active_id')).exists()
        if not order:
            return values
        latest = self.env['dac.design.revision'].search(
            [('order_id', '=', order.id)],
            order='revision_number desc, id desc',
            limit=1,
        )
        values.update({
            'order_label': order.name,
            'design_version': latest.name if latest else 'V1',
            'designer_id': order.user_id_design.id,
        })
        return values

    def action_confirm_handover(self):
        self.ensure_one()
        order = self.env['sale.order'].browse(self.env.context.get('active_id')).exists()
        if not order:
            raise UserError(_('Không tìm thấy đơn hàng.'))
        version = (self.design_version or '').strip()
        if not version:
            raise UserError(_('Vui lòng nhập phiên bản bản vẽ.'))
        revision_model = self.env['dac.design.revision']
        latest = revision_model.search(
            [('order_id', '=', order.id)],
            order='revision_number desc, id desc',
            limit=1,
        )
        revision_values = {
            'name': version,
            'state': 'approved',
            'reviewed_by_user_id': self.env.user.id,
            'reviewed_at': fields.Datetime.now(),
        }
        if latest and latest.name == version:
            latest.write(revision_values)
        else:
            revision_values.update({
                'order_id': order.id,
                'submitted_by_user_id': order.user_id_design.id,
                'submitted_at': fields.Datetime.now(),
                'parent_revision_id': latest.id,
            })
            revision_model.create(revision_values)
        order.write({
            'design_done': True,
            'design_done_date': fields.Datetime.now(),
            'design_done_user_id': self.env.user.id,
        })
        return {'type': 'ir.actions.client', 'tag': 'reload'}
