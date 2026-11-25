"""Chatbot chào khách mới trên Pancake — chế độ DUYỆT QUA ZALO.

Luồng (cron mỗi 2 phút, dựa trên bản ghi "khách đang chờ trả lời" của dac_pancake_remind):
1. Khách đang chờ → xác định khách MỚI hay ĐÃ LIÊN HỆ (hội thoại đã có tin trước đó, đang trao đổi/khách cũ, số điện thoại đã liên hệ,
   shop nhắn trước, có đơn trong Odoo, hoặc AI đọc tin thấy khách nhắc lần liên hệ trước). Khách đã liên hệ: chỉ ghi nhận, KHÔNG chào
   (luồng nhắc khách chưa được trả lời vẫn báo nhân viên như cũ).
2. Khách mới → bộ prompt Chào hỏi soạn lời chào → gửi người duyệt (Zalo) ảnh hội thoại + câu định gửi + mã G<id>.
3. Người duyệt trả lời: "duyệt" → gửi; "bỏ" → không gửi; câu khác = góp ý → lưu góp ý (chèn vào prompt các lần sau) và soạn lại.
4. Ngay trước khi gửi: tải lại tin mới nhất từ Pancake. Nhân viên đã nhắn → không gửi. Khách nhắn thêm → soạn lại, xin duyệt lại.
Không bao giờ tự gửi cho khách khi chưa được duyệt.
"""
import json
import logging
import re
import shutil
import time
from datetime import timedelta

import requests

from odoo import api, fields, models
from odoo.exceptions import UserError

from odoo.addons.dac_pancake_remind.models import chat_image
from odoo.addons.dac_pancake_remind.models.pancake_api import API_V1, PancakeError, platform_of, redact
from odoo.addons.dac_pancake_remind.models import zalo_out
from odoo.addons.dac_pancake_remind.models.remind_wait import CODES as REMIND_CODES, NOTES, _picture, classify

from . import chat_stats as cs
from .chat_prompt import SYSTEM_TEXT_RE, channel_key

_logger = logging.getLogger(__name__)

LOOKBACK = timedelta(hours=2)   # chỉ chào khách nhắn trong 2 giờ gần đây (không chào tồn đọng cũ)
EXPIRE = timedelta(hours=3)     # chờ duyệt quá lâu thì bỏ
REMIND_MINUTES = (5, 15, 30, 60)  # nhắc người duyệt sau bấy nhiêu phút kể từ lần gửi xin duyệt gần nhất
HISTORY = 30                    # số tin gần nhất đưa cho chatbot
SEND_TIMEOUT = (10, 30)
_TAIL = r'(?:\s+(?:nha|nhe|nhen|di|luon|em|a|ak|nhé|nhá))*'
APPROVE_RE = re.compile(r'^(?:g\s?\d+\s+)?(?:duyet|ok|oke|okie|okay|oki|dong y|gui|gui di|gui luon|duoc|dc|chot|yes|y|\+1)' + _TAIL + r'(?:\s+g\s?\d+)?$')
CANCEL_RE = re.compile(r'^(?:g\s?\d+\s+)?(?:bo|bo qua|khong gui|ko gui|k gui|huy|thoi|dung gui|skip)' + _TAIL + r'(?:\s+g\s?\d+)?$')
CODE_RE = re.compile(r'\bg\s?(\d{1,7})\b')
STATES = [('waiting', 'Chờ duyệt'), ('sent', 'Đã gửi khách'), ('returning', 'Khách đã liên hệ — không chào'),
          ('staff_replied', 'Nhân viên đã nhắn — không gửi'), ('cancelled', 'Người duyệt bỏ'), ('expired', 'Quá hạn duyệt'),
          ('failed', 'Lỗi')]
ROLE_LABELS = {'customer': 'Khách', 'staff': 'Nhân viên', 'bot': 'Bot'}


SESSION_GAP = timedelta(hours=24)  # khách nhắn tiếp trong khoảng này sau tin bot thì bot trả lời tiếp (cùng phiên)


def _norm(text):
    return ' '.join(cs.fold(text or '').split())


def _fold(text):
    return ' '.join(re.sub(r'[^\w\s+]', ' ', cs.fold(text or '')).split())


def pancake_send(conversation, text):
    """Gửi một tin chữ vào hội thoại Pancake (Zalo/Messenger). Lỗi → PancakeError (không chứa token)."""
    page = conversation.page_id
    url = '%s/pages/%s/conversations/%s/messages' % (API_V1, page.page_fm_id, conversation.conversation_fm_id)
    try:
        response = requests.post(url, params={'page_access_token': page._token()}, json={'action': 'reply_inbox', 'message': text},
                                 headers={'Accept': 'application/json'}, timeout=SEND_TIMEOUT)
    except requests.RequestException as exc:
        raise PancakeError(redact(exc)) from None
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code != 200 or (isinstance(data, dict) and data.get('success') is False):
        message = data.get('message') if isinstance(data, dict) else ''
        raise PancakeError(redact(message or 'HTTP %s' % response.status_code))
    return data if isinstance(data, dict) else {}


