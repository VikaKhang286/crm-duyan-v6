import secrets


def migrate(cr, version):
    """1.2.0 thêm cột access_token; Odoo gán MỘT giá trị mặc định cho mọi lead cũ nên ràng buộc unique không tạo được.
    Cấp lại mã riêng cho mọi lead bị trống hoặc trùng, trước khi Odoo thêm lại ràng buộc."""
    cr.execute("""
        SELECT id FROM dac_web_lead
         WHERE access_token IS NULL
            OR access_token IN (SELECT access_token FROM dac_web_lead GROUP BY access_token HAVING count(*) > 1)
         ORDER BY id""")
    ids = [row[0] for row in cr.fetchall()]
    cr.execute("SELECT access_token FROM dac_web_lead WHERE access_token IS NOT NULL")
    used = {row[0] for row in cr.fetchall()}
    for lead_id in ids:
        token = secrets.token_urlsafe(9)
        while token.isdigit() or token in used:
            token = secrets.token_urlsafe(9)
        used.add(token)
        cr.execute("UPDATE dac_web_lead SET access_token = %s WHERE id = %s", (token, lead_id))
