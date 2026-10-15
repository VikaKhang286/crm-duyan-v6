from odoo import api, fields, models, _
from odoo.tools import html2plaintext
from datetime import timedelta
from odoo.exceptions import AccessError


def _first_line(html_or_text):
    if not html_or_text:
        return ''
    text = html2plaintext(html_or_text).strip()
    first = next((l.strip() for l in text.splitlines() if l.strip()), '')
    return first[:200]


class SaleOrder(models.Model):
    _inherit = "sale.order"

    @api.model
    def dac_get_quick_sale(self, order_id):
        """Small, permission-aware payload for the global order quick view."""
        order = self.browse(int(order_id)).exists()
        if not order:
            return False
        order.check_access('read')
        today = fields.Date.context_today(self)
        deadline = order.production_deadline or order.design_deadline
        tasks = order.task_ids.filtered(lambda task: task.state != 'cancelled').sorted(
            key=lambda task: (task.state == 'done', task.deadline or fields.Datetime.to_datetime('9999-12-31 00:00:00'))
        )
        state_labels = dict(order._fields['order_state_custom'].selection)
        task_state_labels = dict(self.env['dac.work.task']._fields['state'].selection)
        restrict_quick_actions = self.env.user._dac_is_worker_only()

        def _task_payload(task):
            return {
                'id': task.id,
                'name': task.name or '',
                'state': task.state,
                'state_label': task_state_labels.get(task.state, task.state or ''),
                'assignee': task.assigned_user_id.name or '',
                'deadline': fields.Datetime.context_timestamp(self, task.deadline).strftime('%d/%m/%Y') if task.deadline else False,
                'is_overdue': bool(task.deadline and task.deadline < fields.Datetime.now() and task.state != 'done'),
            }

        return {
            'id': order.id,
            'number': order.order_number or order.name or '',
            'title': order.order_title or '',
            'partner_name': order.partner_id.name or '',
            'phone': order.phone or order.partner_id.phone or '',
            'amount_total': order.amount_total,
            'paid_amount': order.deposit_paid_display,
            'remaining_amount': order.remaining_amount_display,
            'currency_symbol': order.currency_id.symbol or '₫',
            'state': order.order_state_custom or 'quotation',
            'state_label': state_labels.get(order.order_state_custom, ''),
            'sale_name': order.user_id.name or '',
            'design_name': order.user_id_design.name or '',
            'production_name': order.user_id_production.name or '',
            'deadline': deadline.strftime('%d/%m/%Y') if deadline else False,
            'is_overdue': bool(deadline and deadline < today),
            'conversation_id': order.conversation_id.id if order.conversation_id else False,
            'tasks': [_task_payload(task) for task in tasks[:5]],
            'task_count': len(tasks),
            'restrict_quick_actions': restrict_quick_actions,
        }

    def _get_partner_vip_class(self, partner):
        if not partner or not partner.pancake_tag_ids:
            return ''
        tag_names = [tag.name.lower() for tag in partner.pancake_tag_ids]
        classes = []
        if 'khách lớn' in tag_names or 'vip' in tag_names:
            classes.append('vip-customer')
        if 'khách quen' in tag_names or 'loyal' in tag_names:
            classes.append('loyal-customer')
        return ' '.join(classes)

    def _get_task_dashboard_data(self, user_id):
        """Lấy task stats và task list cho dashboard Design/Production."""
        now = fields.Datetime.now()
        Task = self.env['dac.work.task'].sudo()

        active_tasks = Task.search([
            ('assigned_user_id', '=', user_id),
            ('state', 'not in', ['done', 'cancelled']),
        ], order='deadline asc nulls last, priority desc')

        week_ago = now - timedelta(days=7)
        done_count = Task.search_count([
            ('assigned_user_id', '=', user_id),
            ('state', '=', 'done'),
            ('write_date', '>=', fields.Datetime.to_string(week_ago)),
        ])

        overdue_count = sum(1 for t in active_tasks if t.deadline and t.deadline < now)
        due_soon_count = sum(
            1 for t in active_tasks
            if t.deadline and now <= t.deadline <= now + timedelta(hours=48)
        )
        urgent_count = sum(1 for t in active_tasks if t.priority == 'urgent')

        def _pack(t):
            dl = t.deadline
            is_overdue = bool(dl and dl < now)
            days_left = False
            if dl and not is_overdue:
                days_left = max(0, int((dl - now).total_seconds() / 86400))
            dl_str = False
            if dl:
                dl_loc = fields.Datetime.context_timestamp(self, dl)
                dl_str = dl_loc.strftime('%d/%m/%Y %H:%M')
            return {
                'id': t.id,
                'name': t.name or '',
                'task_type': t.task_type or 'other',
                'state': t.state,
                'priority': t.priority,
                'deadline_str': dl_str,
                'is_overdue': is_overdue,
                'days_left': days_left,
                'order_id': t.order_id.id if t.order_id else False,
                'order_name': t.order_id.name if t.order_id else False,
                'order_number': (t.order_id.order_number or False) if t.order_id else False,
                'order_title': (t.order_id.order_title or False) if t.order_id else False,
                'partner_name': t.order_id.partner_id.name if t.order_id else '',
                'description': _first_line(t.description),
            }

        buckets = {k: [] for k in ('design', 'production', 'survey', 'supplement', 'other')}
        all_packed = []
        for t in active_tasks:
            p = _pack(t)
            all_packed.append(p)
            ttype = p['task_type']
            buckets[ttype if ttype in buckets else 'other'].append(p)

        return {
            'task_stats': {
                'total': len(active_tasks),
                'overdue': overdue_count,
                'due_soon': due_soon_count,
                'done_week': done_count,
                'urgent': urgent_count,
            },
            'all_tasks': all_packed,
            'tasks_by_type': buckets,
        }

    @api.model
    def dac_get_dashboard_design(self):
        uid = self.env.uid
        user = self.env.user
        if not (user._dac_is_manager() or user._dac_is_design()):
            raise AccessError(_('Bạn không có quyền truy cập dashboard này.'))
        Task = self.env['dac.work.task'].sudo()
        now = fields.Datetime.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        # KPI "Tổng việc": tất cả task chưa hoàn thành được giao cho người xem,
        # thống nhất với Dashboard Thiết kế v5.
        total_active_tasks = Task.search_count([
            ('assigned_user_id', '=', uid),
            ('state', 'not in', ['done', 'cancelled']),
        ])
        total_in_progress_tasks = Task.search_count([
            ('assigned_user_id', '=', uid),
            ('state', '=', 'in_progress'),
        ])
        active_domain = [
            ('task_type', '=', 'design'),
            ('state', 'not in', ['done', 'cancelled']),
            ('order_id.order_state_custom', '=', 'deposit'),
        ]
        completed_domain = [
            ('task_type', '=', 'design'),
            ('state', '=', 'done'),
            ('write_date', '>=', fields.Datetime.to_string(month_start)),
            ('order_id.order_state_custom', '=', 'deposit'),
        ]
        if not user._dac_is_manager():
            active_domain.append(('assigned_user_id', '=', uid))
            completed_domain.append(('assigned_user_id', '=', uid))
        active = Task.search(active_domain, order='priority desc, id desc, deadline asc nulls last')
        completed_month = Task.search(completed_domain)
        overdue = active.filtered(lambda t: t.deadline and t.deadline < now)
        due_soon = active.filtered(
            lambda t: t.deadline and now <= t.deadline <= now + timedelta(hours=48)
        )
        priority = active.filtered(lambda t: t.priority in ('high', 'urgent'))
        designing = active.filtered(lambda t: t.state == 'in_progress')
        waiting = active.filtered(lambda t: t.state == 'draft')
        on_time_done = completed_month.filtered(
            lambda t: not t.deadline or t.write_date <= t.deadline
        )
        def _pack_design_task(task):
            deadline = task.deadline
            is_overdue = bool(deadline and deadline < now)
            days_left = False if not deadline or is_overdue else max(0, int((deadline - now).total_seconds() / 86400))
            deadline_str = fields.Datetime.context_timestamp(self, deadline).strftime('%d/%m/%Y %H:%M') if deadline else False
            return {
                'id': task.id, 'name': task.name or '', 'state': task.state,
                'priority': task.priority, 'deadline_str': deadline_str,
                'is_overdue': is_overdue, 'days_left': days_left,
                'order_id': task.order_id.id if task.order_id else False,
                'order_name': task.order_id.name if task.order_id else False,
                'order_number': (task.order_id.order_number or False) if task.order_id else False,
                'order_title': (task.order_id.order_title or False) if task.order_id else False,
                'partner_name': task.order_id.partner_id.name if task.order_id else '',
                'description': _first_line(task.description),
            }

        design_order_domain = [('order_state_custom', '=', 'deposit')]
        if not user._dac_is_manager():
            design_order_domain.append(('user_id_design', '=', uid))
        design_orders = self.env['sale.order'].sudo().search(design_order_domain)
        priority_orders = []
        for order in design_orders:
            unfinished = order.task_ids.filtered(
                lambda task: task.task_type == 'design'
                and task.state not in ('done', 'cancelled')
            )
            if not unfinished:
                continue
            deadline = order.design_deadline
            is_overdue = bool(deadline and deadline < fields.Date.context_today(self))
            days_left = False
            if deadline and not is_overdue:
                days_left = max(0, (deadline - fields.Date.context_today(self)).days)
            priority_orders.append({
                'id': order.id,
                'order_id': order.id,
                'name': order.order_title or order.partner_id.name or order.name,
                'order_name': order.name or False,
                'order_number': order.order_number or order.name or False,
                'order_title': order.order_title or False,
                'partner_name': order.partner_id.name or '',
                'priority': 'urgent' if order.is_priority_today else ('high' if order.is_priority else 'normal'),
                'deadline_str': deadline.strftime('%d/%m/%Y') if deadline else False,
                'is_overdue': is_overdue,
                'days_left': days_left,
                'description': _('còn %s task') % len(unfinished),
            })
        priority_rank = {'urgent': 0, 'high': 1, 'normal': 2}
        priority_orders.sort(key=lambda item: (
            priority_rank.get(item['priority'], 2),
            not item['is_overdue'],
            item['days_left'] if item['days_left'] is not False else 999999,
            -item['id'],
        ))
        return {
            'task_stats': {
                'total': total_active_tasks,
                'in_progress': total_in_progress_tasks,
                'overdue': len(overdue),
                'due_soon': len(due_soon),
                'done_month': len(completed_month),
                'on_time_rate': round(len(on_time_done) * 100 / len(completed_month)) if completed_month else 100,
            },
            'design_tasks': priority_orders,
            'design_distribution': {
                'total': len(active) + len(completed_month),
                'designing': len(designing),
                'waiting': len(waiting),
                'revision': len(priority),
                'ready': len(completed_month),
            },
            'user_name': user.name,
        }

    @api.model
    def dac_get_dashboard_production(self):
        uid = self.env.uid
        user = self.env.user
        if not (user._dac_is_manager() or user._dac_is_production()):
            raise AccessError(_('Bạn không có quyền truy cập dashboard sản xuất.'))
        data = self._get_task_dashboard_data(uid)
        b = data['tasks_by_type']
        today = fields.Date.context_today(self)
        Order = self.env['sale.order'].sudo()
        domain = [('order_state_custom', '=', 'production')]
        if not user._dac_is_manager():
            domain += ['|', ('user_id_production', '=', uid), ('production_group_ids', 'in', uid)]
        orders = Order.search(domain, order='production_deadline asc nulls last, id desc')

        overdue = orders.filtered(
            lambda o: o.production_deadline and o.production_deadline < today
        )
        finishing_soon = orders.filtered(
            lambda o: o.production_deadline
            and today <= o.production_deadline <= today + timedelta(days=2)
        )

        def _pack_order(order):
            tasks = order.task_ids.filtered(
                lambda t: t.task_type in ('production', 'survey', 'supplement', 'other')
                and t.state != 'cancelled'
            )
            completed = len(tasks.filtered(lambda t: t.state == 'done'))
            progress = round(completed * 100 / len(tasks)) if tasks else 0
            team = order.user_id_production.name if order.user_id_production else ''
            if not team and order.production_group_ids:
                team = ', '.join(order.production_group_ids.mapped('name')[:2])
            return {
                'id': order.id,
                'number': order.order_number or order.name or '',
                'title': order.order_title or order.partner_id.name or _('Đơn sản xuất'),
                'partner_name': order.partner_id.name or '',
                'deadline': order.production_deadline.strftime('%d/%m') if order.production_deadline else '',
                'is_overdue': bool(order.production_deadline and order.production_deadline < today),
                'progress': progress,
                'remaining_tasks': max(len(tasks) - completed, 0),
                'team': team or _('Chưa phân tổ'),
                'amount_total': order.amount_total,
            }

        active_value = sum(orders.mapped('amount_total'))
        return {
            'task_stats': data['task_stats'],
            'design_tasks': b['design'],
            'production_tasks': b['production'] + b['survey'] + b['supplement'] + b['other'],
            'user_name': user.name,
            'is_design_user': (user._dac_is_design() or user._dac_is_manager()),
            'production_stats': {
                'active': len(orders),
                'active_value': active_value,
                'overdue': len(overdue),
                'finishing_soon': len(finishing_soon),
                'on_time_rate': round((len(orders) - len(overdue)) * 100 / len(orders)) if orders else 100,
            },
            'production_orders': [_pack_order(order) for order in orders[:8]],
        }
