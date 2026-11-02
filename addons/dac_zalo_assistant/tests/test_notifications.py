from datetime import datetime, timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user

from ..models.notifications import quiet_until


NOW = datetime(2026, 11, 2, 3, 0)  # 10:00 Vietnam, business hours.


@tagged('post_install', '-at_install')
class TestZaloNotifications(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sale = new_test_user(cls.env, login='notify_sale', groups='base.group_user,dac_erp.group_dac_erp_sale')
        cls.other = new_test_user(cls.env, login='notify_other', groups='base.group_user,dac_erp.group_dac_erp_sale')
        cls.designer = new_test_user(cls.env, login='notify_design', groups='base.group_user,dac_erp.group_dac_erp_design')
        cls.production = new_test_user(cls.env, login='notify_production', groups='base.group_user,dac_erp.group_dac_erp_production')
        cls.manager = new_test_user(cls.env, login='notify_manager', groups='base.group_user,dac_erp.group_dac_erp_manager')
        cls.admin = new_test_user(cls.env, login='notify_admin', groups='base.group_user,base.group_system')
        cls.hr_manager = new_test_user(cls.env, login='notify_hr', groups='base.group_user,hr_attendance.group_hr_attendance_manager') if 'dac.attendance.leave' in cls.env else cls.admin
        cls.users = cls.sale | cls.other | cls.designer | cls.production | cls.manager | cls.admin | cls.hr_manager
        cls.config = cls.env.ref('dac_zalo_assistant.config_default')
        cls.config.write({'active': True, 'notifications_enabled': True,
            'bridge_token': 'test-secret-over-32-characters-long-for-zalo', 'assistant_uid': 'bot-test',
            'notification_started_at': NOW - timedelta(days=2)})
        cls.env['dac.zalo.binding'].create([{'user_id': user.id, 'zalo_uid': 'notify-%s' % user.id,
            'state': 'linked', 'linked_at': NOW} for user in cls.users])
        cls.env['dac.zalo.preference'].create([{'user_id': user.id, 'quiet_enabled': False} for user in cls.users])
        cls.partner = cls.env['res.partner'].create({'name': 'Notification test customer'})
        cls.jobs = cls.env['dac.zalo.notification']

    def setUp(self):
        super().setUp()
        self.clock = patch.object(fields.Datetime, 'now', return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def pref(self, user=None):
        return self.env['dac.zalo.preference'].search([('user_id', '=', (user or self.sale).id)])

    def order(self, **kwargs):
        return self.env['sale.order'].with_user(self.sale).create(dict({'partner_id': self.partner.id,
            'user_id': self.sale.id, 'fulfillment_method': 'delivery'}, **kwargs))

    def task(self, user=None, **kwargs):
        return self.env['dac.work.task'].create(dict({'name': 'Notification task', 'assigned_user_id': (user or self.sale).id,
            'is_personal_reminder': True, 'deadline': NOW + timedelta(hours=1)}, **kwargs))

    def jobs_for(self, category, source=None, user=None):
        domain = [('category', '=', category), ('user_id', 'in', self.users.ids)]
        if source:
            domain += [('res_model', '=', source._name), ('res_id', '=', source.id)]
        if user:
            domain.append(('user_id', '=', user.id))
        return self.jobs.search(domain)

    def test_sale_created_confirmation_exact_message(self):
        order = self.order()
        jobs = self.jobs_for('order_created', order)
        self.assertEqual(jobs.user_id, self.sale)
        self.assertEqual(len(jobs), 1)
        self.assertIn('Bạn đã tạo đơn %s thành công.' % order.order_number, jobs._render())
        self.assertEqual(jobs.state, 'pending')

    def test_manager_created_order_does_not_claim_sale_was_creator(self):
        order = self.env['sale.order'].create({'partner_id': self.partner.id, 'user_id': self.sale.id})
        text = self.jobs_for('order_created', order)._render()
        self.assertIn('giao cho bạn phụ trách', text)
        self.assertNotIn('Bạn đã tạo', text)

    def test_batch_create_routes_each_sale_and_no_external_calls(self):
        with patch.object(type(self.config), '_bridge_request', side_effect=AssertionError('must not send inside create')):
            orders = self.env['sale.order'].create([{'partner_id': self.partner.id, 'user_id': user.id} for user in (self.sale, self.other)])
        self.assertEqual(set(self.jobs_for('order_created').mapped('user_id').ids), {self.sale.id, self.other.id})
        self.assertEqual(len(orders), 2)

    def test_order_and_notification_rollback_together(self):
        before = self.jobs.search_count([])
        try:
            with self.env.cr.savepoint():
                self.order()
                raise RuntimeError('simulate business transaction rollback')
        except RuntimeError:
            pass
        self.assertEqual(self.jobs.search_count([]), before)

    def test_notification_failure_does_not_break_order_creation(self):
        with patch.object(type(self.jobs), '_emit', side_effect=RuntimeError('outbox failure')):
            order = self.order()
        self.assertTrue(order.exists())

    def test_transport_request_identity_survives_dispatch_rollback(self):
        order = self.order()
        job = self.jobs_for('order_created', order)
        old_id = request_id = None
        try:
            with self.env.cr.savepoint():
                self.jobs._cron_dispatch()
                old_id = job.message_id.id
                request_id = job.message_id._bridge_request_id()
                raise RuntimeError('worker crashed before committing its transport row')
        except RuntimeError:
            pass
        self.jobs._cron_dispatch()
        self.assertNotEqual(job.message_id.id, old_id)
        self.assertEqual(job.message_id._bridge_request_id(), request_id)

    def test_order_confirmation_can_be_disabled_individually(self):
        self.pref().order_created = False
        order = self.order()
        self.assertFalse(self.jobs_for('order_created', order))

    def test_global_notification_switch(self):
        before = self.jobs.search_count([])
        self.config.notifications_enabled = False
        self.order()
        self.assertEqual(self.jobs.search_count([]), before)

    def test_admin_category_switch_cannot_be_overridden_by_employee(self):
        self.config.notify_order_created = False
        self.pref().order_created = True
        order = self.order()
        self.assertFalse(self.jobs_for('order_created', order))

    def test_blocked_task_cannot_emit_success_notification(self):
        task = self.task(state='in_progress', is_blocked=True)
        before = self.jobs.search_count([])
        with self.assertRaises(ValidationError):
            with self.env.cr.savepoint():
                task.write({'state': 'done'})
        self.assertEqual(task.state, 'in_progress')
        self.assertEqual(self.jobs.search_count([]), before)

    def test_preferences_cannot_be_forged_for_another_employee(self):
        own = self.pref().with_user(self.sale)
        with self.assertRaises(AccessError):
            with self.env.cr.savepoint():
                own.user_id = self.other
        with self.assertRaises(AccessError):
            self.pref(self.other).with_user(self.sale).write({'notifications_enabled': False})
        with self.assertRaises(AccessError):
            self.jobs.with_user(self.sale).create({'user_id': self.other.id, 'category': 'order_created',
                'res_model': 'res.users', 'res_id': self.other.id, 'event_key': 'forged'})

    def test_queued_order_reassignment_cancels_old_owner(self):
        order = self.order()
        job = self.jobs_for('order_created', order)
        self.jobs._cron_dispatch()
        order.with_user(self.env.user).write({'user_id': self.other.id})
        with patch.object(type(self.config), '_bridge_request', side_effect=AssertionError('must not send stale ownership')):
            job.message_id._send(self.config)
        self.assertEqual(job.state, 'cancelled')

    def test_preference_change_cancels_already_queued_message(self):
        order = self.order()
        self.jobs._cron_dispatch()
        job = self.jobs_for('order_created', order)
        self.pref().notifications_enabled = False
        with patch.object(type(self.config), '_bridge_request', side_effect=AssertionError('must not send after opt-out')):
            job.message_id._send(self.config)
        self.assertEqual(job.message_id.state, 'cancelled')

    def test_quiet_hours_and_pause_defer_without_sending(self):
        self.pref().write({'quiet_enabled': True, 'quiet_start': 9, 'quiet_end': 11})
        order = self.order()
        self.jobs._cron_dispatch()
        job = self.jobs_for('order_created', order)
        self.assertEqual(job.message_id.next_attempt_at, NOW + timedelta(hours=1))
        with patch.object(type(self.config), '_bridge_request', side_effect=AssertionError('quiet hours')):
            job.message_id._send(self.config)
        self.assertEqual(job.state, 'queued')
        self.pref().paused_until = NOW + timedelta(hours=3)
        self.assertEqual(job._delay(), NOW + timedelta(hours=3))

    def test_quiet_window_crosses_midnight_and_dst(self):
        self.assertEqual(quiet_until(datetime(2026, 11, 2, 14), 'Asia/Ho_Chi_Minh', 19, 7), datetime(2026, 11, 3, 0))
        self.assertIsNone(quiet_until(NOW, 'Asia/Ho_Chi_Minh', 19, 7))
        self.assertEqual(quiet_until(datetime(2026, 11, 1, 5, 30), 'America/New_York', 22, 7), datetime(2026, 11, 1, 12))

    def test_urgent_bypass_requires_explicit_employee_choice(self):
        self.pref().write({'quiet_enabled': True, 'quiet_start': 9, 'quiet_end': 11})
        task = self.task(priority='urgent')
        item = self.jobs_for('task_assigned', task, self.sale)
        self.assertTrue(item.urgent)
        self.assertEqual(item._delay(), NOW + timedelta(hours=1))
        self.pref().urgent_outside_hours = True
        self.assertIsNone(item._delay())
        self.pref().notifications_enabled = False
        self.assertFalse(self.jobs._enabled(self.sale, 'task_assigned'))

    def test_task_times_use_notification_timezone_not_profile_default(self):
        self.sale.tz = 'UTC'
        self.pref().timezone = 'Asia/Ho_Chi_Minh'
        task = self.task(deadline=NOW + timedelta(hours=1))
        self.assertIn('11:00', self.jobs_for('task_assigned', task, self.sale)._render())

    def test_presentation_only_writes_do_not_send_workflow_notifications(self):
        order = self.order()
        count = self.jobs.search_count([])
        order.write({'workflow_review_state': 'quotation'})
        order.write({'list_note': 'Only a list annotation'})
        self.assertEqual(self.jobs.search_count([]), count)

    def test_returned_create_record_still_tracks_subsequent_writes(self):
        order = self.order()
        self.assertFalse(order.env.context.get('dac_zalo_order_creating'))
        order.write({'user_id_design': self.designer.id})
        task = self.env['dac.work.task'].search([('order_id', '=', order.id), ('task_type', '=', 'design')], limit=1)
        self.assertEqual(len(self.jobs_for('task_assigned', task, self.designer)), 1)
        count = self.jobs.search_count([])
        order.write({'user_id_design': self.designer.id})
        self.assertEqual(self.jobs.search_count([]), count)

    def test_task_reassignment_and_completion_invalidate_old_reminders(self):
        task = self.task()
        self.jobs._scan_tasks(self.config, NOW)
        warning = self.jobs_for('deadline_warning', task, self.sale)
        self.jobs._cron_dispatch()
        task.assigned_user_id = self.other
        self.assertFalse(warning._valid())
        task.state = 'done'
        self.assertFalse(warning._valid())
        self.assertTrue(self.jobs_for('task_updates', task))

    def test_deadline_changes_cancel_queued_warning_and_reissue_once(self):
        task = self.task()
        self.jobs._scan_tasks(self.config, NOW)
        old = self.jobs_for('deadline_warning', task)
        task.deadline = NOW + timedelta(minutes=90)
        self.config.notification_scan_cursor = 0
        self.jobs._scan_tasks(self.config, NOW)
        self.assertFalse(old._valid())
        self.assertEqual(len(self.jobs_for('deadline_warning', task)), 2)
        self.config.notification_scan_cursor = 0
        self.jobs._scan_tasks(self.config, NOW)
        self.assertEqual(len(self.jobs_for('deadline_warning', task)), 2)

    def test_personal_reminder_and_daily_overdue_deduplicate(self):
        task = self.task(deadline=NOW - timedelta(hours=1), remind_at=NOW - timedelta(minutes=1))
        for _ in range(2):
            self.config.notification_scan_cursor = 0
            self.jobs._scan_tasks(self.config, NOW)
        self.assertEqual(len(self.jobs_for('personal_reminder', task)), 1)
        self.assertEqual(len(self.jobs_for('task_overdue', task)), 1)

    def test_overdue_escalates_to_manager_after_24_hours(self):
        task = self.task(deadline=NOW - timedelta(hours=25))
        self.jobs._scan_tasks(self.config, NOW)
        self.assertTrue(self.jobs_for('task_overdue', task, self.manager))

    def test_overdue_escalation_reaches_order_sale_and_rechecks_owner(self):
        order = self.order()
        task = self.task(self.designer, order_id=order.id, deadline=NOW - timedelta(hours=25))
        self.jobs._scan_tasks(self.config, NOW)
        alert = self.jobs_for('task_overdue', task, self.sale)
        self.assertEqual(len(alert), 1)
        self.assertTrue(alert._valid())
        self.jobs._cron_dispatch()
        with patch.object(type(self.config), '_bridge_request', return_value={'delivery': 'sent', 'message_id': 'escalated-to-sale'}):
            alert.message_id._send(self.config)
        self.assertEqual(alert.state, 'sent')
        order.with_user(self.env.user).user_id = self.other
        self.assertFalse(alert._valid())

    def test_quiet_hours_do_not_build_two_overdue_pings_for_next_morning(self):
        previous_night = NOW - timedelta(hours=10)
        self.pref().write({'quiet_enabled': True, 'quiet_start': 19, 'quiet_end': 7})
        task = self.task(deadline=previous_night - timedelta(minutes=30))
        with patch.object(fields.Datetime, 'now', return_value=previous_night):
            self.jobs._scan_tasks(self.config, previous_night)
            self.jobs._cron_dispatch()
        self.config.notification_scan_cursor = 0
        self.jobs._scan_tasks(self.config, NOW)
        items = self.jobs_for('task_overdue', task, self.sale)
        self.assertEqual(len(items), 1)
        with patch.object(type(self.config), '_bridge_request', return_value={'delivery': 'sent', 'message_id': 'overdue-day-1'}):
            items.message_id._send(self.config)
        self.config.notification_scan_cursor = 0
        self.jobs._scan_tasks(self.config, NOW)
        self.assertEqual(len(self.jobs_for('task_overdue', task, self.sale)), 1)

    def test_handover_and_group_assignment_recipients(self):
        order = self.order()
        order.with_user(self.env.user).write({'user_id_production': self.production.id,
            'production_deadline': NOW.date() + timedelta(days=1), 'order_state_custom': 'production'})
        self.assertTrue(self.jobs_for('handover', order, self.production))
        self.assertTrue(self.jobs_for('handover', order, self.sale))

    def test_delivery_daily_scan_does_not_duplicate_stage_notification(self):
        order = self.order()
        order.with_user(self.env.user).write({'order_state_custom': 'delivery'})
        before = len(self.jobs_for('delivery', order))
        self.jobs._scan_orders(self.config, NOW)
        self.assertEqual(len(self.jobs_for('delivery', order)), before)

    def test_production_lead_at_creation_gets_an_assignment(self):
        order = self.order(user_id_production=self.production.id)
        self.assertTrue(self.jobs_for('task_assigned', order, self.production))

    def test_financial_is_never_sent_to_design_or_production(self):
        self.assertFalse(self.jobs._financial_orders(self.designer))
        self.assertFalse(self.jobs._financial_orders(self.production))
        item = self.jobs._emit('financial', self.designer, self.designer, 'invalid-recipient', payload={'mode': 'summary'})
        self.jobs._cron_dispatch()
        self.assertEqual(item.state, 'cancelled')

    def test_financial_summary_rechecks_paid_state_before_delivery(self):
        order = self.order()
        item = self.jobs._emit('financial', self.sale, self.sale, 'financial-test', payload={'mode': 'summary'})
        with patch.object(type(self.jobs), '_financial_orders', return_value=order):
            self.jobs._cron_dispatch()
        self.assertEqual(item.state, 'queued')
        with patch.object(type(self.jobs), '_financial_orders', return_value=order.browse()), patch.object(type(self.config), '_bridge_request', side_effect=AssertionError('paid orders must not trigger reminders')):
            item.message_id._send(self.config)
        self.assertEqual(item.state, 'cancelled')

    def test_financial_summary_uses_paid_invoice_aggregation_in_employee_scope(self):
        product = self.env['product.product'].create({'name': 'Notification finance product', 'type': 'service'})
        order = self.order(order_state_custom='deposit', order_line=[fields.Command.create({
            'product_id': product.id, 'name': product.name, 'product_uom_qty': 1, 'price_unit': 1000})])
        self.assertIn(order, self.jobs._financial_orders(self.sale))
        self.assertNotIn(order, self.jobs._financial_orders(self.other))
        scopes = []
        def paid_groups(records, domain, fields, groupby, **kwargs):
            scopes.append(records.env.uid)
            self.assertIn(('payment_state', '=', 'paid'), domain)
            self.assertIn(('state', '!=', 'cancel'), domain)
            return [{'invoice_origin': order.name, 'amount_total': 1000}]
        with patch.object(type(self.env['account.move']), 'read_group', autospec=True, side_effect=paid_groups):
            self.assertNotIn(order, self.jobs._financial_orders(self.sale))
        self.assertEqual(scopes, [self.sale.id, self.sale.id])

    def test_digest_schedules_local_time_once_a_day(self):
        self.pref().morning_hour = 10
        for _ in range(2):
            self.config.notification_user_cursor = 0
            self.jobs._scan_users(self.config, NOW)
        items = self.jobs_for('digest_morning', self.sale, self.sale)
        self.assertEqual(len(items), 1)
        self.assertNotIn(self.other.name, items._render())

    def test_internal_note_notifies_participants_but_not_author(self):
        order = self.order()
        order.write({'user_id_design': self.designer.id})
        order.message_post(body='<div class="dac-internal-note-message">Please review</div>', subtype_xmlid='mail.mt_note')
        self.assertTrue(self.jobs_for('mentions', order, self.designer))
        self.assertFalse(self.jobs_for('mentions', order, self.sale))

    def test_chat_opt_out_and_pause_do_not_call_ai(self):
        binding = self.env['dac.zalo.binding'].search([('user_id', '=', self.sale.id)])
        pref = self.env['dac.zalo.preference']
        self.assertTrue(pref._chat_command(binding, '/tat-thongbao'))
        self.assertFalse(self.pref().notifications_enabled)
        pref._chat_command(binding, '/bat-thongbao')
        self.assertTrue(self.pref().notifications_enabled)
        pref._chat_command(binding, '/tam-dung 60')
        self.assertEqual(self.pref().paused_until, NOW + timedelta(hours=1))
        pref._chat_command(binding, '/tiep-tuc')
        self.assertFalse(self.pref().paused_until)

    def test_hr_pending_and_result_recipients_and_staleness(self):
        if 'dac.attendance.leave' not in self.env:
            self.skipTest('Attendance addon is optional')
        employee = self.env['hr.employee'].create({'name': 'Notification employee', 'user_id': self.sale.id})
        leave = self.env['dac.attendance.leave'].create({'employee_id': employee.id,
            'date_from': NOW.date(), 'date_to': NOW.date(), 'reason': 'Test private reason'})
        self.jobs._scan_hr(self.config, NOW)
        pending = self.jobs_for('hr_requests', leave, self.hr_manager)
        self.assertTrue(pending)
        self.assertNotIn('Test private reason', pending._render())
        leave.with_user(self.hr_manager).action_approve()
        self.jobs._scan_hr(self.config, NOW)
        self.assertFalse(pending._valid())
        self.assertEqual(len(self.jobs_for('hr_results', leave, self.sale)), 1)

    def test_health_has_odoo_fallback_and_resolves_without_duplicate_alerts(self):
        for _ in range(2):
            self.jobs._health_problem('test-outage', True, 'Test outage', self.admin)
        items = self.jobs_for('system_alerts', user=self.admin)
        self.assertEqual(len(items), 1)
        activity = self.env['mail.activity'].search([('summary', '=', '[Zalo] test-outage')])
        self.assertEqual(len(activity), 1)
        self.jobs._health_problem('test-outage', False, '', self.admin)
        self.assertFalse(items._valid())
        self.assertFalse(activity.exists())

    def test_health_scanner_detects_pancake_and_zalo_failure_without_sending(self):
        self.env['ir.config_parameter'].set_param('page_fm.token_check_status', 'expired')
        with patch.object(type(self.config), '_bridge_request', return_value={'state': 'disconnected'}):
            self.jobs._scan_health(self.config, NOW)
        self.assertTrue(self.env['mail.activity'].search([('summary', '=', '[Zalo] pancake')]))
        self.assertTrue(self.env['mail.activity'].search([('summary', '=', '[Zalo] connection')]))
        self.assertTrue(self.jobs_for('system_alerts', user=self.admin))

    def test_unlinked_user_and_expired_notifications_cannot_send(self):
        order = self.order()
        item = self.jobs_for('order_created', order)
        item.expires_at = NOW - timedelta(minutes=1)
        self.assertFalse(item._valid())
        self.env['dac.zalo.binding'].search([('user_id', '=', self.sale.id)]).action_revoke()
        self.order()
        self.assertEqual(len(self.jobs_for('order_created')), 1)
