"""Kết nối Pancake gọn, độc lập với CRM_DAC: mỗi page một Page Access Token do quản trị viên tự nhập.

Chỉ đọc (không gửi tin, không đổi gì trên Pancake): lấy hội thoại vừa cập nhật của page đã chọn và vài chục tin gần nhất của
hội thoại đó, lưu bản gọn để phục vụ nhắc khách chưa được trả lời và thống kê khách mới.
"""
import base64
import json
import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone

import requests

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.addons.dac_erp.models.phone_utils import normalize_phone_vn

_logger = logging.getLogger(__name__)

API_V1 = 'https://pages.fm/api/public_api/v1'
API_V2 = 'https://pages.fm/api/public_api/v2'
AVATAR_URL = 'https://pages.fm/api/v1/pages/%s/avatar/%s'  # → chuyển hướng tới ảnh trên content.pancake.vn; khách không có ảnh: 404
HISTORY_SINCE = 1262304000  # 01/01/2010: lấy toàn bộ lịch sử hội thoại của page
MESSAGE_BATCH = 30          # Pancake trả tối đa 30 tin mỗi lần, `current_count` = số tin (tính từ mới nhất) đã lấy
HISTORY_BUDGET = 240        # giây mỗi lần chạy cron lấy lịch sử (cron chạy 5 phút một lần)
HISTORY_PAUSE = 0.4         # nghỉ giữa hai lần gọi khi lấy tin cũ: Pancake giới hạn tần suất ("Too many requests")
_TRANSIENT = ('HTTP 5', 'HTTP 429', 'Too many', 'Connection', 'timed out')
_ATTACHMENT_KEYS = ('type', 'url', 'origin_url', 'thumb_url', 'name', 'mime_type', 'title', 'description', 'size')
_SENDER_KEYS = ('id', 'name', 'admin_id', 'admin_name', 'is_group', 'avatar_url', 'ai_generated')
TIMEOUT = (10, 30)
MAX_LIST_PAGES = 5          # mỗi lần quét: tối đa 5 trang danh sách hội thoại (~ vài trăm hội thoại)
MAX_FETCH_PER_RUN = 100     # mỗi page mỗi lần quét: tối đa 100 hội thoại được tải tin
_SECRET = re.compile(r'(?i)((?:page_)?access_token|api_key)(["\']?\s*[=:]\s*["\']?)[^&\s,;"\']+')


def redact(value):
    return _SECRET.sub(r'\1\2***', str(value or ''))[:300]


def parse_time(value):
    """Chuỗi thời gian Pancake (ISO, thường không có múi giờ = UTC) → datetime UTC không tz (kiểu Odoo)."""
    if not value or not isinstance(value, str):
        return False
    try:
        moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return False
    if moment.tzinfo:
        moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
    return moment.replace(microsecond=0)


def platform_of(conv):
    sender = str((conv.get('from') or {}).get('id') or '').lower()
    page = str(conv.get('page_id') or '').lower()
    if sender.startswith('pzl_') or page.startswith('pzl_'):
        return 'Zalo'
    if sender.startswith('igo_') or page.startswith('igo_'):
        return 'Instagram'
    if page.isdigit() or page.startswith('fb_'):
        return 'Facebook'
    return ''


class PancakeError(Exception):
    pass


def _pause():
    if not getattr(threading.current_thread(), 'testing', False):
        time.sleep(HISTORY_PAUSE)


def _slim_attachment(item, depth=0):
    if not isinstance(item, dict):
        return None
    out = {k: item[k] for k in _ATTACHMENT_KEYS if item.get(k) not in (None, '', 'https:')}
    inner = [a for a in (_slim_attachment(i, depth + 1) for i in item.get('attachments') or [] if depth < 2) if a]
    if inner:
        out['attachments'] = inner
    return out or None


