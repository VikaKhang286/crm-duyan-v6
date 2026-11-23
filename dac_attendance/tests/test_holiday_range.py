from datetime import date
from unittest.mock import patch

from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestHolidayRange(TransactionCase):
    def test_range_lookup_dashboard_and_refresh(self):
        Holiday = self.env['dac.holiday']
        company = self.env.company
        with patch.object(type(self.env['hr.contract']), '_refresh_holiday_dates') as refresh:
            holiday = Holiday.create({'name': 'Nghỉ lễ', 'date': '2095-04-29', 'date_to': '2095-05-02',
                                      'company_id': company.id})
            expected = {(company.id, date(2095, 4, 29)), (company.id, date(2095, 4, 30)),
                        (company.id, date(2095, 5, 1)), (company.id, date(2095, 5, 2))}
            self.assertEqual(holiday._company_dates(), expected)
            self.assertEqual(refresh.call_args.args[0], expected)
            self.assertIn(holiday, Holiday._in_period(company, date(2095, 5, 1), date(2095, 5, 1)))
            days = self.env['dac.attendance.dashboard']._get_company_holidays(
                company, date(2095, 4, 30), date(2095, 5, 1))
            self.assertEqual(days, {(company.id, date(2095, 4, 30)): 'Nghỉ lễ',
                                    (company.id, date(2095, 5, 1)): 'Nghỉ lễ'})
            holiday.write({'date_to': '2095-05-01'})
            self.assertEqual(refresh.call_args.args[0], {(company.id, date(2095, 5, 2))})

    def test_overlap_and_invalid_range_rejected(self):
        Holiday = self.env['dac.holiday']
        Holiday.create({'name': 'Nghỉ lễ', 'date': '2095-04-29', 'date_to': '2095-05-02'})
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            Holiday.create({'name': 'Trùng', 'date': '2095-05-01'})
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            Holiday.create({'name': 'Sai ngày', 'date': '2095-06-02', 'date_to': '2095-06-01'})
        single = Holiday.create({'name': 'Một ngày', 'date': '2095-06-03'})
        self.assertEqual(list(single._days()), [date(2095, 6, 3)])
