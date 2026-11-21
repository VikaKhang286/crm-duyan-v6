"""Phản hồi của nhân viên trong nhóm Zalo về khách Pancake đang được nhắc.

Nhân viên trả lời (reply) tin nhắc của trợ lý, hoặc nhắc tên trợ lý ("@Tro Ly Dac Đã xem"): trợ lý thả ❤️ để báo đã nhận,
đọc lại hội thoại trên Pancake rồi trả lời ngắn ngay dưới tin của nhân viên (cùng một luồng với tin nhắc khách đó):
  - khách đã được trả lời trên Pancake, hoặc nhân viên báo đã xử lý (gọi điện, nhắn ngoài Pancake...) → ghi nhận, thôi nhắc;
  - mới chỉ xem → nhắc phản hồi khách ngay.
Mỗi phản hồi được lưu lại (ai, lúc nào, nói gì, Pancake lúc đó ra sao) làm bằng chứng, gắn với hội thoại / khách hàng."""
import logging
import re
from datetime import timedelta

from odoo import api, fields, models
from odoo.addons.dac_push.models.contact_intent import fold

_logger = logging.getLogger(__name__)

INTENTS = [('order', 'Báo đã lên đơn / chốt'), ('contacted', 'Báo đã liên hệ (gọi điện, khách qua cửa hàng, Zalo riêng...)'),
           ('replied', 'Báo đã trả lời khách'), ('handling', 'Đang xử lý'), ('seen', 'Đã xem'), ('reaction', 'Thả cảm xúc'), ('other', 'Khác')]
CLOSING = {'order': 'đã lên đơn', 'contacted': 'đã liên hệ khách', 'replied': 'đã trả lời khách'}  # sale báo → chốt, thôi nhắc
ORDER_HINT = re.compile(r'\b(?:len don|chot don|tao don|lam don|ra don|chot roi|da chot|dat coc|coc roi|nhan coc)\b')
CONTACTED_HINT = re.compile(r'\b(?:da|vua|em da)\s+(?:goi|lien he|lien lac|ket ban|nhan rieng|nhan zalo|ib rieng|xin so|co so|co sdt)\b'
                            r'|\b(?:goi|lien he|lien lac)\s+(?:roi|xong|duoc)\b|\bgoi dien\b|\bdien thoai\b'
                            r'|\bkhach (?:den|qua|ghe|toi|o) (?:cua hang|shop|tiem|xuong|van phong|cong ty)\b|\bzalo rieng\b')
REPLIED_HINT = re.compile(r'\b(?:da|vua|em da)\s+(?:rep|tra loi|phan hoi|bao gia|gui gia|gui|chot|xu ly|tu van|nhan|ib|inbox)\b'
                          r'|\b(?:rep|tra loi|phan hoi|xu ly|bao gia)\s+(?:roi|xong)\b|^xong\b|^done\b')
HANDLING_HINT = re.compile(r'\b(?:dang|de em|em (?:xu ly|lo|rep|tra loi|goi|lam|check|kiem tra|lien he)|ngay day|em vao|ok em)\b')
SEEN_HINT = re.compile(r'\b(?:da xem|xem roi|da doc|doc roi|ok|oke|okie|okay|vang|da nhan|nhan roi|roger)\b|^da$')
RECENT = timedelta(hours=12)       # tin "@Trợ lý" không trả lời tin nào: ghép với khách được nhắc trong 12 giờ gần đây
NO_CUSTOMER_TEXT = 'Dạ em đã nhận ạ! Nếu là về khách Pancake, anh/chị bấm trả lời (reply) vào tin nhắc của khách đó giúp em nhé.'
AI_SCHEMA = {'type': 'object', 'properties': {
    'intent': {'type': 'string', 'enum': ['order', 'contacted', 'replied', 'handling', 'seen', 'other'],
               'description': 'order = đã lên đơn/chốt/đặt cọc; contacted = đã gọi điện/đã liên hệ/khách đến cửa hàng/nhắn Zalo riêng; '
                              'replied = đã trả lời/báo giá/tư vấn khách; handling = đang/sẽ xử lý ngay; seen = chỉ báo đã xem/đã biết; '
                              'other = nội dung khác'},
}, 'required': ['intent']}
AI_SYSTEM = ('Bạn đọc một tin nhắn ngắn của nhân viên kinh doanh trong nhóm Zalo, trả lời tin nhắc "có khách đang chờ được trả lời". '
             'Phân loại ý của nhân viên.')


