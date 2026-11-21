def migrate(cr, version):
    """3 bộ prompt đổi thành Chào hỏi / Hỏi thông tin / Tư vấn: đổi mã cũ trước khi Odoo nạp danh sách lựa chọn mới."""
    cr.execute("UPDATE dac_chat_prompt SET mode = 'info' WHERE mode = 'info_basic'")
    cr.execute("UPDATE dac_chat_prompt SET mode = 'consult' WHERE mode = 'info_full'")
