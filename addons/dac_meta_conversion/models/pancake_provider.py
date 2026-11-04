import re
from datetime import timedelta

from odoo import fields, models, _
from odoo.exceptions import ValidationError


class MetaConversionEvent(models.Model):
    _inherit = 'dac.meta.conversion.event'

    delivery_provider = fields.Selection([('meta_direct', 'Meta trực tiếp'), ('pancake_pos', 'Pancake POS')],
        default='meta_direct', required=True, readonly=True, index=True, string='Đích gửi')
    pancake_shop_id = fields.Char(string='Shop POS lúc tạo đơn', readonly=True)
    pancake_remote_id = fields.Char(string='Mã đơn POS đã nhận', readonly=True)
    pancake_fingerprint = fields.Char(readonly=True, groups='base.group_system')

    def _deliver(self):
        self.ensure_one()
        if self.delivery_provider != 'pancake_pos':
            if self.company_id.sudo().meta_conversion_provider != 'meta_direct':
                return  # Never reroute an existing direct event into a new provider.
            return super()._deliver()
        return self._deliver_pancake()

    def _cron_dispatch(self):
        stale = self.sudo().search([('delivery_provider', '=', 'pancake_pos'), ('state', '=', 'sending'),
            ('last_attempt_at', '<', fields.Datetime.now() - timedelta(minutes=15))])
        self.env['dac.pancake.api.log'].sudo().search([('order_id', 'in', stale.order_id.ids),
            ('state', '=', 'started')]).write({'state': 'uncertain', 'completed_at': fields.Datetime.now(),
                'summary': _('Tiến trình POS bị gián đoạn. Đối soát trước khi gửi lại.')})
        return super()._cron_dispatch()

    def _pancake_payload(self):
        self.ensure_one()
        company, order = self.company_id.sudo(), self.order_id
        conversation = order.conversation_id
        if not conversation:
            candidates = self.env['page.fm.conversation'].search([
                ('partner_id', '=', order.partner_id.id), ('platform_fm', '=ilike', 'facebook'),
                ('conversation_type_fm', '=', 'INBOX'), ('is_internal_conversation', '=', False),
                ('page_fm_page_id.page_fm_id_str', '=', self.page_id)], limit=2)
            if len(candidates) == 1:
                conversation = candidates
        if not conversation or conversation.partner_id != order.partner_id or conversation.is_internal_conversation:
            return {}, _('Gắn đúng hội thoại Pancake của khách trên đơn trước khi gửi POS.')
        page = conversation.conv_page_fm_id or conversation.page_fm_page_id.page_fm_id_str
        if page != self.page_id or (conversation.platform_fm or '').lower() != 'facebook' or conversation.conversation_type_fm != 'INBOX':
            return {}, _('Chỉ nhận inbox Facebook của đúng Page đã cấu hình; không dùng Instagram, bình luận hoặc hội thoại nội bộ.')
        if not conversation.conversation_fm_id:
            return {}, _('Hội thoại chưa có ID Pancake. Đồng bộ lại rồi kiểm tra.')
        if order.pancake_order_id:
            return {}, _('Đơn đã có mã POS. Đối soát trước khi xuất để tránh tạo thêm đơn.')
        if order.currency_id.name != 'VND' or order.amount_total <= 0 or not order.partner_id:
            return {}, _('Luồng POS hiện hỗ trợ đơn VND có khách hàng và tổng tiền lớn hơn 0.')
        if abs(order.amount_total - round(order.amount_total)) > 0.001:
            return {}, _('POS cần tổng tiền VND nguyên; kiểm tra làm tròn tổng đơn.')
        reference = 'ODOO-' + self.event_id
        # One service SKU deliberately carries the final CRM total, including
        # tax/discount/shipping already included in that total. No stock SKU sync.
        payload = {'shop_id': int(self.pancake_shop_id), 'status': 0,
            'custom_id': reference, 'note': reference, 'page_id': self.page_id,
            'conversation_id': conversation.conversation_fm_id,
            'bill_full_name': order.partner_id.name,
            'bill_phone_number': order.partner_id.mobile or order.partner_id.phone or '',
            'bill_email': order.partner_id.email or '', 'order_currency': 'VND',
            'warehouse_id': company.pancake_pos_warehouse_id,
            'items': [{'variation_id': company.pancake_pos_summary_sku, 'quantity': 1,
                'variation_info': {'retail_price': round(order.amount_total)}}],
            'shipping_fee': 0, 'total_discount': 0, 'surcharge': 0, 'received_at_shop': False,
            'is_smc': False}
        return payload, ''

    def _pancake_call(self, operation, method, path, payload=None):
        company = self.company_id.sudo()
        Log = self.env['dac.pancake.api.log']
        log = Log._start(company, operation, method, path, payload, self.order_id)
        # Both the stable reference and the started log survive a killed worker.
        self.env.cr.commit()
        return Log._request(company, operation, method, path, payload=payload, order=self.order_id, log=log)

    def _pancake_find(self):
        reference = self.order_id.pancake_export_reference
        if not reference:
            return None, False
        result = self.env['dac.pancake.api.log']._request(self.company_id,
            'Đối soát mã đơn CRM trên POS', 'GET', 'shops/%s/orders' % self.pancake_shop_id,
            params={'search': reference, 'page_size': 100, 'page_number': 1}, order=self.order_id)
        rows = result['data'].get('data')
        if not result['ok'] or not isinstance(rows, list):
            return None, False
        matched = [row for row in rows if isinstance(row, dict) and row.get('custom_id') == reference]
        if len(matched) > 1:
            return None, False
        if matched:
            return matched[0], True
        # An incomplete search never authorizes another POST.
        total = result['data'].get('total_entries')
        pages = result['data'].get('total_pages')
        complete = (isinstance(total, int) and total <= len(rows)) or (isinstance(pages, int) and pages <= 1)
        return None, complete

    def _pancake_record(self, result):
        data = result.get('data', {})
        row = data.get('data') if isinstance(data.get('data'), dict) else data
        remote_id = str(row.get('id') or '')
        return row if re.fullmatch(r'[0-9]+', remote_id) and str(row.get('shop_id') or '') == self.pancake_shop_id else None

    def _pancake_validate_remote(self, row):
        payload = self.payload or {}
        if (row.get('custom_id') != payload.get('custom_id') or str(row.get('shop_id')) != self.pancake_shop_id
                or str(row.get('page_id')) != self.page_id
                or row.get('conversation_id') != payload.get('conversation_id')
                or row.get('order_currency') != 'VND'):
            return False
        expected = payload['items'][0]['variation_info']['retail_price']
        # POS total_price is returned by the order API; do not confirm a draft
        # if pricing was transformed by stock, promotions, taxes or defaults.
        if any(row.get(key) not in (None, 0) for key in ('total_discount', 'shipping_fee', 'surcharge', 'discount_by_customer_level')):
            return False
        return isinstance(row.get('total_price'), (int, float)) and abs(row['total_price'] - expected) < 0.5

    def _pancake_mark_received(self, row):
        self.write({'pancake_remote_id': str(row['id']), 'state': 'pos_sent',
            'sent_at': fields.Datetime.now(),
            'message': _('Pancake POS đã xác nhận đơn. Cần đối chiếu Purchase trong Events Manager và doanh thu trong Ads Manager; chưa có xác nhận trực tiếp từ Meta.')})
        self.order_id.write({'pancake_order_id': str(row['id']), 'pancake_system_id': str(row.get('system_id') or '')})

    def _pancake_readonly_reconcile(self):
        self.ensure_one()
        self.env.cr.execute('SELECT id FROM dac_meta_conversion_event WHERE id=%s FOR UPDATE NOWAIT', [self.id])
        self.invalidate_recordset()
        if self.state == 'sending':
            return
        company = self.company_id.sudo()
        if company.pancake_pos_shop_id != self.pancake_shop_id:
            self.write({'message': _('Khôi phục đúng Shop ID của sự kiện trước khi đối soát.')})
            return
        row, complete = self._pancake_find()
        if row and self._pancake_validate_remote(row) and row.get('status') == 1:
            self._pancake_mark_received(row)
        else:
            self.write({'message': _('Đối soát chỉ đọc chưa tìm thấy đúng đơn Đã xác nhận với giá trị và hội thoại khớp. Kiểm tra POS thủ công; không tự tạo hoặc xác nhận lại đơn.')})

    def _deliver_pancake(self):
        self.env.cr.execute('SELECT id FROM dac_meta_conversion_event WHERE id=%s FOR UPDATE SKIP LOCKED', [self.id])
        if not self.env.cr.fetchone():
            return
        self.invalidate_recordset()
        if self.state not in ('pending', 'waiting', 'retry') or self.next_attempt_at > fields.Datetime.now():
            return
        company = self.company_id.sudo()
        if not company.meta_conversion_enabled or company.meta_conversion_provider != 'pancake_pos':
            return
        try:
            company._check_pancake_export()
        except ValidationError:
            self.write({'state': 'waiting', 'message': _('Kiểm tra lại cấu hình Kết nối Pancake trước khi gửi POS.'),
                'next_attempt_at': fields.Datetime.now() + timedelta(minutes=5)})
            return
        if company.pancake_pos_shop_id != self.pancake_shop_id or company.pancake_pos_page_id != self.page_id:
            self.write({'state': 'error', 'message': _('Shop/Page hiện tại khác lúc tạo đơn; khôi phục cấu hình và đối soát.')})
            return
        if self.order_id.state == 'cancel' or self.order_id.order_state_custom == 'cancel':
            self.write({'state': 'cancelled', 'message': _('Đơn đã hủy trước khi xuất POS.')})
            return
        if self.event_time < fields.Datetime.now() - timedelta(days=7):
            self.write({'state': 'error', 'message': _('Đơn đã quá 7 ngày; không tự thay thời gian mua để gửi lại.')})
            return
        if self.pancake_fingerprint and self.pancake_fingerprint != company._pancake_fingerprint():
            self.write({'state': 'error', 'message': _('Cấu hình đã đổi sau khi chuẩn bị đơn POS; đối soát trước khi gửi tiếp.')})
            return
        payload, reason = (self.payload, '') if self.payload else self._pancake_payload()
        if reason:
            self.write({'state': 'waiting', 'message': reason, 'next_attempt_at': fields.Datetime.now() + timedelta(minutes=5)})
            return
        self.write({'payload': payload, 'pancake_fingerprint': company._pancake_fingerprint()})
        self.order_id.write({'pancake_export_reference': payload['custom_id'], 'pancake_export_shop_id': self.pancake_shop_id})
        row, complete = self._pancake_find()
        if not complete:
            self.write({'state': 'waiting', 'message': _('Chưa tra cứu đầy đủ được mã đơn trên POS. Chưa tạo thêm đơn.'),
                'next_attempt_at': fields.Datetime.now() + timedelta(minutes=5)})
            return
        if row:
            if self._pancake_validate_remote(row) and row.get('status') == 1:
                self._pancake_mark_received(row)
            else:
                self.write({'state': 'uncertain', 'message': _('Đã thấy mã đối chiếu trên POS. Kiểm tra đơn đã có; không tự tạo hoặc xác nhận lại.')})
            return
        self.write({'state': 'sending', 'attempt_count': self.attempt_count + 1, 'last_attempt_at': fields.Datetime.now(),
            'message': _('Đang tạo đơn nháp POS và kiểm tra giá trị trước khi xác nhận.')})
        path = 'shops/%s/orders' % self.pancake_shop_id
        result = self._pancake_call('Tạo đơn nháp POS từ CRM', 'POST', path, payload)
        row = self._pancake_record(result)
        if not result['ok'] or not row:
            self.write({'state': 'uncertain', 'message': _('Chưa xác định POS đã tạo đơn hay chưa. Dùng Đối soát POS; không tự gửi lại POST.')})
            return
        self.write({'pancake_remote_id': str(row['id'])})
        self.order_id.write({'pancake_order_id': str(row['id']), 'pancake_system_id': str(row.get('system_id') or '')})
        # Re-read instead of trusting a create acknowledgement that can omit
        # calculated totals. Only one transition to confirmed is ever sent.
        result = self._pancake_call('Đọc lại giá trị đơn nháp POS', 'GET', path + '/' + str(row['id']))
        row = self._pancake_record(result)
        if not result['ok'] or not row or not self._pancake_validate_remote(row) or row.get('status') != 0:
            self.write({'state': 'uncertain', 'message': _('Đơn POS chưa khớp giá trị, tiền tệ hoặc hội thoại/trạng thái. Đã dừng trước bước xác nhận; kiểm tra POS thủ công.')})
            return
        result = self._pancake_call('Xác nhận đơn POS để kích hoạt Purchase của Pancake', 'PUT',
            path + '/' + str(row['id']), {'status': 1})
        confirmed = self._pancake_record(result)
        if result['ok'] and confirmed and confirmed.get('status') == 1 and self._pancake_validate_remote(confirmed):
            self._pancake_mark_received(confirmed)
        else:
            self.write({'state': 'uncertain', 'message': _('Chưa xác nhận được trạng thái cuối trên POS. Đối soát POS/Meta; không tự gửi lại bước xác nhận.')})
