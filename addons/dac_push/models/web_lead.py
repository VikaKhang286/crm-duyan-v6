import logging
import re
import secrets
from datetime import timedelta
from urllib.parse import urlsplit

from odoo import api, fields, models

from .contact_intent import THANKS_TEXT, means_contacted
from .push_event import clean
from .working_time import add_working_minutes, latest_summary_slot, next_summary_slot

_logger = logging.getLogger(__name__)
REMIND_AFTER_MINUTES = 30  # phút làm việc chờ trước khi nhắc người nhận lead
REMINDER_TEXT = 'Bạn đã liên hệ khách chưa ạ'
SUMMARY_PARAM = 'dac_push.last_summary_slot'
SUMMARY_MAX_LEADS = 15  # số khách tối đa liệt kê trong một tin nhắc tổng
SUMMARY_MAX_AGE = timedelta(days=7)  # lead quá cũ mà vẫn chưa ai chốt thì thôi không nhắc tổng nữa
SUMMARY_STALE = timedelta(hours=2)  # Odoo ngừng hoạt động qua mốc quá lâu thì bỏ mốc đó, chờ mốc kế tiếp
REMINDER_GIVE_UP = timedelta(hours=24)  # tin gốc kẹt hàng chờ quá lâu thì bỏ nhắc, tránh nhắc muộn vô nghĩa

CONTACT_STATES = [('new', 'Chưa liên hệ'), ('contacted', 'Đã liên hệ'), ('unreachable', 'Liên hệ không được')]
FORM_TYPES = [('callback', 'Gọi lại'), ('quote', 'Báo giá'), ('bee_form', 'Bee Form'),
              ('elementor', 'Elementor'), ('other', 'Khác')]


