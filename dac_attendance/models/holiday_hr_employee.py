from odoo import api, fields, models
from odoo.exceptions import AccessError

ATTENDANCE_MANAGER = 'hr_attendance.group_hr_attendance_manager'
EMPLOYMENT_TYPES = [('full_time', 'Full-time'), ('part_time', 'Part-time')]


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    dac_employment_type = fields.Selection(
        EMPLOYMENT_TYPES, string='Loại nhân viên', required=True, default='full_time',
        groups='hr.group_hr_user',
        help='Khi nghỉ lễ, nhân viên full-time hưởng đủ giờ theo lịch làm việc. '
             'Nhân viên part-time chỉ hưởng lương cho giờ thực làm.',
    )

    def write(self, vals):
        if 'dac_employment_type' not in vals:
            return super().write(vals)
        changed = self.filtered(lambda emp: emp.dac_employment_type != vals['dac_employment_type'])
        result = super().write(vals)
        if changed:
            dates = self.env['dac.holiday'].sudo().search([
                ('company_id', 'in', changed.company_id.ids),
            ])._company_dates()
            self.env['hr.contract']._refresh_holiday_dates(dates, changed)
        return result


class EmployeeAttendanceConfig(models.Model):
    _inherit = 'hr.employee.attendance.config'

    dac_employment_type = fields.Selection(
        EMPLOYMENT_TYPES, string='Loại nhân viên', compute='_compute_dac_employment_type',
        inverse='_inverse_dac_employment_type', groups=ATTENDANCE_MANAGER,
    )

    @api.depends('employee_id.dac_employment_type', 'employee_id.company_id')
    @api.depends_context('uid', 'company')
    def _compute_dac_employment_type(self):
        for config in self:
            employee = config.employee_id.sudo()
            config.dac_employment_type = (
                employee.dac_employment_type if employee.company_id in self.env.companies else False
            )

    def _inverse_dac_employment_type(self):
        if not self.env.user.has_group(ATTENDANCE_MANAGER):
            raise AccessError('Chỉ quản lý chấm công mới được đổi loại nhân viên tại đây.')
        for config in self:
            employee = config.employee_id.sudo()
            if employee.company_id not in self.env.companies:
                raise AccessError('Nhân viên phải thuộc công ty đang được chọn.')
            employee.dac_employment_type = config.dac_employment_type