def slim(raw):
    """Bản gọn của một tin Pancake để lưu: chữ, người gửi, link ảnh/file (KHÔNG tải ảnh/file về), số điện thoại khách để lại."""
    sender = raw.get('from') if isinstance(raw.get('from'), dict) else {}
    out = {k: raw[k] for k in ('id', 'inserted_at', 'message', 'type', 'is_removed', 'conversation_id', 'page_id') if raw.get(k) not in (None, '')}
    out['from'] = {k: sender[k] for k in _SENDER_KEYS if sender.get(k) not in (None, '')}
    attachments = [a for a in (_slim_attachment(a) for a in raw.get('attachments') or []) if a]
    if attachments:
        out['attachments'] = attachments
    if isinstance(raw.get('phone_info'), list) and raw['phone_info']:
        out['phone_info'] = [{k: p.get(k) for k in ('phone_number', 'captured', 'status') if isinstance(p, dict) and p.get(k) is not None}
                             for p in raw['phone_info'][:5] if isinstance(p, dict)]
    return out


def attachment_lines(raw):
    """Mỗi ảnh/file một dòng "loại: link (tên)" — để người hoặc AI Agent mở được khi cần."""
    lines = []

    def walk(items):
        for item in items or []:
            if not isinstance(item, dict):
                continue
            url = item.get('url') or item.get('origin_url')
            if url and str(url).startswith('http'):
                lines.append('%s: %s%s' % (item.get('type') or 'file', url, ' (%s)' % item['name'] if item.get('name') else ''))
            walk(item.get('attachments'))
    walk(raw.get('attachments'))
    return '\n'.join(lines)


def page_ref(value):
    """Page ID hoặc link Pancake (https://pancake.vn/pzl_849...?c_id=...) → mã page (phần ngay sau tên miền)."""
    value = re.sub(r'^[a-z]+://', '', (value or '').strip(), flags=re.I)
    host, slash, rest = value.partition('/')
    if slash and '.' in host:  # có tên miền (pancake.vn/...): bỏ tên miền
        value = rest
    return next((part.strip() for part in re.split(r'[/?#]', value) if part.strip()), '')


def token_page_id(token):
    """Mã page mà Pancake cấp token này (Page Access Token là JWT có trường `id`); không đọc được thì ''."""
    try:
        payload = (token or '').strip().split('.')[1]
        data = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
    except (IndexError, ValueError, TypeError):
        return ''
    page = data.get('id') if isinstance(data, dict) else None
    return str(page).strip() if isinstance(page, (str, int)) and str(page).strip() else ''


