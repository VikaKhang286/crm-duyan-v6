"""1.12.2: "Cải tiến prompt từ góp ý" đổi thinking Tối đa → Cao.

Đo thật 23/11/2026: Claude Haiku qua 9router ở mức Tối đa suy nghĩ tới hết giới hạn token (32–64k) mà không gọi tool (không ra kết quả),
mất 2–4 phút rồi phải dùng model dự phòng; mức Cao xong trong ~30 giây."""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    task = env['dac.chat.task'].search([('code', '=', 'improve_prompt')], limit=1)
    if task.thinking == 'max':
        task.thinking = 'high'
