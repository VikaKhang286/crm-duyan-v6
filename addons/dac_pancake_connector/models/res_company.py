import hashlib
import json
import re

from odoo import fields, models, _
from odoo.exceptions import ValidationError


class ResCompany(models.Model):
    _inherit = 'res.company'

    pancake_pos_api_key = fields.Char(string='API key Pancake POS', groups='base.group_system', copy=False)
    pancake_pos_shop_id = fields.Char(string='Shop ID Pancake POS', groups='base.group_system')
    pancake_pos_page_id = fields.Char(string='Page ID Facebook Messenger', groups='base.group_system')
    pancake_pos_summary_sku = fields.Char(string='SKU tổng giá trị đơn trên POS', groups='base.group_system')
    pancake_pos_warehouse_id = fields.Char(string='Warehouse ID trên POS', groups='base.group_system')
    pancake_pos_capi_ready = fields.Boolean(string='Đã cấu hình Purchase khi Đã xác nhận trên POS', groups='base.group_system')
    pancake_pos_silent_ready = fields.Boolean(string='Đã tắt hóa đơn, thông báo và Botcake cho luồng này', groups='base.group_system')
    pancake_pos_product_ready = fields.Boolean(string='SKU tổng giá trị là sản phẩm dịch vụ phù hợp, không trừ kho hàng thật', groups='base.group_system')
    pancake_pos_checked_at = fields.Datetime(string='Lần kiểm tra POS', groups='base.group_system', copy=False)
    pancake_pos_check_status = fields.Selection([('unchecked', 'Chưa kiểm tra'), ('ok', 'Đọc được shop'), ('error', 'Lỗi kết nối')], default='unchecked', groups='base.group_system', copy=False)
    pancake_pos_check_message = fields.Text(string='Kết quả kiểm tra POS', groups='base.group_system', copy=False)
    pancake_pos_check_fingerprint = fields.Char(groups='base.group_system', copy=False)

    def _pancake_fingerprint(self):
        self.ensure_one()
        return hashlib.sha256(json.dumps([self.pancake_pos_api_key, self.pancake_pos_shop_id,
            self.pancake_pos_page_id, self.pancake_pos_summary_sku, self.pancake_pos_warehouse_id,
            self.pancake_pos_capi_ready, self.pancake_pos_silent_ready, self.pancake_pos_product_ready]).encode()).hexdigest()

    def _check_pancake_export(self):
        self.ensure_one()
        if not re.fullmatch(r'[0-9]+', self.pancake_pos_shop_id or '') or not (self.pancake_pos_api_key or '').strip():
            raise ValidationError(_('Nhập API key và Shop ID dạng số trong Kết nối Pancake.'))
        if not re.fullmatch(r'[0-9]+', self.pancake_pos_page_id or ''):
            raise ValidationError(_('Nhập Page ID Facebook Messenger trong Kết nối Pancake.'))
        if not (self.pancake_pos_summary_sku or '').strip() or not (self.pancake_pos_warehouse_id or '').strip():
            raise ValidationError(_('Chọn SKU tổng giá trị đơn và Warehouse ID của POS.'))
        if not all([self.pancake_pos_capi_ready, self.pancake_pos_silent_ready, self.pancake_pos_product_ready]):
            raise ValidationError(_('Hoàn tất và xác nhận cấu hình CAPI, tắt tin nhắn/hóa đơn và SKU dịch vụ trên POS trước khi gửi đơn.'))
        if self.pancake_pos_check_status != 'ok' or self.pancake_pos_check_fingerprint != self._pancake_fingerprint():
            raise ValidationError(_('Lưu rồi kiểm tra lại kết nối Pancake POS sau khi thay đổi cấu hình.'))
