"""1.16.0: nhóm báo nhân viên vào tiếp = nhóm thử "Beework Test" (quản lý chọn 25/11/2026; đổi sang nhóm sale thật khi đã ổn)."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    config = env['dac.chat.config']._get()
    if not config.greeting_handoff_channel_id:
        channel = env['dac.zalo.channel'].search([('thread_type', '=', 'group'), ('name', '=', 'Beework Test')], limit=1)
        if channel:
            config.greeting_handoff_channel_id = channel
