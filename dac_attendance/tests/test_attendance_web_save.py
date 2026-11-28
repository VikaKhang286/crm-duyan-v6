from datetime import datetime

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestAttendanceWebSave(TransactionCase):
    def test_save_regular_shift_then_overtime_with_web_timestamps(self):
        calendar = self.env['resource.calendar'].create({
            'name': 'Vietnam attendance calendar', 'tz': 'Asia/Ho_Chi_Minh',
        })
        employee = self.env['hr.employee'].create({
            'name': 'Attendance form employee', 'resource_calendar_id': calendar.id,
        })
        attendance = self.env['hr.attendance']
        specification = {'check_in': {}, 'check_out': {}, 'overtime_check_in': {}}

        regular, = attendance.web_save({
            'employee_id': employee.id,
            'check_in': '2026-11-16 01:00:00',
            'check_out': '2026-11-16 10:29:59',
        }, specification)
        self.assertEqual(regular['check_in'], datetime(2026, 11, 16, 1))
        self.assertEqual(regular['check_out'], datetime(2026, 11, 16, 10, 29, 59))
        self.assertFalse(regular['overtime_check_in'])

        overtime, = attendance.web_save({
            'employee_id': employee.id,
            'check_in': '2026-11-16 10:30:00',  # 17:30 in the employee's timezone.
        }, specification)
        self.assertEqual(overtime['id'], regular['id'])
        self.assertEqual(overtime['check_in'], regular['check_in'])
        self.assertFalse(overtime['check_out'])
        self.assertEqual(overtime['overtime_check_in'], datetime(2026, 11, 16, 10, 30))
