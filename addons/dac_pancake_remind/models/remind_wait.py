import io
import json
import logging
import re
import shutil
from datetime import timedelta, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import requests

from odoo import api, fields, models
from odoo.addons.dac_push.models.contact_intent import fold
from odoo.addons.dac_push.models.working_time import add_working_minutes
from odoo.addons.dac_zalo_assistant.models import channels as zalo_channels

from . import chat_image, reply_check, zalo_out
from .pancake_api import PancakeError
from .zalo_out import HEADLINE, ICONS, LEVELS, RANK, Builder, escalate, waited_text, working_minutes_between

_logger = logging.getLogger(__name__)
zalo_channels.GROUP_ICONS.setdefault('Pancake', 'fa-comments')

TZ = ZoneInfo('Asia/Ho_Chi_Minh')
REMIND_TEXT = 'Dạ có khách cần được trả lời ạ!'
KIND_LABELS = {'new': 'Khách mới', 'ongoing': 'Đang trao đổi', 'old': 'Khách cũ'}
KIND_SCENARIO = {'new': 'new_first', 'ongoing': 'ongoing', 'old': 'old'}  # kịch bản quyết định hạn trả lời và bật/tắt của từng loại khách
DIGEST_MAX = 25                        # số khách tối đa trong một tin gom
CODES = ('pancake.unanswered',)
DISCOVER_WINDOW = timedelta(hours=3)   # chỉ tìm khách mới trong tin vài giờ gần đây (cron chạy mỗi 10 phút)
MAX_AGE = timedelta(days=3)            # khách chờ quá lâu (vd tin cũ, không ai đăng ký nhận) thì thôi, không nhắc muộn vô nghĩa
MAX_PER_RUN = 30                       # số khách xử lý (đồng bộ + dựng ảnh) mỗi lượt cron
POLL_OVERLAP = timedelta(minutes=5)    # lấy chồng lên lần trước một chút để không sót hội thoại cập nhật sát mốc
OLD_CONVERSATION = timedelta(days=7)   # hội thoại bắt đầu trước đó hơn 7 ngày = khách cũ quay lại, không phải khách mới
SHOWN_MESSAGES = 8
SYSTEM_TYPES = {'reaction', 'pzl_chat_recommended', 'zalo_system_message', 'pzl_new_friend_request'}
BOT_NAMES = ('chatbot', 'pancake bot', 'auto')
MAX_PICTURE_BYTES = 3 * 1024 * 1024
OPEN_STATES = ('waiting', 'done', 'ignored', 'not_needed')  # còn mở: chưa có nhân viên trả lời; không tạo theo dõi mới chồng lên
CHAT_LINES = 16                                            # số tin gần nhất đưa cho AI đọc
NOTES = {'sticker': '[Sticker]', 'audio': '[Tin nhắn thoại]', 'video': '[Video]', 'link': '[Liên kết]',
         'location': '[Vị trí]', 'address': '[Địa chỉ]', 'document': '[Tệp]'}


# Thông báo của hệ thống Zalo nằm lẫn trong hội thoại, không phải khách nhắn:
#  "12/11 Sinh nhật của Pham Thi Kim Oanh Hãy gửi lời chúc tốt đẹp!", "Hôm nay là sinh nhật của ...", "Zalo chỉ hiển thị tin nhắn từ sau ..."
IGNORED_TEXT = re.compile(r'^(?:\d{1,2} \d{1,2} )?(?:hom nay la )?sinh nhat cua .+ hay gui loi chuc|^zalo chi hien thi tin nhan')


def plain_text(message):
    return fold(re.sub(r'<[^>]+>', ' ', message.content_html or ''))