def rule_intent(text):
    folded = fold(text or '')
    if ORDER_HINT.search(folded):
        return 'order'
    if CONTACTED_HINT.search(folded):
        return 'contacted'
    if REPLIED_HINT.search(folded):
        return 'replied'
    if HANDLING_HINT.search(folded):
        return 'handling'
    if SEEN_HINT.search(folded):
        return 'seen'
    return 'other'


class PancakeFeedback(models.Model):
    _name = 'dac.pancake.feedback'
    _description = 'Phản hồi của nhân viên trong nhóm Zalo (khách Pancake)'
    _order = 'at desc, id desc'
    _rec_name = 'text'
    _sql_constraints = [('message_unique', 'unique(channel_id, zalo_message_id)', 'Tin này đã được ghi nhận.')]

    at = fields.Datetime(string='Lúc', required=True, default=fields.Datetime.now, index=True)
    wait_id = fields.Many2one('dac.pancake.remind.wait', string='Lượt nhắc khách', ondelete='set null', index=True)
    conversation_id = fields.Many2one('dac.pancake.conversation', string='Hội thoại Pancake', ondelete='set null', index=True)
    customer_name = fields.Char(string='Khách', related='conversation_id.customer_name')
    partner_id = fields.Many2one('res.partner', string='Khách hàng', related='conversation_id.partner_id', store=True, index=True)
    external_url = fields.Char(string='Link Pancake', related='conversation_id.external_url')
    channel_id = fields.Many2one('dac.zalo.channel', string='Nhóm Zalo', required=True, ondelete='cascade')
    zalo_message_id = fields.Char(string='Mã tin Zalo', required=True, index=True)
    quoted_message_id = fields.Many2one('dac.zalo.channel.message', string='Trả lời tin nhắc', ondelete='set null')
    sender_uid = fields.Char(string='Mã Zalo người gửi', index=True)
    sender_name = fields.Char(string='Nhân viên (Zalo)')
    user_id = fields.Many2one('res.users', string='Nhân viên (Odoo)', ondelete='set null', index=True)
    text = fields.Char(string='Nhân viên nói')
    kind = fields.Selection([('reply', 'Trả lời tin nhắc'), ('mention', 'Nhắc tên trợ lý'), ('reaction', 'Thả cảm xúc')],
        string='Cách phản hồi', required=True)
    intent = fields.Selection(INTENTS, string='Ý nhân viên')
    intent_source = fields.Selection([('rules', 'Luật từ khóa'), ('ai', 'AI')], string='Hiểu bằng')
    state = fields.Selection([('pending', 'Chờ trợ lý xử lý'), ('done', 'Đã phản hồi'), ('logged', 'Chỉ ghi nhận'), ('skipped', 'Bỏ qua')],
        string='Trạng thái', default='pending', required=True, index=True)
    pancake_answered = fields.Boolean(string='Pancake đã có tin trả lời khách')
    result = fields.Char(string='Trợ lý ghi nhận')
    reacted = fields.Boolean(string='Đã thả ❤️')
    bot_message_id = fields.Many2one('dac.zalo.channel.message', string='Tin trợ lý trả lời', ondelete='set null')
    is_test = fields.Boolean(related='channel_id.is_test', string='Nhóm thử nghiệm')

    # ── Ghi nhận (từ bridge) ─────────────────────────────────────────────────

    @api.model
    def _person(self, channel, uid):
        """Mã Zalo → (tài khoản Odoo, tên): nhân viên Pancake đã ghép Zalo, hoặc liên kết trợ lý; không có thì tên trong nhóm."""
        member = channel.sudo().member_ids.filtered(lambda m: m.uid == uid)[:1]
        user = self._user_of_uid(uid)
        return user, member.name or user.name or uid

    @api.model
    def _user_of_uid(self, uid):
        """Tài khoản Odoo của một mã Zalo: ghép trực tiếp ở "Thành viên Zalo ↔ Odoo", hoặc nhân viên Pancake đã ghép Zalo,
        hoặc nhân viên đã liên kết trợ lý. Chưa ghép → rỗng."""
        if not uid:
            return self.env['res.users']
        # sudo(): luồng bridge (máy) không có user; chỉ tra tài khoản theo mã Zalo
        member = self.env['dac.zalo.channel.member'].sudo().search([('uid', '=', uid), ('user_id', '!=', False)], limit=1)
        staff = self.env['dac.pancake.staff'].sudo().search([('zalo_uid', '=', uid), ('user_id', '!=', False)], limit=1)
        binding = self.env['dac.zalo.binding'].sudo().search([('zalo_uid', '=', uid), ('state', '=', 'linked')], limit=1)
        return member.user_id or staff.user_id or binding.user_id

    @api.model
    def _relink(self, uids):
        """Mã Zalo vừa được ghép tài khoản Odoo (vd tạo user cho anh An rồi ghép Zalo): mọi phản hồi cũ của mã đó ghi nhận luôn
        tài khoản Odoo (người báo trạng thái khách hàng = tài khoản Odoo đó)."""
        Feedback = self.sudo()
        for uid in {u for u in uids if u}:
            user = self._user_of_uid(uid)
            Feedback.search([('sender_uid', '=', uid), ('user_id', '!=', user.id)]).write({'user_id': user.id or False})

    @api.model
    def _record(self, channel, kind, message_id, sender_uid, text='', wait=None, quoted=None, at=None):
        Feedback = self.sudo()
        if Feedback.search_count([('channel_id', '=', channel.id), ('zalo_message_id', '=', str(message_id))]):
            return Feedback.browse()
        user, name = self._person(channel, sender_uid)
        vals = {'channel_id': channel.id, 'zalo_message_id': str(message_id), 'sender_uid': sender_uid, 'sender_name': name[:120],
                'user_id': user.id or False, 'text': (text or '')[:500], 'kind': kind, 'at': at or fields.Datetime.now(),
                'wait_id': wait.id if wait else False, 'conversation_id': wait.conversation_id.id if wait else False,
                'quoted_message_id': quoted.id if quoted else False}
        if kind == 'reaction':
            vals.update(intent='reaction', state='logged', result='Thả cảm xúc %s lên tin nhắc' % (text or ''))
        feedback = Feedback.create(vals)
        if feedback.state == 'pending':
            self.env.ref('dac_pancake_remind.cron_pancake_feedback').sudo()._trigger()
        return feedback

    # ── Xử lý (cron, gọi ngay khi có phản hồi) ───────────────────────────────

    @api.model
    def _cron_process(self):
        """Không raise."""
        for feedback in self.sudo().search([('state', '=', 'pending')], order='at, id', limit=20):
            try:
                with self.env.cr.savepoint():
                    feedback._process()
            except Exception:  # noqa: BLE001
                _logger.exception('Phản hồi trong nhóm Zalo: lỗi xử lý %s', feedback.id)
                feedback.write({'state': 'skipped', 'result': 'Lỗi khi xử lý (xem log)'})

    def _intent(self):
        self.ensure_one()
        intent = rule_intent(self.text)
        if intent != 'other':
            return intent, 'rules'
        AI = self.env['dac.ai.connection']
        try:
            if AI._ready():
                data, _kind = AI._json(AI_SYSTEM, 'Tin nhân viên: %s' % self.text, AI_SCHEMA, name='report_feedback',
                                       description='Ý của nhân viên trong tin nhắn')
                if data.get('intent') in dict(INTENTS):
                    return data['intent'], 'ai'
        except Exception as error:  # noqa: BLE001 — AI lỗi: dùng luật
            _logger.info('Phản hồi trong nhóm: AI không đọc được (%s)', type(error).__name__)
        return 'other', 'rules'

    def _process(self):
        self.ensure_one()
        Scenario = self.env['dac.pancake.remind.scenario']
        scenario = Scenario._get('feedback')
        if not scenario.enabled:
            self.write({'state': 'skipped', 'result': 'Kịch bản "Nhân viên phản hồi trong nhóm" đang tắt'})
            return
        channel = self.channel_id
        reacted = channel._react(self.zalo_message_id, 'heart')
        intent, source = self._intent()
        vals = {'reacted': reacted, 'intent': intent, 'intent_source': source, 'state': 'done'}
        wait = self.wait_id
        if not wait:
            text = NO_CUSTOMER_TEXT
            vals['result'] = 'Chưa rõ khách nào: nhờ nhân viên trả lời vào tin nhắc'
        else:
            config = self.env['dac.pancake.remind.config']._get()
            wait._live_sync(config)  # đọc lại đúng hội thoại này trên Pancake
            values = {'ten_khach': wait.customer_name or 'Khách', 'ten_nhan_vien': self.sender_name or ''}
            answer = wait._answer_vals() if wait.state != 'answered' else {}
            report = {'report_feedback_id': self.id, 'report_status': intent} if intent in CLOSING else {}
            who = '%s báo %s' % (self.user_id.name or self.sender_name or 'nhân viên', CLOSING.get(intent, ''))
            if wait.state == 'answered' or answer:
                if answer or report:
                    wait.write(dict(answer, **report, **({'due_at': False} if answer else {})))
                shown = wait.answered_by or 'đã có người trả lời'
                text = scenario._render(dict(values, nguoi_tra_loi=shown + ('; ' + who if report else '')), line=True)
                vals.update(pancake_answered=True, result='Pancake đã có tin trả lời (%s)%s: thôi nhắc'
                            % (wait.answered_by or '', '; ghi nhận ' + who if report else ''))
            elif report:
                wait.write(dict(report, state='answered', answered_at=fields.Datetime.now(), answered_by=who[:120], answer_source='staff',
                                due_at=False, digest_pending=False))
                text = scenario._render(dict(values, nguoi_tra_loi=who), line=True)
                vals['result'] = 'Pancake chưa có tin trả lời; ghi nhận trạng thái do sale báo (%s): thôi nhắc' % who
            else:
                text = scenario._render(values)
                vals['result'] = 'Pancake chưa có tin trả lời: nhắc phản hồi khách ngay (vẫn kiểm tra lại theo lịch)'
        event_type = self.quoted_message_id.event_type_id or self.env.ref('dac_pancake_remind.event_type_pancake_unanswered')
        bot = channel._queue(text, 'pancake-feedback:%s' % self.id, event_type, quote_message_id=self.zalo_message_id)
        self.env['dac.zalo.channel']._trigger_send()
        vals['bot_message_id'] = bot.id
        self.write(vals)
        scenario._log(bot, wait.conversation_id if wait else None, wait=wait or None, is_test=channel.is_test)


