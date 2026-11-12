import logging
import uuid
from datetime import timedelta
from urllib.parse import quote

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)
MAX_TEXT = 12000
MAX_MENTIONS = 30  # bridge từ chối quá 30 lượt nhắc tên trong một tin
THREAD_TYPES = [('group', 'Nhóm'), ('user', 'Chat riêng')]


# Icon FontAwesome theo nhóm thông báo; addon khác thêm nhóm mới bằng cách bổ sung khóa vào dict này.
GROUP_ICONS = {
    'Website': 'fa-globe',
    'Hệ thống': 'fa-cog',
    'Đơn hàng': 'fa-shopping-cart',
    'Công việc': 'fa-tasks',
    'Nhân sự': 'fa-id-badge',
    'Tài chính': 'fa-credit-card',
}


def utf16_len(text):
    """Zalo/zca-js đánh vị trí theo ký tự JavaScript (UTF-16): emoji ngoài BMP chiếm 2 đơn vị."""
    return len(text.encode('utf-16-le')) // 2


class ZaloEventType(models.Model):
    """Danh mục loại thông báo mà addon khác đăng ký (qua file data) để kênh Zalo chọn nhận."""
    _name = 'dac.zalo.event.type'
    _description = 'Loại thông báo gửi vào hội thoại Zalo'
    _order = 'group_name, sequence, id'

    name = fields.Char(string='Tên thông báo', required=True, translate=False)
    code = fields.Char(string='Mã', required=True, index=True, readonly=True,
        help='Mã kỹ thuật, ví dụ web_lead.created. Addon nguồn dùng mã này khi gọi dac.zalo.channel._notify().')
    group_name = fields.Char(string='Nhóm', default='Khác')
    description = fields.Text(string='Mô tả')
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    channel_ids = fields.Many2many('dac.zalo.channel', 'dac_zalo_channel_event_rel', 'event_type_id', 'channel_id',
        string='Hội thoại nhận tin')
    icon = fields.Char(string='Icon', compute='_compute_icon')
    channel_count = fields.Integer(string='Số hội thoại nhận', compute='_compute_channel_count')

    @api.depends('group_name')
    def _compute_icon(self):
        for event_type in self:
            event_type.icon = GROUP_ICONS.get(event_type.group_name, 'fa-bell-o')

    @api.depends('channel_ids.active')
    def _compute_channel_count(self):
        for event_type in self:
            event_type.channel_count = len(event_type.channel_ids.filtered('active'))
    _sql_constraints = [('code_unique', 'unique(code)', 'Mã loại thông báo đã tồn tại.')]