def classify(message, bots=BOT_NAMES):
    """'customer' | 'staff' | 'bot' | None (không phải tin hội thoại: reaction, thông báo hệ thống/sinh nhật, tin bị xóa, bình luận,
    chat nhóm/cộng đồng Zalo...).

    Tin của page có from.id == page_id (hoặc có admin_name/admin_id khi nhân viên gửi từ Pancake)."""
    if message.type_content in SYSTEM_TYPES:
        return None
    try:
        raw = json.loads(message.raw_json or '{}')
    except ValueError:
        return None
    if not isinstance(raw, dict) or raw.get('is_removed') or raw.get('type', 'INBOX') != 'INBOX':
        return None
    sender = raw.get('from') if isinstance(raw.get('from'), dict) else {}
    if sender.get('is_group') or str(raw.get('conversation_id') or '').startswith('pzl_g_'):
        return None  # nhóm/cộng đồng Zalo, không phải khách hỏi hàng
    if sender.get('id') and sender.get('id') == raw.get('page_id') or sender.get('admin_id') or sender.get('admin_name'):
        name = (sender.get('admin_name') or '').strip().lower()
        return 'bot' if name in bots else 'staff'
    if IGNORED_TEXT.match(plain_text(message)):
        return None
    return 'customer'


def _picture(url, max_width=480):
    """Tải ảnh (https, ≤ 3 MB) và đổi sang JPEG rộng tối đa `max_width`; lỗi → None. Không ghi URL (có khóa) vào log."""
    from PIL import Image
    if not url or urlsplit(url).scheme != 'https':
        return None
    try:
        with requests.get(url, timeout=(5, 10), stream=True) as response:
            response.raise_for_status()
            blob = response.raw.read(MAX_PICTURE_BYTES + 1, decode_content=True)
        if not blob or len(blob) > MAX_PICTURE_BYTES:
            return None
        with Image.open(io.BytesIO(blob)) as image:
            image = image.convert('RGB')
            if image.width > max_width:
                image = image.resize((max_width, max(1, round(image.height * max_width / image.width))))
            out = io.BytesIO()
            image.save(out, 'JPEG', quality=85)
            return out.getvalue()
    except (requests.RequestException, OSError, ValueError):
        _logger.info('Không tải được ảnh từ %s', urlsplit(url).netloc)
        return None


def _local(moment):
    return moment.replace(tzinfo=timezone.utc).astimezone(TZ)


