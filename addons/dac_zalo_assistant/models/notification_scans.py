from datetime import timedelta

import pytz

from odoo import api, fields, models
from odoo.exceptions import AccessError

from .notifications import TASK_OPEN, fingerprint


class NotificationScans(models.Model):
    _inherit = 'dac.zalo.notification'

    @api.model
    def _managers(self, group='dac_erp.group_dac_erp_manager'):
        target = self.env.ref(group, raise_if_not_found=False)
        return self.env['res.users'].search([('active', '=', True), ('share', '=', False), ('groups_id', 'in', target.ids)]) if target else self.env['res.users']

    @api.model
    def _watch(self, name):
        model = self.env['dac.zalo.event.state']
        return model.search([('name', '=', name)], limit=1) or model.create({'name': name, 'value': {}})

    @api.model
    def _already_active(self, category, source, user, now, day_start):
        items = self.search([('category', '=', category), ('res_model', '=', source._name),
            ('res_id', '=', source.id), ('user_id', '=', user.id), ('state', 'in', ['pending', 'queued', 'sent'])])
        for item in items:
            if item.state == 'sent':
                if item.message_id.sent_at and item.message_id.sent_at >= day_start:
                    return True
            elif (not item.expires_at or item.expires_at > now) and item._valid():
                return True
        return False

    @api.model
    def _scan_tasks(self, config, now):
        domain = [('state', 'in', TASK_OPEN), ('assigned_user_id', '!=', False), ('id', '>', config.notification_scan_cursor)]
        tasks = self.env['dac.work.task'].search(domain, order='id', limit=250)
        if not tasks and config.notification_scan_cursor:
            tasks = self.env['dac.work.task'].search(domain[:-1], order='id', limit=250)
        config.notification_scan_cursor = tasks[-1].id if tasks else 0
        floor = max(config.notification_started_at or now, now - timedelta(hours=48))
        for task in tasks:
            payload = {'assignee': task.assigned_user_id.id, 'deadline': str(task.deadline or '')}
            if task.remind_at and floor <= task.remind_at <= now:
                self._emit('personal_reminder', task, task.assigned_user_id, ['remind', task.remind_at],
                    payload=dict(payload, remind_at=str(task.remind_at), summary='Đã đến giờ bạn đặt nhắc.'), expires_at=now + timedelta(days=1), urgent=task.priority == 'urgent')
            if not task.deadline:
                continue
            if now < task.deadline <= now + timedelta(hours=2):
                self._emit('deadline_warning', task, task.assigned_user_id, ['warning', task.deadline], payload=payload, expires_at=task.deadline, urgent=task.priority == 'urgent')
            elif floor <= task.deadline <= now:
                targets = task.assigned_user_id
                if task.deadline < now - timedelta(hours=24):
                    targets |= self._managers()
                    if task.order_id:
                        targets |= task.order_id.user_id
                for user in targets:
                    zone = pytz.timezone(self._timezone(user))
                    local_day = pytz.UTC.localize(now).astimezone(zone).date()
                    day_start = zone.localize(fields.Datetime.to_datetime(str(local_day))).astimezone(pytz.UTC).replace(tzinfo=None)
                    if self._already_active('task_overdue', task, user, now, day_start):
                        continue
                    self._emit('task_overdue', task, user, ['overdue', task.deadline, local_day],
                        payload=dict(payload, summary='Công việc đã quá hạn; vui lòng cập nhật tiến độ.'), expires_at=now + timedelta(days=1), urgent=task.priority == 'urgent')

    @api.model
    def _scan_orders(self, config, now):
        orders = self.env['sale.order'].search([('order_state_custom', 'in', ['delivery', 'installation']),
            ('id', '>', config.notification_delivery_cursor)], order='id', limit=250)
        if not orders and config.notification_delivery_cursor:
            orders = self.env['sale.order'].search([('order_state_custom', 'in', ['delivery', 'installation'])], order='id', limit=250)
        config.notification_delivery_cursor = orders[-1].id if orders else 0
        for order in orders:
            people = order.user_id | order.user_id_production | order.production_group_ids
            for user in people:
                day = pytz.UTC.localize(now).astimezone(pytz.timezone(self._timezone(user))).date()
                if not order.delivery_date or not day - timedelta(days=1) <= order.delivery_date <= day + timedelta(days=1):
                    continue
                zone = pytz.timezone(self._timezone(user))
                day_start = zone.localize(fields.Datetime.to_datetime(str(day))).astimezone(pytz.UTC).replace(tzinfo=None)
                if self._already_active('delivery', order, user, now, day_start) or self.search_count([('category', '=', 'delivery'), ('res_model', '=', 'sale.order'),
                    ('res_id', '=', order.id), ('user_id', '=', user.id), ('create_date', '>=', day_start)]):
                    continue
                self._emit('delivery', order, user, ['delivery', day, order.delivery_date, order.order_state_custom],
                    payload={'stage': order.order_state_custom, 'planned_date': str(order.delivery_date or '')}, expires_at=now + timedelta(days=1))

    @api.model
    def _financial_orders(self, user):
        allowed = user.has_group('base.group_system') or user.has_group('dac_erp.group_dac_erp_manager') or user.has_group('dac_erp.group_dac_erp_sale') or user.has_group('dac_erp.group_dac_erp_sale_all')
        model = self.env['sale.order'].with_user(user).sudo(False)
        if not allowed:
            return model.browse()
        domain = [('order_state_custom', 'in', ['deposit', 'production', 'delivery', 'installation', 'completed'])]
        if not (user.has_group('base.group_system') or user.has_group('dac_erp.group_dac_erp_manager')):
            domain.append(('user_id', '=', user.id))
        orders = model.search(domain, order='id desc')
        if not orders:
            return orders
        # Batch the same paid-invoice accounting used by DAC's computed totals;
        # avoid one invoice query per order and never silently omit older debts.
        invoices = self.env['account.move'].with_user(user).sudo(False)
        paid_domain = [('invoice_origin', 'in', orders.mapped('name')), ('move_type', '=', 'out_invoice'),
            ('payment_state', '=', 'paid'), ('state', '!=', 'cancel')]
        paid = {row['invoice_origin']: row['amount_total'] for row in invoices.read_group(paid_domain, ['invoice_origin', 'amount_total:sum'], ['invoice_origin'])}
        deposits = {row['invoice_origin']: row['amount_total'] for row in invoices.read_group(paid_domain + [('dac_deposit_invoice', '=', True)], ['invoice_origin', 'amount_total:sum'], ['invoice_origin'])}
        return orders.filtered(lambda order: (order.order_state_custom == 'deposit' and order.has_deposit
            and (order.deposit_amount if order.is_deposit_confirmed else deposits.get(order.name, 0)) <= 0)
            or max(order.amount_total - paid.get(order.name, 0), 0) > 0)

    @api.model
    def _scan_users(self, config, now):
        bindings = self.env['dac.zalo.binding'].search([('state', '=', 'linked'), ('user_id.active', '=', True),
            ('id', '>', config.notification_user_cursor)], order='id', limit=100)
        if not bindings and config.notification_user_cursor:
            bindings = self.env['dac.zalo.binding'].search([('state', '=', 'linked'), ('user_id.active', '=', True)], order='id', limit=100)
        config.notification_user_cursor = bindings[-1].id if bindings else 0
        for binding in bindings:
            user = binding.user_id
            pref = self._preference(user)
            zone = pytz.timezone(pref.timezone if pref else (user.tz or 'Asia/Ho_Chi_Minh'))
            local = pytz.UTC.localize(now).astimezone(zone)
            hour = local.hour + local.minute / 60
            for category, slot in [('digest_morning', pref.morning_hour if pref else 7.5), ('digest_evening', pref.evening_hour if pref else 16.5)]:
                if slot <= hour < min(slot + 2, 24):
                    self._emit(category, user, user, [category, local.date()], expires_at=now + timedelta(hours=3))
            if 7 <= hour < 19 and self._enabled(user, 'financial'):
                try:
                    key = ['financial-summary', local.date()]
                    event_key = 'financial:res.users:%s:%s:%s' % (user.id, user.id, fingerprint(key))
                    if not self.search_count([('event_key', '=', event_key)]) and self._financial_orders(user):
                        self._emit('financial', user, user, ['financial-summary', local.date()],
                            payload={'mode': 'summary'}, expires_at=now + timedelta(hours=12))
                except AccessError:
                    pass

    @api.model
    def _scan_hr(self, config, now):
        reviewers = self._managers('hr_attendance.group_hr_attendance_manager')
        for name in ('dac.attendance.leave', 'dac.attendance.amendment'):
            if name not in self.env:
                continue
            cursor = self._watch('cursor:' + name)
            after = (cursor.value or {}).get('after', 0)
            records = self.env[name].search([('write_date', '>=', config.notification_started_at or now), ('id', '>', after)], order='id', limit=250)
            if not records and after:
                records = self.env[name].search([('write_date', '>=', config.notification_started_at or now)], order='id', limit=250)
            cursor.value = {'after': records[-1].id if records else 0}
            for record in records:
                watch = self._watch('hr:%s:%s' % (name, record.id))
                before = watch.value or {}
                if before.get('state') == record.state:
                    continue
                revision = before.get('revision', 0) + 1
                watch.value = {'state': record.state, 'revision': revision}
                if record.state == 'pending':
                    self._emit('hr_requests', record, reviewers, ['hr', revision], payload={'hr_state': record.state})
                elif record.state in ('approved', 'refused', 'cancelled'):
                    self._emit('hr_results', record, record.employee_id.user_id, ['hr', revision], payload={'hr_state': record.state})

    @api.model
    def _health_problem(self, name, is_open, summary, admins):
        watch = self._watch('health:' + name)
        old = watch.value or {}
        revision = old.get('revision', 0) + (1 if bool(old.get('open')) != bool(is_open) else 0)
        watch.value = {'open': bool(is_open), 'revision': revision}
        config = self.env['dac.zalo.config']._config()
        model_id = self.env['ir.model']._get_id('dac.zalo.config')
        activities = self.env['mail.activity'].search([('res_model_id', '=', model_id), ('res_id', '=', config.id),
            ('summary', '=', '[Zalo] ' + name)])
        if not is_open:
            activities.unlink()
            return
        for admin in admins:
            if not self._enabled(admin, 'system_alerts'):
                continue
            if not activities.filtered(lambda activity: activity.user_id == admin):
                self.env['mail.activity'].create({'res_model_id': model_id, 'res_id': config.id, 'user_id': admin.id,
                    'activity_type_id': self.env.ref('mail.mail_activity_data_todo').id,
                    'summary': '[Zalo] ' + name, 'note': summary, 'date_deadline': fields.Date.context_today(config)})
        self._emit('system_alerts', watch, admins, ['health', name, revision], payload={'summary': summary}, urgent=True)

    @api.model
    def _scan_health(self, config, now):
        if config.notification_health_checked_at and now - config.notification_health_checked_at < timedelta(minutes=15):
            return
        config.notification_health_checked_at = now
        admins = self._managers('base.group_system')
        token_status = self.env['ir.config_parameter'].get_param('page_fm.token_check_status')
        self._health_problem('pancake', token_status in ('expired', 'invalid'), 'Token Pancake hết hạn hoặc không hợp lệ.', admins)
        try:
            status = config._bridge_request('/status')
            bad_bridge = status.get('state') != 'connected' or not config.assistant_uid or status.get('account_id') != config.assistant_uid
        except Exception:
            bad_bridge = True
        self._health_problem('connection', bad_bridge, 'Kết nối trợ lý Zalo bị gián đoạn; kiểm tra phiên và dịch vụ.', admins)
        failed = self.env['dac.zalo.message'].search_count([('state', 'in', ['failed', 'uncertain']),
            ('create_date', '>=', now - timedelta(days=1)), '|', ('notification_id', '=', False), ('notification_id.category', '!=', 'system_alerts')])
        self._health_problem('delivery', bool(failed), 'Có tin nhắn lỗi hoặc chưa xác nhận gửi trong 24 giờ qua.', admins)

    @api.model
    def _cron_scan(self):
        service = self._root()
        config = service.env['dac.zalo.config']._config()
        if not config.active or not config.notifications_enabled:
            return
        # A single scheduler instance owns scanning; transport has its own bounded cron.
        service.env.cr.execute('SELECT pg_try_advisory_xact_lock(%s, %s)', (73463, config.id))
        if not service.env.cr.fetchone()[0]:
            return
        now = fields.Datetime.now()
        for method in ('_scan_tasks', '_scan_orders', '_scan_users', '_scan_hr', '_scan_health'):
            try:
                with service.env.cr.savepoint():
                    getattr(service, method)(config, now)
                    previous = service.env['dac.zalo.event.state'].search([('name', '=', 'health:scanner' + method)], limit=1)
                    if previous and (previous.value or {}).get('open'):
                        service._health_problem('scanner' + method, False, '', service._managers('base.group_system'))
            except Exception:
                # Keep the other categories running; an admin can inspect this durable status.
                service._health_problem('scanner' + method, True, 'Một nhóm thông báo cần kiểm tra: ' + method,
                    service._managers('base.group_system'))
        service._cron_dispatch()
