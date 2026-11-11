import re
import secrets
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
