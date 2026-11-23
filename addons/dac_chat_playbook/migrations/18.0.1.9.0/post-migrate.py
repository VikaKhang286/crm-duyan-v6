"""1.9.0: giờ làm việc trong kiến thức doanh nghiệp theo giờ nội bộ (có nghỉ trưa, Chủ nhật nghỉ) và điền sẵn Định hướng của quản lý.

Dữ liệu kiến thức là noupdate nên nâng cấp không tự ghi đè: chỉ sửa khi mục còn nguyên chữ cũ lấy từ website (quản lý chưa sửa tay)."""
from odoo import SUPERUSER_ID, api

HOURS = ('Thứ 2 đến thứ 7: 8:00–12:00 và 13:30–17:30 (nghỉ trưa 12:00–13:30). Chủ nhật nghỉ. '
         'Khi hẹn khách, luôn dùng mốc làm việc kế tiếp trong phần Bối cảnh.')


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    item = env.ref('dac_chat_playbook.kb_contact_hours', raise_if_not_found=False)
    if item and '08:00–18:00' in (item.content or ''):
        item.write({'name': 'Giờ làm việc', 'content': HOURS})
    from odoo.addons.dac_chat_playbook.models.chat_config import DEFAULT_GUIDANCE
    config = env['dac.chat.config']._get()
    if not (config.agent_guidance or '').strip():
        config.agent_guidance = DEFAULT_GUIDANCE
