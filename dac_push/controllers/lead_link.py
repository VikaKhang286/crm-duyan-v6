import re

from odoo import fields, http
from odoo.http import request

TZ = 'Asia/Ho_Chi_Minh'
NO_INDEX = {'X-Robots-Tag': 'noindex, nofollow', 'Referrer-Policy': 'no-referrer', 'Cache-Control': 'no-store'}


class LeadLinkController(http.Controller):

    @http.route('/lead/<int:lead_id>', type='http', auth='user', methods=['GET'])
    def open_lead(self, lead_id, **kwargs):
        """Link cũ theo số thứ tự (tin Zalo đã gửi trước đây) → bắt đăng nhập rồi mở form; quyền xem do ACL quyết định.
        Link mới trong tin Zalo dùng mã ngẫu nhiên (route bên dưới), không dùng số thứ tự vì đoán được."""
        return request.redirect('/odoo/action-dac_push.web_lead_action/%d' % lead_id)

    def _lead_by_token(self, slug):
        """`slug` là `<số điện thoại>-<mã>` (link mới) hoặc chỉ `<mã>` (link đã gửi trước đây). Chỉ mã quyết định quyền truy cập;
        số điện thoại trong link để người nhận nhìn là biết lead nào."""
        if not slug or slug.isdigit() or len(slug) > 80:
            return None
        candidates = [slug]
        prefixed = re.match(r'^\d{6,15}-(.+)$', slug)
        if prefixed:
            candidates.append(prefixed.group(1))
        for token in candidates:
            # sudo(): khách chưa đăng nhập mở link — quyền truy cập chính là mã ngẫu nhiên trong link, tra đúng một bản ghi theo mã
            lead = request.env['dac.web.lead'].sudo().search([('access_token', '=', token)], limit=1)
            if lead:
                return lead
        return None

    @http.route(['/lead/<string:token>', '/l/<string:token>'], type='http', auth='public', methods=['GET'], sitemap=False)
    def lead_public(self, token, done=None, **kwargs):
        """Trang công khai (không cần đăng nhập): chỉ thông tin lead + 2 nút. Nhân viên đã đăng nhập có quyền xem
        thì được chuyển thẳng sang form đầy đủ trong Odoo."""
        lead = self._lead_by_token(token)
        if not lead:
            return request.make_response('Liên kết không tồn tại hoặc đã bị thu hồi.', status=404,
                                         headers=[('Content-Type', 'text/plain; charset=utf-8'), *NO_INDEX.items()])
        if not request.env.user._is_public() and request.env['dac.web.lead'].browse(lead.id).has_access('read'):
            return request.redirect('/odoo/action-dac_push.web_lead_action/%d' % lead.id)

        def local(dt):
            return fields.Datetime.context_timestamp(lead.with_context(tz=TZ), dt).strftime('%H:%M %d/%m/%Y') if dt else ''

        extra = [ln.strip() for ln in (lead.extra_info or '').splitlines() if ln.strip()]
        html = request.env['ir.qweb']._render('dac_push.lead_public_page', {
            'lead': lead, 'token': token, 'received': local(lead.received_at), 'state_at': local(lead.contact_state_at),
            'extra': extra, 'visited': [ln for ln in (lead.visited_urls or '').splitlines() if ln.strip()],
            'just_saved': bool(done),
            'form_label': dict(lead._fields['form_type'].selection).get(lead.form_type, ''),
        })
        # doctype thêm ở đây vì QWeb escape mọi chuỗi trong template (và str + Markup cũng escape, nên phải str(html)); không có doctype thì trình duyệt vào quirks mode
        return request.make_response('<!DOCTYPE html>' + str(html), headers=[('Content-Type', 'text/html; charset=utf-8'), *NO_INDEX.items()])

    @http.route('/lead/<string:token>/contact', type='http', auth='public', methods=['POST'], csrf=False, sitemap=False)
    def lead_public_contact(self, token, result=None, **kwargs):
        """Hai nút trên trang công khai. csrf=False có chủ đích: không có phiên đăng nhập để gắn CSRF, mã ngẫu nhiên
        trong link là thông tin xác thực duy nhất; chỉ cho đổi trạng thái liên hệ, không đổi gì khác."""
        lead = self._lead_by_token(token)
        state = {'contacted': 'contacted', 'unreachable': 'unreachable'}.get(result)
        if not lead or not state:
            return request.make_response('Yêu cầu không hợp lệ.', status=404 if not lead else 400,
                                         headers=[('Content-Type', 'text/plain; charset=utf-8'), *NO_INDEX.items()])
        # sudo() ở _lead_by_token: người dùng công khai không có quyền ghi dac.web.lead; chỉ ghi trạng thái liên hệ.
        # Đã chốt rồi thì giữ nguyên (nút trên trang đã bị khóa; chặn cả bấm đúp và gửi lại); nhân viên muốn sửa thì sửa trong Odoo.
        if lead.contact_state == 'new':
            lead._set_contact_state(state)
        return request.redirect('/lead/%s?done=1' % token)