class PancakePage(models.Model):
    _name = 'dac.pancake.page'
    _description = 'Page Pancake (token theo page)'
    _order = 'sequence, name, id'

    name = fields.Char(string='Tên page', required=True)
    page_fm_id = fields.Char(string='Page ID', index=True,
        help='Dán Page ID hoặc link Pancake của page (VD https://pancake.vn/pzl_849...). Có token thì Odoo tự lấy đúng '
             'mã page ghi trong token để gọi Pancake; mã trong link được giữ để mở hội thoại.')
    page_slug = fields.Char(string='Mã trên link Pancake', readonly=True, copy=False,
        help='Phần sau pancake.vn/ khi mở page trên trình duyệt (page Zalo cá nhân: pzl_ + số điện thoại), dùng để dựng link '
             'mở hội thoại. Trống = dùng Page ID.')
    page_access_token = fields.Char(string='Page Access Token', groups='base.group_system', copy=False,
        help='Pancake → chọn page → Cài đặt → Công cụ → Page Access Token.')
    has_token = fields.Boolean(string='Có token', compute='_compute_has_token')
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    token_status = fields.Selection([('unchecked', 'Chưa kiểm tra'), ('valid', 'Hợp lệ'), ('invalid', 'Không hợp lệ'),
        ('error', 'Lỗi kết nối')], string='Token', default='unchecked', readonly=True, copy=False)
    token_message = fields.Char(string='Kết quả kiểm tra', readonly=True, copy=False)
    token_checked_at = fields.Datetime(string='Kiểm tra lúc', readonly=True, copy=False)
    last_polled_at = fields.Datetime(string='Lấy tin gần nhất', readonly=True, copy=False)
    last_error = fields.Char(string='Lỗi gần nhất', readonly=True, copy=False)
    conversation_count = fields.Integer(string='Hội thoại', compute='_compute_conversation_count')
    history_enabled = fields.Boolean(string='Lấy toàn bộ tin cũ', default=True,
        help='Chạy nền: lấy mọi hội thoại cũ của page và toàn bộ tin của từng hội thoại (ảnh/file chỉ lưu link) để thống kê '
             'đúng lần nhắn đầu tiên của khách.')
    history_until = fields.Datetime(string='Lấy lịch sử đến mốc', readonly=True, copy=False)
    history_cursor = fields.Char(readonly=True, copy=False)
    history_listed = fields.Boolean(string='Đã có đủ danh sách hội thoại cũ', readonly=True, copy=False)
    history_progress = fields.Char(string='Tiến độ lấy tin cũ', compute='_compute_history_progress')

    _sql_constraints = [('page_fm_id_unique', 'unique(page_fm_id)', 'Page này đã được thêm.')]

    def _compute_history_progress(self):
        Conversation = self.env['dac.pancake.conversation'].sudo()
        for page in self:
            total = Conversation.search_count([('page_id', '=', page.id)])
            done = Conversation.search_count([('page_id', '=', page.id), ('history_state', '=', 'done')])
            if not page.history_enabled:
                page.history_progress = 'Tắt'
            elif page.history_listed and done == total:
                page.history_progress = 'Xong: %s hội thoại' % total
            else:
                page.history_progress = 'Đã lấy đủ tin %s/%s hội thoại%s' % (
                    done, total, '' if page.history_listed else ' (đang tìm thêm hội thoại cũ)')

    def _compute_has_token(self):
        # sudo(): chỉ cho biết có/không có token, không lộ giá trị cho người không phải quản trị
        tokens = {page.id: bool((page.page_access_token or '').strip()) for page in self.sudo()}
        for page in self:
            page.has_token = tokens.get(page.id, False)

    def _compute_conversation_count(self):
        counts = dict(self.env['dac.pancake.conversation']._read_group(
            [('page_id', 'in', self.ids)], ['page_id'], ['__count']))
        for page in self:
            page.conversation_count = counts.get(page, 0)

    @staticmethod
    def _resolve_ids(entered, token):
        """(Page ID gọi API, mã trên link) từ ô Page ID/link người dùng dán và token."""
        entered = page_ref(entered)
        claimed = token_page_id(token)
        if claimed and entered and claimed != entered:
            return claimed, entered
        return claimed or entered, False

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if 'page_fm_id' in vals or vals.get('page_access_token'):
                page_id, slug = self._resolve_ids(vals.get('page_fm_id'), vals.get('page_access_token'))
                vals.update(page_fm_id=page_id, page_slug=slug)
            if not vals.get('page_fm_id'):
                raise UserError('Nhập Page ID / link Pancake của page, hoặc dán Page Access Token.')
        return super().create(vals_list)

    def write(self, vals):
        vals = dict(vals)
        if 'page_access_token' in vals:
            vals.update(token_status='unchecked', token_message=False)
        if 'page_fm_id' not in vals and 'page_access_token' not in vals:
            return super().write(vals)
        result = True
        for page in self:
            # sudo(): đọc token đang lưu (chỉ quản trị mới sửa được trường token) để lấy mã page ghi trong token
            token = vals['page_access_token'] if 'page_access_token' in vals else page.sudo().page_access_token
            entered = vals['page_fm_id'] if 'page_fm_id' in vals else (page.page_slug or page.page_fm_id)
            page_id, slug = self._resolve_ids(entered, token)
            if not page_id:
                raise UserError('Nhập Page ID / link Pancake của page, hoặc dán Page Access Token.')
            result &= super(PancakePage, page).write(dict(vals, page_fm_id=page_id, page_slug=slug))
        return result

    # ── Gọi API ───────────────────────────────────────────────────────────────

    def _token(self):
        self.ensure_one()
        # sudo(): token chỉ quản trị đọc được, nhưng cron (không user) cần dùng để gọi Pancake
        token = (self.sudo().page_access_token or '').strip()
        if not token:
            raise PancakeError('Page %s chưa có Page Access Token' % self.name)
        return token

    def _get(self, url, params):
        """GET Pancake (chỉ đọc). Lỗi HTTP/mạng/token → PancakeError (không chứa token)."""
        self.ensure_one()
        params = dict(params, page_access_token=self._token(), page_id=self.page_fm_id)
        try:
            response = requests.get(url, params=params, headers={'Accept': 'application/json'}, timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise PancakeError(redact(exc)) from None
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code != 200 or not isinstance(data, dict) or data.get('success') is False:
            message = data.get('message') if isinstance(data, dict) else ''
            raise PancakeError(redact(message or 'HTTP %s' % response.status_code))
        return data

    def _list_conversations(self, since, until, max_pages=MAX_LIST_PAGES):
        """Hội thoại cập nhật trong [since, until] (mới nhất trước)."""
        rows, last_id = [], None
        for _ in range(max_pages):
            params = {'since': int(since.replace(tzinfo=timezone.utc).timestamp()),
                      'until': int(until.replace(tzinfo=timezone.utc).timestamp()), 'order_by': 'updated_at'}
            if last_id:
                params['last_conversation_id'] = last_id
            batch = self._get('%s/pages/%s/conversations' % (API_V2, self.page_fm_id), params).get('conversations') or []
            batch = [c for c in batch if isinstance(c, dict) and c.get('id')]
            if not batch:
                break
            rows += batch
            if batch[-1]['id'] == last_id:
                break
            last_id = batch[-1]['id']
        return rows

    def action_check_token(self):
        """Kiểm tra token: đọc thử danh sách hội thoại 1 ngày gần nhất (không gửi gì)."""
        if not self.env.user.has_group('base.group_system'):
            raise UserError('Chỉ quản trị viên được kiểm tra token page.')
        now = fields.Datetime.now()
        lines = []
        for page in self:
            try:
                count = len(page._list_conversations(now - timedelta(days=1), now, max_pages=1))
                status, message = 'valid', 'Token hợp lệ, đọc được %s hội thoại trong 24 giờ qua.' % count
            except PancakeError as exc:
                text = str(exc)
                status = 'error' if 'HTTP 5' in text or 'Connection' in text or 'timed out' in text else 'invalid'
                message = 'Không dùng được token: %s' % text
            page.write({'token_status': status, 'token_message': message[:250], 'token_checked_at': now})
            lines.append('%s: %s' % (page.name, message))
        ok = all(page.token_status == 'valid' for page in self)
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': 'Kiểm tra token page', 'message': '\n'.join(lines), 'sticky': not ok,
            'type': 'success' if ok else 'warning', 'next': {'type': 'ir.actions.client', 'tag': 'reload'}}}

    def action_open_conversations(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': 'Hội thoại %s' % self.name, 'res_model': 'dac.pancake.conversation',
                'view_mode': 'list,form', 'domain': [('page_id', '=', self.id)]}

    # ── Lấy dữ liệu ──────────────────────────────────────────────────────────

    def _poll(self, since, until, max_fetch=MAX_FETCH_PER_RUN):
        """Lưu các hội thoại chat riêng (không phải nhóm, không phải bình luận) cập nhật trong khoảng; tải tin cho hội thoại có
        thay đổi. Trả về recordset hội thoại đã tải tin. Lỗi Pancake → PancakeError (ghi vào last_error)."""
        self.ensure_one()
        Conversation = self.env['dac.pancake.conversation']
        try:
            rows = self._list_conversations(since, until)
        except PancakeError as exc:
            self.write({'last_error': str(exc)[:250]})
            raise
        changed = Conversation.browse()
        for row in rows:
            if row.get('type', 'INBOX') != 'INBOX' or str(row['id']).startswith('pzl_g_'):
                continue
            conversation, is_changed = Conversation._upsert(self, row)
            if is_changed:
                changed |= conversation
        fetched = Conversation.browse()
        for conversation in changed.sorted('updated_at_fm')[-max_fetch:]:
            try:
                with self.env.cr.savepoint():
                    conversation._fetch_messages()
                    fetched |= conversation
            except PancakeError as exc:
                _logger.warning('Pancake: không tải được tin hội thoại %s: %s', conversation.id, exc)
        self.write({'last_polled_at': until, 'last_error': False})
        return fetched

    # ── Lấy toàn bộ tin cũ (chạy nền, dừng/tiếp tục được) ─────────────────────

    def _commit(self):
        # Cron chạy lâu: lưu tiến độ sau từng phần để lần sau chạy tiếp (trong test không được commit)
        if not getattr(threading.current_thread(), 'testing', False) and not self.env.registry.in_test_mode():
            self.env.cr.commit()

    @api.model
    def _cron_crawl_history(self, budget=HISTORY_BUDGET):
        """Mỗi 5 phút: lấy tiếp danh sách hội thoại cũ và toàn bộ tin của hội thoại chưa lấy đủ, trong tối đa `budget` giây. Không raise."""
        self.env.cr.execute('SELECT pg_try_advisory_xact_lock(%s, %s)', (73464, 2))
        if not self.env.cr.fetchone()[0]:
            return
        deadline = time.monotonic() + budget
        # sudo(): cron không có user; chỉ đọc từ Pancake và ghi vào bảng của addon này
        pages = self.sudo().search([('history_enabled', '=', True)]).filtered('has_token')
        # Danh sách hội thoại của mọi page trước (nhanh), rồi mới lấy tin: page nhỏ không phải chờ page lớn lấy xong
        for step in ('_crawl_list', '_crawl_messages'):
            for page in pages:
                if time.monotonic() >= deadline:
                    break
                page._crawl_step(step, deadline)

    def _crawl_step(self, step, deadline):
        """Chạy một bước lấy tin cũ (`_crawl_list` / `_crawl_messages`) cho page này; lỗi chỉ ghi lại, không raise."""
        self.ensure_one()
        try:
            getattr(self, step)(deadline)
        except PancakeError as exc:
            self.write({'last_error': str(exc)[:250]})
            _logger.warning('Pancake: lấy tin cũ của page %s lỗi: %s', self.id, exc)
        except Exception:  # noqa: BLE001 — cron không được raise
            self.env.cr.rollback()
            _logger.exception('Pancake: lấy tin cũ của page %s lỗi', self.id)
        self._commit()

    def _crawl_list(self, deadline):
        """Đi lùi qua mọi hội thoại của page (mới → cũ), lưu hội thoại chat riêng; mỗi hội thoại mới chờ lấy đủ tin."""
        self.ensure_one()
        Conversation = self.env['dac.pancake.conversation']
        if self.history_listed:
            return
        if not self.history_until:
            self.history_until = fields.Datetime.now()
        until = int(self.history_until.replace(tzinfo=timezone.utc).timestamp())
        while time.monotonic() < deadline:
            params = {'since': HISTORY_SINCE, 'until': until, 'order_by': 'updated_at'}
            if self.history_cursor:
                params['last_conversation_id'] = self.history_cursor
            batch = [c for c in self._get('%s/pages/%s/conversations' % (API_V2, self.page_fm_id), params).get('conversations') or []
                     if isinstance(c, dict) and c.get('id')]
            if not batch or str(batch[-1]['id']) == self.history_cursor:
                self.history_listed = True
                break
            for row in batch:
                if row.get('type', 'INBOX') == 'INBOX' and not str(row['id']).startswith('pzl_g_'):
                    Conversation._upsert(self, row)
            self.history_cursor = str(batch[-1]['id'])
            self._commit()
            _pause()

    def _crawl_messages(self, deadline):
        """Lấy đủ tin (từ mới tới cũ nhất) cho các hội thoại chưa lấy đủ; xong hội thoại nào thì cập nhật thống kê hội thoại đó."""
        self.ensure_one()
        pending = self.env['dac.pancake.conversation'].search([('page_id', '=', self.id), ('history_state', '=', 'pending')],
                                                              order='updated_at_fm desc, id desc', limit=300)
        for conversation in pending:
            if time.monotonic() >= deadline:
                break
            try:
                if conversation._crawl_all_messages(deadline):
                    self.env['dac.new.contact']._after_history(conversation)
            except PancakeError as exc:
                if any(mark in str(exc) for mark in _TRANSIENT):
                    raise  # Pancake quá tải/mạng lỗi: dừng lần này (hội thoại vẫn chờ), lần sau lấy tiếp
                conversation.history_state = 'error'
                _logger.warning('Pancake: không lấy được tin cũ của hội thoại %s: %s', conversation.id, exc)
            self._commit()

    def action_restart_history(self):
        """Lấy lại từ đầu danh sách hội thoại cũ và tin của mọi hội thoại (tin đã có không bị nhân đôi)."""
        self.write({'history_enabled': True, 'history_listed': False, 'history_cursor': False, 'history_until': False})
        self.env['dac.pancake.conversation'].search([('page_id', 'in', self.ids)]).write({'history_state': 'pending', 'history_offset': 0})
        return True


