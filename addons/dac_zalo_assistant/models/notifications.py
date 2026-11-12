import hashlib
import json
import logging
from datetime import datetime, timedelta
from urllib.parse import urlsplit

import pytz

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

_logger = logging.getLogger(__name__)
CATEGORIES = [
    ('order_created', 'Tạo đơn thành công'), ('order_updates', 'Trạng thái / phụ trách đơn'),
    ('task_assigned', 'Được giao việc'), ('task_updates', 'Tiến độ / trả lại công việc'),
    ('deadline_changed', 'Đổi hạn / yêu cầu quan trọng'), ('deadline_warning', 'Sắp đến hạn'),
    ('task_overdue', 'Quá hạn / báo quản lý'), ('personal_reminder', 'Nhắc việc cá nhân'),
    ('handover', 'Bàn giao thiết kế / sản xuất'), ('delivery', 'Giao hàng / lắp đặt'),
    ('financial', 'Thu cọc / công nợ'), ('mentions', 'Trao đổi nội bộ'),
    ('hr_requests', 'Nhân sự cần duyệt'), ('hr_results', 'Kết quả duyệt nhân sự'),
    ('digest_morning', 'Bản tin đầu ngày'), ('digest_evening', 'Bản tin cuối ngày'),
    ('system_alerts', 'Cảnh báo vận hành (admin)'),
]
TASK_OPEN = ['draft', 'in_progress']
ORDER_MODELS = ['sale.order', 'dac.work.task', 'dac.attendance.leave', 'dac.attendance.amendment', 'res.users', 'dac.zalo.event.state']


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:32]


def quiet_until(now, timezone, start, end):
    """Return naive UTC at the end of a local quiet window, including overnight/DST."""
    if start == end:
        return None
    zone = pytz.timezone(timezone)
    local = pytz.UTC.localize(now).astimezone(zone)
    hour = local.hour + local.minute / 60
    quiet = start <= hour < end if start < end else hour >= start or hour < end
    if not quiet:
        return None
    day = local.date() + timedelta(days=1 if start > end and hour >= start else 0)
    minute = int(round(end * 60))
    wall = datetime.combine(day, datetime.min.time()) + timedelta(minutes=minute)
    return zone.localize(wall, is_dst=False).astimezone(pytz.UTC).replace(tzinfo=None)


