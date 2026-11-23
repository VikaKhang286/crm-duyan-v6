"""1.10.3: thêm vào Định hướng của quản lý: khách không được biết đang nhắn với AI/chatbot (không tự giới thiệu)."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    from odoo.addons.dac_chat_playbook.models.chat_config import IDENTITY_GUIDANCE
    config = env['dac.chat.config']._get()
    guidance = (config.agent_guidance or '').strip()
    if 'AI/chatbot' not in guidance:
        config.agent_guidance = ('%s\n%s' % (guidance, IDENTITY_GUIDANCE)).strip()
