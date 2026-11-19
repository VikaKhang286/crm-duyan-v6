import json
from datetime import date, datetime
from unittest.mock import patch

from odoo import Command
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from ..models import hr_attendance_with_geolocation


@tagged('post_install', '-at_install')
class TestAttendanceBackfill(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.attendance_model = cls.env['hr.attendance']
        cls.params = cls.env['ir.config_parameter'].sudo()
        calendar = cls.env['resource.calendar'].create({
            'name': 'Vietnam backfill test calendar',
            'tz': 'Asia/Ho_Chi_Minh',
            'attendance_ids': [Command.create({
                'name': 'Monday morning',
                'dayofweek': '0',
                'hour_from': 8,
                'hour_to': 12,
                'day_period': 'morning',
            })],
        })
        employees = cls.env['hr.employee'].create([
            {'name': f'Backfill Employee {index}', 'resource_calendar_id': calendar.id}
            for index in range(3)
        ])
        cls.check_in = datetime(2026, 11, 15, 23)  # 06:00 Monday local time.
        cls.overnight_checkout = datetime(2026, 11, 17, 1)
        cls.same_day_checkout = datetime(2026, 11, 16, 12)
        cls.midnight_cutoff = datetime(2026, 11, 16, 16, 59, 59)
        cls.attendances = cls.attendance_model.create([
            {'employee_id': employee.id, 'check_in': cls.check_in, 'check_out': check_out}
            for employee, check_out in zip(employees, [
                cls.overnight_checkout, cls.same_day_checkout, cls.overnight_checkout,
            ])
        ])

    def setUp(self):
        super().setUp()
        batch_size = patch.object(
            hr_attendance_with_geolocation, '_ATTENDANCE_BACKFILL_BATCH_SIZE', 2,
        )
        batch_size.start()
        self.addCleanup(batch_size.stop)

    def _set_backfill_range(self, cron_name):
        key = f'dac_attendance.{cron_name}.progress'
        self.params.set_param(key, json.dumps([
            self.attendances[0].id - 1, self.attendances[-1].id,
        ]))
        return key

    def _run_cron_batch(self, cron_name):
        cron = self.env.ref(f'dac_attendance.{cron_name}')
        cron, progress = cron.with_context(cron_id=cron.id)._add_progress()
        processed = getattr(self.attendance_model.with_env(cron.env), f'_{cron_name}')()
        return processed, progress

    def test_recompute_resumes_and_repairs_stored_values_in_batches(self):
        cron_name = 'cron_recompute_attendance_cutoffs'
        progress_key = self._set_backfill_range(cron_name)
        cron = self.env.ref(f'dac_attendance.{cron_name}')
        cron.active = True
        self.env.flush_all()
        self.env.cr.execute('''
            UPDATE hr_attendance
               SET check_in_date = NULL, worked_hours = 99, worked_hours_display = 99
             WHERE id IN %s
        ''', [tuple(self.attendances.ids)])
        self.attendances.invalidate_recordset(['check_in_date', 'worked_hours', 'worked_hours_display'])

        processed, progress = self._run_cron_batch(cron_name)

        self.assertEqual(processed, 2)
        self.assertFalse(progress.deactivate)
        self.assertEqual(json.loads(self.params.get_param(progress_key)), [
            self.attendances[1].id, self.attendances[2].id,
        ])
        self.assertEqual(self.attendances.mapped('worked_hours'), [11.5, 11.5, 99])

        processed, progress = self._run_cron_batch(cron_name)

        self.assertEqual(processed, 1)
        self.assertTrue(progress.deactivate)
        self.assertEqual(progress.remaining, 0)
        cron.invalidate_recordset(['active'])
        self.assertTrue(cron.active, 'The scheduler owns the job activation state')
        self.attendances.invalidate_recordset(['check_in_date', 'worked_hours', 'worked_hours_display'])
        self.assertEqual(self.attendances.mapped('check_in_date'), [date(2026, 11, 16)] * 3)
        self.assertEqual(self.attendances.mapped('worked_hours'), [11.5] * 3)
        self.assertEqual(self.attendances.mapped('worked_hours_display'), [11.5] * 3)
        self.assertEqual(self.attendances[0].check_out, self.overnight_checkout)
        self.assertEqual(self._run_cron_batch(cron_name)[0], 0)

    def test_normalize_resumes_and_retries_interrupted_batch(self):
        cron_name = 'cron_normalize_overnight_attendances'
        progress_key = self._set_backfill_range(cron_name)
        original_progress = self.params.get_param(progress_key)
        original_cutoff = type(self.attendance_model)._get_auto_checkout_cutoff

        def fail_on_second_record(model, employee, check_in):
            if employee == self.attendances[1].employee_id:
                raise RuntimeError('interrupted attendance backfill')
            return original_cutoff(model, employee, check_in)

        with patch.object(type(self.attendance_model), '_get_auto_checkout_cutoff', fail_on_second_record):
            with self.assertRaisesRegex(RuntimeError, 'interrupted attendance backfill'), self.env.cr.savepoint():
                self._run_cron_batch(cron_name)

        self.assertEqual(self.params.get_param(progress_key), original_progress)
        self.assertEqual(self.attendances[0].check_out, self.overnight_checkout)
        processed, progress = self._run_cron_batch(cron_name)

        self.assertEqual(processed, 1)
        self.assertFalse(progress.deactivate)
        self.assertEqual(self.attendances.mapped('check_out'), [
            self.midnight_cutoff, self.same_day_checkout, self.overnight_checkout,
        ])
        processed, progress = self._run_cron_batch(cron_name)

        self.assertEqual(processed, 1)
        self.assertTrue(progress.deactivate)
        self.assertEqual(self.attendances[2].check_out, self.midnight_cutoff)
        self.assertEqual(self._run_cron_batch(cron_name)[0], 0)

    def test_backfill_uses_the_original_upper_id_when_rows_arrive_between_batches(self):
        cron_xmlid = 'dac_attendance.cron_normalize_overnight_attendances'
        progress_key = f'{cron_xmlid}.progress'
        self.params.set_param(progress_key, False)
        attendance_model = self.attendance_model
        domain = [('employee_id', 'in', self.attendances.employee_id.ids)]
        normalize = type(attendance_model)._normalize_overnight_attendance_batch

        self.assertEqual(attendance_model._run_attendance_backfill(cron_xmlid, domain, normalize), 1)
        self.assertEqual(json.loads(self.params.get_param(progress_key)), [
            self.attendances[1].id, self.attendances[-1].id,
        ])

        late_checkout = datetime(2026, 11, 19, 1)
        new_attendance = attendance_model.create({
            'employee_id': self.attendances[0].employee_id.id,
            'check_in': datetime(2026, 11, 17, 23),
            'check_out': late_checkout,
        })

        self.assertEqual(attendance_model._run_attendance_backfill(cron_xmlid, domain, normalize), 1)
        self.assertEqual(self.attendances[-1].check_out, self.midnight_cutoff)
        self.assertEqual(new_attendance.check_out, late_checkout)
