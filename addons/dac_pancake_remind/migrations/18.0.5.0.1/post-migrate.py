"""Tiến độ cron lịch hẹn chuyển từ dòng cấu hình sang dac.pancake.followup.state (tránh hai cron ghi chung một bản ghi)."""
import json

KEY = 'dac_pancake_remind.followup_state_v501'


def migrate(cr, version):
    cr.execute("SELECT value FROM ir_config_parameter WHERE key = %s", (KEY,))
    row = cr.fetchone()
    if not row:
        return
    cursor, review_date, review_cursor, review_done, morning = json.loads(row[0])
    cr.execute("SELECT id FROM dac_pancake_followup_state ORDER BY id LIMIT 1")
    found = cr.fetchone()
    if found:
        cr.execute("""UPDATE dac_pancake_followup_state SET promise_cursor_id = %s, review_date = %s, review_cursor = %s, review_done = %s,
                      morning_sent_date = %s WHERE id = %s""", (cursor, review_date, review_cursor, review_done, morning, found[0]))
    else:
        cr.execute("""INSERT INTO dac_pancake_followup_state (promise_cursor_id, review_date, review_cursor, review_done, morning_sent_date, create_date, write_date)
                      VALUES (%s, %s, %s, %s, %s, now(), now())""", (cursor, review_date, review_cursor, review_done, morning))
    cr.execute("DELETE FROM ir_config_parameter WHERE key = %s", (KEY,))
