import logging
import uuid
from datetime import timedelta
from urllib.parse import quote

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)
MAX_TEXT = 12000
THREAD_TYPES = [('group', 'Nhóm'), ('user', 'Chat riêng')]


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
    _sql_constraints = [('thread_unique', 'unique(thread_id, thread_type)', 'Hội thoại này đã được thêm.')]

    @api.depends('message_ids.state', 'message_ids.sent_at')
    def _compute_stats(self):
        for channel in self:
            sent = channel.message_ids.filtered(lambda m: m.state == 'sent' and m.sent_at)
            channel.last_sent_at = max(sent.mapped('sent_at')) if sent else False
            channel.failed_count = len(channel.message_ids.filtered(lambda m: m.state in ('failed', 'uncertain')))

    def _queue(self, text, key, event_type=False, styles=None):
        """Đưa tin vào hàng đợi gửi của riêng kênh này. Idempotent theo key.

        `styles`: danh sách {'start', 'len', 'style': 'bold'} (vị trí tính theo ký tự của `text`).
        """
        self.ensure_one()
        messages = self.env['dac.zalo.channel.message']
        existing = messages.search([('event_key', '=', key)], limit=1)
        if existing:
            return existing
        return messages.create({
            'channel_id': self.id, 'event_key': key, 'text': text[:MAX_TEXT],
            'event_type_id': event_type.id if event_type else False, 'state': 'queued',
            'styles': styles or False,
        })

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
            messages |= channel._queue(text, '%s:%s' % (key, channel.id), event_type, styles)
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
        # Tiêu đề in đậm giống tin thật: nhìn là biết định dạng chữ đậm có hiển thị trên Zalo hay không.
        self._queue('%s\nHội thoại "%s" sẽ nhận: %s' % (title, self.name, receives), 'test:%s' % uuid.uuid4().hex, test_type,
                    styles=[{'start': 0, 'len': len(title), 'style': 'bold'}])
        self._trigger_send()
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': _('Đã xếp hàng gửi'), 'message': _('Tin thử sẽ được gửi trong ít phút. Xem tab "Tin đã gửi".'),
            'type': 'success'}}

    @api.model
    def action_open_search(self):
        wizard = self.env['dac.zalo.thread.search'].create({})
        return wizard._reopen()


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

    def _send(self, config):
        self.ensure_one()
        payload = {
            'request_id': 'ch-%s' % self.id, 'account_id': config.assistant_uid,
            'thread_id': self.channel_id.thread_id, 'thread_type': self.channel_id.thread_type, 'text': self.text}
        if self.styles:
            payload['styles'] = self.styles
        try:
            result = config._bridge_request('/send', payload)
        except UserError as error:
            attempts = self.attempts + 1
            self.write({'attempts': attempts, 'state': 'failed' if attempts >= 5 else 'queued',
                'error_message': str(error)[:200], 'next_attempt_at': fields.Datetime.now() + timedelta(minutes=2 ** attempts)})
            return
        if result.get('delivery') == 'sent':
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
        channels = self.env['dac.zalo.channel'].create([{
            'name': line.name, 'thread_id': line.thread_id, 'thread_type': line.thread_type, 'members': line.members,
        } for line in chosen])
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
