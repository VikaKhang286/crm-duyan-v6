import hashlib
import json
import re

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError


CONFIG_FIELDS = (
    'pancake_pos_api_key', 'pancake_pos_shop_id', 'pancake_pos_page_id',
    'pancake_pos_summary_sku', 'pancake_pos_warehouse_id', 'pancake_pos_capi_ready',
    'pancake_pos_silent_ready', 'pancake_pos_product_ready',
)
ITEM_FIELDS = ('pancake_pos_item_mode', 'pancake_pos_item_name')


class PancakePosConnection(models.Model):
    _name = 'dac.pancake.pos.connection'
    _description = 'Kết nối shop Pancake POS'
    _order = 'name, id'
    _check_company_auto = True

    name = fields.Char(string='Tên kết nối', required=True, default='Shop POS mới')
    active = fields.Boolean(default=True, string='Đang sử dụng')
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company, index=True)
    pancake_pos_api_key = fields.Char(string='API key POS', groups='base.group_system', copy=False)
    pancake_pos_shop_id = fields.Char(string='Shop ID')
    pancake_pos_shop_name = fields.Char(string='Tên shop từ Pancake', readonly=True, copy=False)
    pancake_pos_page_id = fields.Char(string='Page ID Facebook Messenger')
    pancake_pos_item_mode = fields.Selection([
        ('service_sku', 'Sản phẩm dịch vụ có sẵn'),
        ('one_time', 'Nhập tên và giá trực tiếp trên đơn'),
    ], string='Cách nhập dòng hàng POS', default='service_sku', required=True)
    pancake_pos_item_name = fields.Char(string='Tên dòng hàng', default='Giá trị đơn Odoo')
    pancake_pos_summary_sku = fields.Char(string='SKU dịch vụ tổng giá trị đơn')
    pancake_pos_warehouse_id = fields.Char(string='Warehouse ID của POS')
    pancake_pos_capi_ready = fields.Boolean(string='Đã bật Purchase khi Đã xác nhận trên shop POS')
    pancake_pos_silent_ready = fields.Boolean(string='Đã tắt hóa đơn, thông báo và Botcake cho luồng này')
    pancake_pos_product_ready = fields.Boolean(string='Đã chuẩn bị SKU dịch vụ không trừ kho hàng thật')
    pancake_pos_checked_at = fields.Datetime(string='Kiểm tra gần nhất', readonly=True, copy=False)
    pancake_pos_check_status = fields.Selection([('unchecked', 'Chưa kiểm tra'), ('ok', 'Đọc được shop'),
        ('error', 'Chưa đọc được shop')], string='Trạng thái kết nối', default='unchecked', readonly=True, copy=False)
    pancake_pos_check_message = fields.Text(string='Kết quả kiểm tra', readonly=True, copy=False)
    pancake_pos_check_fingerprint = fields.Char(readonly=True, copy=False, groups='base.group_system')
    available_shops = fields.Text(string='Shop và Page mà API key truy cập được', readonly=True, copy=False)
    check_is_current = fields.Boolean(string='Kết quả còn khớp cấu hình', compute='_compute_check_current')
    api_log_ids = fields.One2many('dac.pancake.api.log', 'pos_connection_id', string='Lịch sử API', readonly=True)

    @api.constrains('pancake_pos_shop_id', 'pancake_pos_page_id')
    def _check_ids(self):
        for connection in self:
            for name in ('pancake_pos_shop_id', 'pancake_pos_page_id'):
                if connection[name] and not re.fullmatch(r'[0-9]+', connection[name]):
                    raise ValidationError(_('Shop ID và Page ID phải là dãy số. Có thể để trống Shop ID để tra cứu từ API key.'))

    def _pancake_fingerprint(self):
        self.ensure_one()
        values = [self.active] + [self.sudo()[name] for name in CONFIG_FIELDS]
        # Preserve existing SKU-mode fingerprints and queued deliveries.
        if self.pancake_pos_item_mode == 'one_time':
            values += ['one_time', (self.pancake_pos_item_name or '').strip()]
        return hashlib.sha256(json.dumps(values).encode()).hexdigest()

    @api.depends('active', *CONFIG_FIELDS, *ITEM_FIELDS, 'pancake_pos_check_fingerprint')
    def _compute_check_current(self):
        for record in self:
            record.check_is_current = bool(record.sudo().pancake_pos_check_fingerprint and
                record.sudo().pancake_pos_check_fingerprint == record._pancake_fingerprint())

    def _check_pancake_export(self):
        self.ensure_one()
        if not self.active:
            raise ValidationError(_('Kết nối POS này đã ngừng sử dụng.'))
        if not self.pancake_pos_api_key or not re.fullmatch(r'[0-9]+', self.pancake_pos_shop_id or ''):
            raise ValidationError(_('Nhập API key và kiểm tra để chọn đúng Shop ID.'))
        if not re.fullmatch(r'[0-9]+', self.pancake_pos_page_id or ''):
            raise ValidationError(_('Nhập đúng Page ID Facebook Messenger của kết nối này.'))
        if not self.pancake_pos_warehouse_id:
            raise ValidationError(_('Nhập Warehouse ID cho shop này.'))
        if self.pancake_pos_item_mode == 'one_time':
            if not (self.pancake_pos_item_name or '').strip():
                raise ValidationError(_('Nhập tên dòng hàng dùng trên đơn POS.'))
        elif not self.pancake_pos_summary_sku or not self.pancake_pos_product_ready:
            raise ValidationError(_('Nhập và xác nhận SKU dịch vụ, hoặc chọn nhập tên và giá trực tiếp trên đơn.'))
        if not self.pancake_pos_capi_ready or not self.pancake_pos_silent_ready:
            raise ValidationError(_('Hoàn tất CAPI và tắt thông báo/hóa đơn trên đúng shop này.'))
        if self.pancake_pos_check_status != 'ok' or not self.check_is_current:
            raise ValidationError(_('Lưu và kiểm tra lại kết nối sau khi thay đổi cấu hình.'))

    def action_check_connection(self):
        self.ensure_one()
        if not self.env.user.has_group('base.group_system'):
            raise AccessError(_('Chỉ quản trị viên được kiểm tra API key POS.'))
        self.check_access('write')
        if not self.active:
            raise ValidationError(_('Bỏ lưu trữ kết nối trước khi kiểm tra.'))
        ok = False
        message = _('Nhập API key, lưu rồi kiểm tra. Nếu key chỉ có một shop, hệ thống tự điền Shop ID.')
        if self.pancake_pos_api_key:
            result = self.env['dac.pancake.api.log']._request(self.company_id, 'Kiểm tra / tìm shop POS',
                'GET', 'shops', connection=self)
            shops = result['data'].get('shops', [])
            shops = [row for row in shops if isinstance(row, dict)] if isinstance(shops, list) else []
            self.available_shops = '\n'.join('%s — Shop ID: %s; Page Facebook: %s' % (
                row.get('name') or 'Shop', row.get('id') or '',
                ', '.join(str(page.get('id')) for page in row.get('pages', []) if isinstance(page, dict) and page.get('platform') == 'facebook')) for row in shops)[:12000]
            if result['ok'] and not self.pancake_pos_shop_id and len(shops) == 1 and re.fullmatch(r'[0-9]+', str(shops[0].get('id') or '')):
                self.pancake_pos_shop_id = str(shops[0]['id'])
            shop = next((row for row in shops if str(row.get('id')) == self.pancake_pos_shop_id), None)
            ok = result['ok'] and bool(shop)
            if shop:
                self.pancake_pos_shop_name = str(shop.get('name') or '')[:200]
                if self.name == 'Shop POS mới' and self.pancake_pos_shop_name:
                    self.name = self.pancake_pos_shop_name
            if ok and self.pancake_pos_page_id:
                ok = any(str(row.get('id')) == self.pancake_pos_page_id and row.get('platform') == 'facebook'
                    for row in shop.get('pages', []) if isinstance(row, dict))
            message = _('Đọc được đúng shop và Page (nếu đã nhập). Chưa xác minh quyền tạo đơn, SKU/kho, CAPI hoặc tắt thông báo.') if ok else (
                _('API key có nhiều shop: chọn Shop ID từ danh sách bên dưới rồi kiểm tra lại.') if result['ok'] and shops and not self.pancake_pos_shop_id else
                _('Chưa đọc được đúng shop/Page. Kiểm tra API key và quyền shop; xem Lịch sử API.'))
        self.write({'pancake_pos_check_status': 'ok' if ok else 'error', 'pancake_pos_check_message': message,
            'pancake_pos_checked_at': fields.Datetime.now(), 'pancake_pos_check_fingerprint': self._pancake_fingerprint()})
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': _('Kiểm tra kết nối POS'), 'message': message, 'sticky': True,
            'type': 'success' if ok else 'warning', 'next': {
                'type': 'ir.actions.act_window', 'name': self.name, 'res_model': self._name,
                'res_id': self.id, 'views': [(self.env.ref('dac_pancake_connector.pos_connection_form').id, 'form')],
                'view_mode': 'form', 'target': 'current'}}}

    def action_open_api_logs(self):
        self.ensure_one()
        self.check_access('read')
        action = self.env.ref('dac_pancake_connector.action_pancake_api_logs').read()[0]
        action['domain'] = [('pos_connection_id', '=', self.id)]
        return action

    @api.model
    def _migrate_legacy_connections(self):
        for company in self.env['res.company'].sudo().search([]):
            if company.pancake_pos_connection_id or not company.pancake_pos_api_key:
                continue
            old_fingerprint = company._pancake_fingerprint()
            current = company.pancake_pos_check_fingerprint == old_fingerprint
            values = {name: company[name] for name in CONFIG_FIELDS}
            values.update(name='Kết nối POS hiện có — %s' % (company.pancake_pos_shop_id or company.name),
                company_id=company.id, pancake_pos_checked_at=company.pancake_pos_checked_at,
                pancake_pos_check_status=company.pancake_pos_check_status,
                pancake_pos_check_message=company.pancake_pos_check_message)
            connection = self.sudo().create(values)
            if current:
                connection.pancake_pos_check_fingerprint = connection._pancake_fingerprint()
            company.pancake_pos_connection_id = connection
            self.env['dac.pancake.api.log'].sudo().search([('company_id', '=', company.id),
                ('pos_connection_id', '=', False), ('endpoint', '=like', 'https://pos.pages.fm/api/v1/%')]).write({
                    'pos_connection_id': connection.id, 'pos_shop_id': connection.pancake_pos_shop_id})
