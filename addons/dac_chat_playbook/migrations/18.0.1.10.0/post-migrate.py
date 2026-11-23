"""1.10.0: chọn sẵn người duyệt lời chào là chat riêng Zalo của Dương Tuấn Kiệt (nếu có); chào khách mới vẫn TẮT cho tới khi quản lý bật."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    config = env['dac.chat.config']._get()
    if config.greeting_approver_channel_id:
        return
    channel = env['dac.zalo.channel'].search([('thread_type', '=', 'user'), ('name', 'ilike', 'Kiệt')], limit=1)
    if channel:
        config.greeting_approver_channel_id = channel
