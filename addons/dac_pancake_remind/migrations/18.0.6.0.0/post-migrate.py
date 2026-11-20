"""Áp thông số đang dùng (đã giữ ở pre-migrate) vào các Kịch bản nhắc vừa tạo."""
import json

KEY = 'dac_pancake_remind.scenario_values_v600'


def migrate(cr, version):
    cr.execute("SELECT value FROM ir_config_parameter WHERE key = %s", (KEY,))
    row = cr.fetchone()
    if not row:
        return
    old = json.loads(row[0] or '{}')

    def put(code, **vals):
        vals = {k: v for k, v in vals.items() if v is not None}
        if vals:
            cr.execute('UPDATE dac_pancake_remind_scenario SET %s WHERE code = %%s' % ', '.join('%s = %%s' % k for k in vals),
                       (*vals.values(), code))

    put('new_first', wait_minutes=old.get('wait_minutes'))
    put('new_repeat', repeat_minutes=old.get('repeat_minutes'), max_count=old.get('max_reminders'))
    put('ongoing', wait_minutes=old.get('ongoing_wait_minutes'), enabled=False if old.get('only_new_customers') else None)
    put('old', wait_minutes=old.get('old_wait_minutes'), enabled=False if old.get('only_new_customers') else None)
    put('digest', repeat_minutes=old.get('digest_minutes'))
    if old.get('followup_enabled') is False:
        put('appt_pre', enabled=False)
        put('appt_post', enabled=False)
    if old.get('morning_review') is False:
        put('morning', enabled=False)
    cr.execute("DELETE FROM ir_config_parameter WHERE key = %s", (KEY,))