class ZaloPreference(models.Model):
    _name = 'dac.zalo.preference'
    _description = 'Tùy chọn thông báo Zalo của nhân viên'
    _rec_name = 'user_id'

    user_id = fields.Many2one('res.users', string='Nhân viên', required=True, default=lambda self: self.env.user, ondelete='cascade', index=True)
    notifications_enabled = fields.Boolean(string='Nhận thông báo chủ động', default=True)
    paused_until = fields.Datetime(string='Tạm dừng đến')
    timezone = fields.Selection(selection=lambda self: [(tz, tz) for tz in pytz.all_timezones], string='Múi giờ', default='Asia/Ho_Chi_Minh', required=True)
    quiet_enabled = fields.Boolean(string='Dùng giờ yên lặng', default=True)
    quiet_start = fields.Float(string='Yên lặng từ', default=19)
    quiet_end = fields.Float(string='Yên lặng đến', default=7)
    urgent_outside_hours = fields.Boolean(string='Cho phép tin khẩn ngoài giờ', default=False)
    morning_hour = fields.Float(string='Bản tin sáng lúc', default=7.5)
    evening_hour = fields.Float(string='Bản tin chiều lúc', default=16.5)
    order_created = fields.Boolean(string='Tạo đơn thành công', default=True)
    order_updates = fields.Boolean(string='Trạng thái / phụ trách đơn', default=True)
    task_assigned = fields.Boolean(string='Được giao việc', default=True)
    task_updates = fields.Boolean(string='Tiến độ / trả lại việc', default=True)
    deadline_changed = fields.Boolean(string='Đổi hạn / yêu cầu', default=True)
    deadline_warning = fields.Boolean(string='Sắp đến hạn', default=True)
    task_overdue = fields.Boolean(string='Quá hạn / báo quản lý', default=True)
    personal_reminder = fields.Boolean(string='Nhắc việc cá nhân', default=True)
    handover = fields.Boolean(string='Bàn giao thiết kế / sản xuất', default=True)
    delivery = fields.Boolean(string='Giao hàng / lắp đặt', default=True)
    financial = fields.Boolean(string='Thu cọc / công nợ', default=True)
    mentions = fields.Boolean(string='Trao đổi nội bộ', default=True)
    hr_requests = fields.Boolean(string='Đơn nhân sự cần duyệt', default=True)
    hr_results = fields.Boolean(string='Kết quả duyệt nhân sự', default=True)
    digest_morning = fields.Boolean(string='Bản tin đầu ngày', default=True)
    digest_evening = fields.Boolean(string='Bản tin cuối ngày', default=True)
    system_alerts = fields.Boolean(string='Cảnh báo vận hành (admin)', default=True)
    _sql_constraints = [('user_unique', 'unique(user_id)', 'Nhân viên đã có tùy chọn thông báo.')]

    @api.constrains('user_id')
    def _owner_only(self):
        if not self.env.su and not self.env.user.has_group('base.group_system') and any(p.user_id != self.env.user for p in self):
            raise AccessError(_('Bạn chỉ được thay đổi tùy chọn của mình.'))

    @api.constrains('quiet_start', 'quiet_end', 'morning_hour', 'evening_hour')
    def _hours(self):
        for pref in self:
            if any(not 0 <= pref[name] < 24 for name in ('quiet_start', 'quiet_end', 'morning_hour', 'evening_hour')):
                raise ValidationError(_('Giờ phải nằm trong khoảng 00:00–23:59.'))

    @api.model
    def action_my_preferences(self):
        pref = self.search([('user_id', '=', self.env.uid)], limit=1) or self.create({'user_id': self.env.uid})
        return {'type': 'ir.actions.act_window', 'name': _('Thông báo của tôi'), 'res_model': self._name,
            'res_id': pref.id, 'view_mode': 'form', 'target': 'current'}

    @api.model
    def _chat_command(self, binding, text):
        command = text.strip().lower().split()
        if not command or command[0] not in ('/bat-thongbao', '/tat-thongbao', '/tam-dung', '/tiep-tuc'):
            return False
        model = self.with_user(binding.user_id).sudo(False)
        pref = model.search([('user_id', '=', binding.user_id.id)], limit=1) or model.create({'user_id': binding.user_id.id})
        if command[0] == '/tat-thongbao':
            pref.notifications_enabled = False
            return 'Đã tắt thông báo chủ động. Hội thoại và /ai vẫn hoạt động. Dùng /bat-thongbao để bật lại.'
        if command[0] in ('/bat-thongbao', '/tiep-tuc'):
            pref.write({'notifications_enabled': True, 'paused_until': False})
            return 'Đã bật lại thông báo theo các nhóm bạn chọn trong Odoo.'
        if len(command) != 2 or not command[1].isdigit() or not 1 <= int(command[1]) <= 10080:
            return 'Dùng /tam-dung <số phút>, từ 1 đến 10080. Ví dụ: /tam-dung 60.'
        pref.paused_until = fields.Datetime.now() + timedelta(minutes=int(command[1]))
        return 'Đã tạm dừng thông báo trong %s phút. Dùng /tiep-tuc để nhận lại.' % command[1]


