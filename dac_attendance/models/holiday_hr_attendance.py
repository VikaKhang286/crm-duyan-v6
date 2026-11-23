from collections import defaultdict

from odoo import api, models


class HrAttendance(models.Model):
    _inherit = 'hr.attendance'

    def _holiday_workdays(self):
        return {(att.employee_id.company_id.id, att.check_in_date, att.employee_id.id)
                for att in self.filtered('check_out') if att.check_in_date}

    def _refresh_holiday_attendances(self, workdays):
        if not workdays:
            return
        dates = self.env['dac.holiday'].sudo().search([
            ('company_id', 'in', list({company for company, _day, _employee in workdays})),
            ('date', 'in', list({day for _company, day, _employee in workdays})),
        ])._company_dates()
        by_day = defaultdict(set)
        for company_id, day, employee_id in workdays:
            if (company_id, day) in dates:
                by_day[company_id, day].add(employee_id)
        for company_date, employee_ids in by_day.items():
            self.env['hr.contract']._refresh_holiday_dates(
                {company_date}, self.env['hr.employee'].browse(employee_ids))

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        self._refresh_holiday_attendances(records._holiday_workdays())
        return records

    def write(self, vals):
        if not {'check_in', 'check_out', 'employee_id'}.intersection(vals):
            return super().write(vals)
        previous = self._holiday_workdays()
        result = super().write(vals)
        self._refresh_holiday_attendances(previous | self._holiday_workdays())
        return result

    def unlink(self):
        previous = self._holiday_workdays()
        result = super().unlink()
        self._refresh_holiday_attendances(previous)
        return result
