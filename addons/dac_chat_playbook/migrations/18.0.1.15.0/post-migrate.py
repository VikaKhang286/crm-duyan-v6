"""1.15.0 (góp ý quản lý 25/11/2026): bot nói như MỘT nhân viên, hỏi tiếp để giữ cuộc trò chuyện, không hẹn "liên hệ sau" trong giờ."""
from odoo import SUPERUSER_ID, api

LINES = [
    '- Khách chỉ nhắn với MỘT người: không nhắc "bạn phụ trách", nhân viên khác, bộ phận, không nói chuyển người. Cần người thật thì '
    'hệ thống tự gọi nhân viên vào, bot chỉ nhắn câu giữ chuyện tự nhiên.',
    '- Bot hỏi tiếp để giữ cuộc trò chuyện như nhân viên giỏi (có file thiết kế chưa, kích thước ngang x cao / chiều ngang mặt tiền, '
    'dùng cho đâu…). Trong giờ KHÔNG hẹn "liên hệ sau"; buổi tối, Chủ nhật, ngày lễ vẫn hỏi đủ thông tin rồi mới hẹn mốc làm việc kế tiếp.',
]


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    config = env['dac.chat.config']._get()
    guidance = (config.agent_guidance or '').strip()
    if 'MỘT người' not in guidance:
        config.agent_guidance = '\n'.join([guidance] + LINES).strip()
