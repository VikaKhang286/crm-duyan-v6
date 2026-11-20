"""Lịch hẹn với khách: AI tìm lời hẹn của nhân viên trên Pancake, nhắc trước giờ hẹn, kiểm tra sau giờ hẹn, phiếu hẹn để xác nhận.

Luồng (cron "Pancake: lịch hẹn & nhắc việc", mỗi 10 phút):
1. Phát hiện ngay: tin mới của nhân viên có dấu hiệu hẹn ("chiều em gửi báo giá") → AI đọc đoạn chat → tạo phiếu hẹn.
2. Đầu ngày (từ 6:00): AI đọc lại toàn bộ hội thoại ngày làm việc hôm trước có nhân viên nhắn → phiếu hẹn còn sót.
3. 8:00: tin "📋 Nhắc việc từ hôm qua" liệt kê việc đã hẹn chưa làm + hẹn trong hôm nay, nhóm theo nhân viên phụ trách.
4. Hẹn có giờ: nhắc trước 30 phút (ngắn gọn, tag đúng người; chưa rõ người thì tag những người được cài).
5. Sau giờ hẹn 30 phút: AI đọc lại tin nhắn; chưa thấy làm → nhắc kèm link phiếu hẹn; kiểm tra lại mỗi 60 phút, tối đa 3 lần.
Nhân viên bấm "Đã xử lý xong" trên phiếu hẹn (link công khai có mã ngẫu nhiên) cho những việc AI không thấy được: cuộc gọi,
chuyển khoản, tài liệu gửi ngoài Pancake...
"""
import logging
import re
import secrets
import time as clock
from datetime import time, timedelta
from urllib.parse import urlsplit

from odoo import api, fields, models
from odoo.addons.dac_push.models.contact_intent import fold
from odoo.addons.dac_push.models.working_time import WORK_DAYS, add_working_minutes

from . import promise, zalo_out
from .remind_wait import NOTES, classify
from .staff import admin_name
from .zalo_out import Builder, local, utc

_logger = logging.getLogger(__name__)

CODES = ('pancake.followup', 'pancake.unanswered')  # chưa ai đăng ký loại "lịch hẹn" thì gửi vào nơi nhận "khách chưa trả lời"
# Thời gian nhắc trước / sau giờ hẹn, chu kỳ và số lần nhắc: xem Kịch bản nhắc (appt_pre, appt_post, morning)
MORNING_RECHECK = 120       # việc tồn từ hôm qua (đã có trong tin 8:00): kiểm tra lại sau 2 giờ làm việc
DAY_DUE = time(16, 0)       # hẹn trong ngày không có giờ: hạn 16:00 (kiểm tra 16:30)
VAGUE_MINUTES = 120         # hẹn không rõ giờ ("lát em gửi"): hạn sau 2 giờ làm việc
LIVE_BEFORE = timedelta(hours=3)   # đoạn chat trước tin hẹn đưa cho AI
LIVE_MAX_CONVERSATIONS = 15
REVIEW_FROM = time(6, 0)
MORNING_AT = time(8, 0)
MORNING_LATEST = time(9, 0)        # đọc lại chưa xong thì 9:00 vẫn gửi với những gì đã có
ROWS_LIMIT = 80
RUN_SECONDS = 240
KEEP_DAYS = 4                      # phiếu hẹn quá cũ không nhắc nữa


def _host(env):
    # sudo(): đọc tham số hệ thống trong cron
    base = env['ir.config_parameter'].sudo().get_param('web.base.url', '')
    return urlsplit(base).netloc or 'crm.duyan.vn'


