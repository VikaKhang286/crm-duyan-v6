import logging
from datetime import timezone
from zoneinfo import ZoneInfo

from odoo import api, fields, models
from odoo.addons.dac_erp.models.phone_utils import normalize_phone_vn
from odoo.addons.dac_push.models.contact_intent import fold

from .pancake_api import PancakeError

_logger = logging.getLogger(__name__)
TZ = ZoneInfo('Asia/Ho_Chi_Minh')
BACKFILL_FETCH = 300  # tối đa số hội thoại tải tin mỗi page khi bấm "Tính lại thống kê"


def _day(moment):
    return moment.replace(tzinfo=timezone.utc).astimezone(TZ).strftime('%d/%m')


class NewContact(models.Model):
    """Một lượt khách mới liên hệ: lead điền form trên website HOẶC khách mới nhắn tin vào page Pancake.

    `counts_as_new` = lượt này là khách thật sự mới: không phải dữ liệu thử và số điện thoại chưa từng liên hệ trước đó
    (qua form hay Pancake). Thống kê dùng chỉ số này để một khách không bị đếm hai lần.

    sudo(): bản ghi này chỉ do luồng nền (webhook nhận lead, cron quét Pancake, nút tính lại) tạo; người dùng thường chỉ được đọc.
    """
    _name = 'dac.new.contact'
    _description = 'Lượt khách mới liên hệ'
    _order = 'contact_at desc, id desc'

    contact_at = fields.Datetime(string='Liên hệ lúc', required=True, index=True)
    source = fields.Selection([('web_lead', 'Form website'), ('pancake', 'Pancake')], string='Nguồn', required=True, index=True)
    name = fields.Char(string='Khách')
    phone = fields.Char(string='Số điện thoại', index=True)
    lead_id = fields.Many2one('dac.web.lead', string='Lead website', ondelete='cascade', index=True)
    conversation_id = fields.Many2one('dac.pancake.conversation', string='Hội thoại Pancake', ondelete='cascade', index=True)
    page_id = fields.Many2one('dac.pancake.page', string='Page Pancake', ondelete='set null')
    external_url = fields.Char(string='Link Pancake', related='conversation_id.external_url')
    is_test = fields.Boolean(string='Dữ liệu thử', index=True)
    seen_before = fields.Boolean(string='Đã liên hệ trước đó', index=True,
        help='Số điện thoại này đã liên hệ từ trước (điền form hoặc nhắn Pancake): không phải khách mới.')
    seen_note = fields.Char(string='Trùng với')
    is_internal = fields.Boolean(string='Nội bộ', related='conversation_id.is_internal', store=True, index=True)
    staff_first = fields.Boolean(string='Shop nhắn trước', readonly=True,
        help='Trong hội thoại, shop/nhân viên nhắn trước rồi khách mới trả lời.')
    platform = fields.Char(string='Kênh', related='conversation_id.platform', store=True)
    counts_as_new = fields.Boolean(string='Khách mới thật sự', compute='_compute_counts_as_new', store=True, index=True)
    value = fields.Integer(string='Lượt', default=1, readonly=True)

    @api.depends('is_test', 'seen_before', 'is_internal')
    def _compute_counts_as_new(self):
        for contact in self:
            contact.counts_as_new = not contact.is_test and not contact.seen_before and not contact.is_internal

    def init(self):
        # Mỗi lead và mỗi hội thoại chỉ một lượt (chạy lại quét/tính lại không tạo trùng)
        self.env.cr.execute('CREATE UNIQUE INDEX IF NOT EXISTS dac_new_contact_lead_uniq ON dac_new_contact (lead_id) WHERE lead_id IS NOT NULL')
        self.env.cr.execute('CREATE UNIQUE INDEX IF NOT EXISTS dac_new_contact_conv_uniq ON dac_new_contact (conversation_id) WHERE conversation_id IS NOT NULL')

    # ── Trùng số điện thoại ──────────────────────────────────────────────────────

    @api.model
    def _earlier_note(self, phone, at, exclude=None):
        """Mô tả lần liên hệ trước đó của số này (rỗng nếu chưa từng): lượt đã ghi, lead cũ, hoặc hội thoại Pancake cũ."""
        number = normalize_phone_vn(phone)
        if not number:
            return ''
        domain = [('phone', '=', number), ('contact_at', '<', at), ('is_test', '=', False)]
        if exclude:
            domain.append(('id', '!=', exclude.id))
        earlier = self.sudo().search(domain, order='contact_at', limit=1)
        if earlier:
            return 'đã %s ngày %s' % ('điền form' if earlier.source == 'web_lead' else 'nhắn Pancake', _day(earlier.contact_at))
        leads = self.env['dac.web.lead'].sudo().search([('phone', 'in', [number, '+84' + number[1:]]), ('received_at', '<', at), ('is_test', '=', False)], limit=1, order='received_at')
        if leads:
            return 'đã điền form ngày %s' % _day(leads.received_at)
        pages = self.env['dac.pancake.remind.wait']._pages(self.env['dac.pancake.remind.config']._get())
        for conversation in self.env['dac.pancake.phone']._conversations_for(number, pages):
            first = self.env['dac.pancake.message'].sudo().search([('conversation_id', '=', conversation.id), ('inserted_at', '<', at)], order='inserted_at', limit=1)
            started = min(filter(None, [conversation.inserted_at_fm, first.inserted_at]), default=False)
            if started and started < at:
                return 'đã nhắn Pancake (%s) ngày %s' % (conversation.page_id.name or 'page', _day(started))
        return ''

    # ── Ghi nhận ─────────────────────────────────────────────────────────────────

    @api.model
    def _record_lead(self, lead):
        """Lượt khách mới từ lead website (idempotent theo lead)."""
        lead = lead.sudo()
        existing = self.sudo().search([('lead_id', '=', lead.id)], limit=1)
        if existing:
            return existing
        phone = normalize_phone_vn(lead.phone)
        note = self._earlier_note(phone, lead.received_at)
        return self.sudo().create({
            'contact_at': lead.received_at, 'source': 'web_lead', 'name': lead.customer_name, 'phone': phone, 'lead_id': lead.id,
            'is_test': lead.is_test, 'seen_before': bool(note), 'seen_note': note})

    @api.model
    def _has_orders(self, partner):
        return bool(partner) and bool(self.env['sale.order'].sudo().search_count([
            ('partner_id', 'child_of', partner.sudo().commercial_partner_id.id), ('state', '!=', 'cancel')]))

    @api.model
    def _first_customer_message(self, conversation, bots):
        """Tin đầu tiên của hội thoại nếu do KHÁCH nhắn trước (nhân viên nhắn trước thì không phải khách mới liên hệ)."""
        from .remind_wait import classify
        for message in self.env['dac.pancake.message'].sudo().search([('conversation_id', '=', conversation.id)], order='inserted_at, id', limit=60):
            kind = classify(message, bots)
            if kind:
                return message if kind == 'customer' else None
        return None

    @api.model
    def _first_contact(self, conversation, bots):
        """(tin đầu tiên của khách, shop có nhắn trước không) trên toàn bộ lịch sử hội thoại đã lấy đủ."""
        from .remind_wait import classify
        staff_first = False
        offset = 0
        Message = self.env['dac.pancake.message'].sudo()
        while True:
            batch = Message.search([('conversation_id', '=', conversation.id)], order='inserted_at, id', limit=200, offset=offset)
            for message in batch:
                kind = classify(message, bots)
                if kind == 'customer':
                    return message, staff_first
                staff_first = staff_first or kind == 'staff'
            if len(batch) < 200:
                return None, staff_first
            offset += 200

    @api.model
    def _after_history(self, conversation, bots=None):
        """Hội thoại vừa lấy đủ tin cũ: ghi nhận số điện thoại khách để lại và (cập nhật) lượt liên hệ theo lần nhắn đầu tiên thật."""
        Wait = self.env['dac.pancake.remind.wait']
        bots = bots if bots is not None else Wait._bot_names()
        self.env['dac.pancake.phone']._capture(self.env['dac.pancake.message'].sudo().search([
            ('conversation_id', '=', conversation.id), ('raw_json', 'like', '%"phone_info": [{%')]).filtered(
                lambda m: Wait._classify(m, bots) == 'customer'))
        return self._record_conversation(conversation, bots, None)

    @api.model
    def _record_full_history(self, conversation, bots):
        """Lượt liên hệ của hội thoại đã có đủ tin cũ: tính từ tin đầu tiên của khách (không cần mốc, không xét đơn hàng)."""
        first, staff_first = self._first_contact(conversation, bots)
        existing = self.sudo().search([('conversation_id', '=', conversation.id)], limit=1)
        if not first:
            return self.browse()
        phone = self.env['dac.pancake.phone']._known_phone(conversation)
        if existing:
            if existing.contact_at != first.inserted_at or existing.staff_first != staff_first or (phone and existing.phone != phone):
                note = self._earlier_note(phone or existing.phone, first.inserted_at, exclude=existing)
                existing.write({'contact_at': first.inserted_at, 'staff_first': staff_first, 'phone': phone or existing.phone,
                                'seen_before': bool(note), 'seen_note': note})
            return existing
        note = self._earlier_note(phone, first.inserted_at)
        name = conversation.customer_name or 'Khách'
        folded = fold(name)
        return self.sudo().create({
            'contact_at': first.inserted_at, 'source': 'pancake', 'name': name, 'phone': phone, 'conversation_id': conversation.id,
            'page_id': conversation.page_id.id, 'is_test': folded == 'test' or folded.startswith('test '), 'staff_first': staff_first,
            'seen_before': bool(note), 'seen_note': note})

    @api.model
    def _record_conversation(self, conversation, bots, floor):
        """Lượt khách mới từ hội thoại Pancake. Hội thoại đã lấy đủ tin cũ: tính từ lần nhắn đầu tiên thật của khách. Chưa đủ:
        khách nhắn trước, tin đầu tiên không sớm hơn `floor`, khách chưa có đơn hàng (tránh đếm khách cũ là khách mới)."""
        conversation = conversation.sudo()
        if conversation.history_state == 'done':
            return self._record_full_history(conversation, bots)
        if floor is None or self.sudo().search_count([('conversation_id', '=', conversation.id)]):
            return self.browse()
        first = self._first_customer_message(conversation, bots)
        if not first or first.inserted_at < floor or self._has_orders(conversation.partner_id):
            return self.browse()
        if conversation.inserted_at_fm and conversation.inserted_at_fm < floor:  # hội thoại cũ, chỉ là tin đầu trong lô tải về
            return self.browse()
        phone = self.env['dac.pancake.phone']._known_phone(conversation)
        note = self._earlier_note(phone, first.inserted_at)
        name = conversation.customer_name or 'Khách'
        folded = fold(name)
        return self.sudo().create({
            'contact_at': first.inserted_at, 'source': 'pancake', 'name': name, 'phone': phone, 'conversation_id': conversation.id,
            'page_id': conversation.page_id.id, 'is_test': folded == 'test' or folded.startswith('test '),
            'seen_before': bool(note), 'seen_note': note})

    @api.model
    def _backfill(self, config, start):
        """Lập thống kê từ `start`: lead website + hội thoại Pancake lấy lại từ Pancake (giới hạn mỗi lần bấm); trả số lượt mới thêm."""
        before = self.sudo().search_count([])
        Wait = self.env['dac.pancake.remind.wait']
        bots = Wait._bot_names()
        pages = Wait._pages(config)
        now = fields.Datetime.now()
        for page in pages.filtered('has_token'):
            try:
                with self.env.cr.savepoint():
                    page._poll(start, now, max_fetch=BACKFILL_FETCH)
            except PancakeError as exc:
                _logger.warning('Pancake: tính lại thống kê không lấy được page %s: %s', page.id, exc)
        conversations = self.env['dac.pancake.conversation'].sudo().search([
            ('page_id', 'in', pages.ids), '|', ('updated_at_fm', '>=', start), ('message_ids.inserted_at', '>=', start)], order='updated_at_fm, id')
        # Số điện thoại trong tin cũ phải được ghi nhận trước để việc đối chiếu trùng số đủ dữ liệu
        self.env['dac.pancake.phone']._capture(self.env['dac.pancake.message'].sudo().search([
            ('conversation_id', 'in', conversations.ids), ('raw_json', 'like', '%"phone_info": [{%')]).filtered(
                lambda m: Wait._classify(m, bots) == 'customer'))
        for lead in self.env['dac.web.lead'].sudo().search([('received_at', '>=', start)], order='received_at'):
            self._record_lead(lead)
        for conversation in conversations:
            self._record_conversation(conversation, bots, start)
        return self.sudo().search_count([]) - before