class PancakeConversation(models.Model):
    _name = 'dac.pancake.conversation'
    _description = 'Hội thoại Pancake (bản gọn)'
    _order = 'updated_at_fm desc, id desc'
    _rec_name = 'customer_name'

    page_id = fields.Many2one('dac.pancake.page', string='Page', required=True, ondelete='cascade', index=True)
    conversation_fm_id = fields.Char(string='ID hội thoại', required=True, index=True)
    customer_fm_id = fields.Char(string='ID khách')
    customer_name = fields.Char(string='Khách')
    avatar_url = fields.Char(string='Ảnh đại diện')
    platform = fields.Char(string='Kênh')
    snippet = fields.Char(string='Tin gần nhất')
    inserted_at_fm = fields.Datetime(string='Bắt đầu lúc', index=True)
    updated_at_fm = fields.Datetime(string='Cập nhật lúc', index=True)
    messages_updated_at = fields.Datetime(string='Đã tải tin đến', readonly=True,
        help='Mốc updated_at của hội thoại lúc tải tin gần nhất; khác mốc này thì cần tải lại.')
    external_url = fields.Char(string='Link Pancake', compute='_compute_external_url', store=True)
    partner_id = fields.Many2one('res.partner', string='Liên hệ', ondelete='set null', index=True)
    message_ids = fields.One2many('dac.pancake.message', 'conversation_id', string='Tin nhắn')
    is_internal = fields.Boolean(string='Nội bộ', index=True, copy=False,
        help='Nhân viên, người nhà, đối tác…: không tính vào thống kê khách liên hệ và không nhắc trả lời.')
    message_count_fm = fields.Integer(string='Số tin trên Pancake', readonly=True)
    history_state = fields.Selection([('pending', 'Chưa lấy đủ tin cũ'), ('done', 'Đã lấy đủ tin'), ('error', 'Lỗi lấy tin cũ')],
        string='Tin cũ', default='pending', required=True, index=True, readonly=True, copy=False)
    history_offset = fields.Integer(readonly=True, copy=False, help='Số tin (tính từ mới nhất) đã lấy khi lấy tin cũ.')

    _sql_constraints = [('conversation_unique', 'unique(page_id, conversation_fm_id)', 'Hội thoại đã có.')]

    def _crawl_all_messages(self, deadline):
        """Lấy tiếp tin cũ của hội thoại (30 tin/lần, mới → cũ) cho tới hết; trả True nếu đã lấy đủ."""
        self.ensure_one()
        Message = self.env['dac.pancake.message']
        url = '%s/pages/%s/conversations/%s/messages' % (API_V1, self.page_id.page_fm_id, self.conversation_fm_id)
        while time.monotonic() < deadline:
            data = self.page_id._get(url, {'conversation_id': self.conversation_fm_id, 'customer_id': self.customer_fm_id or '',
                                           'current_count': self.history_offset})
            rows = [r for r in data.get('messages') or [] if isinstance(r, dict) and r.get('id')]
            Message._store(self, rows)
            _pause()
            if len(rows) < MESSAGE_BATCH:
                self.write({'history_state': 'done', 'history_offset': self.history_offset + len(rows)})
                return True
            self.history_offset += len(rows)
            self.page_id._commit()
        return False

    def action_mark_internal(self):
        self.write({'is_internal': True})
        # Khách nội bộ không cần nhắc trả lời nữa
        self.env['dac.pancake.remind.wait'].sudo().search([('conversation_id', 'in', self.ids), ('state', '=', 'waiting')]).write({'state': 'ignored'})
        return True

    def action_unmark_internal(self):
        self.write({'is_internal': False})
        return True

    @api.depends('page_id.page_fm_id', 'page_id.page_slug', 'conversation_fm_id')
    def _compute_external_url(self):
        for conversation in self:
            page = conversation.page_id.page_slug or conversation.page_id.page_fm_id
            conversation.external_url = 'https://pancake.vn/%s?c_id=%s' % (page, conversation.conversation_fm_id) if page and conversation.conversation_fm_id else False

    @api.model
    def _upsert(self, page, row):
        """Tạo/cập nhật hội thoại từ dữ liệu danh sách; trả (hội thoại, có tin mới cần tải)."""
        sender = row.get('from') if isinstance(row.get('from'), dict) else {}
        updated = parse_time(row.get('updated_at'))
        customers = [c for c in row.get('customers') or [] if isinstance(c, dict)] if isinstance(row.get('customers'), list) else []
        page_customer = row.get('page_customer') if isinstance(row.get('page_customer'), dict) else {}
        psid = str(page_customer.get('psid') or sender.get('id') or '').strip()
        vals = {
            'customer_fm_id': row.get('customer_id') or (customers[0].get('id') if customers else False),
            'customer_name': sender.get('name') or 'Khách',
            # Facebook/Instagram: Pancake không trả link ảnh, nhưng có ảnh đại diện công khai theo PSID (không cần token)
            'avatar_url': sender.get('picture') or sender.get('avatar') or sender.get('avatar_url')
                          or (AVATAR_URL % (page.page_fm_id, psid) if re.fullmatch(r'[\w.-]{1,80}', psid) else False),
            'platform': platform_of(row),
            'snippet': (row.get('snippet') or '')[:250],
            'inserted_at_fm': parse_time(row.get('inserted_at')),
            'updated_at_fm': updated,
            'message_count_fm': row.get('message_count') if isinstance(row.get('message_count'), int) else False,
        }
        existing = self.search([('page_id', '=', page.id), ('conversation_fm_id', '=', str(row['id']))], limit=1)
        if existing:
            existing.write({k: v for k, v in vals.items() if v or k in ('snippet',)})
            return existing, not existing.messages_updated_at or (updated and updated > existing.messages_updated_at)
        conversation = self.create(dict(vals, page_id=page.id, conversation_fm_id=str(row['id'])))
        return conversation, True

    def _fetch_messages(self):
        """Tải ~vài chục tin gần nhất của hội thoại (Pancake trả lô mới nhất), lưu tin chưa có."""
        self.ensure_one()
        data = self.page_id._get('%s/pages/%s/conversations/%s/messages' % (API_V1, self.page_id.page_fm_id, self.conversation_fm_id),
                                 {'conversation_id': self.conversation_fm_id, 'customer_id': self.customer_fm_id or '', 'current_count': 0})
        self.env['dac.pancake.message']._store(self, data.get('messages') or [])
        self.messages_updated_at = self.updated_at_fm or fields.Datetime.now()

    def action_open_pancake(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_url', 'url': self.external_url or '#', 'target': 'new'}

    def _short_url(self):
        """Link hội thoại rút gọn cho tin Zalo: crm.duyan.vn/p/abc (Pancake tự bắt đăng nhập nên mã ngắn không lộ gì)."""
        self.ensure_one()
        return self.env['dac.short.link']._shorten(self.external_url, source='Pancake', kind='p') if self.external_url else ''


class PancakeMessage(models.Model):
    _name = 'dac.pancake.message'
    _description = 'Tin nhắn Pancake (bản gọn)'
    _order = 'inserted_at, id'

    conversation_id = fields.Many2one('dac.pancake.conversation', required=True, ondelete='cascade', index=True)
    message_fm_id = fields.Char(required=True, index=True)
    inserted_at = fields.Datetime(string='Lúc', index=True)
    content_html = fields.Text(string='Nội dung')
    type_content = fields.Char(string='Loại')
    sender_name = fields.Char(string='Người gửi')
    sender_fm_id = fields.Char(string='Mã người gửi')
    attachment_urls = fields.Text(string='Ảnh/file (link)', help='Mỗi dòng một ảnh/file: loại: link (tên). Ảnh/file không tải về Odoo.')
    raw_json = fields.Text(string='Dữ liệu gốc (bản gọn)')

    _sql_constraints = [('message_unique', 'unique(conversation_id, message_fm_id)', 'Tin đã có.')]

    @api.model
    def _type_of(self, raw):
        attachments = [a for a in raw.get('attachments') or [] if isinstance(a, dict)]
        if not attachments:
            return 'text'
        first = attachments[0]
        kind = first.get('type')
        if not kind:
            inner = [a for a in first.get('attachments') or [] if isinstance(a, dict)]
            kind = inner[0].get('type') if inner else None
        return kind or 'text'

    @api.model
    def _store(self, conversation, rows):
        rows = [r for r in rows if isinstance(r, dict) and r.get('id')]
        known = set(self.search([('conversation_id', '=', conversation.id), ('message_fm_id', 'in', [str(r['id']) for r in rows])]).mapped('message_fm_id'))
        vals_list, seen = [], set()
        for raw in rows:
            key = str(raw['id'])
            if key in known or key in seen:
                continue
            seen.add(key)
            vals_list.append({
                'conversation_id': conversation.id, 'message_fm_id': key, 'inserted_at': parse_time(raw.get('inserted_at')) or fields.Datetime.now(),
                'content_html': raw.get('message') or '', 'type_content': self._type_of(raw),
                'sender_name': ((raw.get('from') or {}).get('admin_name') or (raw.get('from') or {}).get('name') or '')[:120],
                'sender_fm_id': str((raw.get('from') or {}).get('id') or '')[:80] or False,
                'attachment_urls': attachment_lines(raw) or False,
                'raw_json': json.dumps(slim(raw), ensure_ascii=False),
            })
        return self.create(vals_list) if vals_list else self.browse()


def match_partner(env, number):
    """Liên hệ Odoo có số điện thoại `number` (đã chuẩn hóa 0XXXXXXXXX), hoặc recordset rỗng."""
    Partner = env['res.partner'].sudo()
    if not number:
        return Partner
    # Số trong Odoo hay viết có dấu cách/chấm/+84: so trên phần chữ số (9 số cuối) rồi chuẩn hóa lại cho chắc
    env.cr.execute("""SELECT id FROM res_partner WHERE active AND (regexp_replace(coalesce(phone, ''), '\\D', '', 'g') LIKE %s
                      OR regexp_replace(coalesce(mobile, ''), '\\D', '', 'g') LIKE %s) ORDER BY id LIMIT 20""",
                   ('%' + number[-9:], '%' + number[-9:]))
    candidates = Partner.browse([row[0] for row in env.cr.fetchall()])
    return candidates.filtered(lambda p: number in (normalize_phone_vn(p.phone), normalize_phone_vn(p.mobile)))[:1]
