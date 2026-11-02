import logging
import uuid

from odoo import api, models
from odoo.tools import html2plaintext

_logger = logging.getLogger(__name__)
ORDER_FIELDS = ('order_state_custom', 'user_id', 'user_id_design', 'user_id_production', 'production_group_ids',
    'design_deadline', 'production_deadline', 'design_done', 'production_done', 'design_link',
    'order_title', 'order_summary', 'is_priority', 'delivery_date', 'fulfillment_method',
    'production_is_delayed', 'production_delay_date', 'production_delay_reason')
TASK_FIELDS = ('assigned_user_id', 'state', 'deadline', 'remind_at', 'priority', 'is_blocked', 'description')


def snapshot(record, names):
    return {name: tuple(record[name].ids) if record._fields[name].type in ('many2one', 'many2many') else record[name] for name in names}


def notify(env, category, source, users, key, **kwargs):
    """Outbox failures must not break sale/order/task transactions or change their values."""
    try:
        with env.cr.savepoint():
            if source._name == 'dac.work.task':
                kwargs.setdefault('urgent', source.priority == 'urgent')
            elif source._name == 'sale.order':
                kwargs.setdefault('urgent', bool(source.is_priority_today))
            return env['dac.zalo.notification']._emit(category, source, users, key, **kwargs)
    except Exception:
        _logger.warning('Zalo notification deferred unsuccessfully: category=%s model=%s id=%s', category, source._name, source.id)


class SaleOrderZaloNotification(models.Model):
    _inherit = 'sale.order'

    @api.model_create_multi
    def create(self, vals_list):
        orders = super(SaleOrderZaloNotification, self.with_context(dac_zalo_order_creating=True)).create(vals_list)
        orders = orders.with_env(self.env)
        for order in orders:
            notify(self.env, 'order_created', order, order.user_id, ['created', order.id],
                payload={'creator_id': order.create_uid.id})
            for person, kind in [(order.user_id_design, 'design'), (order.user_id_production, 'production')]:
                if person and not self.env['dac.work.task'].search_count([('order_id', '=', order.id),
                    ('task_type', '=', kind), ('assigned_user_id', '=', person.id), ('state', 'in', ['draft', 'in_progress'])]):
                    notify(self.env, 'task_assigned', order, person, ['created-owner', order.id, kind],
                        payload={'assignment': True, 'summary': 'Bạn được giao phụ trách %s của đơn này.' % ('thiết kế' if kind == 'design' else 'sản xuất')})
            # Production group members do not have individual auto-created assignment tasks.
            notify(self.env, 'task_assigned', order, order.production_group_ids, ['created-group', order.id],
                payload={'assignment': True, 'summary': 'Bạn được giao vào nhóm sản xuất của đơn này.'})
        return orders

    def write(self, vals):
        if self.env.context.get('dac_zalo_order_collecting') or self.env.context.get('dac_zalo_order_creating'):
            return super().write(vals)
        before = {order.id: snapshot(order, ORDER_FIELDS) for order in self}
        token = uuid.uuid4().hex
        result = super(SaleOrderZaloNotification, self.with_context(dac_zalo_order_collecting=True)).write(vals)
        for order in self:
            previous = before[order.id]
            after = snapshot(order, ORDER_FIELDS)
            changed = {name for name in ORDER_FIELDS if previous[name] != after[name]}
            if not changed:
                continue
            people = order.user_id | order.user_id_design | order.user_id_production | order.production_group_ids
            if 'user_id' in changed:
                notify(self.env, 'order_updates', order, order.user_id, [token, 'sale-assignment'],
                    payload={'summary': 'Bạn được giao phụ trách đơn này.'})
            for field, kind in [('user_id_design', 'design'), ('user_id_production', 'production')]:
                person = order[field]
                if field in changed and person and not self.env['dac.work.task'].search_count([('order_id', '=', order.id),
                    ('task_type', '=', kind), ('assigned_user_id', '=', person.id), ('state', 'in', ['draft', 'in_progress'])]):
                    notify(self.env, 'task_assigned', order, person, [token, field],
                        payload={'assignment': True, 'summary': 'Bạn được giao phụ trách đơn này.'})
            new_members = order.production_group_ids.filtered(lambda user: user.id not in previous['production_group_ids'])
            if new_members:
                notify(self.env, 'task_assigned', order, new_members, [token, 'group-assignment'],
                    payload={'assignment': True, 'summary': 'Bạn được giao vào nhóm sản xuất của đơn này.'})
            if 'order_state_custom' in changed:
                labels = dict(order._fields['order_state_custom'].selection)
                stage = order.order_state_custom
                category = 'handover' if stage == 'production' else ('delivery' if stage in ('delivery', 'installation') else 'order_updates')
                targets = (order.user_id | order.user_id_production | order.production_group_ids) if stage in ('production', 'delivery', 'installation') else people
                notify(self.env, category, order, targets, [token, stage], payload={
                    'stage': stage, 'summary': 'Tiến trình: %s → %s. Mở đơn để xem yêu cầu và bản vẽ mới nhất.' %
                    (labels.get(previous['order_state_custom'], previous['order_state_custom']), labels.get(stage, stage))})
            for field, description in [('design_done', 'Thiết kế'), ('production_done', 'Sản xuất')]:
                if field in changed:
                    targets = order.user_id | (order.user_id_production | order.production_group_ids if field == 'design_done' else self.env['res.users'])
                    notify(self.env, 'handover', order, targets, [token, field], payload={field: str(order[field] or ''),
                        'summary': '%s %s.' % (description, 'đã hoàn tất; bộ phận tiếp theo có thể kiểm tra' if order[field] else 'cần thực hiện / kiểm tra lại')})
            for field, title, assignee, task_type in [
                ('design_deadline', 'Hạn thiết kế', order.user_id_design, 'design'),
                ('production_deadline', 'Hạn sản xuất', order.user_id_production, 'production'),
            ]:
                if field not in changed:
                    continue
                # The task hook owns the assignee's deadline alert, avoiding an order+task double alert.
                targets = order.user_id | (order.production_group_ids if task_type == 'production' else self.env['res.users'])
                if assignee and not self.env['dac.work.task'].search_count([
                    ('order_id', '=', order.id), ('task_type', '=', task_type),
                    ('assigned_user_id', '=', assignee.id), ('state', 'in', ['draft', 'in_progress'])]):
                    targets |= assignee
                notify(self.env, 'deadline_changed', order, targets, [token, field], payload={field: str(order[field] or ''),
                    'summary': '%s: %s → %s.' % (title, previous[field] or 'chưa đặt', order[field] or 'chưa đặt')})
            if changed.intersection({'design_link', 'order_title', 'order_summary', 'is_priority',
                'production_is_delayed', 'production_delay_date', 'production_delay_reason', 'delivery_date', 'fulfillment_method'}):
                notify(self.env, 'deadline_changed', order, people, [token, 'context'],
                    payload={'summary': 'Yêu cầu, lịch, mức ưu tiên, lý do trễ hoặc bản vẽ đã thay đổi; vui lòng kiểm tra trước khi làm tiếp.'})
        return result

    def message_post(self, **kwargs):
        message = super().message_post(**kwargs)
        body = str(kwargs.get('body') or '')
        internal_panel = 'dac-internal-note-message' in body and bool(html2plaintext(body).strip())
        targets = message.partner_ids.user_ids.filtered(lambda user: not user.share)
        if internal_panel and not targets:
            targets = self.user_id | self.user_id_design | self.user_id_production | self.production_group_ids
        if targets and (message.message_type == 'comment' or internal_panel):
            notify(self.env, 'mentions', self, targets - self.env.user, ['message', message.id],
                payload={'summary': 'Có trao đổi nội bộ mới cần bạn xem. Mở Odoo để đọc và phản hồi.'})
        return message


