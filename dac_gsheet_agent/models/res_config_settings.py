import json

from odoo import api, fields, models
from odoo.exceptions import UserError

from .gsheet_client import GSheetError, load_service_account
from .llm_client import LLMClient, LLMError

PARAM = 'dac_gsheet_agent.'


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    gsa_service_account_json = fields.Char(
        string='Service Account JSON', config_parameter=PARAM + 'service_account_json',
        help='Dán toàn bộ nội dung file JSON key của Google service account.')
    gsa_service_account_email = fields.Char(
        string='Email service account', compute='_compute_gsa_service_account_email')

    gsa_llm_provider = fields.Selection([
        ('anthropic', 'Claude (Anthropic API)'),
        ('openai_compat', 'OpenAI-compatible (9router, Gemini...)'),
    ], string='Loại AI endpoint', default='anthropic', config_parameter=PARAM + 'llm_provider')
    gsa_llm_base_url = fields.Char(
        string='Base URL', config_parameter=PARAM + 'llm_base_url',
        help='Bỏ trống với Claude để dùng https://api.anthropic.com. '
             'Với 9router: https://9router.duyan.vn/v1')
    gsa_llm_api_key = fields.Char(string='API key', config_parameter=PARAM + 'llm_api_key')
    gsa_llm_model = fields.Char(
        string='Model', default='claude-opus-5-5', config_parameter=PARAM + 'llm_model')
    gsa_llm_effort = fields.Selection([
        ('low', 'Thấp (nhanh, rẻ)'), ('medium', 'Vừa'), ('high', 'Cao (kỹ hơn)'),
    ], string='Mức suy nghĩ (Claude)', default='medium', config_parameter=PARAM + 'llm_effort')
    gsa_llm_use_fallbacks = fields.Boolean(
        string='Tự chuyển model dự phòng khi bị từ chối (Claude)', default=True,
        config_parameter=PARAM + 'llm_use_fallbacks')
    gsa_rule_require_review = fields.Boolean(
        string='Quy tắc AI tự rút ra cần quản lý duyệt',
        config_parameter=PARAM + 'rule_require_review',
        help='Bật: quy tắc mới ở trạng thái "Chờ duyệt", chưa đưa vào ngữ cảnh agent.')

    @api.depends('gsa_service_account_json')
    def _compute_gsa_service_account_email(self):
        for rec in self:
            try:
                rec.gsa_service_account_email = json.loads(rec.gsa_service_account_json or '{}').get('client_email', '')
            except ValueError:
                rec.gsa_service_account_email = 'JSON không hợp lệ'

    def action_gsa_test_llm(self):
        self.ensure_one()
        self.execute()
        try:
            client = LLMClient(self.env['dac.gsheet.source'].get_llm_config())
            answer = client.complete('Bạn là trợ lý kiểm tra kết nối.', 'Trả lời đúng một từ: OK')
        except LLMError as exc:
            raise UserError(str(exc)) from exc
        return self._gsa_notify('Kết nối AI thành công', 'Model trả lời: %s' % (answer or '(trống)')[:200])

    def action_gsa_check_service_account(self):
        self.ensure_one()
        self.execute()
        try:
            info = load_service_account(self.gsa_service_account_json)
        except GSheetError as exc:
            raise UserError(str(exc)) from exc
        return self._gsa_notify('Service account hợp lệ',
                                'Chia sẻ file Google Sheet cho: %s' % info['client_email'])

    def _gsa_notify(self, title, message):
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': title, 'message': message, 'sticky': True, 'type': 'success'}}
