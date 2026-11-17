import json

from odoo import SUPERUSER_ID, api

from odoo.addons.dac_pancake_remind.models.pancake_api import attachment_lines, slim


def migrate(cr, version):
    """Tin đã lưu trước bản này: thu gọn dữ liệu gốc (chỉ giữ chữ, người gửi, link ảnh/file), điền link ảnh/file và mã người gửi."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    Message = env['dac.pancake.message']
    for message in Message.search([('sender_fm_id', '=', False)]):
        try:
            raw = json.loads(message.raw_json or '{}')
        except ValueError:
            continue
        if not isinstance(raw, dict):
            continue
        message.write({'raw_json': json.dumps(slim(raw), ensure_ascii=False), 'attachment_urls': attachment_lines(raw) or False,
                       'sender_fm_id': str((raw.get('from') or {}).get('id') or '')[:80] or False})