class ZaloNotificationConfig(models.Model):
    _name = 'dac.zalo.config'
    _inherit = ['dac.zalo.config', 'mail.thread', 'mail.activity.mixin']

    notifications_enabled = fields.Boolean(string='Bật thông báo nghiệp vụ', default=True)
    notify_order_created = fields.Boolean(string='Tạo đơn thành công', default=True)
    notify_order_updates = fields.Boolean(string='Trạng thái / phụ trách đơn', default=True)
    notify_task_assigned = fields.Boolean(string='Được giao việc', default=True)
    notify_task_updates = fields.Boolean(string='Tiến độ công việc', default=True)
    notify_deadline_changed = fields.Boolean(string='Đổi hạn / yêu cầu', default=True)
    notify_deadline_warning = fields.Boolean(string='Sắp đến hạn', default=True)
    notify_task_overdue = fields.Boolean(string='Quá hạn / báo quản lý', default=True)
    notify_personal_reminder = fields.Boolean(string='Nhắc việc cá nhân', default=True)
    notify_handover = fields.Boolean(string='Bàn giao', default=True)
    notify_delivery = fields.Boolean(string='Giao hàng / lắp đặt', default=True)
    notify_financial = fields.Boolean(string='Thu cọc / công nợ', default=True)
    notify_mentions = fields.Boolean(string='Trao đổi nội bộ', default=True)
    notify_hr_requests = fields.Boolean(string='Nhân sự cần duyệt', default=True)
    notify_hr_results = fields.Boolean(string='Kết quả duyệt nhân sự', default=True)
    notify_digest_morning = fields.Boolean(string='Bản tin đầu ngày', default=True)
    notify_digest_evening = fields.Boolean(string='Bản tin cuối ngày', default=True)
    notify_system_alerts = fields.Boolean(string='Cảnh báo vận hành', default=True)
    notification_started_at = fields.Datetime(string='Mốc bắt đầu theo dõi', default=fields.Datetime.now, readonly=True)
    notification_health_checked_at = fields.Datetime(readonly=True, copy=False)
    notification_scan_cursor = fields.Integer(default=0, readonly=True)
    notification_user_cursor = fields.Integer(default=0, readonly=True)
    notification_delivery_cursor = fields.Integer(default=0, readonly=True)


class ZaloEventType(models.Model):
    """Registry of business events that can be delivered through Zalo.

    ``dac_push`` seeds this registry during module upgrades.  Keep the model in
    the Zalo module (which is loaded before ``dac_push``) so its data files do
    not depend on a model that has not been registered yet.
    """
    _name = 'dac.zalo.event.type'
    _description = 'Loại sự kiện Zalo'
    _order = 'sequence, name, id'

    name = fields.Char(string='Tên sự kiện', required=True, translate=True)
    code = fields.Char(string='Mã sự kiện', required=True, index=True)
    group_name = fields.Char(string='Nhóm sự kiện', index=True)
    description = fields.Text(string='Mô tả', translate=True)
    sequence = fields.Integer(string='Thứ tự', default=10)
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ('code_unique', 'unique(code)', 'Mã sự kiện Zalo phải là duy nhất.'),
    ]


class ZaloEventState(models.Model):
    _name = 'dac.zalo.event.state'
    _description = 'Mốc theo dõi sự kiện trợ lý Zalo'

    name = fields.Char(required=True, index=True)
    value = fields.Json(groups='base.group_system')
    _sql_constraints = [('name_unique', 'unique(name)', 'Mốc sự kiện đã tồn tại.')]