class WorkTaskZaloNotification(models.Model):
    _inherit = 'dac.work.task'

    @api.model_create_multi
    def create(self, vals_list):
        tasks = super().create(vals_list)
        for task in tasks:
            if task.assigned_user_id:
                notify(self.env, 'task_assigned', task, task.assigned_user_id, ['created', task.id],
                    payload={'assignee': task.assigned_user_id.id})
        return tasks

    def write(self, vals):
        if self.env.context.get('dac_zalo_task_collecting'):
            return super().write(vals)
        before = {task.id: snapshot(task, TASK_FIELDS) for task in self}
        token = uuid.uuid4().hex
        result = super(WorkTaskZaloNotification, self.with_context(dac_zalo_task_collecting=True)).write(vals)
        for task in self:
            previous = before[task.id]
            after = snapshot(task, TASK_FIELDS)
            changed = {name for name in TASK_FIELDS if previous[name] != after[name]}
            if not changed:
                continue
            assignee = task.assigned_user_id
            if 'assigned_user_id' in changed and assignee:
                notify(self.env, 'task_assigned', task, assignee, [token, 'assigned'], payload={'assignee': assignee.id})
            if changed.intersection({'deadline', 'remind_at'}):
                notify(self.env, 'deadline_changed', task, assignee, [token, 'deadline'],
                    payload={'assignee': assignee.id, 'deadline': str(task.deadline or ''),
                        'summary': 'Lịch hoặc thời điểm nhắc việc đã được cập nhật.'})
            if changed.intersection({'state', 'is_blocked', 'priority', 'description'}):
                targets = assignee
                if task.order_id:
                    targets |= task.order_id.user_id
                labels = dict(task._fields['state'].selection)
                notify(self.env, 'task_updates', task, targets, [token, 'status'],
                    payload={'task_state': task.state, 'summary': '%s%s. Mở công việc để xem yêu cầu.' %
                        (labels.get(task.state, task.state), ' — đang bị chặn' if task.is_blocked else '')})
        return result