class ZaloChannelMessage(models.Model):
    _inherit = 'dac.zalo.channel.message'

    def _pancake_wait(self):
        """Khách (lượt nhắc) mà tin trợ lý này nói tới: tin nhắc/nhắc lại, tin "✅ đã được trả lời", hoặc tin trợ lý trả lời nhân viên."""
        self.ensure_one()
        # sudo(): luồng bridge không có user; chỉ tìm lượt nhắc khách gắn với tin này
        Wait = self.env['dac.pancake.remind.wait'].sudo()
        wait = Wait.search([('zalo_message_ids', 'in', self.ids)], order='id desc', limit=1)
        if wait:
            return wait
        found = re.match(r'^pancake-(?:wait|answered):(\d+):', self.event_key or '')  # mã tin ghi sẵn số lượt nhắc
        if found:
            return Wait.browse(int(found.group(1))).exists()
        return self.env['dac.pancake.feedback'].sudo().search([('bot_message_id', '=', self.id)], limit=1).wait_id

    def _on_reply(self, reply):
        super()._on_reply(reply)
        wait = self._pancake_wait()
        if wait:
            self.env['dac.pancake.feedback']._record(self.channel_id, 'reply', reply['message_id'], reply['sender_uid'], reply.get('text'),
                                                     wait=wait, quoted=self)

    def _on_reaction(self, reaction):
        super()._on_reaction(reaction)
        wait = self._pancake_wait()
        if wait:
            self.env['dac.pancake.feedback']._record(self.channel_id, 'reaction', 'reaction:%s' % reaction.id, reaction.uid, reaction.icon,
                                                     wait=wait, quoted=self)


