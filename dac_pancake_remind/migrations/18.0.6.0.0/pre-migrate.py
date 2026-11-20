"""Thông số nhắc chuyển từ dòng cấu hình sang Kịch bản nhắc: giữ giá trị trước khi Odoo xoá các cột cũ."""
import json

KEY = 'dac_pancake_remind.scenario_values_v600'
FIELDS = ('wait_minutes', 'repeat_minutes', 'max_reminders', 'ongoing_wait_minutes', 'old_wait_minutes', 'digest_minutes',
          'followup_enabled', 'morning_review', 'only_new_customers')


def migrate(cr, version):
    cr.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'dac_pancake_remind_config'")
    present = [f for f in FIELDS if f in {row[0] for row in cr.fetchall()}]
    if not present:
        return
    cr.execute('SELECT %s FROM dac_pancake_remind_config ORDER BY id LIMIT 1' % ', '.join(present))
    row = cr.fetchone()
    if row:
        cr.execute("""INSERT INTO ir_config_parameter (key, value, create_date, write_date) VALUES (%s, %s, now(), now())
                      ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""", (KEY, json.dumps(dict(zip(present, row)))))
