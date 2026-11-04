import re
import hashlib
import json

from odoo import fields, models, _
from odoo.exceptions import ValidationError


class ResCompany(models.Model):
    _inherit = 'res.company'

    meta_conversion_enabled = fields.Boolean(string='Chuyển đổi Meta')
    meta_conversion_setup_path = fields.Selection([
        ('new_app', 'Tôi cần tạo Meta App mới'),
        ('existing_app', 'Tôi đã có app tích hợp Messenger'),
    ], string='Cách chuẩn bị ứng dụng', default='new_app', required=True, groups='base.group_system')
    meta_conversion_app_id = fields.Char(string='Meta App ID', groups='base.group_system', copy=False)
    meta_conversion_dataset_id = fields.Char(string='Dataset / Pixel ID', groups='base.group_system')
    meta_conversion_page_id = fields.Char(string='Facebook Page ID', groups='base.group_system')
    meta_conversion_access_token = fields.Char(string='Access Token Meta', groups='base.group_system', copy=False)
    meta_conversion_api_version = fields.Char(string='Phiên bản Graph API', default='v26.0', groups='base.group_system')
    meta_conversion_test_code = fields.Char(string='Mã sự kiện thử nghiệm', groups='base.group_system', copy=False)
    meta_conversion_channel = fields.Selection([
        ('messenger', 'Quảng cáo Messenger (Page ID + PSID)'),
        ('chat', 'Tin nhắn — so khớp bằng số điện thoại / email'),
    ], string='Nguồn chuyển đổi', default='messenger', required=True, groups='base.group_system')
    meta_conversion_checked_at = fields.Datetime(string='Kiểm tra gần nhất', groups='base.group_system', copy=False)
    meta_conversion_check_status = fields.Selection([
        ('unchecked', 'Chưa kiểm tra'), ('ok', 'Đã xác nhận kết nối đọc'),
        ('partial', 'Cần kiểm tra thêm'), ('error', 'Chưa kết nối được'),
    ], default='unchecked', groups='base.group_system', copy=False)
    meta_conversion_check_message = fields.Text(string='Chi tiết kiểm tra', groups='base.group_system', copy=False)
    meta_conversion_check_fingerprint = fields.Char(groups='base.group_system', copy=False)

    def _meta_connection_fingerprint(self):
        self.ensure_one()
        values = [self.meta_conversion_app_id, self.meta_conversion_channel, self.meta_conversion_api_version,
                  self.meta_conversion_dataset_id, self.meta_conversion_page_id,
                  self.meta_conversion_access_token]
        return hashlib.sha256(json.dumps(values).encode()).hexdigest()

    def _check_meta_configuration(self, force=False):
        for company in self.sudo():
            if not force and not company.meta_conversion_enabled:
                continue
            if company.meta_conversion_app_id and not re.fullmatch(r'[0-9]+', company.meta_conversion_app_id):
                raise ValidationError(_('Meta App ID phải là ID số lấy từ App Dashboard, không phải URL hoặc Access Token.'))
            if not (company.meta_conversion_access_token or '').strip():
                raise ValidationError(_('Hãy nhập Access Token Meta trước khi bật Chuyển đổi Meta.'))
            if not re.fullmatch(r'[0-9]+', company.meta_conversion_dataset_id or ''):
                raise ValidationError(_('Dataset / Pixel ID phải là ID số do Meta cung cấp.'))
            if company.meta_conversion_channel == 'messenger' and not re.fullmatch(r'[0-9]+', company.meta_conversion_page_id or ''):
                raise ValidationError(_('Luồng Messenger cần Facebook Page ID dạng số.'))
            if not re.fullmatch(r'v[0-9]+\.0', company.meta_conversion_api_version or ''):
                raise ValidationError(_('Phiên bản Graph API phải có dạng v26.0.'))
            token = company.meta_conversion_access_token.strip()
            if any(char.isspace() for char in token):
                raise ValidationError(_('Access Token không được chứa khoảng trắng. Chỉ dán token, không kèm chữ Bearer.'))