class PancakeFollowupState(models.Model):
    """Tiến độ của cron lịch hẹn (một dòng). Tách khỏi dòng cấu hình để hai cron (nhắc khách / lịch hẹn) không ghi chung
    một bản ghi cùng lúc (lỗi SerializationFailure)."""
    _name = 'dac.pancake.followup.state'
    _description = 'Tiến độ cron lịch hẹn Pancake'

    promise_cursor_id = fields.Integer(readonly=True)
    review_date = fields.Date(string='Đọc lại cho ngày', readonly=True)
    review_cursor = fields.Integer(readonly=True)
    review_done = fields.Boolean(string='Đọc lại xong', readonly=True)
    morning_sent_date = fields.Date(string='Đã gửi nhắc việc ngày', readonly=True)

    @api.model
    def _get(self):
        # sudo(): cron không có user; chỉ đọc/ghi tiến độ của chính addon này
        return self.sudo().search([], limit=1) or self.sudo().create({})


class PancakeAppointment(models.Model):
    _name = 'dac.pancake.appointment'
    _description = 'Lịch hẹn với khách (Pancake)'
    _order = 'due_at desc, id desc'
    _rec_name = 'task'

    conversation_id = fields.Many2one('dac.pancake.conversation', string='Hội thoại', required=True, ondelete='cascade', index=True)
    customer_name = fields.Char(string='Khách', related='conversation_id.customer_name')
    page_name = fields.Char(string='Page', related='conversation_id.page_id.name')
    external_url = fields.Char(string='Link Pancake', related='conversation_id.external_url')
    staff_id = fields.Many2one('dac.pancake.staff', string='Nhân viên hẹn', ondelete='set null', index=True)
    task = fields.Char(string='Việc đã hẹn', required=True)
    quote = fields.Text(string='Tin hẹn của nhân viên', readonly=True)
    message_fm_id = fields.Char(string='Mã tin hẹn', readonly=True, index=True)
    promised_at = fields.Datetime(string='Hẹn lúc', readonly=True, index=True)
    due_at = fields.Datetime(string='Hạn', index=True)
    precision = fields.Selection([('time', 'Có giờ cụ thể'), ('part', 'Theo buổi'), ('day', 'Trong ngày'), ('vague', 'Không rõ giờ')],
        string='Loại hạn', default='vague', required=True)
    state = fields.Selection([('open', 'Chờ xử lý'), ('done', 'Đã xử lý (nhân viên xác nhận)'), ('auto_done', 'Đã làm (AI thấy trong tin nhắn)'),
        ('cancelled', 'Không cần nữa'), ('overdue', 'Quá hẹn, đã nhắc đủ')], string='Trạng thái', default='open', required=True, index=True)
    source = fields.Selection([('live', 'Phát hiện ngay'), ('morning', 'Đọc lại đầu ngày'), ('manual', 'Tạo tay')], string='Nguồn',
        default='manual', readonly=True)
    ai_source = fields.Char(string='AI', readonly=True)
    check_note = fields.Char(string='Kết quả kiểm tra gần nhất', readonly=True)
    pre_reminded_at = fields.Datetime(string='Nhắc trước giờ hẹn lúc', readonly=True)
    check_at = fields.Datetime(string='Kiểm tra lúc', index=True)
    followup_count = fields.Integer(string='Số lần nhắc sau hẹn', readonly=True)
    in_morning_list = fields.Boolean(string='Đã có trong tin 8:00', readonly=True)
    done_at = fields.Datetime(string='Xong lúc', readonly=True)
    done_by = fields.Char(string='Xác nhận bởi', readonly=True)
    access_token = fields.Char(string='Mã phiếu', readonly=True, copy=False, index=True, default=lambda self: secrets.token_urlsafe(12))
    zalo_message_ids = fields.Many2many('dac.zalo.channel.message', 'dac_pancake_appointment_zalo_message_rel', 'appointment_id', 'message_id',
        string='Tin Zalo đã gửi', readonly=True, copy=False)
    is_test = fields.Boolean(string='Dữ liệu thử nghiệm', readonly=True)
    ticket_url = fields.Char(string='Link phiếu hẹn', compute='_compute_ticket_url')
    _sql_constraints = [('token_unique', 'unique(access_token)', 'Mã phiếu hẹn phải là duy nhất.')]

    def _compute_ticket_url(self):
        host = _host(self.env)
        for appointment in self:
            appointment.ticket_url = 'https://%s/hen/%s' % (host, appointment.access_token) if appointment.access_token else False

    def _short_ticket(self):
        """Link phiếu hẹn trong tin Zalo: crm.duyan.vn/r/<4 ký tự> (addon dac_short_link, có chặn dò mã) → /hen/<mã dài>."""
        self.ensure_one()
        return self.env['dac.short.link']._shorten(self.ticket_url, source='Phiếu hẹn', kind='r')

    # ── Xác nhận ─────────────────────────────────────────────────────────────

    def _close(self, state, by):
        self.filtered(lambda a: a.state in ('open', 'overdue')).write({'state': state, 'done_at': fields.Datetime.now(), 'done_by': by[:120]})

    def action_mark_done(self):
        self._close('done', self.env.user.name)

    def action_cancel(self):
        self._close('cancelled', self.env.user.name)

    def action_reopen(self):
        self.write({'state': 'open', 'done_at': False, 'done_by': False})

    # ── Cron ─────────────────────────────────────────────────────────────────

    @api.model
    def _cron_followup(self):
        """Mỗi 10 phút. Không raise."""
        config = self.env['dac.pancake.remind.config']._get()
        Scenario = self.env['dac.pancake.remind.scenario']
        if not config.active or not any(Scenario._on(code) for code in ('appt_pre', 'appt_post', 'morning')):
            return
        self.env.cr.execute('SELECT pg_try_advisory_xact_lock(%s, %s)', (73465, config.id))
        if not self.env.cr.fetchone()[0]:
            return
        now = fields.Datetime.now()
        deadline = clock.monotonic() + RUN_SECONDS
        for step in (self._pre_remind, self._post_check, self._scan_promises, self._morning_review, self._send_morning):
            try:
                with self.env.cr.savepoint():
                    step(config, now, deadline)
            except Exception:  # noqa: BLE001 — cron không được raise; không log nội dung tin
                _logger.exception('Lịch hẹn Pancake: bước %s lỗi', step.__name__)

    @api.model
    def _ai_config(self):
        # sudo(): cấu hình AI (key) chỉ quản trị đọc được; cron không có user
        return self.env['dac.zalo.config'].sudo()._config()

    @api.model
    def _commit(self):
        self.env['dac.pancake.page']._commit()

    # ── Đọc tin ──────────────────────────────────────────────────────────────

    @api.model
    def _rows(self, conversation, start, end=None):
        """Tin khách/nhân viên của hội thoại trong khoảng (cũ → mới), tối đa ROWS_LIMIT tin gần nhất."""
        domain = [('conversation_id', '=', conversation.id), ('inserted_at', '>=', start)]
        if end:
            domain.append(('inserted_at', '<', end))
        # sudo(): luồng nền đọc tin Pancake
        messages = self.env['dac.pancake.message'].sudo().search(domain, order='inserted_at desc, id desc', limit=ROWS_LIMIT * 2)
        bots = self.env['dac.pancake.remind.wait']._bot_names()
        rows = []
        for message in messages:
            kind = classify(message, bots)
            if kind not in ('customer', 'staff'):
                continue
            rows.append({'kind': kind, 'at': message.inserted_at, 'staff': admin_name(message) if kind == 'staff' else '',
                         'text': re.sub(r'<[^>]+>', ' ', message.content_html or '').strip(), 'message': message,
                         'note': NOTES.get(message.type_content, '[Hình ảnh]' if message.type_content == 'photo' else '')})
            if len(rows) >= ROWS_LIMIT:
                break
        return rows[::-1]

    @api.model
    def _schedule(self, precision, due_text, promised_at):
        """(loại hạn, hạn, lúc kiểm tra) từ kết quả AI; hạn vô lý (quá khứ xa, quá 30 ngày) → coi như không rõ giờ."""
        moment, timed = promise.parse_due(due_text)
        due = None
        if moment and precision in ('time', 'part') and timed:
            due = utc(moment.date(), moment.time())
        elif moment and precision in ('time', 'part', 'day'):
            precision, due = 'day', utc(moment.date(), DAY_DUE)
        if due is None or not promised_at - timedelta(hours=1) <= due <= promised_at + timedelta(days=30):
            precision, due = 'vague', add_working_minutes(promised_at, VAGUE_MINUTES)
        return precision, due, add_working_minutes(due, self.env['dac.pancake.remind.scenario']._get('appt_post').wait_minutes)

    @api.model
    def _extract_from(self, conversation, start, now, source):
        """AI đọc đoạn chat từ `start` tới giờ, tạo phiếu cho lời hẹn chưa làm. Trả số phiếu mới; AI lỗi → raise."""
        rows = self._rows(conversation, start)
        if not any(row['kind'] == 'staff' for row in rows):
            return 0
        found, ai_source = promise.extract(self._ai_config(), rows, now)
        grouped = {}
        for item in found:
            if not item['fulfilled']:
                grouped.setdefault(item['line'], []).append(item)
        created = 0
        Staff = self.env['dac.pancake.staff']
        name = fold(conversation.customer_name or '')
        for line, items in grouped.items():
            row = rows[line]
            message = row['message']
            if self.sudo().search_count([('conversation_id', '=', conversation.id), ('message_fm_id', '=', message.message_fm_id)]):
                continue  # đã có phiếu cho tin hẹn này (vd vừa phát hiện ngay, sáng nay đọc lại)
            first = items[0]
            precision, due, check_at = self._schedule(first['precision'], first['due'], row['at'])
            if source == 'morning' and due <= now:
                # Việc tồn từ hôm qua: tin 8:00 đã nhắc, kiểm tra lại sau 2 giờ làm việc
                check_at = add_working_minutes(max(now, utc(local(now).date(), MORNING_AT)), MORNING_RECHECK)
            staff = Staff._for_name(row['staff']) if row['staff'] else Staff._of_conversation(conversation, before=row['at'])
            # sudo(): cron tạo phiếu hẹn (người dùng thường chỉ xem)
            self.sudo().create({
                'conversation_id': conversation.id, 'staff_id': staff.id, 'task': '; '.join(dict.fromkeys(i['task'] for i in items))[:240],
                'quote': (row['text'] or row['note'] or '')[:500], 'message_fm_id': message.message_fm_id, 'promised_at': row['at'],
                'due_at': due, 'precision': precision, 'check_at': check_at, 'source': source, 'ai_source': ai_source,
                'is_test': name == 'test' or name.startswith('test '),
            })
            created += 1
        return created

    @api.model
    def _scope(self, config):
        pages = self.env['dac.pancake.remind.wait']._pages(config)
        return [('conversation_id.page_id', 'in', pages.ids), ('conversation_id.conversation_fm_id', 'not like', 'pzl_g_%'),
                ('conversation_id.is_internal', '=', False)]

    @api.model
    def _scan_promises(self, config, now, deadline):
        """Tin mới của nhân viên có dấu hiệu hẹn → AI đọc ngay để kịp nhắc trước giờ hẹn."""
        Message = self.env['dac.pancake.message'].sudo()  # sudo(): luồng nền đọc tin Pancake
        state = self.env['dac.pancake.followup.state']._get()
        cursor = state.promise_cursor_id
        if not cursor:
            # Lần đầu bật: bắt đầu từ tin hiện có, không đọc lại lịch sử (việc của bước đọc lại đầu ngày)
            last = Message.search([], order='id desc', limit=1)
            state.promise_cursor_id = last.id or 1
            return
        messages = Message.search([('id', '>', cursor), ('inserted_at', '>=', now - timedelta(days=1))] + self._scope(config),
                                  order='id', limit=1000)
        if not messages:
            return
        new_cursor = messages[-1].id
        bots = self.env['dac.pancake.remind.wait']._bot_names()
        groups = {}
        for message in messages:
            if classify(message, bots) == 'staff' and promise.has_hint(message.content_html and re.sub(r'<[^>]+>', ' ', message.content_html)):
                groups.setdefault(message.conversation_id, []).append(message)
        done = 0
        for conversation, items in sorted(groups.items(), key=lambda pair: pair[1][0].id):
            if done >= LIVE_MAX_CONVERSATIONS or clock.monotonic() > deadline:
                new_cursor = min(new_cursor, items[0].id - 1)
                break
            try:
                self._extract_from(conversation, min(m.inserted_at for m in items) - LIVE_BEFORE, now, 'live')
            except Exception as error:  # noqa: BLE001 — AI lỗi: lần sau đọc lại từ hội thoại này
                _logger.warning('Lịch hẹn Pancake: AI không đọc được hội thoại %s: %s', conversation.id, type(error).__name__)
                new_cursor = min(new_cursor, items[0].id - 1)
                break
            done += 1
        state.promise_cursor_id = new_cursor

    @api.model
    def _review_day(self, now):
        """(ngày cần đọc lại bắt đầu, kết thúc) = ngày làm việc trước hôm nay (thứ 2 thì gồm cả Chủ nhật), naive UTC."""
        today = local(now).date()
        back = 1
        while (today - timedelta(days=back)).weekday() not in WORK_DAYS and back < 7:
            back += 1
        return utc(today - timedelta(days=back), time(0, 0)), utc(today, time(0, 0))

    @api.model
    def _morning_review(self, config, now, deadline):
        """Từ 6:00 ngày làm việc: AI đọc lại mọi hội thoại có nhân viên nhắn trong ngày làm việc hôm trước (chia nhiều lượt)."""
        at = local(now)
        if at.weekday() not in WORK_DAYS or at.time() < REVIEW_FROM or not self.env['dac.pancake.remind.scenario']._on('morning'):
            return
        state = self.env['dac.pancake.followup.state']._get()
        today = at.date()
        if state.review_date != today:
            state.write({'review_date': today, 'review_cursor': 0, 'review_done': False})
        if state.review_done:
            return
        start, end = self._review_day(now)
        Message = self.env['dac.pancake.message'].sudo()  # sudo(): luồng nền đọc tin Pancake
        messages = Message.search([('inserted_at', '>=', start), ('inserted_at', '<', end)] + self._scope(config), order='conversation_id, id')
        bots = self.env['dac.pancake.remind.wait']._bot_names()
        conversations = []
        for message in messages:
            if message.conversation_id.id <= state.review_cursor:
                continue  # đã đọc ở lượt trước (đọc theo thứ tự mã hội thoại)
            if (not conversations or conversations[-1] != message.conversation_id) and classify(message, bots) == 'staff':
                conversations.append(message.conversation_id)
        for conversation in conversations:
            if clock.monotonic() > deadline:
                return
            try:
                self._extract_from(conversation, start, now, 'morning')
            except Exception as error:  # noqa: BLE001 — AI lỗi: lượt sau đọc lại từ hội thoại này
                _logger.warning('Nhắc việc từ hôm qua: AI không đọc được hội thoại %s: %s', conversation.id, type(error).__name__)
                return
            state.review_cursor = conversation.id
            self._commit()
        state.review_done = True

    # ── Gửi nhắc ─────────────────────────────────────────────────────────────

    def _targets(self, channel, types):
        return zalo_out.targets_for(channel, self.staff_id, types)

    def _values(self, now):
        late = zalo_out.working_minutes_between(self.due_at, now) if self.due_at and self.due_at < now else 0
        return {'gio_hen': local(self.due_at).strftime('%H:%M') if self.due_at else '', 'ten_khach': self.customer_name or 'Khách',
                'viec': self.task, 'phieu': self._short_ticket(),
                'qua_han': ('Quá hẹn %s' % zalo_out.waited_text(late)) if late else 'Đã quá hẹn',
                'nhan_vien': '' if self.staff_id.zalo_uid or not self.staff_id else ' (%s)' % self.staff_id.name}

    def _send(self, config, key, build, reply=False, scenario=None):
        """Gửi một tin của phiếu này; `reply`: trả lời tin nhắc trước đó của phiếu trong cùng hội thoại."""
        self.ensure_one()
        previous = {}
        if reply:
            for message in self.zalo_message_ids.filtered(lambda m: m.state == 'sent').sorted('id'):
                previous[message.channel_id.id] = message
        sent = zalo_out.send(self.env, config, CODES, key, build, is_test=self.is_test, reply_to=previous)
        if sent:
            self.write({'zalo_message_ids': [(4, m.id) for m in sent]})
            if scenario:
                scenario._log(sent, self.conversation_id, appointment=self, is_test=self.is_test)
        return sent

    def _fulfilled(self, config, now):
        """(đã làm?, ghi chú). Chưa có tin nhân viên nào sau lời hẹn → chưa làm (không cần hỏi AI)."""
        self.ensure_one()
        rows = [r for r in self._rows(self.conversation_id, self.promised_at - timedelta(minutes=1)) if r['at'] >= self.promised_at]
        if not any(row['kind'] == 'staff' and row['message'].message_fm_id != self.message_fm_id for row in rows):
            return False, 'Chưa thấy nhân viên nhắn gì sau lời hẹn'
        try:
            done, reason, source = promise.check(self._ai_config(), self.task, rows, now)
            self.ai_source = source
            return done, reason or ('AI thấy đã làm' if done else 'AI chưa thấy làm')
        except Exception as error:  # noqa: BLE001 — AI lỗi: coi như chưa thấy làm (nhân viên bấm xác nhận nếu đã xong)
            _logger.warning('Lịch hẹn Pancake: AI không kiểm tra được phiếu %s: %s', self.id, type(error).__name__)
            return False, 'Chưa kiểm tra được bằng AI'

    def _live_sync(self, config):
        if not config.live_check:
            return
        try:
            with self.env.cr.savepoint():
                self.conversation_id.sudo()._fetch_messages()
        except Exception:  # noqa: BLE001 — lỗi mạng/Pancake không chặn nhắc; dùng dữ liệu đã có
            _logger.warning('Lịch hẹn Pancake: không đồng bộ lại được hội thoại %s', self.conversation_id.id)

    @api.model
    def _pre_remind(self, config, now, deadline):
        """Hẹn có giờ: nhắc ngắn trước giờ hẹn (nếu nhân viên chưa làm sớm)."""
        scenario = self.env['dac.pancake.remind.scenario']._get('appt_pre')
        if not scenario.enabled:
            return
        due = self.search([('state', '=', 'open'), ('precision', '=', 'time'), ('pre_reminded_at', '=', False),
                           ('due_at', '>', now), ('due_at', '<=', now + timedelta(minutes=scenario.wait_minutes))], order='due_at, id', limit=20)
        for appointment in due:
            with self.env.cr.savepoint():
                appointment._live_sync(config)
                done, note = appointment._fulfilled(config, now)
                if done:
                    appointment.write({'state': 'auto_done', 'check_note': note, 'done_at': now, 'done_by': 'AI'})
                    continue
                values = appointment._values(now)

                def build(channel, types, a=appointment, values=values):
                    out = Builder().tags(a._targets(channel, types))
                    out.add((' ' if out.parts else '') + scenario._render(values))
                    return out.result()
                if appointment._send(config, 'pancake-appt:%s:pre' % appointment.id, build, scenario=scenario):
                    appointment.pre_reminded_at = now

    @api.model
    def _post_check(self, config, now, deadline):
        """Quá giờ hẹn: AI đọc lại tin nhắn; chưa thấy làm → nhắc kèm link phiếu; kiểm tra lại theo chu kỳ, tối đa N lần (kịch bản appt_post)."""
        scenario = self.env['dac.pancake.remind.scenario']._get('appt_post')
        if not scenario.enabled:
            return
        for appointment in self.search([('state', '=', 'open'), ('check_at', '<=', now)], order='check_at, id', limit=20):
            if clock.monotonic() > deadline:
                return
            with self.env.cr.savepoint():
                if appointment.promised_at and appointment.promised_at < now - timedelta(days=KEEP_DAYS):
                    appointment.write({'state': 'overdue', 'check_note': 'Quá cũ, không nhắc nữa'})
                    continue
                appointment._live_sync(config)
                done, note = appointment._fulfilled(config, now)
                if done:
                    appointment.write({'state': 'auto_done', 'check_note': note, 'done_at': now, 'done_by': 'AI'})
                    continue
                count = appointment.followup_count + 1
                values = appointment._values(now)

                def build(channel, types, a=appointment, values=values):
                    out = Builder().tags(a._targets(channel, types))
                    out.add((' ' if out.parts else '') + scenario._render(values))
                    return out.result()
                appointment._send(config, 'pancake-appt:%s:post%s' % (appointment.id, count), build, reply=True, scenario=scenario)
                vals = {'followup_count': count, 'check_note': note}
                if count >= scenario.max_count:
                    vals.update(state='overdue', check_at=False)
                else:
                    vals['check_at'] = add_working_minutes(now, scenario.repeat_minutes)
                appointment.write(vals)

    @api.model
    def _send_morning(self, config, now, deadline):
        """8:00 ngày làm việc: "📋 Nhắc việc từ hôm qua" — việc đã hẹn chưa làm + hẹn trong hôm nay, nhóm theo nhân viên."""
        at = local(now)
        today = at.date()
        state = self.env['dac.pancake.followup.state']._get()
        if at.weekday() not in WORK_DAYS or at.time() < MORNING_AT or state.morning_sent_date == today or not self.env['dac.pancake.remind.scenario']._on('morning'):
            return
        if not (state.review_date == today and state.review_done) and at.time() < MORNING_LATEST:
            return  # đợi đọc lại xong (tối đa tới 9:00)
        state.morning_sent_date = today
        start, _end = self._review_day(now)
        items = self.search([('state', 'in', ('open', 'overdue')), ('promised_at', '>=', start - timedelta(days=1)),
                             ('promised_at', '<', utc(today, time(0, 0))), ('due_at', '<', utc(today + timedelta(days=1), time(0, 0)))],
                            order='due_at, id')
        for is_test in (False, True):
            group = items.filtered(lambda a: a.is_test == is_test)
            if group:
                self._morning_message(config, group, today, now, is_test)
        items.write({'in_morning_list': True})

    @api.model
    def _morning_message(self, config, items, today, now, is_test):
        scenario = self.env['dac.pancake.remind.scenario']._get('morning')

        def line(a):
            when = '⏰' if a.due_at <= now else '🗓 %s' % local(a.due_at).strftime('%H:%M')
            return scenario._render(dict(a._values(now), icon=when), line=True)

        def build(channel, types):
            out = Builder().add(scenario._render({'ngay': today.strftime('%d/%m')}))
            mapped = {}
            unknown = []
            for appointment in items:
                targets = zalo_out.staff_targets(channel, appointment.staff_id)
                if targets:
                    mapped.setdefault(targets[0], []).append(appointment)
                else:
                    unknown.append(appointment)
            for target, group in mapped.items():
                out.add('\n').tags([target])
                for appointment in group:
                    out.add('\n' + line(appointment))
            if unknown:
                out.add('\nChưa rõ người phụ trách')
                fallback = zalo_out.fallback_targets(channel, types)
                if fallback:
                    out.add(': ').tags(fallback)
                for appointment in unknown:
                    out.add('\n' + line(appointment))
            return out.result()
        sent = zalo_out.send(self.env, config, CODES, 'pancake-morning:%s:%s' % (today.isoformat(), int(is_test)), build, is_test=is_test)
        scenario._log(sent, is_test=is_test, count=len(items))
