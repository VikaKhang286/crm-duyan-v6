import json
import logging
import re
from datetime import timedelta

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)
# Xử lý lỗi (vd cơ sở dữ liệu va chạm lúc nâng cấp) thì tự xử lý lại sau từng khoảng này (phút); hết lượt mới để người bấm tay
EVENT_RETRY_MINUTES = (2, 5, 15, 30, 60)
_CTRL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')


def clean(value, limit):
    """Cắt độ dài và bỏ ký tự điều khiển — dữ liệu từ website là không đáng tin."""
    return _CTRL.sub('', str(value if value is not None else '')).strip()[:limit]


def clean_payload(value, depth=0):
    """Làm sạch đệ quy dữ liệu từ website trước khi lưu JSON (PostgreSQL từ chối NUL; giới hạn độ sâu/độ dài)."""
    if isinstance(value, str):
        return clean(value, 5000)
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return value
    if depth >= 4:
        return None
    if isinstance(value, (list, tuple)):
        return [clean_payload(v, depth + 1) for v in value[:50]]
    if isinstance(value, dict):
        return {clean(k, 100): clean_payload(v, depth + 1) for k, v in list(value.items())[:50]}
    return None


class PushEvent(models.Model):
    """Nhật ký mọi gói dữ liệu nhận từ website; mỗi event_type có một method _handle_<event_type>."""
    _name = 'dac.push.event'
    _description = 'Sự kiện đẩy từ website'
    _order = 'id desc'
    _rec_name = 'event_type'

    source = fields.Char(string='Nguồn', required=True, index=True)
    event_id = fields.Char(string='Mã sự kiện', required=True, index=True, readonly=True)
    event_type = fields.Char(string='Loại', required=True, index=True, readonly=True)
    payload = fields.Json(string='Dữ liệu', readonly=True, groups='base.group_system')
    state = fields.Selection([('received', 'Đã nhận'), ('processed', 'Đã xử lý'),
        ('failed', 'Lỗi xử lý'), ('ignored', 'Không có bộ xử lý')], default='received', required=True, index=True)
    error_message = fields.Char(string='Lỗi', readonly=True)
    handled_at = fields.Datetime(readonly=True)
    attempts = fields.Integer(string='Số lần xử lý lỗi', readonly=True, copy=False)
    next_attempt_at = fields.Datetime(string='Tự xử lý lại lúc', readonly=True, copy=False, index=True,
        help='Trống khi đã xử lý xong hoặc đã hết lượt tự thử lại (lúc đó cần bấm "Xử lý lại").')
    alerted = fields.Boolean(string='Đã cảnh báo', readonly=True, copy=False)
    _sql_constraints = [('source_event_unique', 'unique(source, event_id)', 'Sự kiện này đã được nhận.')]

    @api.model
    def _receive(self, envelope):
        """Idempotent: trả (event, created). `envelope` đã qua xác thực HMAC và kiểm tra cấu trúc."""
        source = clean(envelope.get('source') or 'unknown', 100)
        event_id = clean(envelope['event_id'], 150)
        existing = self.search([('source', '=', source), ('event_id', '=', event_id)], limit=1)
        if existing:
            return existing, False
        event = self.create({
            'source': source, 'event_id': event_id, 'event_type': clean(envelope['event_type'], 100),
            'payload': clean_payload(envelope.get('data')) if isinstance(envelope.get('data'), dict) else {},
        })
        event._dispatch()
        return event, True

    def _handler_name(self):
        return '_handle_' + re.sub(r'[^a-z0-9]+', '_', (self.event_type or '').lower()).strip('_')

    def _dispatch(self):
        for event in self:
            handler = getattr(event, event._handler_name(), None)
            if not handler:
                event.write({'state': 'ignored', 'error_message': 'Chưa có bộ xử lý %s' % event._handler_name()})
                continue
            try:
                with self.env.cr.savepoint():
                    handler()
                event.write({'state': 'processed', 'error_message': False, 'handled_at': fields.Datetime.now(), 'next_attempt_at': False})
            except Exception as exc:  # noqa: BLE001 — lưu lỗi để xử lý lại, không làm mất gói dữ liệu
                _logger.exception('dac.push.event %s xử lý lỗi', event.id)
                attempts = event.attempts + 1
                event.write({'state': 'failed', 'error_message': clean(exc, 200), 'handled_at': fields.Datetime.now(), 'attempts': attempts,
                             'next_attempt_at': fields.Datetime.now() + timedelta(minutes=EVENT_RETRY_MINUTES[attempts - 1])
                             if attempts <= len(EVENT_RETRY_MINUTES) else False})

    def action_reprocess(self):
        self.filtered(lambda e: e.state in ('failed', 'ignored'))._dispatch()

    @api.model
    def _cron_reprocess_failed(self):
        """Tự xử lý lại sự kiện lỗi đến hạn (xử lý idempotent theo mã sự kiện nên an toàn), rồi cảnh báo các sự kiện đã hết lượt. Không raise."""
        try:
            due = self.search([('state', '=', 'failed'), ('next_attempt_at', '!=', False),
                               ('next_attempt_at', '<=', fields.Datetime.now())], order='next_attempt_at', limit=20)
            due._dispatch()
        except Exception:  # noqa: BLE001
            _logger.exception('Không tự xử lý lại được sự kiện lỗi')
        try:
            with self.env.cr.savepoint():
                self._alert_exhausted()
        except Exception:  # noqa: BLE001
            _logger.exception('Không tạo được cảnh báo sự kiện lỗi')

    @api.model
    def _alert_exhausted(self):
        stuck = self.search([('state', '=', 'failed'), ('next_attempt_at', '=', False), ('alerted', '=', False)])
        if not stuck:
            return
        text = ('⚠️ Hệ thống: %d dữ liệu từ website chưa xử lý được (đã tự thử lại nhiều lần), có thể chưa có lead tương ứng.\n'
                'Vào Odoo → Lead website → Sự kiện nhận từ website, chọn mục lỗi rồi bấm "Xử lý lại".' % len(stuck))
        self.env['dac.zalo.channel']._notify('system.alert', text, 'alert-push:%s' % max(stuck.ids))
        stuck.write({'alerted': True})


class PushEventSystem(models.Model):
    _inherit = 'dac.push.event'

    def _handle_system_ping(self):
        """Plugin WordPress bấm "Gửi thử kết nối": chỉ xác nhận chữ ký + cấu hình, không có tác dụng phụ."""
        return True