class ZaloNotification(models.Model):
    _name = 'dac.zalo.notification'
    _description = 'Thông báo nghiệp vụ qua Zalo'
    _order = 'id desc'
    _rec_name = 'category'

    user_id = fields.Many2one('res.users', string='Người nhận', required=True, ondelete='cascade', index=True)
    category = fields.Selection(CATEGORIES, string='Nhóm', required=True, index=True)
    event_key = fields.Char(required=True, index=True, readonly=True)
    res_model = fields.Char(string='Nguồn', required=True, readonly=True, index=True)
    res_id = fields.Integer(string='Mã bản ghi', required=True, readonly=True, index=True)
    payload = fields.Json(readonly=True)
    state = fields.Selection([('pending', 'Chờ xử lý'), ('queued', 'Chờ gửi'), ('sent', 'Đã gửi'),
        ('cancelled', 'Đã hủy'), ('failed', 'Lỗi'), ('uncertain', 'Cần kiểm tra gửi')], default='pending', required=True, index=True)
    message_id = fields.Many2one('dac.zalo.message', string='Tin nhắn', readonly=True, ondelete='set null')
    expires_at = fields.Datetime(string='Hết hiệu lực', readonly=True)
    urgent = fields.Boolean(readonly=True)
    error_message = fields.Char(string='Ghi chú', readonly=True)
    _sql_constraints = [('event_unique', 'unique(event_key)', 'Thông báo đã được ghi nhận.')]

    @api.model
    def _root(self):
        # Private, server-only outbox service: public ORM create stays denied to employees.
        # Hooks never change business records or inherit an administrator's read scope for the recipient.
        return self.with_user(SUPERUSER_ID)

    @api.model
    def _preference(self, user):
        return self.env['dac.zalo.preference'].search([('user_id', '=', user.id)], limit=1)

    @api.model
    def _timezone(self, user):
        pref = self._preference(user)
        return pref.timezone if pref else (user.tz or 'Asia/Ho_Chi_Minh')

    @api.model
    def _enabled(self, user, category):
        if not user or not user.active or user.share:
            return False
        config = self.env['dac.zalo.config']._config()
        if not config.notifications_enabled or not config['notify_' + category]:
            return False
        pref = self._preference(user)
        return not pref or (pref.notifications_enabled and pref[category])

    @api.model
    def _emit(self, category, source, users, key, payload=None, expires_at=None, urgent=False):
        service = self._root()
        config = service.env['dac.zalo.config']._config()
        if not config.active or not config.notifications_enabled or self.env.context.get('dac_zalo_skip_notifications'):
            return service.browse()
        if category not in dict(CATEGORIES) or source._name not in ORDER_MODELS or not source.id:
            return service.browse()
        payload = payload or {}
        emitted = service.browse()
        for user in users:
            if not service._enabled(user, category):
                continue
            binding = service.env['dac.zalo.binding'].search([('user_id', '=', user.id), ('state', '=', 'linked')], limit=1)
            if not binding:
                continue
            try:
                source.with_user(user).sudo(False).check_access('read')
            except AccessError:
                continue
            event_key = '%s:%s:%s:%s:%s' % (category, source._name, source.id, user.id, fingerprint(key))
            if service.search_count([('event_key', '=', event_key)]):
                continue
            # A savepoint keeps an outbox collision/failure from aborting the order's transaction.
            try:
                with service.env.cr.savepoint():
                    item = service.create({'category': category, 'res_model': source._name, 'res_id': source.id,
                        'user_id': user.id, 'event_key': event_key, 'payload': payload,
                        'expires_at': expires_at or (fields.Datetime.now() + timedelta(days=2)), 'urgent': urgent})
                    emitted |= item
            except Exception:
                _logger.warning('Zalo outbox could not enqueue category=%s model=%s id=%s', category, source._name, source.id)
        return emitted

    def _source(self):
        self.ensure_one()
        if self.res_model not in ORDER_MODELS or self.res_model not in self.env:
            return False
        record = self.env[self.res_model].with_user(self.user_id).sudo(False).browse(self.res_id).exists()
        if not record:
            return False
        try:
            record.check_access('read')
            return record
        except AccessError:
            return False

    def _is_manager(self):
        return self.user_id.has_group('dac_erp.group_dac_erp_manager') or self.user_id.has_group('base.group_system')

    def _valid(self):
        self.ensure_one()
        if self.expires_at and self.expires_at <= fields.Datetime.now():
            return False
        source = self._source()
        if not source:
            return False
        payload = self.payload or {}
        user = self.user_id
        if source._name == 'sale.order':
            participants = source.user_id | source.user_id_design | source.user_id_production | source.production_group_ids
            if user not in participants and not self._is_manager() and self.category != 'mentions':
                return False
            if self.category == 'order_created':
                return source.user_id == user and source.order_state_custom != 'cancel'
            if self.category == 'financial':
                if not (source.user_id == user or self._is_manager()) or not (self._is_manager() or user.has_group('dac_erp.group_dac_erp_sale') or user.has_group('dac_erp.group_dac_erp_sale_all')):
                    return False
                if source.order_state_custom == 'cancel':
                    return False
                return (source.order_state_custom == 'deposit' and source.has_deposit and source.deposit_paid_display <= 0) if payload.get('mode') == 'deposit' else source.outstanding_amount_display > 0
            if self.category == 'delivery':
                if source.order_state_custom not in ('delivery', 'installation') or (payload.get('stage') and source.order_state_custom != payload['stage']):
                    return False
                if 'planned_date' not in payload:
                    return True
                return not (source.is_delivery_confirmed if source.order_state_custom == 'delivery' else source.is_installation_confirmed) and str(source.delivery_date or '') == payload['planned_date']
            if source.order_state_custom == 'cancel' and payload.get('stage') != 'cancel':
                return False
            if payload.get('stage') and source.order_state_custom != payload['stage']:
                return False
            for key in ('design_deadline', 'production_deadline', 'design_done', 'production_done'):
                if key in payload and str(source[key] or '') != str(payload[key] or ''):
                    return False
            if payload.get('assignment'):
                return source.order_state_custom != 'completed' and user in source.user_id_design | source.user_id_production | source.production_group_ids
            if self.category == 'handover' and source.order_state_custom == 'completed':
                return False
        elif source._name == 'dac.work.task':
            allowed = source.assigned_user_id == user or (source.order_id and source.order_id.user_id == user) or self._is_manager()
            if not allowed:
                return False
            if self.category in ('task_assigned', 'deadline_changed', 'deadline_warning', 'task_overdue', 'personal_reminder'):
                if source.state not in TASK_OPEN:
                    return False
                sale_escalation = (self.category == 'task_overdue' and source.order_id
                    and source.order_id.user_id == user and source.deadline
                    and source.deadline < fields.Datetime.now() - timedelta(hours=24))
                if not self._is_manager() and source.assigned_user_id != user and not sale_escalation:
                    return False
            if 'assignee' in payload and source.assigned_user_id.id != payload['assignee']:
                return False
            if 'deadline' in payload and str(source.deadline or '') != str(payload['deadline'] or ''):
                return False
            if 'remind_at' in payload and str(source.remind_at or '') != str(payload['remind_at'] or ''):
                return False
            if 'task_state' in payload and source.state != payload['task_state']:
                return False
            if self.category == 'deadline_warning' and (not source.deadline or source.deadline <= fields.Datetime.now()):
                return False
        elif source._name.startswith('dac.attendance.'):
            if source.state != payload.get('hr_state'):
                return False
            if self.category == 'hr_requests':
                return source.state == 'pending' and user.has_group('hr_attendance.group_hr_attendance_manager')
            return source.employee_id.user_id == user
        elif source._name == 'res.users':
            return source.id == user.id and (bool(self._financial_orders(user)) if self.category == 'financial' else True)
        elif source._name == 'dac.zalo.event.state':
            return user.has_group('base.group_system') and (source.value or {}).get('open')
        return True

    def _delay(self):
        self.ensure_one()
        pref = self._preference(self.user_id)
        now = fields.Datetime.now()
        delayed = pref.paused_until if pref and pref.paused_until and pref.paused_until > now else None
        if pref and pref.quiet_enabled and not (self.urgent and pref.urgent_outside_hours):
            quiet = quiet_until(now, pref.timezone, pref.quiet_start, pref.quiet_end)
        elif not pref:
            quiet = quiet_until(now, self.user_id.tz or 'Asia/Ho_Chi_Minh', 19, 7)
        else:
            quiet = None
        return max(value for value in (delayed, quiet) if value) if delayed or quiet else None

    def _link(self):
        base = self.env['ir.config_parameter'].get_param('web.base.url') or ''
        # Existing production base.url may still use HTTP; public deep links should use HTTPS.
        if urlsplit(base).hostname == 'crm.duyan.vn':
            base = base.replace('http://', 'https://', 1)
        if self.res_model == 'res.users':
            return base.rstrip('/') + ('/web#action=dac_erp.dac_sale_order_custom_action' if self.category == 'financial' else '/web#action=dac_erp.action_dac_work_task')
        if self.res_model == 'dac.zalo.event.state':
            return base.rstrip('/') + '/web#action=dac_zalo_assistant.config_action'
        return '%s/web#id=%s&model=%s&view_type=form' % (base.rstrip('/'), self.res_id, self.res_model)

    def _render(self):
        self.ensure_one()
        record = self._source()
        payload = self.payload or {}
        labels = dict(CATEGORIES)
        if record._name == 'sale.order':
            code = record.order_number or record.name
            if self.category == 'order_created':
                text = ('Bạn đã tạo đơn %s thành công.' % code) if payload.get('creator_id') == self.user_id.id else ('Đơn %s đã được tạo thành công và giao cho bạn phụ trách.' % code)
            elif self.category == 'financial':
                text = 'Nhắc thu tiền cọc cho đơn %s.' % code if payload.get('mode') == 'deposit' else 'Đơn %s còn khoản phải thu. Vui lòng mở Odoo kiểm tra công nợ.' % code
            elif self.category == 'delivery':
                text = ('Đơn %s: %s' % (code, payload['summary'])) if payload.get('summary') else ('Đơn %s cần %s. Vui lòng kiểm tra ngày hẹn và xác nhận thông tin.' % (code, 'giao hàng' if record.order_state_custom == 'delivery' else 'thi công / lắp đặt'))
            else:
                text = '%s — đơn %s. %s' % (labels[self.category], code, payload.get('summary', 'Vui lòng mở đơn để xem thay đổi.'))
        elif record._name == 'dac.work.task':
            zone = pytz.timezone(self._timezone(self.user_id))
            deadline = pytz.UTC.localize(record.deadline).astimezone(zone).strftime('%d/%m/%Y %H:%M') if record.deadline else 'chưa có hạn'
            text = '%s: %s. Hạn: %s. %s' % (labels[self.category], record.name, deadline, payload.get('summary', ''))
        elif record._name.startswith('dac.attendance.'):
            kind = 'nghỉ phép' if record._name.endswith('.leave') else 'điều chỉnh chấm công'
            dates = '%s → %s' % (record.date_from, record.date_to) if record._name.endswith('.leave') else str(record.target_date)
            text = ('%s gửi đơn %s (%s) cần bạn duyệt; mở Odoo để xem chi tiết.' % (record.employee_id.name, kind, dates)) if self.category == 'hr_requests' else ('Đơn %s của bạn (%s): %s.' % (kind, dates, dict(record._fields['state'].selection).get(record.state, record.state)))
        elif record._name == 'res.users':
            if self.category == 'financial':
                orders = self._financial_orders(self.user_id)
                return ('Nhắc thu cọc / công nợ: %s đơn cần kiểm tra.\n%s\n%s' %
                    (len(orders), '\n'.join('- ' + (o.order_number or o.name) for o in orders[:5]), self._link()))[:12000]
            tasks = self.env['dac.work.task'].with_user(self.user_id).sudo(False)
            domain = [('state', 'in', TASK_OPEN)]
            if not self._is_manager():
                domain.append(('assigned_user_id', '=', self.user_id.id))
            due = tasks.search(domain, order='deadline asc, id', limit=10)
            lines = ['- %s' % t.name for t in due]
            text = '%s: %s công việc đang mở%s.\n%s' % (labels[self.category], tasks.search_count(domain), ' trong phạm vi quản lý' if self._is_manager() else ' của bạn', '\n'.join(lines) or 'Không có công việc đang mở.')
        else:
            text = 'Trợ lý DAC cần kiểm tra: %s. Mở cấu hình trong Odoo để xử lý.' % (payload.get('summary') or 'tích hợp gặp sự cố')
        return (text + '\n' + self._link())[:12000]

    @api.model
    def _cron_dispatch(self):
        service = self._root()
        config = service.env['dac.zalo.config']._config()
        if not config.active:
            return
        for item in service.search([('state', '=', 'pending')], order='id', limit=200):
            try:
                with service.env.cr.savepoint():
                    if not config.notifications_enabled or not service._enabled(item.user_id, item.category) or not item._valid():
                        item.write({'state': 'cancelled', 'error_message': 'Đã tắt hoặc thông tin không còn phù hợp.'})
                        continue
                    binding = service.env['dac.zalo.binding'].search([('user_id', '=', item.user_id.id), ('state', '=', 'linked')], limit=1)
                    if not binding:
                        item.state = 'cancelled'
                        continue
                    conversation = service.env['dac.zalo.conversation'].search([('binding_id', '=', binding.id), ('zalo_uid', '=', binding.zalo_uid)], limit=1)
                    if not conversation:
                        conversation = service.env['dac.zalo.conversation'].create({'binding_id': binding.id, 'zalo_uid': binding.zalo_uid})
                    message = conversation._queue(item._render(), 'notification:%s' % item.id, kind='notification')
                    message.write({'notification_id': item.id, 'next_attempt_at': item._delay() or False})
                    item.write({'message_id': message.id, 'state': 'queued'})
            except (AccessError, UserError):
                item.write({'state': 'cancelled', 'error_message': 'Không còn quyền xem thông tin nguồn.'})
            except Exception:
                item.write({'state': 'failed', 'error_message': 'Không tạo được thông báo; kiểm tra nguồn trong Odoo.'})


