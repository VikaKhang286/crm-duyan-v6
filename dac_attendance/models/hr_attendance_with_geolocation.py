# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging
import math
import json
import time as time_module
import requests
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from psycopg2 import OperationalError
from odoo import models, fields, api
from odoo.exceptions import ValidationError


_logger = logging.getLogger(__name__)


def haversine_distance(lat1, lon1, lat2, lon2):
    """
    Calculate distance between two GPS coordinates using Haversine formula.
    Returns distance in meters.
    """
    R = 6371000  # Earth's radius in meters
    
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    
    a = (math.sin(delta_phi / 2) ** 2 +
         math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    
    return R * c


class HrAttendanceWithGeolocation(models.Model):
    """
    Extended hr.attendance model with geolocation and photo capture support.
    """
    _inherit = 'hr.attendance'
    _table = 'hr_attendance'

    @api.model
    def dac_reverse_geocode(self, latitude, longitude):
        """Resolve an address server-side with a shared, area-based cache."""
        try:
            latitude = float(latitude)
            longitude = float(longitude)
        except (TypeError, ValueError):
            return {'success': False, 'error': 'Tọa độ GPS không hợp lệ'}
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            return {'success': False, 'error': 'Tọa độ GPS nằm ngoài phạm vi'}

        # Four decimals groups positions within roughly 11 metres. This keeps
        # the cache useful without replacing the precise GPS stored on photos.
        cache_location = f'{latitude:.4f},{longitude:.4f}'
        cache_key = f'dac_attendance.reverse_geocode.{cache_location}'
        cache_ttl = timedelta(days=30)
        params = self.env['ir.config_parameter'].sudo()

        def _cached_address():
            raw_value = params.get_param(cache_key)
            if not raw_value:
                return False
            try:
                cached = json.loads(raw_value)
                cached_at = fields.Datetime.from_string(cached.get('cached_at'))
                if (cached.get('address') and cached_at
                        and fields.Datetime.now() - cached_at <= cache_ttl):
                    return cached['address']
            except (TypeError, ValueError, json.JSONDecodeError):
                return False
            return False

        address = _cached_address()
        if address:
            return {'success': True, 'address': address, 'cached': True}

        # Serialize cache misses across all workers. Nominatim's public API is
        # not a bulk endpoint, so different employee locations must not create
        # a burst of parallel requests.
        self.env.cr.execute('SELECT pg_advisory_xact_lock(%s, %s)', [9183, 1])
        address = _cached_address()
        if address:
            return {'success': True, 'address': address, 'cached': True}

        query = {
            'lat': latitude,
            'lon': longitude,
            'format': 'jsonv2',
            'addressdetails': 1,
            'zoom': 18,
            'accept-language': 'vi',
        }
        headers = {
            'Accept': 'application/json',
            'User-Agent': 'DAC-Attendance/1.0 (https://crm.duyan.vn)',
        }
        last_error = False
        for attempt in range(2):
            try:
                last_request_key = 'dac_attendance.reverse_geocode.last_request'
                last_request = float(params.get_param(last_request_key, '0') or 0)
                wait_seconds = 1.0 - (time_module.time() - last_request)
                if wait_seconds > 0:
                    time_module.sleep(wait_seconds)
                params.set_param(last_request_key, str(time_module.time()))
                response = requests.get(
                    'https://nominatim.openstreetmap.org/reverse',
                    params=query,
                    headers=headers,
                    timeout=(3, 5),
                )
                if response.status_code == 429:
                    last_error = 'Dịch vụ địa chỉ đang giới hạn truy cập'
                elif response.status_code >= 500:
                    last_error = f'Dịch vụ địa chỉ lỗi HTTP {response.status_code}'
                else:
                    response.raise_for_status()
                    address = response.json().get('display_name')
                    if address:
                        params.set_param(cache_key, json.dumps({
                            'address': address,
                            'cached_at': fields.Datetime.to_string(fields.Datetime.now()),
                        }, ensure_ascii=False))
                        return {'success': True, 'address': address, 'cached': False}
                    last_error = 'Không tìm thấy địa chỉ cho vị trí này'
            except (requests.RequestException, ValueError) as error:
                last_error = str(error)
        _logger.warning(
            'DAC Attendance reverse geocoding failed for %s: %s',
            cache_location, last_error,
        )
        return {'success': False, 'error': last_error or 'Không xác định được địa chỉ'}

    check_in_date = fields.Date(
        string='Ngày check-in',
        compute='_compute_check_in_date',
        store=True
    )
    overtime_check_in = fields.Datetime(
        string='Bắt đầu làm thêm', readonly=True, copy=False)
    overtime_check_out = fields.Datetime(
        string='Kết thúc làm thêm', readonly=True, copy=False)

    @api.depends('check_in', 'employee_id')
    def _compute_check_in_date(self):
        for record in self:
            if record.check_in and record.employee_id:
                employee_tz = ZoneInfo(record._get_employee_timezone(record.employee_id))
                record.check_in_date = record.check_in.replace(
                    tzinfo=ZoneInfo('UTC')).astimezone(employee_tz).date()
            else:
                record.check_in_date = False

    # GPS/Location fields
    check_in_latitude = fields.Float(
        string='Vĩ độ check-in',
        digits=(10, 7),
        readonly=True,
        help='Vĩ độ khi check-in'
    )
    check_in_longitude = fields.Float(
        string='Kinh độ check-in',
        digits=(10, 7),
        readonly=True,
        help='Kinh độ khi check-in'
    )
    check_in_gps_accuracy = fields.Float(
        string='Độ chính xác GPS check-in',
        digits=(6, 2),
        readonly=True,
        help='Độ chính xác GPS khi check-in (mét)'
    )
    check_out_latitude = fields.Float(
        string='Vĩ độ check-out',
        digits=(10, 7),
        readonly=True,
        help='Vĩ độ khi check-out'
    )
    check_out_longitude = fields.Float(
        string='Kinh độ check-out',
        digits=(10, 7),
        readonly=True,
        help='Kinh độ khi check-out'
    )
    check_out_gps_accuracy = fields.Float(
        string='Độ chính xác GPS check-out',
        digits=(6, 2),
        readonly=True,
        help='Độ chính xác GPS khi check-out (mét)'
    )
    
    # Network/Location info
    check_in_ip_address = fields.Char(
        string='IP check-in',
        readonly=True,
        help='Địa chỉ IP khi check-in'
    )
    check_in_wifi_ssid = fields.Char(
        string='WiFi SSID check-in',
        readonly=True,
        help='Tên WiFi khi check-in'
    )
    check_out_ip_address = fields.Char(
        string='IP check-out',
        readonly=True,
        help='Địa chỉ IP khi check-out'
    )
    check_out_wifi_ssid = fields.Char(
        string='WiFi SSID check-out',
        readonly=True,
        help='Tên WiFi khi check-out'
    )
    
    # Photo fields
    check_in_photo = fields.Binary(
        string='Ảnh check-in',
        readonly=True,
        attachment=False,
        help='Ảnh chụp khi check-in'
    )
    check_out_photo = fields.Binary(
        string='Ảnh check-out',
        readonly=True,
        attachment=False,
        help='Ảnh chụp khi check-out'
    )
    check_in_photo_timestamp = fields.Datetime(
        string='Thời gian chụp check-in',
        readonly=True,
        help='Thời gian chụp ảnh check-in'
    )
    check_out_photo_timestamp = fields.Datetime(
        string='Thời gian chụp check-out',
        readonly=True,
        help='Thời gian chụp ảnh check-out'
    )
    
    # Photo metadata (JSON: timestamp, GPS)
    check_in_photo_metadata = fields.Text(
        string='Metadata ảnh check-in',
        readonly=True,
        help='Metadata JSON của ảnh check-in (thời gian, vị trí GPS)'
    )
    check_out_photo_metadata = fields.Text(
        string='Metadata ảnh check-out',
        readonly=True,
        help='Metadata JSON của ảnh check-out (thời gian, vị trí GPS)'
    )
    
    # Validation status
    gps_validated = fields.Boolean(
        string='GPS đã xác thực',
        default=False,
        readonly=True,
        help='GPS đã được xác thực nằm trong phạm vi cho phép'
    )
    wifi_validated = fields.Boolean(
        string='WiFi đã xác thực',
        default=False,
        readonly=True,
        help='WiFi đã được xác thực'
    )
    ip_validated = fields.Boolean(
        string='IP đã xác thực',
        default=False,
        readonly=True,
        help='IP đã được xác thực nằm trong dải cho phép'
    )
    photo_validated = fields.Boolean(
        string='Ảnh đã xác thực',
        default=False,
        readonly=True,
        help='Ảnh đã được chụp và xác thực'
    )
    
    # Custom computed fields
    worked_hours_display = fields.Float(
        string='Số giờ làm việc',
        compute='_compute_worked_hours_display',
        store=True,
        digits=(6, 2),
    )
    is_late = fields.Boolean(
        string='Đi muộn',
        default=False,
        help='Đánh dấu nếu check-in muộn hơn giờ làm việc'
    )
    is_early_leave = fields.Boolean(
        string='Về sớm',
        default=False,
        help='Đánh dấu nếu check-out sớm hơn giờ tan làm'
    )
    late_minutes = fields.Integer(
        string='Phút đi muộn',
        default=0,
        help='Số phút check-in muộn so với giờ làm việc'
    )

    @api.depends('worked_hours')
    def _compute_worked_hours_display(self):
        """Display worked hours with 2 decimal places."""
        for record in self:
            record.worked_hours_display = round(record.worked_hours, 2)

    @api.depends('check_in', 'check_out')
    def _compute_worked_hours(self):
        """Keep the real check-out time, but stop regular shift hours at 17:30."""
        for attendance in self:
            if attendance.check_out and attendance.check_in and attendance.employee_id:
                shift_cutoff = attendance._get_shift_work_cutoff(
                    attendance.employee_id, attendance.check_in)
                effective_check_out = min(attendance.check_out, shift_cutoff)
                attendance.worked_hours = attendance._calculate_worked_hours(
                    attendance.check_in, effective_check_out)
            else:
                attendance.worked_hours = False

    def _calculate_worked_hours(self, start_dt, end_dt):
        """Calculate hours through Odoo's native compute on every Odoo 18 build.

        Newer Odoo 18 releases expose ``_get_worked_hours_in_range`` while
        older production builds keep the calculation inline in
        ``_compute_worked_hours``. A virtual record plus ``super`` preserves
        each build's calendar/lunch logic without relying on the newer API.
        """
        self.ensure_one()
        if not start_dt or not end_dt or end_dt <= start_dt:
            return 0.0
        virtual_attendance = self.new({
            'employee_id': self.employee_id.id,
            'check_in': start_dt,
            'check_out': end_dt,
        })
        super(HrAttendanceWithGeolocation, virtual_attendance)._compute_worked_hours()
        return virtual_attendance.worked_hours or 0.0

    def _get_worked_hours_as_of(self, as_of=None):
        """Return calendar-aware hours for open and closed attendance records."""
        self.ensure_one()
        if not self.check_in:
            return 0.0
        as_of = as_of or fields.Datetime.now()
        effective_end = min(
            self.check_out or as_of,
            as_of,
            self._get_shift_work_cutoff(self.employee_id, self.check_in),
        )
        if effective_end <= self.check_in:
            return 0.0
        return self._calculate_worked_hours(self.check_in, effective_end)

    def _get_employee_calendar(self, employee=None):
        # employee=None: called by Odoo base without arg (uses self.employee_id)
        emp = employee or self.employee_id
        return emp.resource_calendar_id or emp.company_id.resource_calendar_id

    def _get_employee_timezone(self, employee):
        calendar = self._get_employee_calendar(employee)
        return (
            getattr(calendar, 'tz', None)
            or employee.tz
            or employee.user_id.tz
            or employee.company_id.partner_id.tz
            or 'Asia/Ho_Chi_Minh'
        )

    def _get_attendance_day_bounds(self, employee, target_dt):
        """Return the UTC bounds of the employee's local attendance day."""
        if isinstance(target_dt, str):
            target_dt = fields.Datetime.from_string(target_dt)
        employee_tz = ZoneInfo(self._get_employee_timezone(employee))
        localized_dt = target_dt.replace(tzinfo=ZoneInfo('UTC')).astimezone(employee_tz)
        local_day_start = datetime.combine(
            localized_dt.date(), time.min, tzinfo=employee_tz)
        local_next_day_start = local_day_start + timedelta(days=1)
        return (
            local_day_start.astimezone(ZoneInfo('UTC')).replace(tzinfo=None),
            local_next_day_start.astimezone(ZoneInfo('UTC')).replace(tzinfo=None),
        )

    def _get_workday_attendance(self, employee, check_in_dt, exclude_ids=None):
        """Find an attendance already started on the employee's local day."""
        day_start, next_day_start = self._get_attendance_day_bounds(employee, check_in_dt)
        domain = [
            ('employee_id', '=', employee.id),
            ('check_in', '>=', day_start),
            ('check_in', '<', next_day_start),
        ]
        if exclude_ids:
            domain.append(('id', 'not in', exclude_ids))
        return self.sudo().search(domain, limit=1)

    def _lock_employee_attendance(self, employee_id):
        """Serialize attendance state changes for one employee per transaction."""
        self.env.cr.execute(
            'SELECT pg_advisory_xact_lock(%s, %s)', [9182, employee_id])

    def _upsert_daily_check_in(self, existing, vals):
        """Reopen one daily record without destroying its regular-shift check-in."""
        check_in = vals['check_in']
        employee = existing.employee_id
        is_overtime = not self._get_auto_checkout_cutoff(employee, check_in)
        reset_vals = {
            'check_out': False,
            'check_out_photo': False,
            'check_out_photo_timestamp': False,
            'check_out_photo_metadata': False,
            'check_out_latitude': 0.0,
            'check_out_longitude': 0.0,
            'check_out_gps_accuracy': 0.0,
            'is_early_leave': False,
        }
        if is_overtime:
            # A regular check-in already exists: retain it for attendance and
            # payroll, and store the overtime session separately on the same
            # one-record-per-day attendance.
            has_regular_shift = bool(
                self._get_auto_checkout_cutoff(employee, existing.check_in))
            write_vals = reset_vals | {
                'overtime_check_in': check_in,
                'overtime_check_out': False,
            }
            if not has_regular_shift:
                # The day contains only overtime, so the latest repeated
                # check-in remains the visible check-in as required by the
                # overwrite rule.
                write_vals.update(vals)
            existing.write(write_vals)
            return existing, 'Đã bắt đầu làm thêm giờ. Hãy check-out khi kết thúc.'

        vals.update(reset_vals | {
            'overtime_check_in': False,
            'overtime_check_out': False,
        })
        existing.write(vals)
        return existing, 'Đã ghi đè giờ check-in hôm nay. Hãy check-out lại khi kết thúc làm việc.'

    @api.model_create_multi
    def create(self, vals_list):
        """Create or update the single attendance record for each local day."""
        employee_ids = sorted({
            vals.get('employee_id') for vals in vals_list if vals.get('employee_id')
        })
        for employee_id in employee_ids:
            # A transaction-scoped PostgreSQL advisory lock also covers two
            # simultaneous browser/API requests for the same employee.
            self._lock_employee_attendance(employee_id)
        attendances = self.browse()
        Employee = self.env['hr.employee'].sudo()
        for vals in vals_list:
            employee_id = vals.get('employee_id')
            check_in = vals.get('check_in')
            if employee_id and check_in:
                existing = self._get_workday_attendance(Employee.browse(employee_id), check_in)
                if existing:
                    attendance, _message = self._upsert_daily_check_in(existing, vals)
                    attendances |= attendance
                    continue
                employee = Employee.browse(employee_id)
                if not self._get_auto_checkout_cutoff(employee, check_in):
                    vals['overtime_check_in'] = check_in
            attendances |= super(HrAttendanceWithGeolocation, self).create([vals])
        return attendances

    def _get_workday_bounds(self, employee, target_dt=None):
        target_dt = target_dt or fields.Datetime.now()
        if isinstance(target_dt, str):
            target_dt = fields.Datetime.from_string(target_dt)
        tz_name = self._get_employee_timezone(employee)
        tz = ZoneInfo(tz_name)
        localized_dt = target_dt.replace(tzinfo=ZoneInfo('UTC')).astimezone(tz)
        calendar = self._get_employee_calendar(employee)
        if not calendar:
            return {
                'tz': tz_name,
                'start': None,
                'end': None,
            }

        weekday = str(localized_dt.weekday())
        attendances = calendar.attendance_ids.filtered(lambda a: a.dayofweek == weekday and not a.display_type)
        if not attendances:
            return {
                'tz': tz_name,
                'start': None,
                'end': None,
            }

        start_hour = min(attendances.mapped('hour_from'))
        end_hour = max(attendances.mapped('hour_to'))

        start_dt_local = localized_dt.replace(
            hour=int(start_hour),
            minute=int(round((start_hour % 1) * 60)),
            second=0,
            microsecond=0,
        )
        end_dt_local = localized_dt.replace(
            hour=int(end_hour),
            minute=int(round((end_hour % 1) * 60)),
            second=0,
            microsecond=0,
        )

        return {
            'tz': tz_name,
            'start': start_dt_local.astimezone(ZoneInfo('UTC')).replace(tzinfo=None),
            'end': end_dt_local.astimezone(ZoneInfo('UTC')).replace(tzinfo=None),
        }

    def _build_check_in_status(self, employee, check_in_dt):
        bounds = self._get_workday_bounds(employee, check_in_dt)
        planned_start = bounds.get('start')
        if not planned_start or check_in_dt <= planned_start:
            return {
                'is_late': False,
                'late_minutes': 0,
            }

        late_minutes = max(0, int((check_in_dt - planned_start).total_seconds() // 60))
        return {
            'is_late': late_minutes > 0,
            'late_minutes': late_minutes,
        }

    def _build_check_out_status(self, employee, check_out_dt):
        bounds = self._get_workday_bounds(employee, check_out_dt)
        planned_end = bounds.get('end')
        is_early_leave = bool(planned_end and check_out_dt < planned_end)
        early_minutes = 0
        if is_early_leave:
            early_minutes = max(0, int((planned_end - check_out_dt).total_seconds() // 60))
        return {
            'is_early_leave': is_early_leave,
            'early_minutes': early_minutes,
            'planned_end': planned_end,
        }

    @api.model
    def _cron_auto_check_out_at_cutoff(self):
        """Close forgotten open attendances at the employee-local 18:00 cutoff."""
        now_utc = fields.Datetime.now()
        closed_count = 0

        open_attendances = self.sudo().search([
            ('check_out', '=', False),
            ('check_in', '!=', False),
        ])
        for attendance in open_attendances:
            try:
                if attendance._auto_check_out_if_past_cutoff(now_utc):
                    closed_count += 1
            except OperationalError:
                # Let Odoo restart the whole transaction with a fresh snapshot
                # when a user checks out at the same instant as this job.
                raise
            except Exception:
                _logger.exception(
                    'Unable to auto check-out attendance %s for employee %s',
                    attendance.id, attendance.employee_id.id,
                )

        _logger.info('DAC Attendance: automatically checked out %s open attendance(s)', closed_count)
        return closed_count

    @api.model
    def _cron_auto_check_out_at_midnight(self):
        """Compatibility alias for the former midnight scheduled action."""
        return self._cron_auto_check_out_at_cutoff()

    def _auto_check_out_if_past_cutoff(self, now_utc=None):
        """Close this open attendance at the first local 18:00 after check-in.

        Besides the scheduled action, this is intentionally called while the
        systray refreshes the current employee's state.  It is a narrowly
        scoped fallback: a delayed/disabled cron must not leave an employee
        shown as checked in after their attendance day has ended.
        """
        self.ensure_one()
        now_utc = now_utc or fields.Datetime.now()
        employee = self.employee_id
        self._lock_employee_attendance(employee.id)
        # Another request may have checked out while this transaction waited
        # for the employee lock. Refresh before deciding which request wins.
        self.invalidate_recordset([
            'check_in', 'check_out', 'overtime_check_in', 'overtime_check_out',
        ])
        if self.check_out or not self.check_in:
            return False
        if self.overtime_check_in and not self.overtime_check_out:
            return False

        cutoff_utc = self._get_auto_checkout_cutoff(employee, self.check_in)
        if not cutoff_utc:
            return False
        if now_utc < cutoff_utc:
            return False

        checkout_status = self._build_check_out_status(employee, cutoff_utc)
        self.write({
            'check_out': cutoff_utc,
            'is_early_leave': checkout_status['is_early_leave'],
        })
        return True

    def _get_auto_checkout_cutoff(self, employee, check_in):
        """Return 18:00 for a normal shift.

        A check-in at/after 18:00 is deliberate overtime (the client asks for
        confirmation), so it requires a manual check-out and has no automatic
        cutoff.
        """
        employee_tz = ZoneInfo(self._get_employee_timezone(employee))
        check_in_local = check_in.replace(
            tzinfo=ZoneInfo('UTC')).astimezone(employee_tz)
        cutoff_local = datetime.combine(
            check_in_local.date(), time(hour=18), tzinfo=employee_tz)
        if check_in_local >= cutoff_local:
            return False
        return cutoff_local.astimezone(ZoneInfo('UTC')).replace(tzinfo=None)

    def _get_auto_checkout_message(self, employee, cutoff_utc):
        """Build a truthful message for the regular or overtime cutoff."""
        employee_tz = ZoneInfo(self._get_employee_timezone(employee))
        cutoff_local = cutoff_utc.replace(
            tzinfo=ZoneInfo('UTC')).astimezone(employee_tz)
        return f"Hệ thống đã tự check-out lúc {cutoff_local.strftime('%H:%M')}."

    @api.model
    def _cron_recompute_attendance_cutoffs(self):
        """One-time upgrade repair for stored dates and capped worked hours."""
        attendances = self.sudo().search([('check_in', '!=', False)])
        for attendance in attendances:
            attendance._compute_check_in_date()
            attendance._compute_worked_hours()
            attendance._compute_worked_hours_display()
        cron = self.env.ref(
            'dac_attendance.cron_recompute_attendance_cutoffs',
            raise_if_not_found=False,
        )
        if cron:
            # ir.cron refuses ORM writes to the job currently holding its
            # execution lock. This transaction already owns that row lock, so
            # disable the one-shot job directly and commit it with the repair.
            self.env.cr.execute(
                'UPDATE ir_cron SET active = FALSE WHERE id = %s', [cron.id])
        return len(attendances)

    def _get_shift_work_cutoff(self, employee, check_in):
        """Return 17:30 on the employee-local check-in date in UTC."""
        employee_tz = ZoneInfo(self._get_employee_timezone(employee))
        check_in_local = check_in.replace(
            tzinfo=ZoneInfo('UTC')).astimezone(employee_tz)
        cutoff_local = datetime.combine(
            check_in_local.date(), time(hour=17, minute=30), tzinfo=employee_tz)
        return cutoff_local.astimezone(ZoneInfo('UTC')).replace(tzinfo=None)

    def _is_after_shift_work_cutoff(self, employee, now_utc=None):
        """Return whether the employee-local time has reached 17:30."""
        now_utc = now_utc or fields.Datetime.now()
        employee_tz = ZoneInfo(self._get_employee_timezone(employee))
        now_local = now_utc.replace(
            tzinfo=ZoneInfo('UTC')).astimezone(employee_tz)
        return now_local.time().replace(tzinfo=None) >= time(hour=17, minute=30)

    def _is_after_auto_checkout_cutoff(self, employee, now_utc=None):
        """Compatibility alias for the former overtime-confirmation helper."""
        return self._is_after_shift_work_cutoff(employee, now_utc)

    def _auto_check_out_if_past_midnight(self, now_utc=None):
        """Compatibility alias retained for callers from an older deployment."""
        return self._auto_check_out_if_past_cutoff(now_utc)

    @api.model
    def dac_get_overtime_checkin_confirmation_info(self):
        """Tell the systray whether check-in requires overtime confirmation."""
        employee = self.env['hr.employee'].sudo().search(
            [('user_id', '=', self.env.uid)], limit=1)
        if not employee:
            return {'needs_confirmation': False}
        needs_confirmation = self._is_after_shift_work_cutoff(employee)
        return {
            'needs_confirmation': needs_confirmation,
            'message': (
                'Đã qua 17 giờ 30. Bạn muốn làm thêm giờ?'
                if needs_confirmation else False
            ),
        }

    @api.model
    def _cron_normalize_overnight_attendances(self):
        """One-time repair for historical attendances spanning midnight.

        Before the midnight auto-checkout rule existed, an employee who forgot
        to check out could leave a closed attendance spanning several days.
        The agreed business rule is that every attendance ends at the first
        local midnight after its check-in, so historical records follow that
        same rule.  The scheduled action disables itself once complete.
        """
        utc_tz = ZoneInfo('UTC')
        repaired_count = 0
        failed_count = 0
        attendances = self.sudo().search([
            ('check_in', '!=', False),
            ('check_out', '!=', False),
        ])

        for attendance in attendances:
            employee = attendance.employee_id
            try:
                employee_tz = ZoneInfo(self._get_employee_timezone(employee))
                check_in_local = attendance.check_in.replace(
                    tzinfo=utc_tz).astimezone(employee_tz)
                next_midnight_local = datetime.combine(
                    check_in_local.date() + timedelta(days=1), time.min,
                    tzinfo=employee_tz,
                )
                cutoff_utc = next_midnight_local.astimezone(utc_tz).replace(tzinfo=None)

                if attendance.check_out > cutoff_utc:
                    checkout_status = self._build_check_out_status(
                        employee, cutoff_utc - timedelta(microseconds=1))
                    attendance.write({
                        'check_out': cutoff_utc,
                        'is_early_leave': checkout_status['is_early_leave'],
                    })
                    repaired_count += 1
            except Exception:
                failed_count += 1
                _logger.exception(
                    'Unable to normalize overnight attendance %s for employee %s',
                    attendance.id, employee.id,
                )

        cron = self.env.ref(
            'dac_attendance.cron_normalize_overnight_attendances',
            raise_if_not_found=False,
        )
        if cron and not failed_count:
            # ir.cron locks its own row while executing, so ORM write() is
            # rejected here. Updating within the current transaction safely
            # marks this one-shot repair job inactive.
            self.env.cr.execute(
                'UPDATE ir_cron SET active = FALSE WHERE id = %s',
                [cron.id],
            )
        _logger.info(
            'DAC Attendance: normalized %s historical overnight attendance(s); %s failed',
            repaired_count, failed_count,
        )
        return repaired_count

    def _validate_gps_location(self, employee_id, latitude, longitude, accuracy=None):
        """
        Validate if GPS location is within allowed radius.
        Returns: (is_valid, distance_meters, error_message)
        """
        Config = self.env['hr.employee.attendance.config']
        config = Config.get_config_for_employee(employee_id)
        
        if not config or not config.allowed_gps_lat:
            # No config = no GPS validation required
            return True, 0, None
        
        distance = haversine_distance(
            config.allowed_gps_lat, config.allowed_gps_lng,
            latitude, longitude
        )
        
        max_distance = config.gps_radius_meters
        if distance > max_distance:
            return False, distance, f'Vị trí cách xa {distance:.0f}m (tối đa {max_distance}m)'
        
        return True, distance, None

    def _validate_ip_address(self, employee_id, ip_address):
        """
        Validate if IP address is within allowed range.
        Returns: (is_valid, error_message)
        """
        Config = self.env['hr.employee.attendance.config']
        config = Config.get_config_for_employee(employee_id)
        
        if not config or not config.validate_by_ip:
            return True, None
        
        if not config.allowed_ip_range:
            return True, None
        
        # Simple IP range validation (CIDR notation)
        import ipaddress
        try:
            allowed_network = ipaddress.ip_network(config.allowed_ip_range, strict=False)
            if ipaddress.ip_address(ip_address) not in allowed_network:
                return False, f'IP {ip_address} không nằm trong dải cho phép {config.allowed_ip_range}'
        except ValueError:
            # If CIDR is invalid, just allow any IP
            pass
        
        return True, None

    def _validate_wifi_ssid(self, employee_id, wifi_ssid):
        """
        Validate if WiFi SSID matches allowed SSID.
        Returns: (is_valid, error_message)
        """
        Config = self.env['hr.employee.attendance.config']
        config = Config.get_config_for_employee(employee_id)
        
        if not config or not config.validate_by_wifi:
            return True, None
        
        if not config.allowed_wifi_ssid:
            return True, None
        
        if wifi_ssid != config.allowed_wifi_ssid:
            return False, f'SSID "{wifi_ssid}" không được phép. Cần kết nối: {config.allowed_wifi_ssid}'
        
        return True, None

    @api.model
    def _create_attendance_with_geolocation(self, employee_id, action_time=None, values=None):
        """
        Create attendance record with geolocation validation.
        This is called by the kiosk/mobile check-in process.
        """
        values = values or {}
        
        # Validate GPS if provided
        if values.get('check_in_latitude') and values.get('check_in_longitude'):
            is_valid, distance, error = self._validate_gps_location(
                employee_id,
                values['check_in_latitude'],
                values['check_in_longitude'],
                values.get('check_in_gps_accuracy')
            )
            if not is_valid:
                raise ValidationError(f'Lỗi GPS: {error}')
            values['gps_validated'] = is_valid
        
        # Validate IP if provided
        if values.get('check_in_ip_address'):
            is_valid, error = self._validate_ip_address(
                employee_id,
                values['check_in_ip_address']
            )
            if not is_valid:
                raise ValidationError(f'Lỗi IP: {error}')
            values['ip_validated'] = is_valid
        
        # Validate WiFi if provided
        if values.get('check_in_wifi_ssid'):
            is_valid, error = self._validate_wifi_ssid(
                employee_id,
                values['check_in_wifi_ssid']
            )
            if not is_valid:
                raise ValidationError(f'Lỗi WiFi: {error}')
            values['wifi_validated'] = is_valid
        
        # Check if photo is required
        Config = self.env['hr.employee.attendance.config']
        config = Config.get_config_for_employee(employee_id)
        if config and config.photo_mandatory and not values.get('check_in_photo'):
            raise ValidationError('Yêu cầu chụp ảnh khi check-in')
        
        if values.get('check_in_photo'):
            values['photo_validated'] = True
            values['check_in_photo_timestamp'] = fields.Datetime.now()
        
        return self.create(values)

    @api.model
    def dac_check_in(self, data=None):
        """
        Check-in method callable from frontend via orm.call().
        Returns dict with success status and message.
        """
        import base64
        import json
        data = data or {}
        try:
            employee = self.env['hr.employee'].sudo().search([('user_id', '=', self.env.uid)], limit=1)
            if not employee:
                return {'success': False, 'message': 'Không tìm thấy nhân viên cho user hiện tại'}

            # Writes are elevated only after resolving the employee bound to
            # the current user. A repeated check-in overwrites that employee's
            # single record for the local workday.
            Attendance = self.sudo()
            Attendance._lock_employee_attendance(employee.id)

            Config = self.env['hr.employee.attendance.config']
            config = Config.get_config_for_employee(employee.id)
            method = config.check_in_method if config else 'location'

            check_in_time = fields.Datetime.now()
            timing_status = self._build_check_in_status(employee, check_in_time)
            success_message = (
                f"Bạn đã đi làm trễ {timing_status['late_minutes']} phút, hãy chú ý thời gian làm việc nhé!"
                if timing_status['is_late']
                else 'Chúc mừng bạn đã đi làm đúng giờ, hãy tiếp tục phát huy nhé'
            )
            vals = {
                'employee_id': employee.id,
                'check_in': check_in_time,
                'is_late': timing_status['is_late'],
                'late_minutes': timing_status['late_minutes'],
            }

            if method == 'location':
                validation_type = config.location_validation_type if config else 'ip'
                ip_valid, gps_valid = True, True
                ip_error, gps_error = None, None

                if validation_type in ('ip', 'ip_or_gps') and config and config.allowed_ip_range:
                    ip_addr = data.get('ip_address', '')
                    vals['check_in_ip_address'] = ip_addr
                    if ip_addr:
                        ip_valid, ip_error = self._validate_ip_simple(ip_addr, config.allowed_ip_range)
                        vals['ip_validated'] = ip_valid

                if validation_type in ('gps', 'ip_or_gps'):
                    lat = data.get('latitude')
                    lng = data.get('longitude')
                    if lat and lng:
                        vals['check_in_latitude'] = lat
                        vals['check_in_longitude'] = lng
                        vals['check_in_gps_accuracy'] = data.get('accuracy', 0)
                        if config and config.allowed_gps_lat:
                            gps_valid, gps_error = self._validate_gps_simple(
                                lat, lng, config.allowed_gps_lat,
                                config.allowed_gps_lng, config.gps_radius_meters)
                            vals['gps_validated'] = gps_valid
                    elif validation_type == 'gps':
                        gps_valid = False
                        gps_error = 'Không lấy được vị trí GPS'

                if validation_type == 'ip' and not ip_valid:
                    return {'success': False, 'message': f'Lỗi IP: {ip_error}'}
                elif validation_type == 'gps' and not gps_valid:
                    return {'success': False, 'message': f'Lỗi GPS: {gps_error}'}
                elif validation_type == 'ip_or_gps' and not ip_valid and not gps_valid:
                    return {'success': False, 'message': f'IP: {ip_error}. GPS: {gps_error}'}

            elif method == 'photo':
                photo_b64 = data.get('photo_base64')
                if not photo_b64:
                    return {'success': False, 'message': 'Vui lòng chụp ảnh để check-in'}
                vals['check_in_photo'] = photo_b64
                vals['check_in_photo_timestamp'] = fields.Datetime.now()
                vals['photo_validated'] = True
                metadata = data.get('photo_metadata', {})
                if metadata:
                    import json as json_mod
                    vals['check_in_photo_metadata'] = json_mod.dumps(metadata)
                    if metadata.get('latitude') and metadata.get('longitude'):
                        vals['check_in_latitude'] = metadata['latitude']
                        vals['check_in_longitude'] = metadata['longitude']

            existing_today = Attendance._get_workday_attendance(employee, check_in_time)
            if existing_today:
                attendance, success_message = Attendance._upsert_daily_check_in(
                    existing_today, vals)
            else:
                attendance = Attendance.create(vals)
            return {
                'success': True,
                'message': success_message,
                'attendance_id': attendance.id,
                'check_in_time': str(attendance.check_in),
                'is_late': attendance.is_late,
                'late_minutes': attendance.late_minutes,
            }
        except OperationalError:
            raise
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @api.model
    def dac_check_out(self, data=None):
        """Check-out method callable from frontend via orm.call()."""
        import json
        data = data or {}
        try:
            employee = self.env['hr.employee'].sudo().search([('user_id', '=', self.env.uid)], limit=1)
            if not employee:
                return {'success': False, 'message': 'Không tìm thấy nhân viên'}

            # Employees may read, but cannot write, hr.attendance records with
            # Odoo's standard "own attendance" group.  Use sudo only after
            # resolving the current user's employee and constrain the lookup
            # to that employee.
            self._lock_employee_attendance(employee.id)
            attendance = self.sudo().search([
                ('employee_id', '=', employee.id),
                ('check_out', '=', False),
            ], limit=1, order='check_in desc')

            if not attendance:
                latest = self.sudo().search([
                    ('employee_id', '=', employee.id),
                    ('check_in', '!=', False),
                    ('check_out', '!=', False),
                ], limit=1, order='check_in desc')
                now_utc = fields.Datetime.now()
                if latest:
                    cutoff_utc = self._get_auto_checkout_cutoff(
                        employee, latest.check_in)
                    if (cutoff_utc and latest.check_out == cutoff_utc
                            and now_utc >= cutoff_utc):
                        return {
                            'success': True,
                            'message': self._get_auto_checkout_message(
                                employee, cutoff_utc),
                            'attendance_id': latest.id,
                            'worked_hours': round(latest.worked_hours or 0, 2),
                            'is_early_leave': latest.is_early_leave,
                            'early_minutes': 0,
                            'auto_checked_out': True,
                        }
                return {'success': False, 'message': 'Không tìm thấy bản ghi check-in'}

            Config = self.env['hr.employee.attendance.config']
            config = Config.get_config_for_employee(employee.id)
            method = config.check_in_method if config else 'location'

            check_out_time = fields.Datetime.now()
            checkout_status = self._build_check_out_status(employee, check_out_time)
            success_message = f"Check-out thành công! Số giờ làm: {round(attendance.worked_hours or 0, 2)}h"
            if checkout_status['is_early_leave']:
                success_message += ' Bạn đã check-out sớm, hệ thống đã ghi nhận lại.'
            vals = {
                'check_out': check_out_time,
                'is_early_leave': checkout_status['is_early_leave'],
            }
            if attendance.overtime_check_in and not attendance.overtime_check_out:
                vals['overtime_check_out'] = check_out_time

            if method == 'photo':
                photo_b64 = data.get('photo_base64')
                if not photo_b64:
                    return {'success': False, 'message': 'Vui lòng chụp ảnh để check-out'}
                vals['check_out_photo'] = photo_b64
                vals['check_out_photo_timestamp'] = fields.Datetime.now()
                metadata = data.get('photo_metadata', {})
                if metadata:
                    vals['check_out_photo_metadata'] = json.dumps(metadata)

            if data.get('latitude') and data.get('longitude'):
                vals['check_out_latitude'] = data['latitude']
                vals['check_out_longitude'] = data['longitude']

            attendance.write(vals)
            worked_hours = round(attendance.worked_hours or 0, 2)
            success_message = f"Check-out thành công! Số giờ làm: {worked_hours}h"
            if checkout_status['is_early_leave']:
                success_message += ' Bạn đã check-out sớm, hệ thống đã ghi nhận lại.'
            return {
                'success': True,
                'message': success_message,
                'attendance_id': attendance.id,
                'worked_hours': worked_hours,
                'is_early_leave': attendance.is_early_leave,
                'early_minutes': checkout_status['early_minutes'],
            }
        except OperationalError:
            raise
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @api.model
    def dac_get_checkout_confirmation_info(self):
        employee = self.env['hr.employee'].sudo().search([('user_id', '=', self.env.uid)], limit=1)
        if not employee:
            return {
                'needs_confirmation': False,
            }

        now_dt = fields.Datetime.now()
        status = self._build_check_out_status(employee, now_dt)
        if not status['is_early_leave']:
            return {
                'needs_confirmation': False,
            }

        return {
            'needs_confirmation': True,
            'early_minutes': status['early_minutes'],
            'message': f'Bạn có muốn check-out sớm không? Hệ thống ghi nhận bạn về sớm {status["early_minutes"]} phút.',
        }

    def _validate_ip_simple(self, ip_address, allowed_range):
        """Simple IP validation. Returns (is_valid, error_msg)."""
        import ipaddress
        try:
            network = ipaddress.ip_network(allowed_range, strict=False)
            if ipaddress.ip_address(ip_address) not in network:
                return False, f'IP {ip_address} không trong dải {allowed_range}'
            return True, None
        except ValueError:
            return True, None

    def _validate_gps_simple(self, lat, lng, allowed_lat, allowed_lng, radius):
        """Simple GPS validation. Returns (is_valid, error_msg)."""
        distance = haversine_distance(allowed_lat, allowed_lng, lat, lng)
        if distance > radius:
            return False, f'Cách {distance:.0f}m (tối đa {radius}m)'
        return True, None