class ZaloChannel(models.Model):
    _name = 'dac.zalo.channel'
    _description = 'Hội thoại Zalo nhận thông báo'
    _order = 'sequence, name'

    name = fields.Char(string='Tên hội thoại', required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(string='Đang nhận tin', default=True)
    thread_id = fields.Char(string='Mã hội thoại Zalo', required=True, index=True, copy=False)
    thread_type = fields.Selection(THREAD_TYPES, string='Loại', required=True, default='group')
    members = fields.Integer(string='Số thành viên', readonly=True)
    event_type_ids = fields.Many2many('dac.zalo.event.type', 'dac_zalo_channel_event_rel', 'channel_id', 'event_type_id',
        string='Thông báo được gửi vào đây')
    message_ids = fields.One2many('dac.zalo.channel.message', 'channel_id', string='Tin đã gửi')
    last_sent_at = fields.Datetime(string='Gửi gần nhất', compute='_compute_stats')
    failed_count = fields.Integer(string='Tin lỗi', compute='_compute_stats')
    note = fields.Char(string='Ghi chú')
    member_ids = fields.One2many('dac.zalo.channel.member', 'channel_id', string='Thành viên nhóm')
    member_count = fields.Integer(string='Thành viên đã tải', compute='_compute_member_count')
    members_synced_at = fields.Datetime(string='Tải danh sách lúc', readonly=True)
    mention_ids = fields.One2many('dac.zalo.channel.mention', 'channel_id', string='Nhắc tên theo loại thông báo')
    _sql_constraints = [('thread_unique', 'unique(thread_id, thread_type)', 'Hội thoại này đã được thêm.')]

    mention_summary = fields.Char(string='Đang nhắc tên', compute='_compute_mention_summary')

    @api.depends('mention_ids.member_ids.name')
    def _compute_mention_summary(self):
        for channel in self:
            names = sorted({m.name for m in channel.mention_ids.member_ids if m.name})
            channel.mention_summary = ', '.join('@' + n for n in names)

    @api.depends('member_ids')
    def _compute_member_count(self):
        for channel in self:
            channel.member_count = len(channel.member_ids)

    @api.depends('message_ids.state', 'message_ids.sent_at')
    def _compute_stats(self):
        for channel in self:
            sent = channel.message_ids.filtered(lambda m: m.state == 'sent' and m.sent_at)
            channel.last_sent_at = max(sent.mapped('sent_at')) if sent else False
            channel.failed_count = len(channel.message_ids.filtered(lambda m: m.state in ('failed', 'uncertain')))

    def _queue(self, text, key, event_type=False, styles=None, mentions=None, reply_to=None):
        """Đưa tin vào hàng đợi gửi của riêng kênh này. Idempotent theo key.

        `styles`: danh sách {'start', 'len', 'style': 'bold'} (vị trí tính theo ký tự của `text`).
        `mentions`: danh sách {'pos', 'len', 'uid'} nhắc tên thành viên nhóm (xem `_with_mentions`).
        `reply_to`: tin đã gửi trước đó trong cùng hội thoại; tin mới hiện dưới dạng "trả lời" tin đó trên Zalo.
        """
        self.ensure_one()
        messages = self.env['dac.zalo.channel.message']
        existing = messages.search([('event_key', '=', key)], limit=1)
        if existing:
            return existing
        return messages.create({
            'channel_id': self.id, 'event_key': key, 'text': text[:MAX_TEXT],
            'event_type_id': event_type.id if event_type else False, 'state': 'queued',
            'styles': styles or False, 'mentions': mentions or False, 'reply_to_id': reply_to.id if reply_to else False,
        })

    def _mention_members(self, event_type):
        """Thành viên cần nhắc tên cho loại thông báo này (chỉ nhóm; tối đa MAX_MENTIONS người)."""
        self.ensure_one()
        if self.thread_type != 'group' or not event_type:
            return self.env['dac.zalo.channel.member']
        rule = self.mention_ids.filtered(lambda r: r.event_type_id == event_type)[:1]
        return rule.member_ids.sorted(lambda m: (m.name or '').lower())[:MAX_MENTIONS]

    def _with_mentions(self, text, members, leading=False):
        """Thêm "@An @Bình" và trả (text, mentions) để Zalo gạch tên và báo cho đúng người được nhắc.

        Mặc định thêm ở dòng cuối nên không làm lệch vị trí chữ đậm đã tính ở đầu tin.
        `leading=True`: đặt tên ngay đầu câu ("@An @Bình Bạn đã liên hệ chưa ạ") — dùng cho tin nhắc nhở ngắn.
        """
        if not members:
            return text, None
        tokens = ['@' + ' '.join((m.name or m.uid).split()) for m in members]
        extra = ' '.join(tokens)
        if leading:
            body = text[:max(0, MAX_TEXT - len(extra) - 1)]
            mentions, pos = [], 0
            for member, token in zip(members, tokens):
                mentions.append({'pos': pos, 'len': utf16_len(token), 'uid': member.uid})
                pos += utf16_len(token) + 1
            return '%s %s' % (extra, body), mentions
        base = text[:max(0, MAX_TEXT - len(extra) - 1)] + '\n'
        pos = utf16_len(base)
        mentions = []
        for member, token in zip(members, tokens):
            mentions.append({'pos': pos, 'len': utf16_len(token), 'uid': member.uid})
            pos += utf16_len(token) + 1
        return base + extra, mentions

    @api.model
    def _notify(self, event_code, text, key, styles=None):
        """API cho addon khác: gửi `text` tới mọi hội thoại đang đăng ký `event_code`.

        `styles` (tùy chọn): định dạng chữ, vd [{'start': 0, 'len': 20, 'style': 'bold'}] để in đậm tiêu đề.

        `key` phải ổn định cho một sự kiện (vd 'web_lead:duyan.vn:123') để không gửi trùng khi gọi lại.
        Trả về recordset tin đã xếp hàng (rỗng nếu chưa có hội thoại nào đăng ký).
        """
        event_type = self.env['dac.zalo.event.type'].sudo().search([('code', '=', event_code)], limit=1)
        if not event_type:
            _logger.warning('Zalo notify: loại thông báo %s chưa được đăng ký', event_code)
            return self.env['dac.zalo.channel.message']
        # sudo(): luồng nền (webhook/cron) không có user; chỉ ghi vào outbox riêng của kênh
        channels = self.sudo().search([('active', '=', True), ('event_type_ids', 'in', event_type.id)])
        messages = self.env['dac.zalo.channel.message']
        for channel in channels:
            channel_text, mentions = channel._with_mentions(text, channel._mention_members(event_type))
            messages |= channel._queue(channel_text, '%s:%s' % (key, channel.id), event_type, styles, mentions)
        if messages:
            self._trigger_send()
        return messages

    @api.model
    def _trigger_send(self):
        try:
            # sudo(): đánh thức cron gửi ngay thay vì chờ phút kế tiếp
            self.env.ref('dac_zalo_assistant.cron_zalo_channel_process').sudo()._trigger()
        except Exception:  # noqa: BLE001 — không để lỗi lập lịch làm hỏng nghiệp vụ nguồn
            _logger.exception('Không đặt được lịch gửi Zalo ngay; cron định kỳ sẽ xử lý')

    def action_send_test(self):
        self.ensure_one()
        test_type = self.env.ref('dac_zalo_assistant.event_type_channel_test')
        title = 'Tin thử từ Odoo'
        receives = ', '.join(self.event_type_ids.mapped('name')) or 'chưa chọn loại thông báo nào'
        text = '%s\nHội thoại "%s" sẽ nhận: %s' % (title, self.name, receives)
        # Tin thử nhắc tên mọi người đã cài trong bất kỳ loại thông báo nào, để thấy tên được gạch đúng trên Zalo.
        tagged = self.mention_ids.filtered(lambda r: r.event_type_id in self.event_type_ids).mapped('member_ids')
        text, mentions = self._with_mentions(text, tagged.sorted(lambda m: (m.name or '').lower())[:MAX_MENTIONS]) if self.thread_type == 'group' else (text, None)
        # Tiêu đề in đậm giống tin thật: nhìn là biết định dạng chữ đậm có hiển thị trên Zalo hay không.
        self._queue(text, 'test:%s' % uuid.uuid4().hex, test_type,
                    styles=[{'start': 0, 'len': len(title), 'style': 'bold'}], mentions=mentions)
        self._trigger_send()
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': _('Đã xếp hàng gửi'), 'message': _('Tin thử sẽ được gửi trong ít phút. Xem tab "Tin đã gửi".'),
            'type': 'success'}}

    def _sync_members(self):
        """Tải danh sách thành viên nhóm từ bridge (thêm người mới, đổi tên, bỏ người đã rời nhóm)."""
        self.ensure_one()
        if self.thread_type != 'group':
            raise UserError(_('Chỉ nhóm Zalo mới có danh sách thành viên.'))
        # sudo(): người quản lý không đọc được bridge_url/token (chỉ system); chỉ gọi lệnh đọc danh sách thành viên
        config = self.env['dac.zalo.config'].sudo()._config()
        result = config._bridge_request('/threads/%s/members' % quote(self.thread_id, safe=''))
        seen = {}
        for item in result.get('members', []) if isinstance(result.get('members'), list) else []:
            uid = str(item.get('uid') or '') if isinstance(item, dict) else ''
            if uid.isdigit():
                seen[uid] = ' '.join(str(item.get('name') or uid).split())[:160] or uid
        existing = {m.uid: m for m in self.member_ids}
        for uid, member in existing.items():
            if uid in seen and member.name != seen[uid]:
                member.name = seen[uid]
        self.env['dac.zalo.channel.member'].create([{'channel_id': self.id, 'uid': uid, 'name': name}
                                                    for uid, name in seen.items() if uid not in existing])
        # Người đã rời nhóm: xóa luôn khỏi các dòng nhắc tên (quan hệ nhiều-nhiều tự gỡ)
        self.member_ids.filtered(lambda m: m.uid not in seen).unlink()
        self.members_synced_at = fields.Datetime.now()
        return len(seen)

    def action_sync_members(self):
        self.ensure_one()
        count = self._sync_members()
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': _('Đã tải danh sách thành viên'), 'message': _('Nhóm có %s thành viên để nhắc tên.') % count,
            'type': 'success', 'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'}}}

    @api.model
    def action_open_search(self):
        return self.env['dac.zalo.thread.search'].create({})._open_with_listing()


