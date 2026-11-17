from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Page đã thêm trước bản này: lấy lại Page ID gọi API từ token (mã người dùng nhập được giữ làm mã trên link)."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    for page in env['dac.pancake.page'].with_context(active_test=False).search([]):
        page.write({'page_fm_id': page.page_slug or page.page_fm_id})
