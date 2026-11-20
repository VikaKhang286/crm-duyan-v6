import logging
from datetime import timedelta, timezone
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

    # ── Bảng số liệu (dashboard) ─────────────────────────────────────────────────

    @api.model
    def _period_range(self, period, now, date_from=None, date_to=None):
        """Kỳ thống kê theo giờ Việt Nam → dict naive UTC: start/end (end loại trừ), prev_start/prev_end (kỳ so sánh),
        bucket ('hour'|'day'|'month'), label."""
        local_now = now.replace(tzinfo=timezone.utc).astimezone(TZ)
        today = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        day = timedelta(days=1)
        if period == 'custom' and date_from and date_to:
            first, last = fields.Date.to_date(date_from), fields.Date.to_date(date_to)
            if first > last:
                first, last = last, first
            first = max(first, last - timedelta(days=3 * 366))  # tối đa ~3 năm
            start = today.replace(year=first.year, month=first.month, day=first.day)
            end = min(today.replace(year=last.year, month=last.month, day=last.day) + day, local_now)
            bucket = 'day' if (end - start).days <= 62 else 'month'
            label = 'Tuỳ chọn'
            prev_start, prev_end = start - (end - start), start
        elif period == 'today':
            start, end, bucket, label = today, local_now, 'hour', 'Hôm nay'
            prev_start, prev_end = start - day, end - day  # hôm qua, cùng khung giờ
        elif period == 'yesterday':
            start, end, bucket, label = today - day, today, 'hour', 'Hôm qua'
            prev_start, prev_end = start - day, start
        else:
            if period == '7d':
                start, label = today - 6 * day, '7 ngày qua'
            elif period == 'month':
                start, label = today.replace(day=1), 'Tháng %s' % today.strftime('%m/%Y')
            else:
                start, label = today - 29 * day, '30 ngày qua'
            end, bucket = local_now, 'day'
            prev_start, prev_end = start - (end - start), start
        to_utc = lambda moment: moment.astimezone(timezone.utc).replace(tzinfo=None)
        return {'start': to_utc(start), 'end': to_utc(end), 'prev_start': to_utc(prev_start), 'prev_end': to_utc(prev_end),
                'bucket': bucket, 'label': label}

    @api.model
    def _channel_of(self, contact):
        if contact.source == 'web_lead':
            return 'web'
        platform = (contact.platform or '').lower()
        return {'zalo': 'zalo', 'facebook': 'messenger', 'messenger': 'messenger'}.get(platform, 'other')

    @api.model
    def dashboard_data(self, period='30d', date_from=None, date_to=None):
        """Số liệu cho bảng "Khách liên hệ": khách mới thật sự (không test, không nội bộ, không trùng số) theo nguồn.
        `period`: today | yesterday | 7d | 30d | month | custom (kèm `date_from`, `date_to` dạng YYYY-MM-DD)."""
        now = fields.Datetime.now()
        window = self._period_range(period, now, date_from, date_to)
        start, end, bucket, label = window['start'], window['end'], window['bucket'], window['label']
        real = [('counts_as_new', '=', True)]
        contacts = self.search(real + [('contact_at', '>=', start), ('contact_at', '<', end)], order='contact_at')
        previous = self.search_count(real + [('contact_at', '>=', window['prev_start']), ('contact_at', '<', window['prev_end'])])
        channels = {'web': 0, 'zalo': 0, 'messenger': 0, 'other': 0}
        local = lambda moment: moment.replace(tzinfo=timezone.utc).astimezone(TZ)
        key_format = {'hour': '%H', 'day': '%Y-%m-%d', 'month': '%Y-%m'}[bucket]
        # Cột biểu đồ: đủ mọi giờ/ngày/tháng trong kỳ, kể cả khi không có khách
        if bucket == 'hour':
            keys = ['%02d' % hour for hour in range(24)]
        else:
            keys, cursor, last = [], local(start), local(end - timedelta(seconds=1))
            while cursor.date() <= last.date():
                key = cursor.strftime(key_format)
                if key not in keys:
                    keys.append(key)
                cursor += timedelta(days=1)
        series = {key: dict(channels) for key in keys}
        hours = [0] * 24
        pages = {}
        for contact in contacts:
            channel = self._channel_of(contact)
            channels[channel] += 1
            moment = local(contact.contact_at)
            key = moment.strftime(key_format)
            if key in series:
                series[key][channel] += 1
            hours[moment.hour] += 1
            page = 'Form website' if contact.source == 'web_lead' else (contact.page_id.name or 'Pancake')
            pages[page] = pages.get(page, 0) + 1
        today_start = local(now).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).replace(tzinfo=None)
        Wait = self.env['dac.pancake.remind.wait']
        recent = self.search(real, order='contact_at desc, id desc', limit=12)
        return {
            'period': period, 'label': label,
            'range': local(start).strftime('%d/%m/%Y') if bucket == 'hour' else '%s – %s' % (
                local(start).strftime('%d/%m/%Y'), local(end - timedelta(seconds=1)).strftime('%d/%m/%Y')),
            'total': len(contacts), 'previous': previous,
            'today': self.search_count(real + [('contact_at', '>=', today_start)]),
            'channels': channels,
            'repeat': self.search_count([('contact_at', '>=', start), ('contact_at', '<', end), ('seen_before', '=', True), ('is_test', '=', False)]),
            'internal': self.search_count([('contact_at', '>=', start), ('contact_at', '<', end), ('is_internal', '=', True)]),
            'waiting': Wait.search_count([('state', '=', 'waiting'), ('is_test', '=', False)]),
            'bucket': bucket,
            'series': [dict(series[key], key=key, label={'hour': lambda k: k + 'h', 'month': lambda k: k[5:7] + '/' + k[2:4],
                                                         'day': lambda k: k[8:10] + '/' + k[5:7]}[bucket](key)) for key in keys],
            'hours': hours,
            'pages': sorted(({'name': name, 'count': count} for name, count in pages.items()), key=lambda row: -row['count']),
            'recent': [{
                'id': contact.id, 'at': local(contact.contact_at).strftime('%H:%M %d/%m'), 'name': contact.name or 'Khách',
                'phone': contact.phone or '', 'channel': self._channel_of(contact),
                'page': 'Form website' if contact.source == 'web_lead' else (contact.page_id.name or ''),
                'url': contact.external_url or (contact.lead_id and '/lead/%s' % contact.lead_id.id) or '',
            } for contact in recent],
            'start': fields.Datetime.to_string(start), 'end': fields.Datetime.to_string(end),
            'date_from': local(start).strftime('%Y-%m-%d'), 'date_to': local(end - timedelta(seconds=1)).strftime('%Y-%m-%d'),
        }
