"""Chuyển API AI riêng của Trợ lý Zalo (AI xử lý việc, Gemini, AI trò chuyện) thành các dòng Kết nối AI dùng chung.

Gemini đang bật (đã chạy được) giữ nguyên trạng thái dùng được; kết nối khác cần bấm "Kiểm tra gọi tool" mới được dùng."""
import json

from odoo import SUPERUSER_ID, api

KEY = 'dac_zalo_assistant.ai_settings_v300'


def migrate(cr, version):
    cr.execute("SELECT value FROM ir_config_parameter WHERE key = %s", (KEY,))
    row = cr.fetchone()
    if not row:
        return
    saved = json.loads(row[0] or '{}')
    env = api.Environment(cr, SUPERUSER_ID, {})
    Connection = env['dac.ai.connection'].with_context(active_test=False)

    def add(name, sequence, kind, key, model, base_url=None, active=True, verified=False, note=None):
        if not key:
            return
        connection = Connection.create({'name': name, 'sequence': sequence, 'kind': kind, 'base_url': base_url, 'api_key': key,
                                        'active': active, 'note': note})
        if model:
            env['dac.ai.model'].create({'connection_id': connection.id, 'name': model, 'sequence': 1, 'selected': True,
                                        'verified': bool(verified)})

    tool = saved.get('tool')
    if tool and tool[0]:
        url, key, model, enabled, verified = tool
        add('AI xử lý việc (từ Trợ lý Zalo)', 10, 'openai', key, model, url, active=True, verified=bool(enabled and verified))
    gemini = saved.get('gemini')
    if gemini:
        key, model, enabled = gemini
        add('Google Gemini (từ Trợ lý Zalo)', 20, 'gemini', key, model, active=bool(enabled), verified=bool(enabled))
    chat = saved.get('chat')
    if chat and chat[1]:
        url, key, model = chat
        add('AI trò chuyện cũ của Trợ lý Zalo', 30, 'openai', key, model, url or 'https://api.openai.com/v1', active=False,
            note='Chuyển từ tab Trợ lý AI cũ; bật và bấm Kiểm tra nếu muốn dùng.')
    cr.execute("DELETE FROM ir_config_parameter WHERE key = %s", (KEY,))
