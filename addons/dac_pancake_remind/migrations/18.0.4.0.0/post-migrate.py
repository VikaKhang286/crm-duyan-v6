from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Nhắc v2: khách mới nhắc lại mỗi 10 phút tới khi có người trả lời, khách cũ cũng được nhắc (một lần, sau 30 phút)."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['dac.pancake.remind.config'].search([]).write({'repeat_minutes': 10, 'old_wait_minutes': 30, 'only_new_customers': False, 'ai_check': True})
    event_type = env.ref('dac_pancake_remind.event_type_pancake_unanswered', raise_if_not_found=False)
    if event_type:
        event_type.description = ('Khách nhắn page Pancake chưa được nhân viên trả lời: khách mới sau 15 phút (rồi nhắc lại mỗi 10 phút bằng '
                                  'cách trả lời vào ảnh đã gửi), khách cũ sau 30 phút (một lần). AI đọc tin trước để bỏ qua khách không cần trả lời.')
