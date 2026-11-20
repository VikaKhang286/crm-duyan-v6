"""Nhắc khách v3: khách mới 15 → 10 phút, tối đa 4 lần nhắc; cập nhật mô tả loại thông báo (dữ liệu noupdate)."""


def migrate(cr, version):
    cr.execute("UPDATE dac_pancake_remind_config SET wait_minutes = 10 WHERE wait_minutes = 15")
    cr.execute("UPDATE dac_pancake_remind_config SET max_reminders = 4 WHERE max_reminders IS NULL OR max_reminders < 4")
    cr.execute("""UPDATE dac_zalo_event_type SET description = %s WHERE code = 'pancake.unanswered'""", (
        'Khách nhắn page Pancake mà quá hạn chưa có nhân viên trả lời: khách mới 10 phút (nhắc riêng có ảnh, tối đa 4 lần), '
        'đang trao đổi 15 phút, khách cũ 30 phút (gom vào một tin mỗi 30 phút). Kèm mức độ gấp do AI đánh giá: 🚨 Khẩn · ⚠️ Chú ý · 🔔 Thường.',))
