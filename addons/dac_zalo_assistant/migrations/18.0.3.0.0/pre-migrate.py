"""Trước khi bỏ các ô API AI riêng của Trợ lý Zalo: giữ lại giá trị để post-migrate chuyển sang Kết nối AI dùng chung."""
import json

KEY = 'dac_zalo_assistant.ai_settings_v300'


def _columns(cr, table):
    cr.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %s", (table,))
    return {row[0] for row in cr.fetchall()}


def migrate(cr, version):
    cols = _columns(cr, 'dac_zalo_config')
    if not cols:
        return
    saved = {}
    if {'tool_ai_base_url', 'tool_ai_api_key'} <= cols:
        cr.execute("""SELECT c.tool_ai_base_url, c.tool_ai_api_key, m.name, c.tool_ai_enabled, c.tool_ai_verified
                        FROM dac_zalo_config c LEFT JOIN dac_zalo_tool_model m ON m.id = c.tool_ai_model_id ORDER BY c.id LIMIT 1""")
        saved['tool'] = cr.fetchone()
    if {'gemini_api_key', 'gemini_model_id'} <= cols:
        cr.execute("""SELECT c.gemini_api_key, m.name, c.gemini_enabled
                        FROM dac_zalo_config c LEFT JOIN dac_zalo_gemini_model m ON m.id = c.gemini_model_id ORDER BY c.id LIMIT 1""")
        saved['gemini'] = cr.fetchone()
    if {'ai_base_url', 'ai_api_key', 'ai_model'} <= cols:
        cr.execute("SELECT ai_base_url, ai_api_key, ai_model FROM dac_zalo_config ORDER BY id LIMIT 1")
        saved['chat'] = cr.fetchone()
    cr.execute("""INSERT INTO ir_config_parameter (key, value, create_date, write_date) VALUES (%s, %s, now(), now())
                  ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""", (KEY, json.dumps(saved)))
    # View tab Gemini/AI xử lý việc cũ (file đã bỏ) còn trỏ tới các trường sắp xoá: gỡ trước để form chính kiểm tra được
    cr.execute("""DELETE FROM ir_ui_view WHERE id IN (SELECT res_id FROM ir_model_data
                  WHERE module = 'dac_zalo_assistant' AND model = 'ir.ui.view' AND name = 'config_form_gemini')""")
    cr.execute("DELETE FROM ir_model_data WHERE module = 'dac_zalo_assistant' AND model = 'ir.ui.view' AND name = 'config_form_gemini'")