class ZaloChannelMember(models.Model):
    """Thành viên của một nhóm Zalo, tải từ bridge để chọn người cần nhắc tên (@tag)."""
    _name = 'dac.zalo.channel.member'
    _description = 'Thành viên nhóm Zalo'
    _order = 'name, id'

    channel_id = fields.Many2one('dac.zalo.channel', string='Nhóm', required=True, ondelete='cascade', index=True)
    uid = fields.Char(string='Mã Zalo', required=True, index=True, readonly=True)
    name = fields.Char(string='Tên Zalo', required=True)
    _sql_constraints = [('channel_uid_unique', 'unique(channel_id, uid)', 'Thành viên này đã có trong nhóm.')]


class ZaloChannelMention(models.Model):
    """Người cần nhắc tên (@tag) khi một loại thông báo được gửi vào nhóm. Mỗi nhóm + loại thông báo một dòng."""
    _name = 'dac.zalo.channel.mention'
    _description = 'Nhắc tên theo loại thông báo'
    _order = 'channel_id, event_type_id'

    channel_id = fields.Many2one('dac.zalo.channel', string='Nhóm', required=True, ondelete='cascade', index=True)
    event_type_id = fields.Many2one('dac.zalo.event.type', string='Loại thông báo', required=True, ondelete='cascade')
    member_ids = fields.Many2many('dac.zalo.channel.member', 'dac_zalo_mention_member_rel', 'mention_id', 'member_id',
        string='Nhắc tên')
    _sql_constraints = [('channel_event_unique', 'unique(channel_id, event_type_id)', 'Loại thông báo này đã có dòng nhắc tên.')]

    @api.constrains('member_ids', 'channel_id')
    def _check_members(self):
        for rule in self:
            if len(rule.member_ids) > MAX_MENTIONS:
                raise UserError(_('Mỗi tin chỉ nhắc tối đa %s người.') % MAX_MENTIONS)
            if rule.member_ids.channel_id - rule.channel_id:
                raise UserError(_('Chỉ chọn người thuộc đúng nhóm này.'))


