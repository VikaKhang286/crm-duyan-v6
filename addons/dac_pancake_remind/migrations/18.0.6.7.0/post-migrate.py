"""Tin có người thả cảm xúc bị lưu loại "reaction" dù có nội dung thật (vd ảnh nhân viên gửi trả lời khách) → lấy lại loại đúng."""
import json
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("SELECT id, raw_json FROM dac_pancake_message WHERE type_content = 'reaction'")
    fixed = {}
    for message_id, raw_json in cr.fetchall():
        try:
            raw = json.loads(raw_json or '{}')
        except ValueError:
            continue
        content = [a for a in (raw.get('attachments') or []) if isinstance(a, dict) and a.get('type') and a.get('type') != 'reaction']
        if content:
            fixed.setdefault(content[0]['type'][:64], []).append(message_id)
    for kind, ids in fixed.items():
        cr.execute('UPDATE dac_pancake_message SET type_content = %s WHERE id = ANY(%s)', (kind, ids))
    _logger.info('Pancake: sửa loại %d tin có cảm xúc kèm nội dung: %s', sum(map(len, fixed.values())),
                 {k: len(v) for k, v in fixed.items()})