class PancakeRemindWait(models.Model):
    """Một khách đang chờ trả lời trên Pancake: từ tin đầu tiên của khách chưa có nhân viên trả lời."""
    _name = 'dac.pancake.remind.wait'
    _description = 'Khách Pancake đang chờ trả lời'
    _order = 'since desc, id desc'

    conversation_id = fields.Many2one('dac.pancake.conversation', string='Hội thoại', required=True, ondelete='cascade', index=True)
    customer_name = fields.Char(string='Khách', related='conversation_id.customer_name')
    page_name = fields.Char(string='Page', related='conversation_id.page_id.name')
    external_url = fields.Char(string='Link Pancake', related='conversation_id.external_url')
    since = fields.Datetime(string='Khách nhắn lúc', required=True, index=True)
    due_at = fields.Datetime(string='Nhắc tiếp lúc', index=True, help='Đã tính theo giờ làm việc.')
    state = fields.Selection([('waiting', 'Đang chờ trả lời'), ('answered', 'Đã có người trả lời'),
        ('done', 'Đã nhắc đủ số lần'), ('not_needed', 'Không cần trả lời (AI)'), ('expired', 'Quá hạn, bỏ qua'),
        ('ignored', 'Bỏ qua (khách cũ/nội bộ)')], default='waiting', required=True, index=True)
    customer_kind = fields.Selection([('new', 'Khách mới'), ('ongoing', 'Đang trao đổi'), ('old', 'Khách cũ')], string='Loại khách',
        default='new', readonly=True, help='Khách mới: lần đầu nhắn, chưa từng được trả lời (hạn 10 phút, nhắc riêng có ảnh, tối đa 4 lần). '
        'Đang trao đổi: nhân viên đã trả lời trong ngày, khách nhắn tiếp (hạn 15 phút). Khách cũ quay lại (hạn 30 phút). '
        'Hai loại sau được gom vào một tin mỗi 30 phút, trừ khi AI đánh giá Khẩn.')
    level = fields.Selection(LEVELS, string='Mức độ gấp', readonly=True, help='AI đánh giá; tự nâng khi khách chờ quá 1 giờ / 2 giờ làm việc.')
    summary = fields.Char(string='Khách cần gì (AI tóm tắt)', readonly=True)
    staff_id = fields.Many2one('dac.pancake.staff', string='Nhân viên phụ trách', readonly=True, ondelete='set null',
        help='Người đã chat với khách gần nhất trên Pancake; được tag khi nhắc (xem Nhân viên Pancake ↔ Zalo).')
    digest_pending = fields.Boolean(string='Chờ vào tin gom', readonly=True, index=True)
    ai_state = fields.Selection([('none', 'Chưa đọc'), ('needed', 'Cần trả lời'), ('not_needed', 'Không cần trả lời')],
        string='AI đánh giá', default='none', readonly=True)
    ai_reason = fields.Char(string='Lý do (AI)', readonly=True)
    ai_source = fields.Selection([('openai', 'AI tương thích OpenAI'), ('gemini', 'Gemini'), ('rules', 'Luật từ khóa')],
        string='Đánh giá bằng', readonly=True)
    ai_checked_at = fields.Datetime(string='AI đọc lúc', readonly=True)
    is_test = fields.Boolean(string='Dữ liệu thử nghiệm', readonly=True)
    remind_count = fields.Integer(string='Số lần đã nhắc', readonly=True)
    last_reminded_at = fields.Datetime(string='Nhắc gần nhất', readonly=True, index=True)
    zalo_message_ids = fields.Many2many('dac.zalo.channel.message', 'dac_pancake_wait_zalo_message_rel', 'wait_id', 'message_id',
        string='Tin Zalo đã gửi', readonly=True, copy=False)
    answered_at = fields.Datetime(string='Trả lời lúc', readonly=True)

    # ── Quét ──────────────────────────────────────────────────────────────────────

    @api.model
    def _cron_scan(self):
        """Mỗi 10 phút: tìm khách mới chờ trả lời, rồi nhắc những khách đã quá hạn. Không raise."""
        config = self.env['dac.pancake.remind.config']._get()
        if not config.active:
            return
        # Một bản quét tại một thời điểm (nhiều worker cron không nhắc trùng)
        self.env.cr.execute('SELECT pg_try_advisory_xact_lock(%s, %s)', (73464, config.id))
        if not self.env.cr.fetchone()[0]:
            return
        now = fields.Datetime.now()
        for step in (self._fetch, self._discover, self._process, self._send_digest):
            try:
                with self.env.cr.savepoint():
                    step(config, now)
            except Exception:  # noqa: BLE001 — cron không được raise; không log nội dung tin
                _logger.exception('Nhắc khách Pancake: bước %s lỗi', step.__name__)

    @api.model
    def _pages(self, config):
        """Page áp dụng: các page chọn trong cấu hình, để trống = mọi page Pancake đang dùng (đã thêm và còn hoạt động)."""
        # sudo(): luồng nền không có user; chỉ đọc danh sách page
        return (config.page_ids or self.env['dac.pancake.page'].sudo().search([])).filtered('active')

    @api.model
    def _fetch(self, config, now):
        """Lấy hội thoại vừa cập nhật (và tin của chúng) từ Pancake cho từng page có token. Lỗi một page không chặn page khác."""
        floor = max(config.activated_at or now, now - DISCOVER_WINDOW)
        for page in self._pages(config).filtered('has_token'):
            since = max(floor, page.last_polled_at - POLL_OVERLAP) if page.last_polled_at else floor
            try:
                with self.env.cr.savepoint():
                    page._poll(since, now)
            except PancakeError as exc:
                page.write({'last_error': str(exc)[:250]})
                _logger.warning('Pancake: không lấy được hội thoại của page %s: %s', page.id, exc)

    @api.model
    def _classify(self, message, bots):
        return classify(message, bots)

    def _is_new_customer(self, conversation, since, bots):
        """Khách mới: chưa có đơn hàng trong Odoo, hội thoại mới bắt đầu gần đây (không phải khách cũ quay lại) và chưa từng có
        nhân viên trả lời trước `since`."""
        if self.env['dac.new.contact']._has_orders(conversation.partner_id):
            return False
        if conversation.inserted_at_fm and conversation.inserted_at_fm < since - OLD_CONVERSATION:
            return False
        earlier = self.env['dac.pancake.message'].sudo().search([('conversation_id', '=', conversation.id), ('inserted_at', '<', since)], order='inserted_at, id', limit=300)
        return not any(classify(m, bots) == 'staff' for m in earlier)

    def _kind(self, conversation, since, bots):
        """'new' | 'ongoing' (nhân viên đã trả lời trong cùng ngày) | 'old'."""
        if self._is_new_customer(conversation, since, bots):
            return 'new'
        earlier = self.env['dac.pancake.message'].sudo().search([('conversation_id', '=', conversation.id), ('inserted_at', '<', since),
                                                                 ('inserted_at', '>=', since - timedelta(hours=24))], order='inserted_at desc, id desc', limit=200)
        last_staff = next((m.inserted_at for m in earlier if classify(m, bots) == 'staff'), None)
        if last_staff and zalo_out.local(last_staff).date() == zalo_out.local(since).date():
            return 'ongoing'
        return 'old'

    @api.model
    def _minutes(self, config, kind):
        return self.env['dac.pancake.remind.scenario']._get(KIND_SCENARIO.get(kind, 'old')).wait_minutes

    @api.model
    def _discover(self, config, now):
        """Quét tin mới: ghi nhận khách mới liên hệ (thống kê), số điện thoại khách để lại, và tạo bản ghi theo dõi cho khách
        chờ trả lời; đóng các bản ghi đã có người trả lời."""
        # sudo(): luồng nền không có user; chỉ đọc tin Pancake để theo dõi
        Message = self.env['dac.pancake.message'].sudo()
        floor = max(config.activated_at or now, now - DISCOVER_WINDOW)
        messages = Message.search([('conversation_id.page_id', 'in', self._pages(config).ids),
                                   ('conversation_id.conversation_fm_id', 'not like', 'pzl_g_%'), ('inserted_at', '>=', floor)],
                                  order='conversation_id, inserted_at, id')
        by_conversation = {}
        for message in messages:
            by_conversation.setdefault(message.conversation_id.id, []).append(message)
        bots = self._bot_names()
        Contact = self.env['dac.new.contact']
        self.env['dac.pancake.phone']._capture([m for items in by_conversation.values() for m in items if classify(m, bots) == 'customer'])
        for conversation_id, items in by_conversation.items():
            conversation = items[0].conversation_id
            first_unanswered = None
            has_customer = False
            for message in items:
                kind = classify(message, bots)
                if kind == 'staff':
                    first_unanswered = None
                elif kind == 'customer':
                    has_customer = True
                    if first_unanswered is None:
                        first_unanswered = message.inserted_at
            if has_customer:
                Contact._record_conversation(conversation, bots, config.activated_at or now)
            # Theo dõi cũ đã có nhân viên trả lời (xét trực tiếp trong dữ liệu, không phụ thuộc cửa sổ quét) thì đóng lại
            open_waits = self.search([('conversation_id', '=', conversation_id), ('state', 'in', OPEN_STATES)])
            for wait in open_waits:
                answered_at = wait._answered_since()
                if answered_at:
                    wait.write({'state': 'answered', 'answered_at': answered_at})
            if conversation.is_internal:
                continue  # nội bộ: không nhắc trả lời
            open_waits = open_waits.filtered(lambda w: w.state in OPEN_STATES)
            # AI đã thấy không cần trả lời, nhưng sau đó khách lại nhắn thêm: đọc lại từ tin mới đó
            for wait in open_waits.filtered(lambda w: w.state == 'not_needed'):
                later = [m.inserted_at for m in items if m.inserted_at > (wait.ai_checked_at or wait.since) and classify(m, bots) == 'customer']
                if later:
                    wait.write({'state': 'waiting', 'since': min(later), 'due_at': zalo_out.due_after(min(later), self._minutes(config, wait.customer_kind)),
                                'ai_state': 'none', 'ai_reason': False, 'ai_source': False, 'level': False, 'summary': False})
            if first_unanswered and not open_waits:
                name = fold(conversation.customer_name or 'Khách')
                kind = self._kind(conversation, first_unanswered, bots)
                skip = not self.env['dac.pancake.remind.scenario']._on(KIND_SCENARIO[kind])  # kịch bản của loại khách này đang tắt
                self.create({
                    'conversation_id': conversation_id, 'since': first_unanswered, 'state': 'ignored' if skip else 'waiting',
                    'customer_kind': kind,
                    'due_at': False if skip else zalo_out.due_after(first_unanswered, self._minutes(config, kind)),
                    'is_test': name == 'test' or name.startswith('test '),
                })

    @api.model
    def _bot_names(self):
        # sudo(): tham số hệ thống (tên bot, tùy chọn); cron không có quyền đọc
        param = self.env['ir.config_parameter'].sudo().get_param('pancake.bot_names')
        return tuple(n.strip().lower() for n in param.split(',') if n.strip()) if param else BOT_NAMES

    @api.model
    def _process(self, config, now):
        waits = self.search([('state', '=', 'waiting'), ('due_at', '<=', now)], order='due_at, id', limit=MAX_PER_RUN)
        for wait in waits:
            try:
                with self.env.cr.savepoint():
                    if wait.since < now - MAX_AGE:
                        wait.state = 'expired'
                        continue
                    if wait.conversation_id.is_internal:
                        wait.state = 'ignored'
                        continue
                    if not zalo_out.channels_for(self.env, config, CODES, wait.is_test)[0]:
                        continue  # chưa hội thoại Zalo nào nhận loại tin này: chưa dựng ảnh, khi có người đăng ký sẽ nhắc ngay
                    wait._remind(config, now)
            except Exception:  # noqa: BLE001
                _logger.exception('Nhắc khách Pancake: lỗi với theo dõi %s', wait.id)

    # ── Nhắc ─────────────────────────────────────────────────────────────────────

    def _answered_since(self):
        """Có nhân viên (không phải bot) trả lời sau tin đầu tiên của khách chưa? Thả cảm xúc không tính là trả lời."""
        self.ensure_one()
        later = self.env['dac.pancake.message'].sudo().search([('conversation_id', '=', self.conversation_id.id), ('inserted_at', '>', self.since)])
        bots = self._bot_names()
        replies = later.filtered(lambda m: classify(m, bots) == 'staff')
        return max(replies.mapped('inserted_at')) if replies else False

    def _live_sync(self, config):
        if not config.live_check:
            return
        try:
            # Kéo lại tin của đúng hội thoại này ngay trước khi nhắc để không nhắc nhầm khi nhân viên vừa trả lời
            with self.env.cr.savepoint():
                self.conversation_id.sudo()._fetch_messages()
        except Exception:  # noqa: BLE001 — lỗi mạng/Pancake không được chặn nhắc; dùng dữ liệu đã có
            _logger.warning('Nhắc khách Pancake: không đồng bộ lại được hội thoại %s', self.conversation_id.id)

    def _close_if_answered(self):
        answered = self._answered_since()
        if answered:
            self.write({'state': 'answered', 'answered_at': answered, 'digest_pending': False})
        return bool(answered)

    def _remind(self, config, now):
        """Đến hạn: đọc lại tin (đã có ai trả lời chưa) → AI đánh giá (lần đầu) → khách mới hoặc Khẩn: nhắc riêng có ảnh;
        đang trao đổi/khách cũ: chờ vào tin gom."""
        self.ensure_one()
        Scenario = self.env['dac.pancake.remind.scenario']
        if not Scenario._on(KIND_SCENARIO[self.customer_kind]):
            self.write({'state': 'ignored', 'due_at': False})  # kịch bản bị tắt sau khi đã bắt đầu theo dõi
            return
        conversation = self.conversation_id.sudo()
        self._live_sync(config)
        if self._close_if_answered():
            return
        waited = working_minutes_between(self.since, now)
        if self.ai_state == 'none':
            result = reply_check.assess(self.env, self._chat_lines(conversation), self.customer_kind, waited, use_ai=config.ai_check)
            vals = {'ai_state': 'needed' if result['need'] else 'not_needed', 'ai_reason': result['reason'], 'ai_source': result['source'],
                    'ai_checked_at': now, 'level': result['level'], 'summary': result['summary']}
            if not self.staff_id and self.customer_kind != 'new':
                vals['staff_id'] = self.env['dac.pancake.staff']._of_conversation(conversation, before=self.since).id
            self.write(vals)
            if not result['need']:
                self.write({'state': 'not_needed', 'due_at': False})
                return
        level = escalate(self.level, waited)
        if self.customer_kind != 'new' and not self.remind_count and not (self.level == 'urgent' and Scenario._on('urgent')):
            # Gom vào tin mỗi 30 phút. Chỉ khi chính AI đánh giá Khẩn mới nhắc riêng có ảnh; mức tự nâng theo thời gian chờ
            # chỉ đổi icon (không thì khách tồn từ hôm qua đều thành "Khẩn" và mỗi người một ảnh)
            if Scenario._on('digest'):
                self.write({'digest_pending': True, 'due_at': False})
            else:
                self.write({'state': 'ignored', 'due_at': False})
            return
        repeat = Scenario._get('new_repeat')
        limit = repeat.max_count if repeat.enabled else 1
        first = Scenario._get('new_first' if self.customer_kind == 'new' else 'urgent')
        values = {'icon': ICONS.get(level, '🔔'), 'muc_do': HEADLINE.get(level, '🔔'), 'loai_khach': KIND_LABELS.get(self.customer_kind, 'Khách'),
                  'cho': waited_text(waited), 'ten_khach': conversation.customer_name or 'Khách', 'tom_tat': self.summary or '',
                  'link': conversation._short_url(), 'lan': self.remind_count + 1, 'toi_da': max(limit, self.remind_count + 1)}
        originals = self.zalo_message_ids.filtered(lambda m: m.image)
        number = self.remind_count + 1
        key = 'pancake-wait:%s:%s' % (self.id, number)
        Message = self.env['dac.zalo.channel.message']
        if originals.filtered(lambda m: m.state == 'sent'):
            # Nhắc lại: tag tên + trả lời vào ảnh nhắc lần đầu (không gửi lại ảnh)
            sent = Message
            types = zalo_out.event_types(self.env, CODES)
            for original in originals.filtered(lambda m: m.state == 'sent' and m.channel_id.active):
                channel = original.channel_id
                out = Builder().tags(zalo_out.targets_for(channel, self.staff_id, types))
                out.add((' ' if out.parts else '') + repeat._render(values))
                text, mentions = out.result()
                sent |= channel._queue(text, '%s:%s' % (key, channel.id), original.event_type_id, mentions=mentions, reply_to=original)
            if sent:
                self.env['dac.zalo.channel']._trigger_send()
                repeat._log(sent, conversation, wait=self, level=level, is_test=self.is_test)
        elif originals.filtered(lambda m: m.state == 'queued'):
            return  # ảnh nhắc lần đầu chưa gửi đi được (bridge đang lỗi?): chờ, lượt sau xét lại
        else:
            def build(channel, types):
                out = Builder().tags(zalo_out.targets_for(channel, self.staff_id, types))
                out.add((' ' if out.parts else '') + first._render(values))
                return out.result()
            sent = zalo_out.send(self.env, config, CODES, key, build, is_test=self.is_test, image=self._render_image(conversation, now))
            first._log(sent, conversation, wait=self, level=level, is_test=self.is_test)
        if not sent:
            return
        vals = {'remind_count': number, 'last_reminded_at': now, 'zalo_message_ids': [(4, m.id) for m in sent]}
        if self.customer_kind == 'new' and number < limit:
            vals['due_at'] = add_working_minutes(now, repeat.repeat_minutes)
        else:
            vals.update(state='done', due_at=False)  # đã nhắc đủ (khách mới 4 lần; khách cũ/đang trao đổi Khẩn 1 lần)
        self.write(vals)

    @api.model
    def _send_digest(self, config, now):
        """Mỗi 30 phút: một tin gom các khách cũ/đang trao đổi cần trả lời (không Khẩn), nhóm theo nhân viên phụ trách."""
        pending = self.search([('state', '=', 'waiting'), ('digest_pending', '=', True)], order='since, id')
        digest = self.env['dac.pancake.remind.scenario']._get('digest')
        if not pending or config.last_digest_at and now < config.last_digest_at + timedelta(minutes=digest.repeat_minutes):
            return
        if not digest.enabled:
            pending.write({'state': 'ignored', 'digest_pending': False})
            return
        fresh = self.browse()
        for wait in pending[:DIGEST_MAX]:
            wait._live_sync(config)
            if wait.conversation_id.is_internal:
                wait.write({'state': 'ignored', 'digest_pending': False})
            elif not wait._close_if_answered():
                fresh |= wait
        if not fresh:
            return
        config.last_digest_at = now
        for is_test in (False, True):
            group = fresh.filtered(lambda w: w.is_test == is_test)
            if not group:
                continue
            levels = {w.id: escalate(w.level, working_minutes_between(w.since, now)) for w in group}
            group = group.sorted(lambda w: (-RANK.get(levels[w.id], 1), w.since))

            def line(wait):
                conversation = wait.conversation_id.sudo()
                return digest._render({
                    'icon': ICONS.get(levels[wait.id], '🔔'), 'ten_khach': conversation.customer_name or 'Khách',
                    'tom_tat': wait.summary or KIND_LABELS.get(wait.customer_kind, ''), 'loai_khach': KIND_LABELS.get(wait.customer_kind, ''),
                    'nhan_vien': ' (%s)' % wait.staff_id.name if wait.staff_id and not wait.staff_id.zalo_uid else '',
                    'cho': waited_text(working_minutes_between(wait.since, now)),
                    'link': conversation._short_url() or conversation.page_id.name or ''}, line=True)

            def build(channel, types, group=group):
                out = Builder().add(digest._render({'so_khach': len(group)}))
                mapped, unknown = {}, []
                for wait in group:
                    targets = zalo_out.staff_targets(channel, wait.staff_id)
                    if targets:
                        mapped.setdefault(targets[0], []).append(wait)
                    else:
                        unknown.append(wait)
                for target, waits in mapped.items():
                    out.add('\n').tags([target])
                    for wait in waits:
                        out.add('\n' + line(wait))
                if unknown:
                    fallback = zalo_out.fallback_targets(channel, types)
                    out.add('\n')
                    if mapped:
                        out.add('Chưa rõ người phụ trách' + (': ' if fallback else ''))
                    out.tags(fallback)
                    for wait in unknown:
                        out.add('\n' + line(wait))
                return out.result()
            sent = zalo_out.send(self.env, config, CODES, 'pancake-digest:%s:%d' % (fields.Datetime.to_string(now), int(is_test)), build, is_test=is_test)
            if not sent:
                continue  # chưa hội thoại nào nhận: giữ lại, có người đăng ký thì gửi
            digest._log(sent, is_test=is_test, count=len(group), level=levels[group[0].id])
            group.write({'state': 'done', 'digest_pending': False, 'remind_count': 1, 'last_reminded_at': now,
                         'zalo_message_ids': [(4, m.id) for m in sent]})

    def _chat_lines(self, conversation):
        """Các tin gần nhất của khách/nhân viên (cũ → mới) dạng (loại, chữ, ghi chú ảnh/file) để AI đọc."""
        bots = self._bot_names()
        recent = self.env['dac.pancake.message'].sudo().search([('conversation_id', '=', conversation.id)],
                                                               order='inserted_at desc, id desc', limit=CHAT_LINES * 3)
        lines = []
        for message in recent:
            kind = classify(message, bots)
            if kind in ('customer', 'staff'):
                note = NOTES.get(message.type_content, '[Hình ảnh]' if message.type_content == 'photo' else '')
                lines.append((kind, re.sub(r'<[^>]+>', ' ', message.content_html or '').strip(), note))
            if len(lines) >= CHAT_LINES:
                break
        return lines[::-1]

    # ── Dựng ảnh ─────────────────────────────────────────────────────────────────

    def _render_image(self, conversation, now):
        """Ảnh "chụp màn hình" hội thoại (JPEG bytes); không dựng được thì trả False — vẫn gửi tin chữ có tag và link."""
        binary = shutil.which('wkhtmltoimage')
        if not binary:
            _logger.warning('Nhắc khách Pancake: máy chủ không có wkhtmltoimage, gửi tin chữ không kèm ảnh')
            return False
        try:
            return chat_image.render_image(self._chat_html(conversation, now), binary)
        except Exception:  # noqa: BLE001 — lỗi dựng ảnh không được chặn lời nhắc
            _logger.exception('Nhắc khách Pancake: không dựng được ảnh hội thoại %s', conversation.id)
            return False

    def _chat_html(self, conversation, now):
        recent = self.env['dac.pancake.message'].sudo().search([('conversation_id', '=', conversation.id)], order='inserted_at desc, id desc', limit=SHOWN_MESSAGES * 3)
        bots = self._bot_names()
        shown = [(m, kind) for m in recent for kind in [classify(m, bots)] if kind][:SHOWN_MESSAGES]  # mới nhất trước
        today = _local(now).date()
        rows, pictures = [], 0
        for message, kind in shown:  # tải ảnh từ tin mới nhất, tối đa 3 ảnh
            raw = json.loads(message.raw_json or '{}')
            attachments = [a for a in raw.get('attachments') or [] if isinstance(a, dict)]
            note, image = '', None
            kinds = {a.get('type') for a in attachments}
            if 'photo' in kinds or 'video' in kinds:
                source = next(a for a in attachments if a.get('type') in ('photo', 'video'))
                if pictures < 3:
                    image = _picture(source.get('url') or source.get('origin_url'))
                    pictures += image is not None
                if image is None or 'video' in kinds:
                    note = NOTES.get('video') if 'video' in kinds else '[Hình ảnh]'
            elif kinds - {'styled_message', None}:
                other = next(a for a in attachments if a.get('type') not in ('styled_message', None))
                note = NOTES.get(other.get('type'), '[Tệp đính kèm]')
                if other.get('type') == 'document' and other.get('name'):
                    note = '[Tệp] %s' % str(other['name'])[:60]
                elif other.get('type') == 'location' and other.get('name'):
                    note = '[Vị trí] %s' % str(other['name'])[:60]
            rows.append({'side': 'in' if kind == 'customer' else 'out', 'text': message.content_html or '', 'image': image, 'note': note})
        rows.reverse()
        since = _local(self.since)
        waiting = 'Khách nhắn lúc %s%s, chưa có ai trả lời' % (
            since.strftime('%H:%M'), '' if since.date() == today else since.strftime(' ngày %d/%m'))
        return chat_image.build_html(conversation.customer_name or 'Khách', self._avatar(conversation, shown), rows,
                                     platform=conversation.platform or '', page_name=conversation.page_id.name or '', waiting_text=waiting)

    def _avatar(self, conversation, shown):
        """Ảnh đại diện khách: ảnh đã tải về Odoo; chưa có thì tải từ Pancake; không được thì trả None (vẽ chữ cái đầu)."""
        url = conversation.avatar_url
        if not url:
            for message, kind in shown:
                if kind == 'customer':
                    url = (json.loads(message.raw_json or '{}').get('from') or {}).get('avatar_url')
                    break
        if not url or url.rstrip('/').endswith('/default'):
            return None
        return _picture(url, max_width=120)