class ZaloChannelMessage(models.Model):
    _name = 'dac.zalo.channel.message'
    _description = 'Tin gửi vào hội thoại Zalo'
    _order = 'id desc'
    _rec_name = 'text'

    channel_id = fields.Many2one('dac.zalo.channel', string='Hội thoại', required=True, ondelete='cascade', index=True)
    event_type_id = fields.Many2one('dac.zalo.event.type', string='Loại thông báo', ondelete='set null')
    event_key = fields.Char(required=True, readonly=True, index=True, copy=False)
    text = fields.Text(string='Nội dung', required=True, readonly=True)
    styles = fields.Json(string='Định dạng chữ', readonly=True, help='Các đoạn in đậm: [{"start", "len", "style": "bold"}].')
    mentions = fields.Json(string='Nhắc tên', readonly=True, help='Các lượt nhắc tên thành viên nhóm: [{"pos", "len", "uid"}].')
    reply_to_id = fields.Many2one('dac.zalo.channel.message', string='Trả lời tin', readonly=True, ondelete='set null',
        help='Tin này hiện trên Zalo dưới dạng trả lời tin đã gửi trước đó.')
    state = fields.Selection([('queued', 'Chờ gửi'), ('sent', 'Đã gửi'), ('failed', 'Lỗi'),
        ('uncertain', 'Cần kiểm tra gửi'), ('cancelled', 'Đã hủy')], string='Trạng thái', default='queued', required=True, index=True)
    attempts = fields.Integer(string='Số lần thử', readonly=True)
    next_attempt_at = fields.Datetime(readonly=True)
    sent_at = fields.Datetime(string='Gửi lúc', readonly=True)
    external_message_id = fields.Char(readonly=True)
    error_message = fields.Char(string='Lỗi', readonly=True)
    _sql_constraints = [('event_unique', 'unique(event_key)', 'Tin này đã được xếp hàng.')]

    @api.model
    def _cron_process(self):
        config = self.env['dac.zalo.config']._config()
        if not config.active or not config.assistant_uid:
            return
        now = fields.Datetime.now()
        for message in self.search([('state', '=', 'queued'),
                '|', ('next_attempt_at', '=', False), ('next_attempt_at', '<=', now)], order='id', limit=20):
            try:
                with self.env.cr.savepoint():
                    if not message.channel_id.active:
                        message.write({'state': 'cancelled', 'error_message': 'Hội thoại đã tắt nhận tin.'})
                        continue
                    message._send(config)
            except Exception:  # noqa: BLE001 — cron không được raise; không log nội dung tin
                message.write({'state': 'failed', 'error_message': 'Không xử lý được; quản trị viên cần kiểm tra.'})

    def _queue_reply(self, text, key):
        """Xếp hàng một tin trả lời tin đã gửi này, nhắc tên đúng những người đã cài cho loại thông báo của nó."""
        self.ensure_one()
        channel = self.channel_id
        text, mentions = channel._with_mentions(text, channel._mention_members(self.event_type_id), leading=True)
        return channel._queue(text, key, self.event_type_id, mentions=mentions, reply_to=self)

    def _send(self, config):
        self.ensure_one()
        payload = {
            'request_id': 'ch-%s' % self.id, 'account_id': config.assistant_uid,
            'thread_id': self.channel_id.thread_id, 'thread_type': self.channel_id.thread_type, 'text': self.text}
        if self.styles:
            payload['styles'] = self.styles
        if self.mentions:
            payload['mentions'] = self.mentions
        quoted_id = self.reply_to_id.external_message_id if self.reply_to_id.state == 'sent' else False
        if quoted_id:
            payload['quote_message_id'] = quoted_id
        try:
            result = config._bridge_request('/send', payload)
        except UserError as error:
            attempts = self.attempts + 1
            self.write({'attempts': attempts, 'state': 'failed' if attempts >= 5 else 'queued',
                'error_message': str(error)[:200], 'next_attempt_at': fields.Datetime.now() + timedelta(minutes=2 ** attempts)})
            return
        if result.get('delivery') == 'sent':
            if quoted_id and result.get('quoted') is False:
                _logger.warning('Zalo: tin %s gửi được nhưng bridge không còn dữ liệu tin gốc nên không có khung trả lời', self.id)
            self.write({'state': 'sent', 'sent_at': fields.Datetime.now(),
                'external_message_id': result.get('message_id'), 'error_message': False})
        elif result.get('delivery') == 'uncertain':
            # Có thể Zalo đã nhận: tuyệt đối không tự gửi lại để tránh trùng tin.
            self.write({'state': 'uncertain', 'error_message': 'Bridge chưa xác nhận gửi. Kiểm tra nhóm trên Zalo; không tự gửi lại.'})
        else:
            raise UserError(_('Bridge trả trạng thái gửi không hợp lệ.'))

    def action_retry(self):
        # Chỉ cho gửi lại tin lỗi rõ ràng; tin "uncertain" cần người kiểm tra trước.
        self.filtered(lambda m: m.state == 'failed').write({'state': 'queued', 'attempts': 0, 'next_attempt_at': False})
        self.env['dac.zalo.channel']._trigger_send()


