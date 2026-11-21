"""Tin nhắc khách mới nhẹ nhàng hơn: lần đầu "Dạ có khách mới liên hệ {chu_de} ạ" (không nêu khách chờ bao lâu), nhắc lại như lead.
Chỉ đổi mẫu tin khi quản trị viên chưa sửa (đang đúng mẫu mặc định cũ)."""

CHANGES = {
    'new_first': ('{muc_do} · Khách mới chờ {cho}\n{tom_tat}\n{link}', 'Dạ có khách mới liên hệ {chu_de} ạ\n{link}',
                  'chu_de, link, ten_khach, tom_tat, muc_do, icon, cho, loai_khach'),
    'new_repeat': ('{icon} Nhắc lần {lan}/{toi_da} · khách chờ {cho}', 'Dạ khách này mình đã phản hồi chưa ạ?',
                   'ten_khach, chu_de, icon, lan, toi_da, cho'),
}


def migrate(cr, version):
    for code, (old, new, placeholders) in CHANGES.items():
        cr.execute("""UPDATE dac_pancake_remind_scenario SET template = %s WHERE code = %s AND (template = %s OR template IS NULL)""",
                   (new, code, old))
        cr.execute("""UPDATE dac_pancake_remind_scenario SET default_template = %s, placeholders = %s WHERE code = %s""",
                   (new, placeholders, code))