class ZaloChannel(models.Model):
    _inherit = 'dac.zalo.channel'

    def _on_mention(self, mention):
        super()._on_mention(mention)
        wait = self._pancake_wait_for(mention['sender_uid'])
        self.env['dac.pancake.feedback']._record(self, 'mention', mention['message_id'], mention['sender_uid'], mention.get('text'), wait=wait)

    def _pancake_wait_for(self, uid):
        """Khách được nhắc trong nhóm này gần đây nhất (ưu tiên tin có tag đúng người đang nói); không có → None."""
        self.ensure_one()
        since = fields.Datetime.now() - RECENT
        # sudo(): luồng bridge không có user
        messages = self.env['dac.zalo.channel.message'].sudo().search([('channel_id', '=', self.id), ('state', '=', 'sent'),
                                                                      ('sent_at', '>=', since)], order='sent_at desc, id desc', limit=50)
        Wait = self.env['dac.pancake.remind.wait'].sudo()
        tagged = messages.filtered(lambda m: any(isinstance(x, dict) and x.get('uid') == uid for x in (m.mentions or [])))
        for group in (tagged, messages):
            for message in group:
                wait = Wait.search([('zalo_message_ids', 'in', message.id)], limit=1)
                if wait:
                    return wait
        return None


class ResPartner(models.Model):
    _inherit = 'res.partner'

    dac_pancake_feedback_count = fields.Integer(string='Xử lý Pancake', compute='_compute_dac_pancake_feedback_count',
        groups='dac_erp.group_dac_erp_manager,base.group_system')

    def _compute_dac_pancake_feedback_count(self):
        counts = dict(self.env['dac.pancake.feedback']._read_group([('partner_id', 'in', self.ids)], ['partner_id'], ['__count']))
        for partner in self:
            partner.dac_pancake_feedback_count = counts.get(partner, 0)

    def action_open_pancake_feedback(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('dac_pancake_remind.feedback_action')
        action.update(domain=[('partner_id', '=', self.id)], context={'search_default_partner_id': self.id})
        return action


class ZaloChannelMember(models.Model):
    _inherit = 'dac.zalo.channel.member'

    user_id = fields.Many2one('res.users', string='Tài khoản Odoo', ondelete='set null', index=True, domain="[('share', '=', False)]",
        help='Người này trên Odoo. Ghép xong, mọi phản hồi trong nhóm của mã Zalo này (kể cả trước đây) ghi nhận đúng tài khoản Odoo.')

    @api.model_create_multi
    def create(self, vals_list):
        members = super().create(vals_list)
        if members.filtered('user_id'):
            self.env['dac.pancake.feedback']._relink(members.filtered('user_id').mapped('uid'))
        return members

    def write(self, vals):
        result = super().write(vals)
        if 'user_id' in vals or 'uid' in vals:
            self.env['dac.pancake.feedback']._relink(self.mapped('uid'))
        return result


class PancakeStaffRelink(models.Model):
    _inherit = 'dac.pancake.staff'

    def write(self, vals):
        before = set(self.mapped('zalo_uid'))
        result = super().write(vals)
        if {'user_id', 'member_id'} & set(vals):
            self.env['dac.pancake.feedback']._relink(before | set(self.mapped('zalo_uid')))
        return result


class ZaloBindingRelink(models.Model):
    _inherit = 'dac.zalo.binding'

    def write(self, vals):
        before = set(self.mapped('zalo_uid'))
        result = super().write(vals)
        if {'zalo_uid', 'state', 'user_id'} & set(vals):
            self.env['dac.pancake.feedback']._relink(before | set(self.mapped('zalo_uid')))
        return result
