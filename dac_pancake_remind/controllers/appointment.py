from werkzeug.urls import url_encode

from odoo import fields, http
from odoo.http import request

TZ = 'Asia/Ho_Chi_Minh'
NO_INDEX = {'X-Robots-Tag': 'noindex, nofollow', 'Referrer-Policy': 'no-referrer', 'Cache-Control': 'no-store'}
RESULTS = {'done': 'done', 'cancel': 'cancelled'}


class AppointmentTicketController(http.Controller):

    def _by_token(self, token):
        if not token or len(token) > 64:
            return None
        # sudo(): nhân viên mở link từ Zalo không cần đăng nhập — mã ngẫu nhiên trong link là quyền truy cập, tra đúng một phiếu
        return request.env['dac.pancake.appointment'].sudo().search([('access_token', '=', token)], limit=1) or None

    def _plain(self, text, status):
        return request.make_response(text, status=status, headers=[('Content-Type', 'text/plain; charset=utf-8'), *NO_INDEX.items()])

    @http.route('/hen/<string:token>', type='http', auth='public', methods=['GET'], sitemap=False)
    def ticket(self, token, saved=None, **kwargs):
        """Phiếu hẹn: khách, việc đã hẹn, hạn, link mở hội thoại Pancake và nút "Đã xử lý xong"."""
        appointment = self._by_token(token)
        if not appointment:
            return self._plain('Phiếu hẹn không tồn tại hoặc đã bị thu hồi.', 404)

        def local(dt):
            return fields.Datetime.context_timestamp(appointment.with_context(tz=TZ), dt).strftime('%H:%M %d/%m/%Y') if dt else ''

        is_open = appointment.state in ('open', 'overdue')
        doer, doer_label = appointment._guess_doer(request.env.user) if is_open else (None, '')
        logged_in = not request.env.user._is_public()
        html = request.env['ir.qweb']._render('dac_pancake_remind.appointment_ticket_page', {
            'a': appointment, 'token': token, 'doer': doer, 'doer_label': doer_label, 'logged_in': logged_in,
            'login_url': '/web/login?' + url_encode({'redirect': '/hen/%s' % token}), 'due': local(appointment.due_at), 'promised': local(appointment.promised_at),
            'done_at': local(appointment.done_at), 'just_saved': bool(saved),
            'precision': dict(appointment._fields['precision'].selection).get(appointment.precision, ''),
            'state_label': dict(appointment._fields['state'].selection).get(appointment.state, ''),
            'is_open': is_open,
        })
        return request.make_response('<!DOCTYPE html>' + str(html), headers=[('Content-Type', 'text/html; charset=utf-8'), *NO_INDEX.items()])

    @http.route('/hen/<string:token>/done', type='http', auth='public', methods=['POST'], csrf=False, sitemap=False)
    def ticket_done(self, token, result=None, by=None, **kwargs):
        """csrf=False có chủ đích: thường không có phiên đăng nhập; mã ngẫu nhiên trong link là xác thực duy nhất.
        Chỉ cho chốt phiếu (đã xử lý / không cần nữa), không đổi gì khác. Đã chốt thì giữ nguyên.
        Người xác nhận: đang đăng nhập Odoo → người đó; chưa → nhân viên đã xác định nhắn khách gần nhất trên Pancake;
        không có → "Chưa xác định" (kèm tên tự gõ nếu có)."""
        appointment = self._by_token(token)
        state = RESULTS.get(result)
        if not appointment or not state:
            return self._plain('Yêu cầu không hợp lệ.', 404 if not appointment else 400)
        user, label = appointment._guess_doer(request.env.user)
        if not user:
            name = ' '.join((by or '').split())[:60]
            label = 'Chưa xác định' + (' — tự ghi: %s' % name if name else '')
        appointment._close(state, 'Qua phiếu hẹn — ' + label, user)
        return request.redirect('/hen/%s?saved=1' % token)