class ZaloThreadSearch(models.TransientModel):
    _name = 'dac.zalo.thread.search'
    _description = 'Tìm hội thoại Zalo để nhận thông báo'

    query = fields.Char(string='Tên nhóm / người')
    line_ids = fields.One2many('dac.zalo.thread.search.line', 'search_id', string='Kết quả')
    searched = fields.Boolean(readonly=True)
    event_codes = fields.Char(string='Loại thông báo đăng ký sẵn', readonly=True,
        help='Mã loại thông báo (cách nhau bằng dấu phẩy) sẽ được bật sẵn cho hội thoại vừa thêm.')
    event_names = fields.Char(compute='_compute_event_names')

    @api.depends('event_codes')
    def _compute_event_names(self):
        for wizard in self:
            codes = [c for c in (wizard.event_codes or '').split(',') if c]
            wizard.event_names = ', '.join(self.env['dac.zalo.event.type'].search([('code', 'in', codes)]).mapped('name'))

    @api.model
    def action_open_for_events(self, codes):
        """Mở wizard chọn hội thoại; hội thoại được thêm sẽ nhận sẵn các loại thông báo `codes` (kèm dòng nhắc tên)."""
        return self.create({'event_codes': ','.join(codes)})._open_with_listing()

    def _open_with_listing(self):
        """Mở wizard và tự liệt kê mọi hội thoại của tài khoản (không cần bấm Tìm); bridge lỗi thì mở danh sách trống."""
        self.ensure_one()
        try:
            self.action_search()
        except UserError:
            _logger.info('Chưa tải được danh sách hội thoại khi mở wizard; người dùng có thể bấm Tìm.')
        return self._reopen()

    def _reopen(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'name': _('Tìm hội thoại Zalo'), 'res_model': self._name,
            'res_id': self.id, 'view_mode': 'form', 'target': 'new'}

    def action_search(self):
        self.ensure_one()
        # sudo(): người quản lý không đọc được bridge_url/token (chỉ system); wizard chỉ gọi lệnh tìm kiếm chỉ-đọc
        config = self.env['dac.zalo.config'].sudo()._config()
        result = config._bridge_request('/threads?q=' + quote(self.query or '', safe=''))
        existing = {(c.thread_type, c.thread_id) for c in self.env['dac.zalo.channel'].with_context(active_test=False).search([])}
        self.line_ids.unlink()
        self.env['dac.zalo.thread.search.line'].create([{
            'search_id': self.id, 'thread_id': item['thread_id'], 'thread_type': item['thread_type'],
            'name': item.get('name') or item['thread_id'], 'members': item.get('members') or 0,
            'added': (item['thread_type'], item['thread_id']) in existing,
        } for item in result.get('threads', []) if item.get('thread_type') in ('user', 'group') and item.get('thread_id')])
        self.searched = True
        return self._reopen()

    def action_add(self):
        self.ensure_one()
        chosen = self.line_ids.filtered(lambda l: l.selected and not l.added)
        if not chosen:
            raise UserError(_('Hãy bật chọn ít nhất một hội thoại chưa được thêm.'))
        event_types = self.env['dac.zalo.event.type'].search([('code', 'in', [c for c in (self.event_codes or '').split(',') if c])])
        channels = self.env['dac.zalo.channel'].create([{
            'name': line.name, 'thread_id': line.thread_id, 'thread_type': line.thread_type, 'members': line.members,
            'event_type_ids': [(6, 0, event_types.ids)],
        } for line in chosen])
        for channel in channels.filtered(lambda c: c.thread_type == 'group'):
            # Nhóm mới: tải thành viên ngay để mở form là chọn được người nhắc tên; lỗi mạng không chặn việc thêm nhóm
            try:
                channel._sync_members()
            except UserError:
                _logger.info('Chưa tải được thành viên nhóm %s; bấm "Tải danh sách thành viên" sau.', channel.id)
            if event_types:
                self.env['dac.zalo.channel.mention'].create([{'channel_id': channel.id, 'event_type_id': et.id} for et in event_types])
        action = {'type': 'ir.actions.act_window', 'name': _('Hội thoại nhận thông báo'),
            'res_model': 'dac.zalo.channel', 'target': 'current'}
        if len(channels) == 1:
            action.update(view_mode='form', res_id=channels.id)
        else:
            action.update(view_mode='list,form', domain=[('id', 'in', channels.ids)])
        return action


