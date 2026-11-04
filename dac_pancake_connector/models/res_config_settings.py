import re

from odoo import fields, models, _
from odoo.exceptions import AccessError


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    pancake_pos_api_key = fields.Char(related='company_id.pancake_pos_api_key', readonly=False)
    pancake_pos_shop_id = fields.Char(related='company_id.pancake_pos_shop_id', readonly=False)
    pancake_pos_page_id = fields.Char(related='company_id.pancake_pos_page_id', readonly=False)
    pancake_pos_summary_sku = fields.Char(related='company_id.pancake_pos_summary_sku', readonly=False)
    pancake_pos_warehouse_id = fields.Char(related='company_id.pancake_pos_warehouse_id', readonly=False)
    pancake_pos_capi_ready = fields.Boolean(related='company_id.pancake_pos_capi_ready', readonly=False)
    pancake_pos_silent_ready = fields.Boolean(related='company_id.pancake_pos_silent_ready', readonly=False)
    pancake_pos_product_ready = fields.Boolean(related='company_id.pancake_pos_product_ready', readonly=False)
    pancake_pos_checked_at = fields.Datetime(related='company_id.pancake_pos_checked_at')
    pancake_pos_check_status = fields.Selection(related='company_id.pancake_pos_check_status')
    pancake_pos_check_message = fields.Text(related='company_id.pancake_pos_check_message')

    def _pancake_admin(self):
        self.ensure_one()
        if not self.env.user.has_group('base.group_system'):
            raise AccessError(_('Chỉ quản trị viên được thiết lập kết nối Pancake.'))

    def action_pancake_connector_check_pos(self):
        self._pancake_admin()
        company = self.company_id.sudo()
        ok = False
        message = _('Nhập API key và Shop ID dạng số, bấm Lưu rồi kiểm tra lại.')
        if company.pancake_pos_api_key and re.fullmatch(r'[0-9]+', company.pancake_pos_shop_id or ''):
            result = self.env['dac.pancake.api.log']._request(company, 'Kiểm tra shop POS', 'GET', 'shops')
            shops = result['data'].get('shops', [])
            shop = next((row for row in shops if isinstance(row, dict) and str(row.get('id')) == company.pancake_pos_shop_id), None) if isinstance(shops, list) else None
            ok = result['ok'] and bool(shop)
            page = company.pancake_pos_page_id
            if ok and page:
                ok = any(str(row.get('id')) == page for row in shop.get('pages', []) if isinstance(row, dict))
            message = _('Đọc được đúng shop và Page đã nhập (nếu có). Kiểm tra này không tạo đơn và chưa xác minh CAPI, SKU, kho hoặc việc tắt thông báo.') if ok else _('Không đọc được đúng shop/Page. Kiểm tra API key, Shop ID và Page nối với shop. Xem lịch sử API; mã 103 thường cần cập nhật token.')
        company.write({'pancake_pos_check_status': 'ok' if ok else 'error',
            'pancake_pos_check_message': message, 'pancake_pos_checked_at': fields.Datetime.now(),
            'pancake_pos_check_fingerprint': company._pancake_fingerprint()})
        return self._pancake_notice(message, ok)

    def action_pancake_connector_check_chat(self):
        self._pancake_admin()
        result = self.env['dac.pancake.api.log']._request(self.company_id, 'Kiểm tra token hội thoại', 'GET', 'pages', chat=True)
        valid = result['ok'] and result['data'].get('success') is True
        expired = result['data'].get('error_code') == 103
        message = _('Token hội thoại hợp lệ. Mở Đồng bộ liên tục để tải danh sách Page và chọn Page cần dùng.') if valid else (_('Token hội thoại đã hết hạn (103). Lấy token mới từ Pancake, nhập lại rồi Lưu và kiểm tra.') if expired else _('Chưa kết nối được Pancake Chat. Kiểm tra token và lịch sử API.'))
        # Reuse the historic token status so both old and new dashboards agree.
        params = self.env['ir.config_parameter'].sudo()
        params.set_param('page_fm.token_check_status', 'valid' if valid else 'expired' if expired else 'invalid')
        params.set_param('page_fm.token_check_at', fields.Datetime.now())
        params.set_param('page_fm.token_check_message', message)
        return self._pancake_notice(message, valid)

    def _pancake_notice(self, message, ok):
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': _('Kết nối Pancake'), 'message': message, 'sticky': True,
            'type': 'success' if ok else 'warning', 'next': {'type': 'ir.actions.client', 'tag': 'reload'}}}