class WebLead(models.Model):
    _name = 'dac.web.lead'
    _description = 'Lead từ form website'
    _order = 'received_at desc, id desc'
    _sql_constraints = [('access_token_unique', 'unique(access_token)', 'Mã truy cập lead phải là duy nhất.')]

    @api.model
    def _new_access_token(self):
        """Mã ngẫu nhiên khó đoán (72 bit) cho link công khai; không thuần số để không đụng route /lead/<id> cũ."""
        while True:
            token = secrets.token_urlsafe(9)
            if not token.isdigit():
                return token

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
    contact_state = fields.Selection(CONTACT_STATES, string='Trạng thái liên hệ', default='new', required=True, index=True, copy=False)
    contact_state_at = fields.Datetime(string='Cập nhật liên hệ lúc', readonly=True, copy=False)
    remind_due_at = fields.Datetime(string='Hạn nhắc liên hệ', readonly=True, copy=False, index=True,
        help='Sau mốc này (đã tính theo giờ làm việc) mà lead vẫn chưa liên hệ thì nhắc trong nhóm Zalo. Trống = không nhắc.')
    reminded_at = fields.Datetime(string='Đã nhắc lúc', readonly=True, copy=False)
    zalo_message_ids = fields.Many2many('dac.zalo.channel.message', 'dac_web_lead_zalo_message_rel', 'lead_id', 'message_id',
        string='Tin Zalo đã báo lead', readonly=True, copy=False)
    reminder_message_ids = fields.Many2many('dac.zalo.channel.message', 'dac_web_lead_reminder_message_rel', 'lead_id', 'message_id',
        string='Tin nhắc nhở đã gửi', readonly=True, copy=False)
    summary_message_ids = fields.Many2many('dac.zalo.channel.message', 'dac_web_lead_summary_message_rel', 'lead_id', 'message_id',
        string='Tin nhắc tổng có số này', readonly=True, copy=False)
    acked_at = fields.Datetime(string='Người được nhắc đã xem lúc', readonly=True, copy=False,
        help='Một trong những người được nhắc tên đã thả cảm xúc lên tin nhắc nhở: không nhắc thêm nữa.')
    acked_uid = fields.Char(string='Mã Zalo người đã xem', readonly=True, copy=False)
    access_token = fields.Char(string='Mã truy cập link', default=lambda self: self._new_access_token(), index=True,
        copy=False, readonly=True, groups='base.group_system',
        help='Ai có link chứa mã này đều xem được lead và đánh dấu liên hệ mà không cần đăng nhập.')

    @api.depends('customer_name', 'phone')
    def _compute_display_name(self):
        for lead in self:
            lead.display_name = ' - '.join(p for p in (lead.customer_name, lead.phone) if p) or 'Lead web'

    @api.depends('customer_name', 'email', 'company_name', 'items', 'message', 'extra_info', 'phone')
    def _compute_phone_only(self):
        for lead in self:
            lead.phone_only = bool(lead.phone) and not any(
                (lead.customer_name, lead.email, lead.company_name, lead.items, lead.message, lead.extra_info))

    def _get_access_token(self):
        """Mã truy cập của lead; lead cũ (tạo trước khi có mã) được cấp mã ngay lần đầu cần tới."""
        self.ensure_one()
        # sudo(): luồng webhook/cron/khách chưa đăng nhập không có quyền đọc-ghi trường mã (chỉ system); đây không phải override create/write
        lead = self.sudo()
        if not lead.access_token:
            lead.write({'access_token': self._new_access_token()})
        return lead.access_token

    def _set_contact_state(self, state):
        """Ghi trạng thái liên hệ + thời điểm; dùng chung cho nút trong Odoo và nút trên trang công khai."""
        assert state in dict(CONTACT_STATES)
        self.write({'contact_state': state, 'contact_state_at': fields.Datetime.now()})

    @api.model
    def _cron_remind_uncontacted(self):
        """Nhắc trong nhóm Zalo các lead quá hạn mà chưa ai bấm "Đã liên hệ" / "Liên hệ không được". Không raise."""
        now = fields.Datetime.now()
        leads = self.search([('contact_state', '=', 'new'), ('remind_due_at', '!=', False), ('remind_due_at', '<=', now),
                             ('reminded_at', '=', False), ('acked_at', '=', False)], order='remind_due_at', limit=50)
        for lead in leads:
            try:
                with self.env.cr.savepoint():
                    lead._send_contact_reminder()
            except Exception:  # noqa: BLE001 — cron không được raise; không log nội dung lead
                _logger.exception('Không gửi được nhắc liên hệ cho lead %s', lead.id)
        try:
            with self.env.cr.savepoint():
                self._cron_summary_uncontacted()
        except Exception:  # noqa: BLE001
            _logger.exception('Không gửi được tin nhắc tổng lead chưa liên hệ')

    @api.model
    def _cron_summary_uncontacted(self):
        """Đến mỗi mốc 8:00 / 11:30 / 14:00 / 17:00 (thứ 2–thứ 7) gửi một tin tổng các số còn chưa liên hệ.

        Mốc chỉ xử lý một lần (lưu ở tham số hệ thống). Lần chạy đầu tiên chỉ ghi nhận mốc hiện tại, không gửi gì, để
        không bắn tin ngay khi vừa bật chức năng.
        """
        now = fields.Datetime.now()
        slot = latest_summary_slot(now)
        if not slot:
            return
        # sudo(): lưu mốc đã nhắc tổng — cron không có user và người dùng thường không có quyền ghi tham số hệ thống
        params = self.env['ir.config_parameter'].sudo()
        last = params.get_param(SUMMARY_PARAM)
        if not last:
            params.set_param(SUMMARY_PARAM, fields.Datetime.to_string(slot))
            return
        if slot <= fields.Datetime.to_datetime(last):
            return
        if now - slot <= SUMMARY_STALE:
            self._send_summary(slot)  # lỗi giữa chừng: chưa ghi mốc nên lần cron sau thử lại (tin xếp hàng có khóa theo mốc, không trùng)
        params.set_param(SUMMARY_PARAM, fields.Datetime.to_string(slot))

    @api.model
    def _send_summary(self, slot):
        leads = self.search([('contact_state', '=', 'new'), ('acked_at', '=', False), ('zalo_message_ids', '!=', False),
                             ('received_at', '<', slot), ('received_at', '>=', slot - SUMMARY_MAX_AGE)], order='received_at, id')
        if not leads:
            return
        event_type = self.env.ref('dac_push.event_type_web_lead_created')

        def delivered_channels(lead):
            return lead.zalo_message_ids.filtered(lambda m: m.state in ('sent', 'queued')).channel_id.filtered('active')

        queued = False
        for channel in leads.mapped(delivered_channels):
            mine = leads.filtered(lambda lead: channel in delivered_channels(lead))
            lines = ['Còn %d khách chưa liên hệ:' % len(mine)]
            # Khách chờ lâu nhất ở trên cùng (leads đã sắp theo received_at tăng dần); mỗi dòng chỉ "số: link rút gọn"
            lines.extend('%s: %s' % (lead.phone or 'chưa có SĐT', lead._short_link(compact=True)) for lead in mine[:SUMMARY_MAX_LEADS])
            if len(mine) > SUMMARY_MAX_LEADS:
                lines.append('… và %d khách nữa (xem trong Odoo → Lead website)' % (len(mine) - SUMMARY_MAX_LEADS))
            text, mentions = channel._with_mentions('\n'.join(lines), channel._mention_members(event_type), leading=True)
            summary = channel._queue(text, 'lead-summary:%s:%s' % (fields.Datetime.to_string(slot), channel.id), event_type, mentions=mentions)
            mine.write({'summary_message_ids': [(4, summary.id)]})  # để trả lời tin tổng cũng chốt được các số trong đó
            queued = True
        if queued:
            self.env['dac.zalo.channel']._trigger_send()

    def _acknowledge(self, uid):
        """Một người được nhắc đã phản hồi tin nhắc nhở (thả cảm xúc): dừng mọi lượt nhắc tiếp theo cho lead này."""
        for lead in self.filtered(lambda l: not l.acked_at):
            lead.write({'acked_at': fields.Datetime.now(), 'acked_uid': uid})

    def _send_contact_reminder(self):
        """Nhắc một lần: trả lời đúng tin báo lead trong từng nhóm, nhắc tên những người được cài cho thông báo lead."""
        self.ensure_one()
        messages = self.zalo_message_ids
        waiting = messages.filtered(lambda m: m.state == 'queued')
        if waiting and self.remind_due_at > fields.Datetime.now() - REMINDER_GIVE_UP:
            return  # tin gốc chưa gửi đi (bridge đang lỗi?) — chưa có gì để trả lời, thử lại ở lần cron sau
        sent = messages.filtered(lambda m: m.state == 'sent')
        reminders = self.env['dac.zalo.channel.message']
        for message in sent:
            reminders |= message._queue_reply(REMINDER_TEXT, 'lead-remind:%s:%s' % (self.id, message.id))
        self.write({'reminded_at': fields.Datetime.now(), 'reminder_message_ids': [(4, r.id) for r in reminders]})
        if sent:
            self.env['dac.zalo.channel']._trigger_send()

    def action_mark_contacted(self):
        self._set_contact_state('contacted')

    def action_mark_unreachable(self):
        self._set_contact_state('unreachable')

    def _link_slug(self):
        """Phần cuối của link: <số điện thoại>-<mã ngẫu nhiên>, vd 0912345678-k3Xw9aQz. Số điện thoại để nhìn là biết lead nào;
        mã ngẫu nhiên mới là thứ bảo vệ (số điện thoại đoán được và một khách có thể có nhiều lead nên không đủ làm khóa)."""
        self.ensure_one()
        digits = re.sub(r'\D', '', self.phone or '')
        if (self.phone or '').startswith('+84') and len(digits) >= 11:
            digits = '0' + digits[2:]
        token = self._get_access_token()
        return '%s-%s' % (digits, token) if 6 <= len(digits) <= 15 else token

    def _short_link(self, compact=False):
        """Link công khai rút gọn, không có scheme cho gọn (Zalo tự nhận ra thành link): crm.duyan.vn/lead/<sđt>-<mã>.

        `compact=True`: crm.duyan.vn/l/<mã> — dùng ở nơi số điện thoại đã hiện ngay bên cạnh (tin nhắc tổng).
        """
        self.ensure_one()
        # sudo(): đọc tham số hệ thống trong luồng webhook/cron (không có user)
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        host = urlsplit(base).netloc or 'crm.duyan.vn'
        if compact:
            return '%s/l/%s' % (host, self._get_access_token())
        return '%s/lead/%s' % (host, self._link_slug())

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
        now = fields.Datetime.now()
        due = add_working_minutes(now, REMIND_AFTER_MINUTES)
        if next_summary_slot(now) <= due:
            due = False  # tin nhắc tổng đến trước (hoặc cùng lúc) hạn nhắc riêng: số này đã nằm trong tin tổng, khỏi nhắc riêng
        lead = self.env['dac.web.lead'].search([('event_id', '=', self.id)], limit=1) or self.env['dac.web.lead'].create({
            'event_id': self.id, 'received_at': now, 'remind_due_at': due, 'site': clean(data.get('site') or self.source, 100), 'form_type': form_type,
            'form_name': clean(data.get('form_name'), 200), 'customer_name': clean(data.get('name'), 200),
            'phone': re.sub(r'[^\d+]', '', str(data.get('phone') or ''))[:20], 'email': clean(data.get('email'), 200),
            'company_name': clean(data.get('company'), 200), 'items': clean(items, 500),
            'message': clean(data.get('message'), 2000), 'page_url': clean(data.get('page_url'), 500),
            'submitted_at': clean(data.get('submitted_at'), 50), 'page_title': clean(data.get('page_title'), 200),
            'extra_info': '\n'.join(extra_lines), 'visited_urls': '\n'.join(visited_lines),
            'source_info': ' / '.join(source_parts),
        })
        text, styles = lead._zalo_message()
        messages = self.env['dac.zalo.channel']._notify('web_lead.created', text, 'push:%s' % self.id, styles=styles)
        lead.write({'zalo_message_ids': [(4, message.id) for message in messages]})  # để sau này trả lời đúng các tin này


