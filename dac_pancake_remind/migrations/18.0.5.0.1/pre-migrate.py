"""Giữ tiến độ cron lịch hẹn trước khi Odoo xoá các cột cũ trên dòng cấu hình (post-migrate chép sang dac.pancake.followup.state)."""
import json


def migrate(cr, version):
    cr.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'dac_pancake_remind_config' AND column_name = 'promise_cursor_id'")
    if not cr.fetchone():
        return
    cr.execute("SELECT promise_cursor_id, review_date::text, review_cursor, review_done, morning_sent_date::text FROM dac_pancake_remind_config ORDER BY id LIMIT 1")
    row = cr.fetchone()
    if row:
        cr.execute("""INSERT INTO ir_config_parameter (key, value, create_date, write_date) VALUES ('dac_pancake_remind.followup_state_v501', %s, now(), now())
                      ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""", (json.dumps(row),))
