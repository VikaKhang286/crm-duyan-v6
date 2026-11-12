import re
import secrets
from datetime import timedelta
from urllib.parse import urlsplit

from odoo import _, api, fields, models
from odoo.exceptions import AccessError


class PushConfig(models.Model):
    """Cấu hình singleton. Secret nằm ở model riêng (chỉ nhóm Settings đọc), không để trong ir.config_parameter."""
    _name = 'dac.push.config'
    _description = 'Cấu hình nhận dữ liệu Push DAC Odoo'

    name = fields.Char(default='Push DAC Odoo', required=True)
    active = fields.Boolean(string='Bật nhận dữ liệu', default=False)
    secret = fields.Char(string='Khóa bí mật (HMAC)', copy=False, groups='base.group_system',
        help='Dán cùng giá trị này vào cài đặt plugin Push DAC Odoo trên WordPress.')
    endpoint_url = fields.Char(string='Địa chỉ nhận (dán vào plugin)', compute='_compute_endpoint_url')
    last_event_at = fields.Datetime(string='Nhận dữ liệu gần nhất', compute='_compute_health')
    events_24h = fields.Integer(string='Dữ liệu nhận 24 giờ qua', compute='_compute_health')
    failed_event_count = fields.Integer(string='Dữ liệu lỗi', compute='_compute_health')
    lead_count = fields.Integer(string='Lead từ website', compute='_compute_health')
    lead_channel_ids = fields.Many2many('dac.zalo.channel', string='Nơi nhận lead trên Zalo', compute='_compute_lead_channels',
        context={'active_test': False})
    lead_channel_count = fields.Integer(compute='_compute_lead_channels')

    def _compute_health(self):
        events = self.env['dac.push.event']
        last = events.search([], order='create_date desc', limit=1).create_date
        day_ago = fields.Datetime.now() - timedelta(hours=24)
        recent = events.search_count([('create_date', '>=', day_ago)])
        failed = events.search_count([('state', '=', 'failed')])
        leads = self.env['dac.web.lead'].search_count([])
        for config in self:
            config.last_event_at = last
            config.events_24h = recent
            config.failed_event_count = failed
            config.lead_count = leads

    def _compute_lead_channels(self):
        event = self.env.ref('dac_push.event_type_web_lead_created', raise_if_not_found=False)
        channels = self.env['dac.zalo.channel'].with_context(active_test=False).search(
            [('event_type_ids', 'in', event.ids)]) if event else self.env['dac.zalo.channel']
        for config in self:
            config.lead_channel_ids = channels
            config.lead_channel_count = len(channels)

    def action_add_lead_channel(self):
        """Mở danh sách hội thoại của tài khoản Zalo; hội thoại được chọn nhận sẵn thông báo lead mới."""
        return self.env['dac.zalo.thread.search'].action_open_for_events(['web_lead.created'])

    def action_open_lead_channels(self):
        action = self.env['ir.actions.actions']._for_xml_id('dac_zalo_assistant.channel_action')
        action.update(name=_('Nơi nhận lead'), domain=[('event_type_ids.code', '=', 'web_lead.created')],
                      context={'active_test': False})
        return action

    def action_open_leads(self):
        return self.env['ir.actions.actions']._for_xml_id('dac_push.web_lead_action')

    def action_open_events(self):
        return self.env['ir.actions.actions']._for_xml_id('dac_push.event_action')

    @api.depends_context('uid')
    def _compute_endpoint_url(self):
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '').rstrip('/')
        parts = urlsplit(base)
        local = parts.hostname in ('localhost', '127.0.0.1', '::1') or bool(re.search(r'\.(local|test|localhost)$', parts.hostname or ''))
        if parts.scheme == 'http' and not local:
            # web.base.url có thể còn là http dù site chạy sau HTTPS; plugin WordPress chỉ nhận https.
            base = 'https://' + base[len('http://'):]
        for config in self:
            config.endpoint_url = base + '/dac_push/v1/events'

    @api.model
    def _config(self):
        return self.env.ref('dac_push.config_default')

    def action_generate_secret(self):
        if not self.env.user.has_group('base.group_system'):
            raise AccessError(_('Chỉ quản trị hệ thống được tạo khóa.'))
        self.ensure_one()
        self.secret = secrets.token_urlsafe(32)


class ZaloConfigPush(models.Model):
    """Thẻ "Website" trong Trung tâm kết nối của Trợ lý Zalo."""
    _inherit = 'dac.zalo.config'

    push_state = fields.Selection([('off', 'Đang tắt'), ('waiting', 'Chờ dữ liệu đầu tiên'), ('ok', 'Đang nhận dữ liệu')],
        string='Kết nối website', compute='_compute_push', groups='base.group_system')
    push_last_event_at = fields.Datetime(string='Website nhận gần nhất', compute='_compute_push', groups='base.group_system')

    def _compute_push(self):
        push = self.env['dac.push.config']._config()
        last = self.env['dac.push.event'].search([], order='create_date desc', limit=1).create_date
        for config in self:
            config.push_last_event_at = last
            config.push_state = 'off' if not push.active else ('ok' if last else 'waiting')

    def action_open_push_config(self):
        return self.env['ir.actions.actions']._for_xml_id('dac_push.config_action')
