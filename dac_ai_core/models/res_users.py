from odoo import fields, models


class ResUsers(models.Model):
    _inherit = 'res.users'

    dac_ai_model_id = fields.Many2one('dac.ai.model', string='Model AI riêng', ondelete='set null', groups='base.group_system',
        domain="[('selected', '=', True), ('verified', '=', True), ('connection_id.active', '=', True)]",
        help='Admin chọn model cho trợ lý của nhân viên này (trò chuyện với Trợ lý Zalo, Trợ lý DAC...). Model này dùng trước; '
             'lỗi hoặc quá tải thì dùng thứ tự chung ở Kết nối AI dùng chung. Để trống = theo thứ tự chung.')
