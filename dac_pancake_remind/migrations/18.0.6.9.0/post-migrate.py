"""Nhắc lại khách mới: mức gấp tăng dần theo lượt (🔔 → ⚠️ → 🚨), câu nhắc riêng cho lần 2/3/4, lần 4 cách lần 3 một tiếng.
Chỉ đổi mẫu tin khi quản trị viên chưa sửa."""
OLD = 'Dạ khách này mình đã phản hồi chưa ạ?'
NEW = '{icon} {loi_nhac}'
PHRASES = 'Dạ khách này mình đã phản hồi chưa ạ?\nDạ khách vẫn đang chờ phản hồi ạ, anh chị xem giúp em nhé!\nDạ khách chờ lâu rồi ạ, nhờ anh chị phản hồi khách ngay giúp em!'


def migrate(cr, version):
    cr.execute("UPDATE dac_pancake_remind_scenario SET template = %s WHERE code = 'new_repeat' AND (template = %s OR template IS NULL)", (NEW, OLD))
    cr.execute("UPDATE dac_pancake_remind_scenario SET line_template = %s WHERE code = 'new_repeat' AND line_template IS NULL", (PHRASES,))
    cr.execute("""UPDATE dac_pancake_remind_scenario SET default_template = %s, default_line_template = %s,
                  placeholders = 'icon, loi_nhac, muc_do, ten_khach, chu_de, lan, toi_da, cho',
                  wait_label = 'Lần nhắc cuối cách lần trước (phút làm việc)', wait_minutes = coalesce(wait_minutes, 60)
                  WHERE code = 'new_repeat'""", (NEW, PHRASES))
    cr.execute("UPDATE dac_pancake_remind_scenario SET wait_minutes = 60 WHERE code = 'new_repeat' AND coalesce(wait_minutes, 0) = 0")
