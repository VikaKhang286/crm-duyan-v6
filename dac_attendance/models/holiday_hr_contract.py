from collections import defaultdict
from datetime import timedelta

import pytz

from odoo import api, fields, models
from odoo.addons.resource.models.utils import Intervals

from .holiday import local_day_bounds


class HrContract(models.Model):
    _inherit = 'hr.contract'

    def _get_resource_calendar_leaves(self, start_dt, end_dt):
        leaves = super()._get_resource_calendar_leaves(start_dt, end_dt)
        if not self:
            return leaves
        holidays = self.env['dac.holiday']._in_period(
            self.company_id, start_dt.date() - timedelta(days=1), end_dt.date() + timedelta(days=1))
        holiday_leaves = self.env['resource.calendar.leaves']
        for holiday in holidays:
            contracts = self.filtered(lambda contract: contract.company_id == holiday.company_id)
            for calendar, group in contracts.grouped(
                lambda contract: contract.resource_calendar_id or contract.company_id.resource_calendar_id).items():
                employees = group.employee_id
                intervals = holiday._unworked_intervals(employees, calendar)
                for employee in employees:
                    xmlid = ('hr_work_entry_contract.work_entry_type_legal_leave'
                             if employee.dac_employment_type == 'full_time'
                             else 'hr_work_entry_contract.work_entry_type_unpaid_leave')
                    for start, stop, _attendance in intervals.get(employee.id, ()):
                        if start < end_dt and stop > start_dt:
                            holiday_leaves += leaves.new({
                                'resource_id': employee.resource_id.id,
                                'calendar_id': calendar.id,
                                'date_from': start.astimezone(pytz.utc).replace(tzinfo=None),
                                'date_to': stop.astimezone(pytz.utc).replace(tzinfo=None),
                                'work_entry_type_id': self.env.ref(xmlid).id,
                            })
        return holiday_leaves | leaves

    def _get_valid_leave_intervals(self, attendances, interval):
        intervals = super()._get_valid_leave_intervals(attendances, interval)
        start, stop, leave = interval
        if not intervals or leave.resource_id or stop <= start:
            return intervals
        calendar = self.resource_calendar_id or self.company_id.resource_calendar_id
        tz = pytz.timezone(calendar.tz or 'UTC')
        holidays = self.env['dac.holiday']._in_period(
            self.company_id, start.astimezone(tz).date(), stop.astimezone(tz).date())
        if not holidays:
            return intervals
        dac_days = Intervals([(*local_day_bounds(holiday.date, tz), holiday) for holiday in holidays])
        return list(Intervals(intervals) - dac_days)

    def _get_contract_work_entries_values(self, date_start, date_stop):
        values = super()._get_contract_work_entries_values(date_start, date_stop)
        for vals in values:
            vals['dac_generation_source'] = 'contract'
        return values

    @api.model
    def _refresh_holiday_dates(self, company_dates, employees=None):
        for company_id, day in sorted(company_dates):
            domain = [
                ('company_id', '=', company_id), ('state', 'in', ['open', 'close']),
                ('date_start', '<=', day), '|', ('date_end', '=', False), ('date_end', '>=', day),
                ('date_generated_from', '<', day + timedelta(days=2)),
                ('date_generated_to', '>', day - timedelta(days=1)),
            ]
            if employees is not None:
                domain.append(('employee_id', 'in', employees.ids))
            for contract in self.sudo().search(domain):
                contract.with_company(contract.company_id)._refresh_holiday_day(day)

    def _refresh_holiday_day(self, day):
        self.ensure_one()
        calendar = self.resource_calendar_id or self.company_id.resource_calendar_id
        tz = pytz.timezone(calendar.tz or 'UTC')
        start, stop = local_day_bounds(day, tz)
        start = max(start.astimezone(pytz.utc).replace(tzinfo=None), self.date_generated_from)
        stop = min(stop.astimezone(pytz.utc).replace(tzinfo=None), self.date_generated_to)
        if start >= stop:
            return
        entries = self.env['hr.work.entry'].search([
            ('contract_id', '=', self.id), ('date_start', '<', stop), ('date_stop', '>', start)])
        if any(entry.state == 'validated' or entry.date_start < start or entry.date_stop > stop
               for entry in entries):
            return
        wanted = defaultdict(list)
        for vals in self._get_contract_work_entries_values(start, stop):
            wanted[vals['date_start'], vals['date_stop'], vals['work_entry_type_id']].append(vals)
        legacy_schedule = set()
        if any(not entry.dac_generation_source for entry in entries):
            intervals = self._get_attendance_intervals(pytz.utc.localize(start), pytz.utc.localize(stop))
            for interval in intervals[self.employee_id.resource_id.id]:
                begin, end, _attendance = interval
                legacy_schedule.add((begin.astimezone(pytz.utc).replace(tzinfo=None),
                                     end.astimezone(pytz.utc).replace(tzinfo=None),
                                     self._get_interval_work_entry_type(interval).id))
        obsolete = self.env['hr.work.entry']
        manual = self.env['hr.work.entry']
        for entry in entries:
            key = (entry.date_start, entry.date_stop, entry.work_entry_type_id.id)
            if wanted[key]:
                wanted[key].pop()
            elif entry.dac_generation_source == 'contract' or (
                    not entry.dac_generation_source and key in legacy_schedule):
                obsolete |= entry
            else:
                manual |= entry
        missing = [vals for group in wanted.values() for vals in group]
        if any(entry.date_start < value['date_stop'] and entry.date_stop > value['date_start']
               for entry in manual for value in missing):
            return
        obsolete.write({'active': False})
        self.env['hr.work.entry'].create(missing)


class HrWorkEntry(models.Model):
    _inherit = 'hr.work.entry'

    dac_generation_source = fields.Selection(
        [('contract', 'Hợp đồng'), ('manual', 'Nhập tay')], readonly=True, copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals.setdefault('dac_generation_source', 'manual')
        return super().create(vals_list)

    def write(self, vals):
        if {'employee_id', 'contract_id', 'work_entry_type_id', 'date_start', 'date_stop', 'duration'}.intersection(vals):
            vals = dict(vals, dac_generation_source='manual')
        return super().write(vals)
