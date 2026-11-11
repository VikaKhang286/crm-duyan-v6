import re

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
    submitted_at = fields.Char(string='Thời điểm gửi (trên web)')
    received_at = fields.Datetime(string='Nhận lúc', default=fields.Datetime.now, readonly=True)

    @api.depends('customer_name', 'phone')
    def _compute_display_name(self):
        for lead in self:
            lead.display_name = ' - '.join(p for p in (lead.customer_name, lead.phone) if p) or 'Lead web'

    def _zalo_text(self):
        self.ensure_one()
        label = dict(FORM_TYPES).get(self.form_type, 'Form')
        lines = ['🔔 Khách mới từ %s' % (self.site or 'website'),
                 'Form: %s%s' % (label, ' (%s)' % self.form_name if self.form_name else '')]
        who = ' — '.join(p for p in (self.customer_name, self.phone and '📞 %s' % self.phone) if p)
        for icon_line in (who and '👤 ' + who, self.email and '✉️ ' + self.email,
                          self.company_name and '🏢 ' + self.company_name,
                          self.items and '📦 Hạng mục: ' + self.items,
                          self.message and '📝 ' + self.message[:500],
                          self.page_url and '🔗 ' + self.page_url,
                          self.submitted_at and '⏰ ' + self.submitted_at):
            if icon_line:
                lines.append(icon_line)
        return '\n'.join(lines)


class PushEventWebLead(models.Model):
    _inherit = 'dac.push.event'

    def _handle_web_lead_created(self):
        self.ensure_one()
        data = self.payload or {}
        form_type = data.get('form_type') if data.get('form_type') in dict(FORM_TYPES) else 'other'
        items = data.get('items')
        if isinstance(items, (list, tuple)):
            items = ', '.join(str(i) for i in items)
        lead = self.env['dac.web.lead'].search([('event_id', '=', self.id)], limit=1) or self.env['dac.web.lead'].create({
            'event_id': self.id, 'site': clean(data.get('site') or self.source, 100), 'form_type': form_type,
            'form_name': clean(data.get('form_name'), 200), 'customer_name': clean(data.get('name'), 200),
            'phone': re.sub(r'[^\d+]', '', str(data.get('phone') or ''))[:20], 'email': clean(data.get('email'), 200),
            'company_name': clean(data.get('company'), 200), 'items': clean(items, 500),
            'message': clean(data.get('message'), 2000), 'page_url': clean(data.get('page_url'), 500),
            'submitted_at': clean(data.get('submitted_at'), 50),
        })
        self.env['dac.zalo.channel']._notify('web_lead.created', lead._zalo_text(), 'push:%s' % self.id)
