from odoo import http
from odoo.http import request


class LeadLinkController(http.Controller):

    @http.route('/lead/<int:lead_id>', type='http', auth='user', methods=['GET'])
    def open_lead(self, lead_id, **kwargs):
        """Link rút gọn trong tin Zalo → trang chi tiết lead. Chưa đăng nhập thì Odoo chuyển sang trang đăng nhập
        rồi quay lại đây; quyền xem vẫn do ACL của dac.web.lead quyết định (link không lộ dữ liệu)."""
        return request.redirect('/odoo/action-dac_push.web_lead_action/%d' % lead_id)
