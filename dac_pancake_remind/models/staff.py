import json
import re
from datetime import timedelta

from psycopg2 import IntegrityError

from odoo import api, fields, models
from odoo.addons.dac_push.models.contact_intent import fold

STAFF_LOOKBACK = timedelta(days=60)
LAST_STAFF_SCAN = 120  # số tin gần nhất của hội thoại để tìm người đã chat với khách


def name_key(name):
    return ' '.join(fold(name or '').split())


def name_score(staff_name, member_name):
    """Độ khớp tên Pancake với tên Zalo (0 = không khớp). Vd "Dao" ~ "Đào Duy An", "Tieu Bang" ~ "Tbăng"."""
    staff, member = name_key(staff_name), name_key(member_name)
    if not staff or not member:
        return 0
    if staff == member:
        return 4
    staff_words, member_words = staff.split(), member.split()
    if set(staff_words) <= set(member_words):
        return 3
    compact = member.replace(' ', '')
    initials = ''.join(w[0] for w in staff_words[:-1]) + staff_words[-1]
    if len(staff_words) > 1 and compact == initials:
        return 2
    if len(staff) >= 4 and (staff.replace(' ', '') in compact or compact in staff.replace(' ', '')):
        return 1
    return 0


class PancakeStaff(models.Model):
    """Nhân viên trả lời khách trên Pancake (tên hiện trong Pancake) ↔ tài khoản Zalo để tag đúng người khi nhắc."""
    _name = 'dac.pancake.staff'
    _description = 'Nhân viên Pancake ↔ Zalo'
    _order = 'message_count desc, name'

    name = fields.Char(string='Tên trên Pancake', required=True, readonly=True)
    key = fields.Char(required=True, readonly=True, index=True)
    member_id = fields.Many2one('dac.zalo.channel.member', string='Tag trên Zalo', ondelete='set null',
        domain="[('channel_id.thread_type', '=', 'group')]",
        help='Người này trong nhóm Zalo. Khi nhắc khách mà người này đã chat, bot tag đúng người này.')
    user_id = fields.Many2one('res.users', string='Tài khoản Odoo', ondelete='set null', index=True,
        domain="[('share', '=', False)]", help='Tài khoản Odoo của nhân viên này (một người: Odoo – Pancake – Zalo).')
    zalo_uid = fields.Char(string='Mã Zalo', readonly=True, index=True)
    zalo_name = fields.Char(string='Tên Zalo', readonly=True)
    suggested = fields.Boolean(string='Tự gợi ý (chưa xác nhận)', readonly=True,
        help='Hệ thống tự ghép theo tên giống nhau. Kiểm tra lại rồi bấm "Xác nhận".')
    message_count = fields.Integer(string='Số tin 60 ngày', readonly=True)
    last_seen_at = fields.Datetime(string='Nhắn gần nhất', readonly=True)
    active = fields.Boolean(default=True)
    _sql_constraints = [('key_unique', 'unique(key)', 'Nhân viên này đã có trong danh sách.')]

    @api.onchange('member_id')
    def _onchange_member(self):
        for staff in self:
            staff.zalo_uid = staff.member_id.uid
            staff.zalo_name = staff.member_id.name

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals.setdefault('key', name_key(vals.get('name')))
            self._copy_member(vals)
        return super().create(vals_list)

    def write(self, vals):
        if 'member_id' in vals:
            vals = dict(vals)
            self._copy_member(vals)
            vals.setdefault('suggested', False)
        return super().write(vals)

    def _copy_member(self, vals):
        if 'member_id' in vals:
            member = self.env['dac.zalo.channel.member'].browse(vals['member_id']) if vals['member_id'] else None
            vals['zalo_uid'] = member.uid if member else False
            vals['zalo_name'] = member.name if member else False

    def action_confirm(self):
        self.write({'suggested': False})

    @api.model
    def _for_name(self, name):
        key = name_key(name)
        if not key:
            return self.browse()
        # sudo(): luồng nền (cron) ghi nhận tên nhân viên mới thấy trên Pancake
        Staff = self.sudo().with_context(active_test=False)
        found = Staff.search([('key', '=', key)], limit=1)
        if found:
            return found
        try:
            with self.env.cr.savepoint():
                return Staff.create({'name': ' '.join(name.split())[:120]})
        except IntegrityError:
            # Cron khác vừa tạo cùng tên (chưa thấy được trong giao dịch này): lần này coi như chưa rõ nhân viên
            return self.browse()

    @api.model
    def _of_conversation(self, conversation, before=None):
        """Nhân viên đã chat với khách gần nhất (tin có tên nhân viên trên Pancake); không thấy → rỗng."""
        domain = [('conversation_id', '=', conversation.id)]
        if before:
            domain.append(('inserted_at', '<=', before))
        bots = self.env['dac.pancake.remind.wait']._bot_names()
        # sudo(): luồng nền đọc tin Pancake
        for message in self.env['dac.pancake.message'].sudo().search(domain, order='inserted_at desc, id desc', limit=LAST_STAFF_SCAN):
            name = admin_name(message)
            if name and name.lower() not in bots:
                return self._for_name(name)
        return self.browse()

    @api.model
    def action_refresh(self):
        """Lập danh sách nhân viên từ tin 60 ngày qua (số tin, lần nhắn cuối) và gợi ý người trùng tên trong nhóm Zalo."""
        since = fields.Datetime.now() - STAFF_LOOKBACK
        bots = self.env['dac.pancake.remind.wait']._bot_names()
        self.env.cr.execute("""
            SELECT raw_json::jsonb -> 'from' ->> 'admin_name' AS name, count(*), max(inserted_at)
              FROM dac_pancake_message
             WHERE inserted_at >= %s AND raw_json LIKE '%%admin_name%%'
             GROUP BY 1""", (since,))
        rows = [(n, c, at) for n, c, at in self.env.cr.fetchall() if n and n.strip() and n.strip().lower() not in bots]
        Staff = self.sudo().with_context(active_test=False)
        for name, count, last in rows:
            Staff._for_name(name).write({'message_count': count, 'last_seen_at': last})
        self._suggest_members()
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': 'Nhân viên Pancake', 'message': 'Đã cập nhật %d nhân viên. Kiểm tra cột "Tag trên Zalo".' % len(rows),
            'type': 'success', 'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'}}}

    @api.model
    def _suggest_members(self):
        """Ghép tự động theo tên cho người chưa ghép (ưu tiên thành viên nhóm thật); đánh dấu "tự gợi ý" để quản lý xác nhận."""
        members = self.env['dac.zalo.channel.member'].sudo().search([('channel_id.thread_type', '=', 'group')])
        members = members.sorted(lambda m: (m.channel_id.is_test, m.id))
        for staff in self.sudo().search([('member_id', '=', False)]):
            scored = sorted(((name_score(staff.name, m.name), -i, m) for i, m in enumerate(members)), key=lambda x: x[:2], reverse=True)
            if scored and scored[0][0] > 0 and (len(scored) == 1 or scored[1][0] < scored[0][0] or scored[1][2].uid == scored[0][2].uid):
                staff.write({'member_id': scored[0][2].id, 'suggested': True})


def admin_name(message):
    """Tên nhân viên Pancake của tin (chỉ tin nhân viên gửi từ Pancake mới có)."""
    if 'admin_name' not in (message.raw_json or ''):
        return ''
    try:
        sender = (json.loads(message.raw_json or '{}').get('from') or {})
    except ValueError:
        return ''
    name = sender.get('admin_name') if isinstance(sender, dict) else ''
    return re.sub(r'\s+', ' ', name).strip() if isinstance(name, str) else ''