class ZaloThreadSearchLine(models.TransientModel):
    _name = 'dac.zalo.thread.search.line'
    _description = 'Kết quả tìm hội thoại Zalo'
    _order = 'thread_type desc, name'

    search_id = fields.Many2one('dac.zalo.thread.search', required=True, ondelete='cascade')
    thread_id = fields.Char(required=True, readonly=True)
    thread_type = fields.Selection(THREAD_TYPES, string='Loại', readonly=True)
    name = fields.Char(string='Tên', readonly=True)
    members = fields.Integer(string='Thành viên', readonly=True)
    added = fields.Boolean(string='Đã thêm', readonly=True)
    selected = fields.Boolean(string='Chọn')


class ZaloConfigOverview(models.Model):
    """Số liệu tổng quan hiển thị ở nút thống kê trên màn hình Kết nối Zalo."""
    _inherit = 'dac.zalo.config'

    channel_count = fields.Integer(string='Đích nhận thông báo', compute='_compute_overview')
    linked_binding_count = fields.Integer(string='Nhân viên đã liên kết', compute='_compute_overview')
    failed_message_count = fields.Integer(string='Tin cần kiểm tra', compute='_compute_overview')

    def _compute_overview(self):
        channels = self.env['dac.zalo.channel'].search_count([('active', '=', True)])
        linked = self.env['dac.zalo.binding'].search_count([('state', '=', 'linked')])
        failed = self.env['dac.zalo.channel.message'].search_count([('state', 'in', ('failed', 'uncertain'))])
        for config in self:
            config.channel_count = channels
            config.linked_binding_count = linked
            config.failed_message_count = failed

    def action_open_channels(self):
        return self.env['ir.actions.actions']._for_xml_id('dac_zalo_assistant.channel_action')

    def action_open_bindings(self):
        return self.env['ir.actions.actions']._for_xml_id('dac_zalo_assistant.bindings_admin_action')

    def action_open_failed_messages(self):
        return {'type': 'ir.actions.act_window', 'name': _('Tin cần kiểm tra'), 'res_model': 'dac.zalo.channel.message',
            'view_mode': 'list', 'domain': [('state', 'in', ('failed', 'uncertain'))]}
