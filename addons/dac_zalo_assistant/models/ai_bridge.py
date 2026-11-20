"""Trợ lý Zalo dùng Kết nối AI dùng chung (addon dac_ai_core): không còn giữ API key riêng.

Giữ nguyên tên hàm cũ để addon khác (dac_push, dac_pancake_remind) gọi như trước:
`config._ai_ready()`, `config._ai_json(system, text, schema, name=...)` → (dict, 'openai'|'gemini'), `config._ask_ai(messages)` → câu trả lời.
"""
from odoo import _, fields, models


class ZaloConfigAI(models.Model):
    _inherit = 'dac.zalo.config'

    ai_connection_summary = fields.Char(string='Kết nối AI đang dùng', compute='_compute_ai_connection_summary')

    def _compute_ai_connection_summary(self):
        chain = self.env['dac.ai.connection']._chain()
        text = ' → '.join('%s (%s)' % (c.name, c.model_summary or '?') for c in chain) or _('Chưa có kết nối AI nào sẵn sàng')
        for config in self:
            config.ai_connection_summary = text

    def _ai_ready(self):
        return self.env['dac.ai.connection']._ready()

    def _ai_json(self, system, text, schema, name='report', description='Trả kết quả phân tích.', timeout=(10, 60)):
        return self.env['dac.ai.connection']._json(system, text, schema, name=name, description=description, timeout=timeout)

    def action_open_ai_connections(self):
        return self.env['ir.actions.actions']._for_xml_id('dac_ai_core.ai_connection_action')