class ZaloNotificationMessage(models.Model):
    _inherit = 'dac.zalo.message'

    notification_id = fields.Many2one('dac.zalo.notification', readonly=True, index=True, ondelete='set null')
    state = fields.Selection(selection_add=[('cancelled', 'Đã hủy')], ondelete={'cancelled': 'set default'})

    def _bridge_request_id(self):
        self.ensure_one()
        # Stable across a dispatcher transaction rollback/re-created transport row.
        return 'notification:' + self.notification_id.event_key if self.notification_id else super()._bridge_request_id()

    def _send(self, config):
        item = self.notification_id
        if item:
            if not config.notifications_enabled or not item._enabled(self.user_id, item.category) or not item._valid():
                self.write({'state': 'cancelled', 'error_message': 'Thông báo đã tắt hoặc không còn phù hợp.'})
                item.state = 'cancelled'
                return
            delay = item._delay()
            if delay:
                self.next_attempt_at = delay
                return
            # Render again after checking access: queued financial/HR content never bypasses current rights.
            self.text = item._render()
        result = super()._send(config)
        if item:
            item.state = self.state if self.state in ('sent', 'failed', 'uncertain', 'cancelled') else 'queued'
        return result

    @api.model
    def _cron_process(self):
        self.env['dac.zalo.notification']._cron_dispatch()
        result = super()._cron_process()
        # Existing transport cancels delivery when the employee unlinks/deactivates.
        for item in self.env['dac.zalo.notification'].search([('state', '=', 'queued'), ('message_id.state', 'in', ['failed', 'cancelled', 'uncertain', 'sent'])], limit=200):
            item.state = item.message_id.state
        return result
