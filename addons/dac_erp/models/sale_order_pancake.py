from odoo import models, api, _
from odoo.tools import html_escape, html2plaintext
import logging

_logger = logging.getLogger(__name__)


class SaleOrderPancake(models.Model):
    _inherit = 'sale.order'

    @api.depends('partner_id')
    def _compute_conversation_count(self):
        for order in self:
            try:
                if order.partner_id and hasattr(order.partner_id, 'commercial_partner_id'):
                    commercial_partner_id = order.partner_id.commercial_partner_id.id
                    count = self.env['page.fm.conversation'].search_count([
                        ('partner_id', 'child_of', commercial_partner_id)
                    ])
                    order.conversation_count = count
                else:
                    order.conversation_count = 0
            except Exception as e:
                _logger.warning(f"Error computing conversation count for order {order.name}: {str(e)}")
                order.conversation_count = 0

    @api.depends('partner_id')
    def _compute_conversation_buttons_html(self):
        """Tạo HTML chứa các buttons cho conversations"""
        for order in self:
            try:
                if order.partner_id and hasattr(order.partner_id, 'commercial_partner_id'):
                    commercial_partner_id = order.partner_id.commercial_partner_id.id
                    all_conversations = self.env['page.fm.conversation'].search([
                        ('partner_id', 'child_of', commercial_partner_id)
                    ])

                    if all_conversations:
                        total_count = len(all_conversations)
                        # Hiển thị tối đa 5 conversations đầu tiên
                        conversations = all_conversations[:5]

                        # Container gọn gàng cho layout mới
                        buttons_html = '<div style="display: flex; gap: 6px; flex-wrap: wrap; align-items: center;">'

                        for conv in conversations:
                            # Màu button theo trạng thái
                            btn_class = 'btn-outline-primary'
                            if hasattr(conv, 'status_state'):
                                if conv.status_state == 'done':
                                    btn_class = 'btn-outline-success'
                                elif conv.status_state == 'waiting':
                                    btn_class = 'btn-outline-warning'

                            if hasattr(conv, 'is_unread_fm') and conv.is_unread_fm:
                                btn_class = 'btn-outline-danger'

                            conv_name = self._get_conversation_button_label(conv)
                            buttons_html += self._render_conversation_button(conv, conv_name, btn_class)

                        # Nếu có nhiều hơn 5 conversations, thêm nút "Xem thêm"
                        if total_count > 5:
                            remaining_count = total_count - 5
                            buttons_html += f'''
                                <button class="btn btn-xs btn-outline-info"
                                        style="font-size: 10px; padding: 3px 8px; margin: 1px; cursor: pointer;"
                                        onclick="this.style.display='none'; this.nextElementSibling.style.display='flex';"
                                        title="Hiển thị {remaining_count} cuộc hội thoại khác">
                                    +{remaining_count}
                                </button>
                                <div style="display: none; gap: 6px; flex-wrap: wrap;">
                            '''

                            # Hiển thị các conversations còn lại
                            for conv in all_conversations[5:]:
                                btn_class = 'btn-outline-primary'
                                if hasattr(conv, 'status_state'):
                                    if conv.status_state == 'done':
                                        btn_class = 'btn-outline-success'
                                    elif conv.status_state == 'waiting':
                                        btn_class = 'btn-outline-warning'

                                if hasattr(conv, 'is_unread_fm') and conv.is_unread_fm:
                                    btn_class = 'btn-outline-danger'

                                conv_name = self._get_conversation_button_label(conv)
                                buttons_html += self._render_conversation_button(conv, conv_name, btn_class)

                            buttons_html += '</div>'

                        buttons_html += '</div>'
                        order.conversation_buttons_html = buttons_html
                    else:
                        order.conversation_buttons_html = '<div style="color: #6c757d; font-size: 11px; font-style: italic;">Không có cuộc hội thoại</div>'
                else:
                    order.conversation_buttons_html = '<div style="color: #6c757d; font-size: 11px; font-style: italic;">Không có cuộc hội thoại</div>'
            except Exception as e:
                _logger.warning(f"Error computing conversation buttons for order {order.name}: {str(e)}")
                order.conversation_buttons_html = '<div style="color: #dc3545; font-size: 11px; font-style: italic;">Lỗi tải cuộc hội thoại</div>'

    def _get_conversation_button_label(self, conversation):
        recent_customer_name, recent_staff_name = self._get_recent_conversation_names(conversation)
        for candidate in [
            self._normalize_conversation_label(getattr(conversation, 'customer_name_clean', False)),
            self._normalize_conversation_label(getattr(conversation, 'customer_name_fm', False)),
            recent_customer_name,
            self._normalize_conversation_label(getattr(conversation, 'name', False)),
            self._normalize_conversation_label(getattr(conversation, 'display_name', False)),
            self._normalize_conversation_label(getattr(conversation.partner_id, 'name', False)),
            self._normalize_conversation_label(getattr(conversation.page_fm_page_id, 'name', False)),
            recent_staff_name,
        ]:
            if candidate:
                return candidate

        return (
            getattr(conversation, 'conversation_fm_id', False)
            or f'Conversation {conversation.id}'
        )

    def _get_conversation_platform_display(self, conversation):
        """Return a readable channel label and matching Font Awesome icon."""
        raw_platform = self._normalize_conversation_label(
            getattr(conversation, 'platform_fm', False)
        ) or ''
        normalized = raw_platform.casefold()
        if 'zalo' in normalized:
            label = 'Zalo OA' if 'oa' in normalized else 'Zalo'
            return label, 'fa-comment'
        if 'instagram' in normalized:
            return 'Instagram', 'fa-instagram'
        if 'whatsapp' in normalized:
            return 'WhatsApp', 'fa-whatsapp'
        if 'facebook' in normalized or 'messenger' in normalized:
            return 'Fanpage Facebook', 'fa-facebook'
        if 'tiktok' in normalized:
            return 'TikTok', 'fa-music'
        return raw_platform or 'Pancake', 'fa-comments'

    def _render_conversation_button(self, conversation, label, btn_class):
        platform_name, platform_icon = self._get_conversation_platform_display(conversation)
        modal_id = f'pancake-conversation-{conversation.id}'
        customer_name = (
            self._normalize_conversation_label(getattr(conversation, 'customer_name_clean', False))
            or self._normalize_conversation_label(getattr(conversation, 'customer_name_fm', False))
            or self._normalize_conversation_label(getattr(conversation.partner_id, 'name', False))
            or label
        )
        phone = (
            self._normalize_conversation_label(getattr(conversation, 'phone', False))
            or self._normalize_conversation_label(getattr(conversation.partner_id, 'phone', False))
            or '—'
        )
        messages = self.env['page.fm.message'].sudo().search(
            [('conversation_id', '=', conversation.id)],
            order='inserted_at_fm desc, id desc',
            limit=100,
        )[::-1]
        message_html = ''
        for message in messages:
            staff_name = self._normalize_conversation_label(
                getattr(message, 'staff_name_fm', False)
                or getattr(getattr(message, 'staff', False), 'name', False)
            )
            is_staff = bool(staff_name or getattr(message, 'staff_id_fm', False))
            sender = staff_name or 'Sale' if is_staff else (
                self._normalize_conversation_label(getattr(message, 'sender_name_fm', False)) or 'Khách hàng'
            )
            body = html2plaintext(getattr(message, 'content_html', False) or '').strip()
            if not body:
                body = self._normalize_conversation_label(getattr(message, 'type_content', False)) or 'Tin nhắn'
            message_html += f'''<div class="dac-pancake-message {'sale' if is_staff else 'customer'}">
                <strong>{html_escape(sender)}</strong><div>{html_escape(body)}</div>
            </div>'''
        if not message_html:
            message_html = '<div class="dac-pancake-empty">Chưa có tin nhắn được đồng bộ.</div>'
        return f'''<a href="#{modal_id}" class="conversation-btn btn btn-xs {btn_class}"
                    style="white-space: nowrap; text-decoration: none; font-size: 10px; padding: 3px 8px; margin: 1px;"
                    title="{html_escape(platform_name)} · {html_escape(label)}">
                    <i class="fa {platform_icon}" style="font-size: 9px; margin-right: 3px;"></i>{html_escape(platform_name)} · {html_escape(label)}
                </a>
                <div id="{modal_id}" class="dac-pancake-modal">
                    <a class="dac-pancake-backdrop" href="#" aria-label="Đóng"></a>
                    <section class="dac-pancake-dialog" role="dialog" aria-modal="true" aria-label="Hội thoại Pancake">
                        <header class="dac-pancake-dialog-header"><h2>Hội thoại Pancake</h2>
                            <a class="dac-pancake-close" href="#" aria-label="Đóng">×</a></header>
                        <div class="dac-pancake-dialog-body">
                            <div class="dac-pancake-customer"><strong>{html_escape(customer_name)}</strong> · {html_escape(phone)}</div>
                            <div class="dac-pancake-channel">Kênh: {html_escape(platform_name)}</div>
                            <div class="dac-pancake-messages">{message_html}</div>
                            <p class="dac-pancake-note">Hội thoại đã đồng bộ từ Pancake.</p>
                        </div>
                        <footer class="dac-pancake-dialog-footer"><a href="#" class="btn btn-secondary">Hủy</a></footer>
                    </section>
                </div>'''

    def _normalize_conversation_label(self, value):
        value = ' '.join(str(value or '').split())
        return value or False

    def _get_recent_conversation_names(self, conversation):
        messages = self.env['page.fm.message'].sudo().search(
            [('conversation_id', '=', conversation.id)],
            order='inserted_at_fm desc, id desc',
            limit=5,
        )
        recent_customer_name = False
        recent_staff_name = False

        for message in messages:
            if not recent_customer_name:
                recent_customer_name = self._normalize_conversation_label(
                    getattr(message, 'sender_name_fm', False)
                )
            if not recent_staff_name:
                recent_staff_name = self._normalize_conversation_label(
                    getattr(message, 'staff_name_fm', False)
                    or getattr(message.staff, 'name', False)
                )
            if recent_customer_name and recent_staff_name:
                break

        return recent_customer_name, recent_staff_name

    def action_open_order_conversation(self):
        """Compatibility entry point for order buttons saved in older views."""
        self.ensure_one()
        self.check_access_rights('read')
        self.check_access_rule('read')
        if self.conversation_id:
            self.conversation_id.check_access_rights('read')
            self.conversation_id.check_access_rule('read')
            return self.action_open_pancake_conversation(self.conversation_id.id)
        return self.action_view_conversations()

    def action_open_map_conversation_wizard(self):
        """Keep legacy database views working after the action was renamed.

        Some production views still call this historical method name.  The
        current order/conversation flow no longer needs a separate mapping
        wizard: an explicitly linked conversation is opened directly, while
        orders without one show the customer's Pancake conversations.
        """
        return self.action_open_order_conversation()

    def action_open_pancake_conversation(self, conversation_id):
        """Mở trực tiếp cuộc hội thoại trên Pancake"""
        self.ensure_one()
        try:
            conversation = self.env['page.fm.conversation'].browse(int(conversation_id))
            if conversation.exists():
                url = conversation.external_url or '#'
                return {
                    'type': 'ir.actions.act_url',
                    'url': url,
                    'target': 'new',
                }
            else:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Lỗi'),
                        'message': _('Không tìm thấy cuộc hội thoại.'),
                        'type': 'danger',
                        'sticky': False,
                    }
                }
        except Exception as e:
            _logger.error(f"Error opening Pancake conversation {conversation_id}: {str(e)}")
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Lỗi'),
                    'message': f'Lỗi khi mở cuộc hội thoại: {str(e)}',
                    'type': 'danger',
                    'sticky': False,
                }
            }

    def action_view_conversations(self):
        """Xem tất cả conversations của khách hàng - tương tự như trong res.partner"""
        self.ensure_one()

        # Safe access partner information
        partner_id = None
        commercial_partner_id = None

        try:
            if self.partner_id:
                partner_id = self.partner_id.id
                # Safe access to commercial_partner_id
                try:
                    if hasattr(self.partner_id, 'commercial_partner_id') and self.partner_id.commercial_partner_id:
                        commercial_partner_id = self.partner_id.commercial_partner_id.id
                    else:
                        commercial_partner_id = partner_id
                except:
                    commercial_partner_id = partner_id
        except Exception as e:
            _logger.warning(f"Error accessing partner info for order {self.name}: {str(e)}")
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Lỗi'),
                    'message': _('Không thể truy cập thông tin khách hàng.'),
                    'type': 'warning',
                    'sticky': False,
                }
            }

        if not commercial_partner_id:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Chưa có khách hàng'),
                    'message': _('Đơn hàng này chưa có khách hàng được gắn.'),
                    'type': 'warning',
                    'sticky': False,
                }
            }

        # Check if any conversations exist for this partner
        conv_count = self.env['page.fm.conversation'].search_count([
            ('partner_id', 'child_of', commercial_partner_id)
        ])

        if not conv_count:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Chưa có hội thoại'),
                    'message': _('Khách hàng này chưa có cuộc hội thoại Pancake nào.'),
                    'type': 'warning',
                    'sticky': False,
                }
            }

        # Return action similar to res.partner's action_view_conversations
        action = {
            'type': 'ir.actions.act_window',
            'name': _('Conversations - %s') % (self.partner_id.name or 'Unknown'),
            'res_model': 'page.fm.conversation',
            'view_mode': 'list,form',
            'target': 'current',
            'domain': [('partner_id', 'child_of', commercial_partner_id)],
            'context': {'search_default_partner_id': partner_id},
        }
        return action