class ZaloChannelMessageLead(models.Model):
    _inherit = 'dac.zalo.channel.message'

    def _on_reaction(self, reaction):
        """Người được nhắc tên thả cảm xúc lên tin nhắc nhở → dừng nhắc lead đó (kể cả nhắc tổng)."""
        super()._on_reaction(reaction)
        # sudo(): luồng nhận reaction từ bridge (máy) không có user; chỉ đánh dấu "đã xem" trên lead
        leads = self.env['dac.web.lead'].sudo().search([('reminder_message_ids', 'in', self.id)])
        if not leads:
            return
        tagged = {m.get('uid') for m in (self.mentions or []) if isinstance(m, dict)}
        if tagged and reaction.uid not in tagged:
            return  # người ngoài danh sách được nhắc thả cảm xúc: chưa tính là đã có người nhận việc
        leads._acknowledge(reaction.uid)

    def _on_reply(self, reply):
        """Nhân viên trả lời (reply) tin báo lead / nhắc riêng / nhắc tổng bằng "đã liên hệ", "đã gọi"... → chốt các lead của tin đó
        là đã liên hệ, rồi bot trả lời lại đúng tin của nhân viên đó để cảm ơn."""
        super()._on_reply(reply)
        if not means_contacted(reply.get('text')):
            return
        # sudo(): luồng nhận tin từ bridge (máy) không có user; chỉ đổi trạng thái liên hệ của lead gắn với tin được trả lời
        Lead = self.env['dac.web.lead'].sudo()
        leads = Lead.search(['|', '|', ('zalo_message_ids', 'in', self.id),
                             ('reminder_message_ids', 'in', self.id), ('summary_message_ids', 'in', self.id)])
        # Tin báo lead gửi từ trước khi có liên kết tin ↔ lead: mã tin có dạng push:<id sự kiện>:<id hội thoại>, tra lead theo sự kiện đó
        origin = re.match(r'^push:(\d+):', self.event_key or '')
        if origin:
            leads |= Lead.search([('event_id', '=', int(origin.group(1)))])
        if not leads:
            return  # trả lời một tin không liên quan tới lead
        for lead in leads.filtered(lambda l: l.contact_state == 'new'):
            lead._set_contact_state('contacted')
        self.channel_id._queue(THANKS_TEXT, 'lead-thanks:%s:%s' % (reply['message_id'], self.channel_id.id), self.event_type_id,
                               quote_message_id=reply['message_id'])
        self.env['dac.zalo.channel']._trigger_send()
