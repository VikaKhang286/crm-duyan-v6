from collections import defaultdict
from datetime import datetime, time, timedelta

import pytz

from odoo import api, fields, models
from odoo.addons.resource.models.utils import Intervals
from odoo.exceptions import AccessError


def local_day_bounds(day, tz):
    start = tz.localize(datetime.combine(day, time.min))
    stop = tz.localize(datetime.combine(day + timedelta(days=1), time.min))
    return start, stop


class DacHoliday(models.Model):
    _name = 'dac.holiday'
    _description = 'Ngày nghỉ lễ'
    _order = 'date desc, name'

    name = fields.Char(string='Tên ngày lễ', required=True)
    date = fields.Date(string='Ngày nghỉ', required=True, default=fields.Date.context_today)
    company_id = fields.Many2one('res.company', string='Công ty', required=True,
                                 default=lambda self: self.env.company)

    _sql_constraints = [('company_date_unique', 'UNIQUE(company_id, date)',
                         'Công ty đã có ngày nghỉ lễ vào ngày này.')]

    @api.constrains('company_id')
    def _check_company_access(self):
        if not self.env.su and self.company_id - self.env.companies:
            raise AccessError('Ngày lễ phải thuộc công ty đang được chọn.')

    @api.model
    def _in_period(self, companies, date_from, date_to):
        return self.search([('company_id', 'in', companies.ids), ('date', '>=', date_from),
                            ('date', '<=', date_to)])

    def _unworked_intervals(self, employees, calendar):
        self.ensure_one()
        if not calendar or not employees:
            return {}
        tz = pytz.timezone(calendar.tz)
        start, stop = local_day_bounds(self.date, tz)
        start_utc = start.astimezone(pytz.utc).replace(tzinfo=None)
        stop_utc = stop.astimezone(pytz.utc).replace(tzinfo=None)
        records = self.env['hr.attendance'].search([
            ('employee_id', 'in', employees.ids), ('check_in', '<', stop_utc),
            ('check_out', '>', start_utc),
        ])
        worked = defaultdict(list)
        for att in records:
            end = min(att.check_out, att._get_shift_work_cutoff(att.employee_id, att.check_in))
            if end > att.check_in:
                worked[att.employee_id.id].append((pytz.utc.localize(att.check_in),
                                                    pytz.utc.localize(end), att))
        scheduled = calendar._attendance_intervals_batch(start, stop, employees.resource_id, tz=tz)
        gaps = {}
        for employee in employees:
            resource_id = employee.resource_id.id if employee.resource_calendar_id else False
            gaps[employee.id] = scheduled[resource_id] - Intervals(worked[employee.id])
        return gaps

    def _paid_hours_by_employee(self, employees):
        paid = defaultdict(float)
        full_time = employees.filtered(lambda e: e.dac_employment_type == 'full_time')
        if not self or not full_time:
            return paid
        contracts = self.env['hr.contract'].search([
            ('employee_id', 'in', full_time.ids), ('state', 'in', ['open', 'close']),
        ]).grouped('employee_id')
        for holiday in self:
            by_calendar = defaultdict(list)
            for employee in full_time.filtered(lambda e: e.company_id == holiday.company_id):
                if employee.departure_date and employee.departure_date < holiday.date:
                    continue
                history = contracts.get(employee)
                if history:
                    current = history.filtered(lambda c: c.date_start <= holiday.date and
                                               (not c.date_end or holiday.date <= c.date_end))
                    if not current:
                        continue
                    calendar = current[:1].resource_calendar_id
                else:
                    if not employee.active and not employee.departure_date:
                        continue
                    calendar = employee.resource_calendar_id
                by_calendar[calendar or holiday.company_id.resource_calendar_id].append(employee.id)
            for calendar, ids in by_calendar.items():
                for employee_id, gaps in holiday._unworked_intervals(employees.browse(ids), calendar).items():
                    paid[employee_id] += sum((end - start).total_seconds() / 3600
                                             for start, end, _attendance in gaps)
        return paid

    def _company_dates(self):
        return {(holiday.company_id.id, holiday.date) for holiday in self}

    @api.model_create_multi
    def create(self, vals_list):
        holidays = super().create(vals_list)
        self.env['hr.contract']._refresh_holiday_dates(holidays._company_dates())
        return holidays

    def write(self, vals):
        if not {'date', 'company_id'}.intersection(vals):
            return super().write(vals)
        previous = self._company_dates()
        result = super().write(vals)
        self.env['hr.contract']._refresh_holiday_dates(previous ^ self._company_dates())
        return result

    def unlink(self):
        previous = self._company_dates()
        result = super().unlink()
        self.env['hr.contract']._refresh_holiday_dates(previous)
        return result


class AttendanceDashboard(models.AbstractModel):
    _inherit = 'dac.attendance.dashboard'

    def _get_company_holidays(self, companies, date_from, date_to):
        holidays = self.env['dac.holiday']._in_period(companies, date_from, date_to)
        return {(holiday.company_id.id, holiday.date): holiday.name for holiday in holidays}