class ChatGreeting(models.Model):
    _name = 'dac.chat.greeting'
    _description = 'Lời chào khách mới (chờ duyệt qua Zalo)'
    _order = 'id desc'

    conversation_id = fields.Many2one('dac.pancake.conversation', string='Hội thoại', required=True, ondelete='cascade', index=True)
    wait_id = fields.Many2one('dac.pancake.remind.wait', string='Khách đang chờ', ondelete='set null')
    customer_name = fields.Char(string='Khách', related='conversation_id.customer_name')
    platform = fields.Char(string='Kênh', related='conversation_id.platform')
    external_url = fields.Char(string='Link Pancake', related='conversation_id.external_url')
    since = fields.Datetime(string='Khách nhắn lúc', required=True, index=True)
    state = fields.Selection(STATES, string='Trạng thái', default='waiting', required=True, index=True)
    is_test = fields.Boolean(string='Thử nghiệm (page thử)', readonly=True, index=True,
        help='Tạo từ nút "Chạy thử chào khách" ở Thiết lập: luôn coi là khách mới để thử trọn quy trình.')
    returning_reason = fields.Char(string='Vì sao là khách đã liên hệ')
    version = fields.Integer(string='Bản', default=0, readonly=True)
    prompt_id = fields.Many2one('dac.chat.prompt', string='Bộ prompt', ondelete='set null', readonly=True)
    messages_json = fields.Text(string='Tin định gửi (JSON)', readonly=True)
    messages_text = fields.Text(string='Tin định gửi', compute='_compute_messages_text')
    handoff_reason = fields.Char(string='Chuyển nhân viên', readonly=True)
    last_message_id = fields.Many2one('dac.pancake.message', string='Tin cuối đã xét', readonly=True, ondelete='set null')
    approval_message_ids = fields.Many2many('dac.zalo.channel.message', 'dac_chat_greeting_zalo_rel', 'greeting_id', 'message_id',
                                            string='Tin gửi người duyệt', readonly=True)
    reply_ids = fields.One2many('dac.chat.greeting.reply', 'greeting_id', string='Người duyệt trả lời', readonly=True)
    log = fields.Text(string='Nhật ký', readonly=True)
    sent_at = fields.Datetime(string='Gửi khách lúc', readonly=True)
    error_message = fields.Char(string='Lỗi', readonly=True)
    tokens_in = fields.Integer(string='Token vào', readonly=True)
    tokens_out = fields.Integer(string='Token ra', readonly=True)
    asked_at = fields.Datetime(string='Gửi xin duyệt lúc', readonly=True)
    remind_count = fields.Integer(string='Số lần nhắc người duyệt', readonly=True)
    gender_used = fields.Char(string='Xưng hô đã dùng', readonly=True)
    session_id = fields.Many2one('dac.chat.greeting', string='Phiên (lượt đầu)', readonly=True, ondelete='set null', index=True,
        help='Bot giữ cuộc trò chuyện nhiều lượt: các lượt sau trỏ về lượt chào đầu tiên.')
    turn = fields.Integer(string='Lượt', default=1, readonly=True)
    handoff_notified = fields.Boolean(string='Đã báo nhóm', readonly=True,
        help='Bot đã báo nhóm Zalo để nhân viên vào nhắn tiếp; phiên bot kết thúc.')

    _sql_constraints = [('conversation_since_unique', 'unique(conversation_id, since)', 'Lượt khách nhắn này đã có lời chào.')]

    @api.depends('messages_json')
    def _compute_messages_text(self):
        for greeting in self:
            greeting.messages_text = '\n'.join(greeting._messages()) or False

    def _messages(self):
        try:
            items = json.loads(self.messages_json or '[]')
        except ValueError:
            return []
        return [str(m) for m in items if str(m).strip()]

    def _note(self, text):
        stamp = fields.Datetime.context_timestamp(self.with_context(tz='Asia/Ho_Chi_Minh'), fields.Datetime.now()).strftime('%d/%m %H:%M')
        self.log = ('%s\n%s  %s' % (self.log or '', stamp, text)).strip()

    # ── Cron ─────────────────────────────────────────────────────────────────
    @api.model
    def _cron_run(self):
        """Mỗi 2 phút. Không raise."""
        config = self.env['dac.chat.config']._get()
        self.env.cr.execute('SELECT pg_try_advisory_xact_lock(%s, %s)', (73480, 1))
        if not self.env.cr.fetchone()[0]:
            return
        for step in (self._discover, self._check_pending):
            try:
                with self.env.cr.savepoint():
                    step(config)
            except Exception:  # noqa: BLE001 — cron không được raise; không log nội dung tin
                _logger.exception('Chào khách mới: bước %s lỗi', step.__name__)

    @api.model
    def _ready(self, config):
        return config.greeting_active and config.greeting_approver_channel_id and config.greeting_approver_channel_id.active

    @api.model
    def _discover(self, config):
        if not self._ready(config):
            return
        now = fields.Datetime.now()
        floor = max(config.greeting_activated_at or now, now - LOOKBACK)
        # sudo(): luồng nền; chỉ đọc bản ghi khách đang chờ của dac_pancake_remind
        waits = self.env['dac.pancake.remind.wait'].sudo().search([('since', '>=', floor), ('conversation_id.is_internal', '=', False)],
                                                                   order='since, id', limit=30)
        for wait in waits:
            if self.search_count([('conversation_id', '=', wait.conversation_id.id), ('since', '=', wait.since)]):
                continue
            session = self._active_session(wait.conversation_id, wait.since)
            greeting = self.create({'conversation_id': wait.conversation_id.id, 'wait_id': wait.id, 'since': wait.since,
                                    'session_id': (session.session_id or session).id if session else False,
                                    'turn': session.turn + 1 if session else 1})
            try:
                with self.env.cr.savepoint():
                    greeting._start(wait.customer_kind)
            except Exception as error:  # noqa: BLE001 — một khách lỗi không chặn khách khác
                _logger.exception('Chào khách mới: không soạn được lời chào %s', greeting.id)
                greeting.write({'state': 'failed', 'error_message': str(error)[:250]})

    @api.model
    def _active_session(self, conversation, since):
        """Lượt bot gần nhất còn giữ cuộc trò chuyện: đã gửi khách, chưa báo nhóm, trong SESSION_GAP, chưa có nhân viên thật vào nhắn."""
        last = self.search([('conversation_id', '=', conversation.id), ('since', '<', since)], order='since desc, id desc', limit=1)
        root = last.session_id or last
        if not last or last.state != 'sent' or last.handoff_notified or root.handoff_notified:
            return self.browse()
        if since - (last.sent_at or last.since) > SESSION_GAP:
            return self.browse()
        bots, bot_texts = last._bots(), last._bot_texts()
        between = self.env['dac.pancake.message'].sudo().search([('conversation_id', '=', conversation.id),
                                                                ('inserted_at', '>=', last.since), ('inserted_at', '<', since)])
        if any(last._kind_of(m, bots, bot_texts) == 'staff' for m in between):
            return self.browse()  # nhân viên thật đã vào: bot thôi
        return last

    def _start(self, kind='new'):
        self.ensure_one()
        reason = '' if (self.is_test or self.session_id) else self._returning_reason(kind)
        if reason:
            self.write({'state': 'returning', 'returning_reason': reason, 'last_message_id': self._latest_message().id})
            self._note('Khách đã liên hệ: %s. Không chào; luồng nhắc khách chưa được trả lời vẫn báo nhân viên.' % reason)
            return
        if self._compose():
            self._ask_approval()

    # ── Khách mới hay đã liên hệ ─────────────────────────────────────────────
    def _bots(self):
        return self.env['dac.pancake.remind.wait']._bot_names()

    def _bot_texts(self):
        """Nội dung các tin bot đã gửi trong hội thoại này (để không nhầm tin bot là nhân viên trả lời)."""
        sent = self.search([('conversation_id', '=', self.conversation_id.id), ('state', '=', 'sent')])
        return {_norm(text) for greeting in sent for text in greeting._messages()}

    def _kind_of(self, message, bots, bot_texts):
        kind = classify(message, bots)
        if kind == 'staff' and bot_texts and _norm(cs.plain(message.content_html or '')) in bot_texts:
            return 'bot'
        return kind

    def _returning_reason(self, kind='new'):
        """Lý do coi là khách ĐÃ LIÊN HỆ ('' = khách mới)."""
        self.ensure_one()
        if kind == 'ongoing':
            return 'đang trao đổi (nhân viên đã trả lời trong ngày)'
        if kind == 'old':
            return 'khách cũ quay lại (hội thoại cũ, đã có nhân viên trả lời hoặc có đơn)'
        conversation = self.conversation_id.sudo()
        bots = self._bots()
        # sudo(): luồng nền đọc tin Pancake
        earlier = self.env['dac.pancake.message'].sudo().search([('conversation_id', '=', conversation.id), ('inserted_at', '<', self.since)],
                                                                order='inserted_at desc, id desc', limit=50)
        first = next((m for m in earlier if classify(m, bots)), None)
        if first:
            return 'hội thoại đã có tin từ %s' % fields.Datetime.context_timestamp(
                self.with_context(tz='Asia/Ho_Chi_Minh'), first.inserted_at).strftime('%d/%m/%Y')
        contact = self.env['dac.new.contact'].sudo().search([('conversation_id', '=', conversation.id)], limit=1)
        if contact.seen_before:
            return 'số điện thoại %s' % (contact.seen_note or 'đã liên hệ trước đó')
        if contact.staff_first:
            return 'shop đã nhắn trước'
        if self.env['dac.new.contact']._has_orders(conversation.partner_id):
            return 'đã có đơn hàng trong Odoo'
        return ''

    # ── Soạn lời chào ────────────────────────────────────────────────────────
    def _latest_message(self):
        return self.env['dac.pancake.message'].sudo().search([('conversation_id', '=', self.conversation_id.id)],
                                                             order='inserted_at desc, id desc', limit=1)

    def _history(self, until=None):
        """[(vai trò, nội dung)] cũ → mới, chỉ tin hội thoại thật (khách / nhân viên / bot); `until`: chỉ tới tin này."""
        bots = self._bots()
        domain = [('conversation_id', '=', self.conversation_id.id)]
        if until:
            domain += ['|', ('inserted_at', '<', until.inserted_at), '&', ('inserted_at', '=', until.inserted_at), ('id', '<=', until.id)]
        recent = self.env['dac.pancake.message'].sudo().search(domain, order='inserted_at desc, id desc', limit=HISTORY * 2)
        bot_texts = self._bot_texts()
        rows = []
        for message in recent:
            kind = self._kind_of(message, bots, bot_texts)
            if not kind:
                continue
            text = cs.plain(message.content_html or '')
            if SYSTEM_TEXT_RE.search(text):
                continue  # thông báo kết bạn của Zalo, không phải khách nhắn
            attachments = (json.loads(message.raw_json or '{}').get('attachments') or []) if message.raw_json else []
            kinds = {a.get('type') for a in attachments if isinstance(a, dict)}
            # Nhãn đúng loại: sticker KHÔNG phải tệp (trước đây gắn chung "[Tệp/đính kèm]" nên bot tưởng khách đã gửi file)
            labels = ['[Hình ảnh]' if kind == 'photo' else NOTES.get(kind, '[Tệp]') for kind in sorted(k for k in kinds if k)
                      if kind != 'styled_message']
            if labels:
                text = ('%s %s' % (' '.join(dict.fromkeys(labels)), text)).strip()
            if text:
                rows.append((kind, text))
            if len(rows) >= HISTORY:
                break
        return rows[::-1]

    @api.model
    def _prompt(self):
        """Bộ Chào hỏi đang dùng; chưa kích hoạt thì dùng bản nháp mới nhất (người duyệt vẫn xem trước khi gửi)."""
        Prompt = self.env['dac.chat.prompt'].sudo()
        return Prompt._active('greeting') or Prompt.search([('mode', '=', 'greeting'), ('state', '=', 'draft')], order='id desc', limit=1)

    def _compose(self, feedback=''):
        """Soạn (lại) lời chào. Trả False nếu AI nhận ra là khách đã liên hệ (đã chuyển trạng thái)."""
        self.ensure_one()
        prompt = self._prompt()
        if not prompt:
            raise UserError('Chưa có bộ prompt Chào hỏi.')
        history = self._history()
        if not history or history[-1][0] != 'customer':
            history.append(('customer', '(khách vừa nhắn)'))
        note = ''
        if feedback:
            note = 'Người duyệt góp ý cho bản trước (%s): "%s". Hãy viết lại lời chào theo góp ý này.' % (
                ' / '.join(self._messages()) or 'trống', feedback)
        result, usage = self._ask_bot(prompt, history, note)
        vals = {'prompt_id': prompt.id, 'tokens_in': self.tokens_in + usage.get('in', 0), 'tokens_out': self.tokens_out + usage.get('out', 0),
                'last_message_id': self._latest_message().id}
        if result.get('returning_customer') and not feedback:
            self.write(dict(vals, state='returning', returning_reason='AI: %s' % (result.get('returning_reason') or 'khách nhắc lần liên hệ trước')[:200]))
            self._note('AI nhận ra khách đã liên hệ: %s. Không chào.' % self.returning_reason)
            return False
        self.write(dict(vals, version=self.version + 1, messages_json=json.dumps(result['messages'], ensure_ascii=False),
                        handoff_reason=(result.get('handoff_reason') or '')[:250] if result.get('handoff') else False, state='waiting'))
        self._note('Soạn bản %s%s: %s' % (self.version, ' theo góp ý' if feedback else '', ' / '.join(result['messages'])))
        if self.handoff_reason:
            self._notify_handoff()
        return True

    def _notify_handoff(self):
        """Bot cần người thật: báo NGAY vào nhóm Zalo đã chọn, tag người phụ trách hội thoại (chưa có thì người được cài cho loại
        thông báo nhắc khách). Mỗi phiên báo một lần; khách không thấy gì (bot chỉ nhắn câu giữ chuyện)."""
        self.ensure_one()
        root = self.session_id or self
        channel = self.env['dac.chat.config']._get().greeting_handoff_channel_id
        if not channel or root.handoff_notified:
            return False
        conversation = self.conversation_id.sudo()
        staff = self.env['dac.pancake.staff']._of_conversation(conversation)
        targets = zalo_out.targets_for(channel, staff, zalo_out.event_types(self.env, REMIND_CODES))
        said = [text for role, text in self._history() if role == 'customer'][-3:]
        out = zalo_out.Builder()
        out.add('🙋 Cần nhân viên vào nhắn tiếp: khách %s (%s)\nLý do: %s\nKhách vừa nhắn: %s\n'
                'Bot đang giữ chuyện, khách không biết đang được chuyển người.\n' % (
                    conversation.customer_name or 'Khách', conversation.platform or 'Pancake', self.handoff_reason,
                    ' / '.join(said) or '(không có)'))
        if targets:
            out.add('Nhờ ').tags(targets).add(' vào hỗ trợ.\n')
        url = conversation._short_url() if conversation.external_url else ''
        if url:
            out.add(url)
        text, mentions = out.result()
        channel._queue(text.strip(), 'greet:%s:handoff' % root.id, mentions=mentions)
        (root | self).write({'handoff_notified': True})
        self._note('Đã báo nhóm "%s" để nhân viên vào nhắn tiếp (%s).' % (channel.name, self.handoff_reason))
        return True

    def _ask_bot(self, prompt, history, note=''):
        """Gọi chatbot; câu nào lộ là AI/tự giới thiệu bị chặn thì soạn lại một lần. Trả (kết quả, token đã dùng)."""
        conversation = self.conversation_id.sudo()
        gender = conversation._gender()
        args = (history, fields.Datetime.now(), conversation.customer_name or '', channel_key(conversation.platform))
        result = prompt._reply(*args, note=note, gender=gender)  # câu bị chặn (lộ AI, "chưa chắc") được _reply soạn lại
        usage = dict(result.get('usage') or {})
        if conversation._gender_from_chat(result.get('customer_gender')):
            gender = conversation._gender()
        self.gender_used = '%s (%s)' % ({'male': 'anh', 'female': 'chị'}.get(gender[0], 'anh/chị'), gender[1])
        return result, usage

    def _preview(self, feedback):
        """Soạn lại theo góp ý chỉ để người duyệt xem (lời chào này không gửi được nữa); dùng đoạn chat lúc soạn bản gốc."""
        self.ensure_one()
        prompt = self._prompt()
        history = self._history(until=self.last_message_id) or [('customer', '(khách vừa nhắn)')]
        note = 'Người duyệt góp ý cho bản trước (%s): "%s". Hãy viết lại lời chào theo góp ý này.' % (' / '.join(self._messages()) or 'trống', feedback)
        result, usage = self._ask_bot(prompt, history, note)
        self.write({'tokens_in': self.tokens_in + usage.get('in', 0), 'tokens_out': self.tokens_out + usage.get('out', 0)})
        self._note('Bản xem lại theo góp ý (không gửi): %s' % ' / '.join(result['messages']))
        return result['messages']

    # ── Người duyệt (Zalo) ───────────────────────────────────────────────────
    def _channel(self):
        return self.env['dac.chat.config']._get().greeting_approver_channel_id

    def _image(self):
        binary = shutil.which('wkhtmltoimage')
        if not binary:
            return False
        bots = self._bots()
        recent = self.env['dac.pancake.message'].sudo().search([('conversation_id', '=', self.conversation_id.id)],
                                                               order='inserted_at desc, id desc', limit=18)
        rows, pictures = [], 0
        for message in recent:
            kind = classify(message, bots)
            if not kind:
                continue
            raw = json.loads(message.raw_json or '{}')
            photos = [a for a in raw.get('attachments') or [] if isinstance(a, dict) and a.get('type') == 'photo']
            image = None
            if photos and pictures < 2:
                image = _picture(photos[0].get('url') or photos[0].get('origin_url'))
                pictures += image is not None
            rows.append({'side': 'in' if kind == 'customer' else 'out', 'text': message.content_html or '', 'image': image,
                         'note': '[Hình ảnh]' if photos and not image else ''})
            if len(rows) >= 6:
                break
        rows.reverse()
        rows += [{'side': 'out', 'text': '🤖 ' + text, 'image': None, 'note': ''} for text in self._messages()]
        try:
            html = chat_image.build_html(self.conversation_id.customer_name or 'Khách', None, rows, platform=self.conversation_id.platform or '',
                                         page_name=self.conversation_id.page_id.name or '',
                                         waiting_text='Tin xanh cuối là lời chào bot ĐỊNH gửi (chưa gửi) — mã G%s' % self.id)
            return chat_image.render_image(html, binary)
        except Exception:  # noqa: BLE001 — không dựng được ảnh vẫn gửi tin chữ
            _logger.exception('Chào khách mới: không dựng được ảnh %s', self.id)
            return False

    def _ask_approval(self, prefix=''):
        self.ensure_one()
        channel = self._channel()
        if not channel:
            return
        moment = fields.Datetime.context_timestamp(self.with_context(tz='Asia/Ho_Chi_Minh'), self.since)
        lines = ['🧪 THỬ NGHIỆM trên page thử, không phải khách thật.'] if self.is_test else []
        lines += [prefix] if prefix else []
        head = ('💬 Khách %s nhắn tiếp (lượt %s, %s)' % (self.conversation_id.customer_name or 'Khách', self.turn, moment.strftime('%H:%M %d/%m'))
                if self.session_id else '🆕 Khách mới nhắn %s: %s (%s)' % (self.conversation_id.platform or 'Pancake',
                                                                          self.conversation_id.customer_name or 'Khách', moment.strftime('%H:%M %d/%m')))
        lines += [head, '%s bot định gửi — mã G%s, bản %s:' % ('Tin' if self.session_id else 'Lời chào', self.id, self.version)]
        lines += ['%s. %s' % (i, text) for i, text in enumerate(self._messages(), 1)]
        if self.gender_used:
            lines.append('Xưng hô: %s' % self.gender_used)
        if self.handoff_reason:
            lines.append('Cần người thật (%s): %s' % (self.handoff_reason, 'đã báo nhóm để nhân viên vào nhắn tiếp, bot dừng sau tin này.'
                                                     if self.handoff_notified else 'chưa chọn nhóm báo nhân viên ở Thiết lập.'))
        others = self.search_count([('state', '=', 'waiting'), ('id', '!=', self.id)])
        lines.append('Trả lời "duyệt" để gửi, "bỏ" để không gửi, hoặc nhắn góp ý để bot soạn lại.%s' % (
            ' Đang có %s lời chào khác chờ duyệt: nhắn kèm mã, vd "duyệt G%s".' % (others, self.id) if others else ''))
        url = self.conversation_id._short_url() if self.conversation_id.external_url else ''
        if url:
            lines.append(url)
        message = channel._queue('\n'.join(lines), 'greet:%s:v%s' % (self.id, self.version), image=self._image() or None)
        self.write({'approval_message_ids': [(4, message.id)], 'asked_at': fields.Datetime.now(), 'remind_count': 0})

    def _tell(self, text, key):
        channel = self._channel()
        if channel:
            channel._queue(text, key)

    # ── Kiểm tra trước khi gửi / trong lúc chờ duyệt ─────────────────────────
    def _changes(self, refresh=False):
        """('staff' | 'customer' | '') — có gì mới trong hội thoại sau tin cuối đã xét."""
        self.ensure_one()
        if refresh:
            try:
                self.conversation_id.sudo()._fetch_messages()
            except PancakeError as error:
                self._note('Không tải lại được tin mới từ Pancake (%s); dùng dữ liệu đã có.' % error)
        domain = [('conversation_id', '=', self.conversation_id.id)]
        if self.last_message_id:
            domain += ['|', ('inserted_at', '>', self.last_message_id.inserted_at),
                       '&', ('inserted_at', '=', self.last_message_id.inserted_at), ('id', '>', self.last_message_id.id)]
        newer = self.env['dac.pancake.message'].sudo().search(domain, order='inserted_at, id')
        bots, bot_texts = self._bots(), self._bot_texts()
        kinds = {self._kind_of(m, bots, bot_texts) for m in newer}
        if 'staff' in kinds:
            return 'staff'
        return 'customer' if 'customer' in kinds else ''

    def _react_to_changes(self, change):
        """Áp dụng thay đổi trong hội thoại; trả True nếu đã xử lý (không gửi bản hiện tại)."""
        if change == 'staff':
            self.write({'state': 'staff_replied'})
            self._note('Nhân viên đã nhắn khách: không gửi lời chào.')
            self._tell('G%s: nhân viên đã nhắn khách %s, bot không gửi lời chào nữa.' % (self.id, self.conversation_id.customer_name or ''),
                       'greet:%s:staff' % self.id)
            return True
        if change == 'customer':
            if self._compose():
                self._ask_approval('Khách vừa nhắn thêm, em soạn lại (bản cũ bỏ):')
            return True
        return False

    @api.model
    def _check_pending(self, config):
        now = fields.Datetime.now()
        for greeting in self.search([('state', '=', 'waiting')], order='id'):
            try:
                with self.env.cr.savepoint():
                    if now - greeting.since > EXPIRE:
                        greeting.write({'state': 'expired'})
                        greeting._note('Quá %s giờ chưa duyệt: bỏ.' % int(EXPIRE.total_seconds() // 3600))
                        continue
                    if not greeting._react_to_changes(greeting._changes()):
                        greeting._remind(now)
            except Exception as error:  # noqa: BLE001
                _logger.exception('Chào khách mới: kiểm tra %s lỗi', greeting.id)
                greeting.write({'error_message': str(error)[:250]})

    def _remind(self, now):
        """Nhắc người duyệt (trả lời ngay tin xin duyệt) khi lời chào chờ quá REMIND_MINUTES."""
        self.ensure_one()
        if not self.asked_at or self.remind_count >= len(REMIND_MINUTES):
            return False
        if now - self.asked_at < timedelta(minutes=REMIND_MINUTES[self.remind_count]):
            return False
        channel = self._channel()
        if not channel:
            return False
        waited = int((now - self.since).total_seconds() // 60)
        text = ('⏰ Nhắc duyệt G%s: khách %s đã chờ %s phút, chưa ai trả lời. Nhắn "duyệt" để gửi lời chào ở trên, "bỏ" để không gửi, '
                'hoặc góp ý để bot soạn lại.%s') % (self.id, self.conversation_id.customer_name or '', waited,
                                                  ' (Nhiều lời chào đang chờ: nhắn kèm mã, vd "duyệt G%s".)' % self.id
                                                  if self.search_count([('state', '=', 'waiting'), ('id', '!=', self.id)]) else '')
        asked = self.approval_message_ids.sorted('id')[-1:]
        reply_to = asked if asked and asked.state == 'sent' else None
        channel._queue(text, 'greet:%s:v%s:remind%s' % (self.id, self.version, self.remind_count + 1), reply_to=reply_to)
        self.remind_count += 1
        self._note('Nhắc người duyệt lần %s (khách chờ %s phút).' % (self.remind_count, waited))
        return True

    # ── Duyệt / bỏ / góp ý ──────────────────────────────────────────────────
    def action_approve(self):
        """Duyệt và gửi (nút trong Odoo; người duyệt nhắn "duyệt" trên Zalo cũng vào đây)."""
        for greeting in self.filtered(lambda g: g.state == 'waiting'):
            if greeting._react_to_changes(greeting._changes(refresh=True)):
                continue
            sent = 0
            try:
                for text in greeting._messages():
                    pancake_send(greeting.conversation_id.sudo(), text)
                    sent += 1
                    time.sleep(1)
            except PancakeError as error:
                greeting.write({'state': 'failed', 'error_message': str(error)[:250]})
                greeting._note('Gửi lỗi sau %s tin: %s' % (sent, error))
                greeting._tell('G%s: gửi cho khách bị lỗi (%s). Nhân viên trả lời khách giúp.' % (greeting.id, str(error)[:120]),
                               'greet:%s:failed' % greeting.id)
                continue
            greeting.write({'state': 'sent', 'sent_at': fields.Datetime.now()})
            greeting._note('Đã gửi %s tin cho khách.' % sent)
            greeting._tell('✅ G%s: đã gửi lời chào cho %s.%s' % (
                greeting.id, greeting.conversation_id.customer_name or 'khách',
                ' Nhân viên vào tiếp nhận: %s.' % greeting.handoff_reason if greeting.handoff_reason else ' Nhân viên vào tiếp tục tư vấn nhé.'),
                'greet:%s:sent' % greeting.id)
        return True

    def action_cancel(self):
        for greeting in self.filtered(lambda g: g.state == 'waiting'):
            greeting.write({'state': 'cancelled'})
            greeting._note('Người duyệt bỏ, không gửi.')
        return True

    def _apply_feedback(self, text, author=''):
        self.ensure_one()
        self.env['dac.chat.feedback'].create({'mode': 'greeting', 'text': text[:1000], 'greeting_id': self.id, 'author': author})
        if self._compose(feedback=text):
            self._ask_approval('Đã ghi nhận góp ý (lưu cho các lần sau). Bản soạn lại:')

    @api.model
    def _handle_reply(self, text, author=''):
        """Một tin người duyệt nhắn → (hành động, lời chào)."""
        if text.strip().startswith('[Tin nhắn'):
            return 'ignored', self.browse()  # sticker/ảnh/tệp: không hiểu là duyệt hay góp ý
        folded = _fold(text)
        if not folded and any(ch in text for ch in ('👍', '👌', '✅', '🆗')):
            folded = 'ok'
        code = CODE_RE.search(folded)
        greeting = self.browse()
        if code:
            greeting = self.search([('id', '=', int(code.group(1)))], limit=1)
        if not greeting:
            greeting = self.search([('state', '=', 'waiting')], order='write_date desc, id desc', limit=1)
        if (not greeting or greeting.state != 'waiting') and not APPROVE_RE.match(folded) and not CANCEL_RE.match(folded):
            return self._late_feedback(text, author, greeting)
        if not greeting or greeting.state != 'waiting':
            self._tell('Không có lời chào nào đang chờ duyệt%s.' % (' với mã G%s' % code.group(1) if code else ''),
                       'greet:none:%s' % fields.Datetime.now().strftime('%Y%m%d%H%M%S%f'))
            return 'none', greeting
        if APPROVE_RE.match(folded):
            greeting._note('Người duyệt: "%s" → gửi.' % text[:100])
            greeting.action_approve()
            return 'approve', greeting
        if CANCEL_RE.match(folded):
            greeting.action_cancel()
            greeting._tell('G%s: đã bỏ, bot không gửi.' % greeting.id, 'greet:%s:cancel' % greeting.id)
            return 'cancel', greeting
        greeting._note('Người duyệt góp ý: "%s".' % text[:300])
        greeting._apply_feedback(text, author)
        return 'feedback', greeting

    @api.model
    def _late_feedback(self, text, author, greeting=None):
        """Góp ý tới khi không còn lời chào chờ duyệt: vẫn lưu cho các lần sau và gửi bản soạn lại của lời chào gần nhất để xem."""
        last = greeting if greeting and greeting.messages_json else self.search([('messages_json', '!=', False)], order='id desc', limit=1)
        self.env['dac.chat.feedback'].create({'mode': 'greeting', 'text': text[:1000], 'greeting_id': last.id or False, 'author': author})
        lines = ['Đã ghi nhận góp ý, áp dụng cho các lời chào sau.']
        if last:
            last._note('Người duyệt góp ý (lời chào đã đóng): "%s".' % text[:300])
            state = dict(STATES).get(last.state, last.state)
            lines.append('G%s không gửi được nữa (%s). Bản soạn lại theo góp ý để xem:' % (last.id, state.lower()))
            lines += ['%s. %s' % (i, m) for i, m in enumerate(last._preview(text), 1)]
        self._tell('\n'.join(lines), 'greet:late:%s' % fields.Datetime.now().strftime('%Y%m%d%H%M%S%f'))
        return 'feedback', last or self.browse()

    @api.model
    def _ingest_approver(self, account_id, events):
        """Tách các tin người duyệt nhắn riêng cho trợ lý Zalo; trả các sự kiện còn lại cho luồng trợ lý."""
        config = self.env['dac.chat.config']._get()
        channel = config.greeting_approver_channel_id
        zalo = self.env['dac.zalo.config']._config()
        if not channel or channel.thread_type != 'user' or not zalo.assistant_uid or account_id != zalo.assistant_uid:
            return events
        rest = []
        Reply = self.env['dac.chat.greeting.reply']
        for event in events:
            if event.get('direction') != 'in' or event.get('historical') or str(event.get('thread_id')) != channel.thread_id:
                rest.append(event)
                continue
            key = '%s:%s' % (account_id, event['message_id'])
            if Reply.search_count([('key', '=', key)]):
                continue  # bridge gửi lại lô cũ
            reply = Reply.create({'key': key, 'text': event['text'][:2000]})
            try:
                with self.env.cr.savepoint():
                    action, greeting = self._handle_reply(event['text'], channel.name or '')
                    reply.write({'action': action, 'greeting_id': greeting.id or False})
            except Exception as error:  # noqa: BLE001 — không để lỗi chặn tin Zalo khác
                _logger.exception('Chào khách mới: không xử lý được tin người duyệt')
                reply.write({'action': 'error', 'error_message': str(error)[:250]})
        return rest


class ChatGreetingReply(models.Model):
    _name = 'dac.chat.greeting.reply'
    _description = 'Tin người duyệt lời chào nhắn qua Zalo'
    _order = 'id desc'

    key = fields.Char(required=True, index=True, readonly=True)
    text = fields.Text(string='Tin nhắn', readonly=True)
    greeting_id = fields.Many2one('dac.chat.greeting', string='Lời chào', ondelete='set null', readonly=True)
    action = fields.Selection([('approve', 'Duyệt'), ('cancel', 'Bỏ'), ('feedback', 'Góp ý'), ('none', 'Không có lời chào chờ'),
                               ('ignored', 'Bỏ qua (sticker/tệp)'), ('error', 'Lỗi')], string='Hiểu là', readonly=True)
    error_message = fields.Char(string='Lỗi', readonly=True)
    _sql_constraints = [('key_unique', 'unique(key)', 'Tin này đã xử lý.')]


class ChatFeedback(models.Model):
    """Góp ý cho chatbot (người duyệt qua Zalo, hoặc admin ở màn hình Huấn luyện & kiểm thử).

    Góp ý chưa được Agent viết vào bản prompt nào thì code chèn vào system prompt của bộ tương ứng ở mọi lần trả lời; khi Agent tối ưu
    prompt từ góp ý, bản mới được ghi ở `applied_prompt_ids` và góp ý không chèn lặp vào bản đó nữa. Tắt dòng nào để bỏ góp ý đó."""
    _name = 'dac.chat.feedback'
    _description = 'Góp ý cho chatbot'
    _order = 'id desc'

    text = fields.Text(string='Góp ý', required=True)
    mode = fields.Selection([('greeting', 'Chào hỏi'), ('info', 'Hỏi thông tin'), ('consult', 'Tư vấn')], string='Bộ', required=True,
                            default='greeting', index=True)
    source = fields.Selection([('approver', 'Người duyệt (Zalo)'), ('trainer', 'Huấn luyện & kiểm thử')], string='Nguồn',
                              default='approver', required=True)
    greeting_id = fields.Many2one('dac.chat.greeting', string='Từ lời chào', ondelete='set null', readonly=True)
    prompt_id = fields.Many2one('dac.chat.prompt', string='Góp ý trên bản', ondelete='set null', readonly=True)
    customer_text = fields.Text(string='Khách nhắn', readonly=True)
    bot_text = fields.Text(string='Bot trả lời', readonly=True)
    applied_prompt_ids = fields.Many2many('dac.chat.prompt', 'dac_chat_feedback_applied_rel', 'feedback_id', 'prompt_id',
                                          string='Đã viết vào bản', readonly=True)
    author = fields.Char(string='Người góp ý', readonly=True)
    active = fields.Boolean(default=True)

    def _line(self):
        self.ensure_one()
        text = ' '.join(self.text.split())
        if self.bot_text:
            return '- Khách: "%s" → bot trả lời: "%s" → góp ý: %s' % (
                ' '.join((self.customer_text or '').split())[:200], ' '.join(self.bot_text.split())[:300], text)
        return '- %s' % text

    @api.model
    def _pending(self, mode, prompt=None, limit=20):
        # sudo(): chatbot chạy nền (cron) vẫn cần đọc góp ý
        items = self.sudo().search([('mode', '=', mode)], limit=limit)
        return items.filtered(lambda f: not prompt or prompt not in f.applied_prompt_ids)

    @api.model
    def _text_for_mode(self, mode, prompt=None, limit=15):
        return '\n'.join(f._line() for f in self._pending(mode, prompt, limit))


class ZaloMessage(models.Model):
    _inherit = 'dac.zalo.message'

    @api.model
    def _ingest(self, account_id, events):
        # Tin người duyệt lời chào (chat riêng với trợ lý) được xử lý trước; còn lại đi luồng trợ lý như cũ
        rest = self.env['dac.chat.greeting'].sudo()._ingest_approver(account_id, events)
        return super()._ingest(account_id, rest)


class ChatConfigTestPage(models.Model):
    """Page thử để kiểm chứng gửi tin qua Pancake và chạy thử trọn quy trình chào khách trước khi bật cho khách thật.

    Page thử được lưu là page Pancake KHÔNG hoạt động, không lấy tin cũ: luồng nhắc khách và lấy tin không quét page này."""
    _inherit = 'dac.chat.config'

    test_page_fm_id = fields.Char(string='ID page thử', help='Page ID (vd pzl_… với Zalo) hoặc link Pancake của page thử.')
    test_page_token = fields.Char(string='Token page thử', groups='base.group_system', copy=False,
                                  help='Page Access Token của page thử (Pancake → Cài đặt page → Công cụ).')
    test_conversation_fm_id = fields.Char(string='ID hội thoại thử', help='Bỏ trống = hội thoại có tin mới nhất của page thử.')
    test_message = fields.Char(string='Tin gửi thử', default='Dạ em chào anh/chị ạ, đây là tin thử từ hệ thống Duy An.')
    test_result = fields.Text(string='Kết quả thử', readonly=True)

    def _test_conversation(self):
        self.ensure_one()
        # sudo(): token page thử chỉ quản trị hệ thống đọc được; nút thử (quản lý bấm) cần token để gọi Pancake, không lộ ra giao diện
        config = self.sudo()
        token = (config.test_page_token or '').strip()
        if not token or not (config.test_page_fm_id or '').strip():
            raise UserError('Nhập ID page thử và token page thử (cần quyền quản trị hệ thống để nhập token).')
        Page = self.env['dac.pancake.page'].sudo().with_context(active_test=False)
        page_id, _slug = Page._resolve_ids(config.test_page_fm_id.strip(), token)
        page = Page.search([('page_fm_id', '=', page_id)], limit=1)
        if not page:
            page = Page.create({'name': 'Page thử chatbot', 'page_fm_id': config.test_page_fm_id.strip(), 'page_access_token': token,
                                'active': False, 'history_enabled': False})
        elif (page.page_access_token or '').strip() != token:
            # Pancake chỉ giữ token MỚI NHẤT của mỗi page (tạo token mới thì token cũ báo "access_token renewed"): dùng token vừa nhập
            # và ghi vào page (kể cả page đang chạy, để luồng kéo tin dùng đúng token). Token mới lỗi thì giao dịch hoàn tác.
            page.write({'page_access_token': token})
        Conversation = self.env['dac.pancake.conversation'].sudo()
        try:
            if (config.test_conversation_fm_id or '').strip():
                fm_id = config.test_conversation_fm_id.strip()
                conversation = Conversation.search([('page_id', '=', page.id), ('conversation_fm_id', '=', fm_id)], limit=1) or Conversation.create({
                    'page_id': page.id, 'conversation_fm_id': fm_id, 'customer_name': 'Hội thoại thử',
                    'platform': platform_of({'page_id': page.page_fm_id})})
            else:
                now = fields.Datetime.now()
                rows = page._list_conversations(now - timedelta(days=30), now, max_pages=1)
                if not rows:
                    raise UserError('Page thử chưa có hội thoại nào trong 30 ngày: nhắn vào page thử một tin rồi bấm lại.')
                conversation, _new = Conversation._upsert(page, rows[0])
            conversation._fetch_messages()
        except PancakeError as error:
            # UserError hoàn tác cả giao dịch: page giữ nguyên token cũ
            raise UserError('Pancake báo lỗi với token vừa nhập: %s' % error) from None
        return conversation

    def _done(self, message, kind='success'):
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': 'Page thử', 'message': message, 'type': kind, 'sticky': kind != 'success',
            'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'}}}

    def action_test_send(self):
        """Gửi một tin chữ vào hội thoại thử; ghi lại Pancake trả gì và tin hiện trên Pancake với người gửi nào."""
        self.ensure_one()
        conversation = self._test_conversation()
        text = (self.test_message or '').strip() or 'Dạ em chào anh/chị ạ, đây là tin thử từ hệ thống Duy An.'
        try:
            data = pancake_send(conversation, text)
        except PancakeError as error:
            self.test_result = 'LỖI khi gửi vào hội thoại "%s" (%s): %s' % (conversation.customer_name, conversation.conversation_fm_id, error)
            return self._done(self.test_result, 'danger')
        time.sleep(3)
        try:
            conversation._fetch_messages()
        except PancakeError:
            pass
        sender, kind = '(chưa thấy tin vừa gửi khi tải lại)', ''
        bots = self.env['dac.pancake.remind.wait']._bot_names()
        for message in self.env['dac.pancake.message'].sudo().search([('conversation_id', '=', conversation.id)], order='inserted_at desc, id desc', limit=5):
            if text[:20] in cs.plain(message.content_html or ''):
                raw = json.loads(message.raw_json or '{}')
                author = raw.get('from') or {}
                sender = '%s%s' % (author.get('name') or '?', ' / admin_name=%s' % author['admin_name'] if author.get('admin_name') else '')
                kind = {'staff': 'tin NHÂN VIÊN (luồng nhắc sẽ coi khách đã được trả lời)', 'bot': 'tin BOT',
                        'customer': 'tin KHÁCH (sai)'}.get(classify(message, bots), 'không xác định')
                break
        self.test_result = ('Gửi OK vào hội thoại "%s" (%s).\nPancake trả về: %s\nTin vừa gửi hiện trên Pancake với người gửi: %s%s' % (
            conversation.customer_name, conversation.conversation_fm_id, redact(json.dumps(data, ensure_ascii=False))[:400], sender,
            (' — luồng nhắc khách hiểu là %s.' % kind) if kind else ''))
        return self._done(self.test_result)

    def action_test_greeting(self):
        """Chạy thử trọn quy trình chào khách trên hội thoại thử: soạn → người duyệt (Zalo) → kiểm tra lại → gửi."""
        self.ensure_one()
        if not self.greeting_approver_channel_id:
            raise UserError('Chọn Người duyệt (Zalo) trước.')
        conversation = self._test_conversation()
        greeting = self.env['dac.chat.greeting'].sudo().create({'conversation_id': conversation.id, 'since': fields.Datetime.now(), 'is_test': True})
        greeting._start()
        self.test_result = 'Đã soạn lời chào thử G%s (%s) và gửi %s duyệt qua Zalo. Trả lời "duyệt" trên Zalo để bot gửi vào hội thoại thử.' % (
            greeting.id, ' / '.join(greeting._messages()) or 'không có câu nào', self.greeting_approver_channel_id.name)
        return self._done(self.test_result)
