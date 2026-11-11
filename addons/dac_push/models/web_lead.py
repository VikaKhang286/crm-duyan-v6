import re
from urllib.parse import urlsplit

from odoo import api, fields, models

from .push_event import clean

FORM_TYPES = [('callback', 'Gọi lại'), ('quote', 'Báo giá'), ('bee_form', 'Bee Form'),
              ('elementor', 'Elementor'), ('other', 'Khác')]


class WebLead(models.Model):
    _name = 'dac.web.lead'
    _description = 'Lead từ form website'
    _order = 'received_at desc, id desc'

    event_id = fields.Many2one('dac.push.event', string='Sự kiện', ondelete='set null', index=True, readonly=True)
    site = fields.Char(string='Website', index=True)
    form_type = fields.Selection(FORM_TYPES, string='Loại form', default='other', index=True)
    form_name = fields.Char(string='Tên form')
    customer_name = fields.Char(string='Họ tên')
    phone = fields.Char(string='Số điện thoại', index=True)
    email = fields.Char(string='Email')
    company_name = fields.Char(string='Công ty')
    items = fields.Char(string='Hạng mục tư vấn')
    message = fields.Text(string='Nội dung')
    page_url = fields.Char(string='Trang gửi')
    page_title = fields.Char(string='Tiêu đề trang gửi')
    extra_info = fields.Text(string='Thông tin khách đã nhập',
        help='Mọi trường khác trong form (ghi chú, yêu cầu bổ sung, kích thước...), mỗi dòng "Nhãn: giá trị".')
    visited_urls = fields.Text(string='Trang khách đã xem (24 giờ)', help='Lấy từ Bee CRM, cũ → mới.')
    source_info = fields.Char(string='Nguồn truy cập')
    phone_only = fields.Boolean(string='Chỉ để lại số điện thoại', compute='_compute_phone_only', store=True)
    submitted_at = fields.Char(string='Thời điểm gửi (trên web)')
    received_at = fields.Datetime(string='Nhận lúc', default=fields.Datetime.now, readonly=True)

    @api.depends('customer_name', 'phone')
    def _compute_display_name(self):
        for lead in self:
            lead.display_name = ' - '.join(p for p in (lead.customer_name, lead.phone) if p) or 'Lead web'

    @api.depends('customer_name', 'email', 'company_name', 'items', 'message', 'extra_info', 'phone')
    def _compute_phone_only(self):
        for lead in self:
            lead.phone_only = bool(lead.phone) and not any(
                (lead.customer_name, lead.email, lead.company_name, lead.items, lead.message, lead.extra_info))

    def _short_link(self):
        """Link rút gọn tới lead, không có scheme cho gọn (Zalo tự nhận ra thành link): crm.duyan.vn/lead/736."""
        self.ensure_one()
        # sudo(): đọc tham số hệ thống trong luồng webhook/cron (không có user)
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        return '%s/lead/%s' % (urlsplit(base).netloc or 'crm.duyan.vn', self.id)

    def _zalo_message(self):
        """Tin Zalo gọn: tiêu đề in đậm, tên (nếu có), số điện thoại, yêu cầu, ghi chú và link xem chi tiết.

        Trang đã xem, nguồn, email, công ty... có đầy đủ ở trang chi tiết (link cuối tin). Trả về (text, styles).
        """
        self.ensure_one()
        title = 'Khách yêu cầu tư vấn'
        lines = [title]
        if self.customer_name:
            lines.append('👤 ' + self.customer_name)
        if self.phone:
            lines.append('📞 ' + self.phone)
        wanted = [self.items.strip()] if (self.items or '').strip() else []  # hạng mục: "In bạt, Standee"
        # Trường dạng URL (vd trang sản phẩm) bỏ khỏi dòng yêu cầu cho gọn; vẫn có ở trang chi tiết.
        wanted += [ln.strip() for ln in (self.extra_info or '').splitlines()
                   if ln.strip() and 'http://' not in ln and 'https://' not in ln]
        if wanted:
            lines.append('➕ Yêu cầu: ' + '; '.join(wanted)[:300])
        if self.message:
            lines.append('💬 ' + self.message[:500])
        lines.append('Xem chi tiết: ' + self._short_link())
        return '\n'.join(lines), [{'start': 0, 'len': len(title), 'style': 'bold'}]

    def _zalo_text(self):
        return self._zalo_message()[0]

class PushEventWebLead(models.Model):
    _inherit = 'dac.push.event'


    def _handle_web_lead_created(self):
        self.ensure_one()
        data = self.payload or {}
        form_type = data.get('form_type') if data.get('form_type') in dict(FORM_TYPES) else 'other'
        items = data.get('items')
        if isinstance(items, (list, tuple)):
            items = ', '.join(str(i) for i in items)
        extra_lines = []
        for item in (data.get('fields') if isinstance(data.get('fields'), list) else [])[:30]:
            if isinstance(item, dict) and clean(item.get('value'), 1000):
                extra_lines.append('%s: %s' % (clean(item.get('label'), 100) or 'Thông tin', clean(item.get('value'), 1000)))
        site = clean(data.get('site') or self.source, 100)
        visited_lines = []
        for page in (data.get('visited') if isinstance(data.get('visited'), list) else [])[:20]:
            if isinstance(page, dict) and clean(page.get('url'), 500):
                at = clean(page.get('at'), 19)
                title = clean(page.get('title'), 120)
                # URL đầy đủ (cả tên miền và tham số truy vấn), không rút gọn
                visited_lines.append(' — '.join(p for p in (at[11:16] if len(at) >= 16 else '', clean(page.get('url'), 500), title) if p))
        src = data.get('source') if isinstance(data.get('source'), dict) else {}
        source_parts = [clean(src.get(k), 100) for k in ('utm_source', 'utm_medium', 'utm_campaign') if clean(src.get(k), 100)]
        if not source_parts and clean(src.get('referrer'), 200):
            source_parts = [urlsplit(clean(src.get('referrer'), 200)).hostname or clean(src.get('referrer'), 100)]
        lead = self.env['dac.web.lead'].search([('event_id', '=', self.id)], limit=1) or self.env['dac.web.lead'].create({
            'event_id': self.id, 'site': clean(data.get('site') or self.source, 100), 'form_type': form_type,
            'form_name': clean(data.get('form_name'), 200), 'customer_name': clean(data.get('name'), 200),
            'phone': re.sub(r'[^\d+]', '', str(data.get('phone') or ''))[:20], 'email': clean(data.get('email'), 200),
            'company_name': clean(data.get('company'), 200), 'items': clean(items, 500),
            'message': clean(data.get('message'), 2000), 'page_url': clean(data.get('page_url'), 500),
            'submitted_at': clean(data.get('submitted_at'), 50), 'page_title': clean(data.get('page_title'), 200),
            'extra_info': '\n'.join(extra_lines), 'visited_urls': '\n'.join(visited_lines),
            'source_info': ' / '.join(source_parts),
        })
        text, styles = lead._zalo_message()
        self.env['dac.zalo.channel']._notify('web_lead.created', text, 'push:%s' % self.id, styles=styles)
