import logging

from odoo import api, models


_logger = logging.getLogger(__name__)


class SaleOrderDepositReminder(models.Model):
    _inherit = 'sale.order'

    @api.model
    def _cron_remind_missing_deposits(self):
        """Notify each salesperson about their deposit-stage unpaid orders."""
        try:
            _logger.warning(
                '[DAC Deposit Reminder] cron started db=%s',
                self.env.cr.dbname,
            )
            # The cron runs as root so managers also see unassigned orders;
            # salespeople still receive only orders assigned to them.
            candidate_orders = self.sudo().search([
                ('order_state_custom', '=', 'deposit'),
            ], order='user_id, date asc, id asc')
            _logger.warning(
                '[DAC Deposit Reminder] candidate_orders=%s ids=%s',
                len(candidate_orders),
                candidate_orders.ids,
            )
            unpaid_orders = candidate_orders.filtered(
                lambda order: order.total_deposit_paid <= 0
            )
            _logger.warning(
                '[DAC Deposit Reminder] unpaid_orders=%s ids=%s',
                len(unpaid_orders),
                unpaid_orders.ids,
            )

            manager_group = self.env.ref('dac_erp.group_dac_erp_manager')
            manager_users = self.env['res.users'].sudo().search([
                ('active', '=', True),
                ('groups_id', 'in', manager_group.ids),
            ])
            _logger.warning(
                '[DAC Deposit Reminder] active_manager_user_ids=%s',
                manager_users.ids,
            )

            orders_by_user = {}
            for order in unpaid_orders:
                if (
                    not order.user_id
                    or not order.user_id.active
                    or order.user_id in manager_users
                ):
                    continue
                orders_by_user.setdefault(order.user_id, self.browse())
                orders_by_user[order.user_id] |= order

            # Managers always receive the complete list, regardless of the
            # salesperson assigned to each order.
            if unpaid_orders:
                for manager in manager_users:
                    orders_by_user[manager] = unpaid_orders

            _logger.warning(
                '[DAC Deposit Reminder] recipient_map=%s',
                {
                    user.id: orders.ids
                    for user, orders in orders_by_user.items()
                },
            )

            for user, orders in orders_by_user.items():
                try:
                    labels = [
                        order.order_number or order.name
                        for order in orders[:5]
                    ]
                    message = 'Chưa nhận cọc: %s' % ', '.join(labels)
                    
                    _logger.warning(
                        '[DAC Deposit Reminder] sending user_id=%s partner_id=%s order_ids=%s',
                        user.id,
                        user.partner_id.id,
                        orders.ids,
                    )
                    self.env['bus.bus']._sendone(
                        user.partner_id,
                        'simple_notification',
                        {
                            'type': 'warning',
                            'title': 'Nhắc thu tiền cọc (%s đơn)' % len(orders),
                            'message': message,
                            'sticky': False,
                            'autocloseDelay': 20000,
                        },
                    )
                    _logger.warning(
                        '[DAC Deposit Reminder] bus notification queued user_id=%s partner_id=%s',
                        user.id,
                        user.partner_id.id,
                    )
                except Exception:
                    _logger.exception(
                        'Unable to send missing-deposit reminder to user %s',
                        user.id,
                    )
        except Exception:
            # Scheduled actions must not interrupt the cron worker.
            _logger.exception('Unable to send missing-deposit reminders')

