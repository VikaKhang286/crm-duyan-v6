import hashlib
import logging
import re
import uuid
from datetime import timedelta, timezone

import requests

from odoo import api, fields, models, registry, SUPERUSER_ID, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)
EVENT_STATES = [
    ('pending', 'Chờ gửi'), ('waiting', 'Thiếu dữ liệu'), ('sending', 'Đang gửi'),
    ('sent', 'Meta đã nhận'), ('test_sent', 'Meta đã nhận — có mã kiểm tra'),
    ('retry', 'Chờ thử lại'), ('error', 'Gửi lỗi'),
    ('uncertain', 'Cần đối soát với Meta'), ('cancelled', 'Đã bỏ qua'),
    ('pos_sent', 'POS đã xác nhận — cần đối chiếu Meta'),
]


def dispatch_committed_events(dbname, event_ids):
    """A separate transaction ensures a rolled-back order can never reach Meta."""
    for event_id in event_ids:
        try:
            with registry(dbname).cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, {})
                env['dac.meta.conversion.event'].browse(event_id)._deliver()
                cr.commit()
        except Exception:
            # Never log HTTP exceptions: these can contain credentials or payloads.
            _logger.error('Meta conversion worker failed for event %s; cron will reconcile its state.', event_id)


class MetaConversionEvent(models.Model):
    _name = 'dac.meta.conversion.event'
    _description = 'Chuyển đổi Meta'
    _order = 'id desc'
    _rec_name = 'order_id'

    order_id = fields.Many2one('sale.order', required=True, ondelete='cascade', index=True, readonly=True)
    company_id = fields.Many2one('res.company', required=True, index=True, readonly=True)
    event_id = fields.Char(default=lambda self: str(uuid.uuid4()), required=True, readonly=True, copy=False)
    event_time = fields.Datetime(required=True, readonly=True)
    dataset_id = fields.Char(required=True, readonly=True)
    page_id = fields.Char(readonly=True)
    channel = fields.Selection([('messenger', 'Messenger'), ('chat', 'Tin nhắn')], required=True, readonly=True)
    test_code = fields.Char(readonly=True)
    state = fields.Selection(EVENT_STATES, default='pending', required=True, index=True, readonly=True)
    message = fields.Text(readonly=True)
    payload = fields.Json(readonly=True, groups='base.group_system')
    attempt_count = fields.Integer(readonly=True)
    next_attempt_at = fields.Datetime(default=fields.Datetime.now, index=True, readonly=True)
    last_attempt_at = fields.Datetime(readonly=True)
    sent_at = fields.Datetime(readonly=True)
    trace_id = fields.Char(readonly=True)
    api_log_ids = fields.One2many('dac.meta.api.log', 'event_id', string='Lịch sử gọi API', readonly=True)

    _sql_constraints = [
        ('order_unique', 'unique(order_id)', 'Mỗi đơn chỉ có một chuyển đổi Purchase.'),
        ('event_unique', 'unique(event_id)', 'Mã sự kiện Meta phải duy nhất.'),
    ]

    def _schedule_dispatch(self):
        if not self:
            return
        cron = self.env.ref('dac_meta_conversion.ir_cron_meta_conversion', raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger()
        dbname, event_ids = self.env.cr.dbname, self.ids
        self.env.cr.postcommit.add(lambda: dispatch_committed_events(dbname, event_ids))

    def _messenger_identity(self):
        self.ensure_one()
        order = self.order_id
        if order.meta_conversion_psid:
            psid = order.meta_conversion_psid.strip()
            if not re.fullmatch(r'[0-9]+', psid):
                return {}, _('PSID phải là ID số của khách trong Facebook Page đã cấu hình.')
            return {'page_id': self.page_id, 'page_scoped_user_id': psid}, ''
        conversation = order.conversation_id
        if not conversation:
            candidates = self.env['page.fm.conversation'].search([
                ('partner_id', '=', order.partner_id.id),
                ('conversation_type_fm', '=', 'INBOX'),
                ('platform_fm', '=ilike', 'facebook'),
                ('page_fm_page_id.page_fm_id_str', '=', self.page_id),
                ('sender_fm_id', '!=', False),
                ('is_internal_conversation', '=', False),
            ], limit=2)
            if len(candidates) == 1:
                conversation = candidates
        if not conversation:
            return {}, _('Chưa có PSID. Gắn đúng hội thoại Messenger vào đơn và đồng bộ Pancake, hoặc nhập PSID của khách trong đơn.')
        page_id = (conversation.conv_page_fm_id or conversation.page_fm_page_id.page_fm_id_str or '').strip()
        psid = (conversation.sender_fm_id or '').strip()
        if (page_id != self.page_id or (conversation.platform_fm or '').lower() != 'facebook'
                or conversation.conversation_type_fm != 'INBOX' or conversation.is_internal_conversation):
            return {}, _('Hội thoại phải là inbox Facebook của đúng Page ID đã cấu hình.')
        if conversation.partner_id and conversation.partner_id != order.partner_id:
            return {}, _('Khách hàng của hội thoại không khớp khách hàng trên đơn. Hãy kiểm tra lại liên kết.')
        if not re.fullmatch(r'[0-9]+', psid):
            return {}, _('Hội thoại chưa có PSID dạng số. Đồng bộ lại Pancake để lấy from.id của khách.')
        return {'page_id': page_id, 'page_scoped_user_id': psid}, ''

    def _contact_identity(self):
        self.ensure_one()
        partner = self.order_id.partner_id
        data = {}
        email = (partner.email or '').strip().lower()
        if email and re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
            data['em'] = [hashlib.sha256(email.encode('utf-8')).hexdigest()]
        for phone in (partner.mobile, partner.phone):
            raw_phone = (phone or '').strip()
            digits = re.sub(r'\D', '', raw_phone)
            if raw_phone.startswith('00'):
                digits = digits[2:]
            elif raw_phone.startswith('+'):
                pass
            elif partner.country_id.code in (False, 'VN'):
                if digits.startswith('0'):
                    digits = '84' + digits.lstrip('0')
                elif not digits.startswith('84'):
                    digits = ''
            else:
                # Avoid guessing country codes for local international numbers.
                digits = ''
            if 8 <= len(digits) <= 15 and not digits.startswith('0'):
                data['ph'] = [hashlib.sha256(digits.encode('ascii')).hexdigest()]
                break
        return data

    def _prepare_payload(self):
        self.ensure_one()
        order = self.order_id
        if not order.partner_id or order.amount_total <= 0 or not order.currency_id:
            return {}, _('Cần khách hàng và tổng tiền đơn hàng lớn hơn 0 trước khi gửi Purchase.')
        user_data = self._contact_identity()
        if self.channel == 'messenger':
            identity, reason = self._messenger_identity()
            if reason:
                return {}, reason
            user_data.update(identity)
        elif not user_data:
            return {}, _('Cần số điện thoại có mã quốc gia hoặc email hợp lệ để Meta so khớp khách hàng.')
        event = {
            'event_name': 'Purchase',
            'event_time': int(self.event_time.replace(tzinfo=timezone.utc).timestamp()),
            'event_id': self.event_id,
            'action_source': 'business_messaging' if self.channel == 'messenger' else 'chat',
            'user_data': user_data,
            'custom_data': {
                'currency': order.currency_id.name.upper(), 'value': order.amount_total,
                'order_id': '%s:%s' % (self.env['ir.config_parameter'].sudo().get_param('database.uuid'), order.id),
            },
        }
        if self.channel == 'messenger':
            event['messaging_channel'] = 'messenger'
        payload = {'data': [event]}
        if self.test_code:
            payload['test_event_code'] = self.test_code
        return payload, ''

    def _send_payload(self, company, api_log=None):
        """Ambiguous delivery must not be retried: messaging CAPI has no dedup."""
        self.ensure_one()
        outcome = self.env['dac.meta.api.log']._call(
            company, 'purchase', 'POST', self.dataset_id + '/events',
            payload=self.payload or {}, event=self, api_log=api_log,
        )
        if outcome.get('transport_error') == 'connect_timeout':
            return self._retry_result(_('Không thiết lập được kết nối đến Meta.'))
        if outcome.get('transport_error'):
            return {'state': 'uncertain', 'message': _('Mất kết nối khi gửi. Cần đối soát Events Manager trước khi gửi lại để tránh tính trùng.')}
        result = outcome['data']
        http_status = outcome['http_status']
        trace = str(result.get('fbtrace_id') or '')[:200]
        if http_status == 429:
            return dict(self._retry_result(_('Meta giới hạn tốc độ gửi.')), trace_id=trace)
        if http_status >= 500 or (200 <= http_status < 300 and not outcome['ok']):
            return {'state': 'uncertain', 'message': _('Chưa xác định Meta đã nhận sự kiện hay chưa. Đối soát Events Manager trước khi gửi lại.'), 'trace_id': trace}
        if outcome['ok']:
            return {
                'state': 'test_sent' if self.test_code else 'sent', 'sent_at': fields.Datetime.now(),
                'message': _('Meta đã nhận sự kiện có mã kiểm tra. Đối chiếu trong Test Events; mã kiểm tra không bảo đảm tách sự kiện khỏi đo lường quảng cáo.') if self.test_code else _('Meta đã nhận Purchase. Kiểm tra khả năng phân bổ về quảng cáo trong Events Manager / Ads Manager.'),
                'trace_id': trace,
            }
        error = result.get('error') if isinstance(result.get('error'), dict) else {}
        # Keep only structured error codes, never response text or access tokens.
        return {
            'state': 'error', 'trace_id': str(error.get('fbtrace_id') or trace)[:200],
            'message': _('Meta từ chối sự kiện: HTTP %(http)s, mã %(code)s, mã phụ %(subcode)s. Kiểm tra token, quyền gửi, Dataset và dữ liệu khách hàng.') % {
                'http': http_status, 'code': error.get('code', '—'), 'subcode': error.get('error_subcode', '—'),
            },
        }

    def _retry_result(self, message):
        if self.attempt_count >= 5:
            return {'state': 'error', 'message': message + ' ' + _('Đã hết 5 lần thử tự động.')}
        return {
            'state': 'retry', 'message': message,
            'next_attempt_at': fields.Datetime.now() + timedelta(minutes=min(2 ** self.attempt_count, 30)),
        }

    def _deliver(self):
        """Called only in a dedicated cursor, never inside the order transaction."""
        self.ensure_one()
        self.env.cr.execute('SELECT id FROM dac_meta_conversion_event WHERE id = %s FOR UPDATE SKIP LOCKED', [self.id])
        if not self.env.cr.fetchone():
            return
        self.invalidate_recordset()
        if self.state not in ('pending', 'waiting', 'retry') or self.next_attempt_at > fields.Datetime.now():
            return
        company = self.company_id.sudo()
        if not company.meta_conversion_enabled:
            return
        try:
            company._check_meta_configuration()
        except ValidationError:
            self.write({'state': 'error', 'message': _('Cấu hình Meta chưa hợp lệ. Quản trị viên cần kiểm tra khóa kết nối trong Cài đặt.')})
            return
        if self.order_id.state == 'cancel' or self.order_id.order_state_custom == 'cancel':
            self.write({'state': 'cancelled', 'message': _('Đơn đã hủy trước khi gửi Meta.')})
            return
        if self.event_time < fields.Datetime.now() - timedelta(days=7):
            self.write({'state': 'error', 'message': _('Sự kiện đã quá 7 ngày. Không đổi thời gian mua hàng để gửi lại.')})
            return
        if company.meta_conversion_dataset_id != self.dataset_id:
            self.write({'state': 'error', 'message': _('Dataset hiện tại khác Dataset của sự kiện. Khôi phục đúng cấu hình trước khi gửi lại.')})
            return
        payload, reason = (self.payload, '') if self.payload else self._prepare_payload()
        if reason:
            self.write({'state': 'waiting', 'message': reason, 'next_attempt_at': fields.Datetime.now() + timedelta(minutes=5)})
            return
        self.write({
            'state': 'sending', 'payload': payload, 'attempt_count': self.attempt_count + 1,
            'last_attempt_at': fields.Datetime.now(), 'message': _('Đang gửi Purchase đến Meta.'),
        })
        api_log = self.env['dac.meta.api.log']._start_call(
            company, 'purchase', 'POST', self.dataset_id + '/events', self.payload, self,
        )
        # A crash after this commit leaves "sending", which is reconciled as
        # uncertain instead of blindly resending an already accepted purchase.
        self.env.cr.commit()
        self.write(self._send_payload(company, api_log=api_log))

    @api.model
    def _cron_dispatch(self):
        stale = self.sudo().search([
            ('state', '=', 'sending'), ('last_attempt_at', '<', fields.Datetime.now() - timedelta(minutes=15)),
        ])
        stale.write({'state': 'uncertain', 'message': _('Lần gửi trước bị gián đoạn. Cần đối soát Meta để tránh gửi trùng.')})
        self.env['dac.meta.api.log'].sudo().search([
            ('event_id', 'in', stale.ids), ('state', '=', 'started'),
        ]).write({
            'state': 'uncertain', 'completed_at': fields.Datetime.now(),
            'summary': _('Tiến trình gửi bị gián đoạn; cần đối soát Meta trước khi gửi lại.'),
        })
        events = self.sudo().search([
            ('state', 'in', ('pending', 'retry', 'waiting')),
            ('next_attempt_at', '<=', fields.Datetime.now()),
            ('company_id.meta_conversion_enabled', '=', True),
        ], order='id', limit=10)
        dispatch_committed_events(self.env.cr.dbname, events.ids)
