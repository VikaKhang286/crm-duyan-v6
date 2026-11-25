"""1.14.0 (góp ý quản lý 25/11/2026): không nói "chưa chắc" về việc DAC có làm hay không.

- Thêm vào Định hướng của quản lý.
- Sửa câu chữ các quy tắc đang dạy bot nói "chưa chắc" (quy tắc chuyển nhân viên khi hỏi sản phẩm ngoài danh mục): giữ việc chuyển
  nhân viên, bỏ câu "nói rõ là chưa chắc". Ghi lại trong review_note để biết đã sửa."""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)
REPLACEMENTS = [
    ('nói rõ là chưa chắc và chuyển nhân viên để gợi ý phương án thay thế. Không im lặng.',
     'không nói "chưa chắc": hỏi thêm khách cần dùng cho đâu, kích thước, số lượng, rồi chuyển nhân viên tư vấn phương án. Không im lặng.'),
    ('Nói rõ là chưa chắc, ghi nhận câu hỏi', 'Không nói "chưa chắc"; ghi nhận câu hỏi'),
    ('Dạ loại này bên em chưa chắc làm được ạ, em chuyển nhân viên xem và gợi ý phương án phù hợp cho mình nha.',
     'Dạ bên em có làm ạ. Anh/chị cần dùng cho đâu và số lượng khoảng bao nhiêu để em tư vấn cho mình nha?'),
]


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    from odoo.addons.dac_chat_playbook.models.chat_config import UNSURE_GUIDANCE
    config = env['dac.chat.config']._get()
    if 'chưa chắc' not in (config.agent_guidance or ''):
        config.agent_guidance = ('%s\n%s' % ((config.agent_guidance or '').strip(), UNSURE_GUIDANCE)).strip()
    rules = env['dac.chat.rule'].with_context(active_test=False).search([('state', 'in', ('approved', 'proposed'))])
    for rule in rules:
        vals = {}
        for field in ('content', 'example'):
            text = rule[field] or ''
            for old, new in REPLACEMENTS:
                text = text.replace(old, new)
            if text != (rule[field] or ''):
                vals[field] = text
        if vals:
            vals['review_note'] = ((rule.review_note or '') + '\n25/11/2026: bỏ câu "chưa chắc" theo góp ý quản lý.').strip()
            rule.write(vals)
            _logger.info('dac_chat_playbook: sửa quy tắc #%s bỏ "chưa chắc"', rule.id)
