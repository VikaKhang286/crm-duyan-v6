/** @odoo-module **/

import { registry } from "@web/core/registry";
import { user } from "@web/core/user";

const DEPOSIT_POLL_INTERVAL = 60_000;
const DEPOSIT_POLL_LOCK_KEY = "dac.deposit_reminder.last_poll";

function claimDepositPollingSlot(lockKey) {
    const now = Date.now();
    try {
        const lastPoll = Number(window.localStorage.getItem(lockKey) || 0);
        if (now - lastPoll < DEPOSIT_POLL_INTERVAL - 5_000) {
            return false;
        }
        window.localStorage.setItem(lockKey, String(now));
        return window.localStorage.getItem(lockKey) === String(now);
    } catch {
        // Privacy settings may disable localStorage. Polling is still preferable
        // to losing reminders, although multiple tabs can then show duplicates.
        return true;
    }
}

function depositReminderOrderCode(order) {
    if (order.order_number) {
        return order.order_number;
    }
    const sequenceMatch = /^S0*(\d+)$/.exec(order.name || "");
    const dateMatch = /^(\d{4})-(\d{2})/.exec(order.date || "");
    if (sequenceMatch && dateMatch) {
        const [, year, month] = dateMatch;
        return `XD-${sequenceMatch[1]}/${month}/${year.slice(-2)}`;
    }
    return order.name;
}

const depositReminderNotificationService = {
    dependencies: ["action", "bus_service", "notification", "orm"],
    start(env, { action, bus_service, notification: notificationService, orm }) {
        let currentUnpaidOrderIds = [];
        document.addEventListener("click", (event) => {
            const notification = event.target.closest(
                ".dac_deposit_reminder_notification"
            );
            if (!notification || event.target.closest("button")) {
                return;
            }
            const orderIds = [...currentUnpaidOrderIds];
            if (!orderIds.length) {
                return;
            }
            void action.doAction("dac_erp.dac_sale_order_custom_action", {
                additionalContext: {
                    deposit_reminder_order_ids: orderIds,
                    search_default_deposit_reminder_unpaid: 1,
                },
            });
            notification.querySelector(".o_notification_close")?.click();
        });

        bus_service.subscribe(
            "simple_notification",
            ({ message, sticky, title, type, autocloseDelay }) => {
                console.log("[DAC Deposit Reminder] notification received", {
                    title,
                    message,
                    sticky,
                    type,
                    autocloseDelay,
                });
                notificationService.add(message, {
                    sticky,
                    title,
                    type,
                    autocloseDelay,
                });
            }
        );
        bus_service.start();

        const setupDepositPolling = async () => {
            const [isSale, isManager] = await Promise.all([
                user.hasGroup("dac_erp.group_dac_erp_sale"),
                user.hasGroup("dac_erp.group_dac_erp_manager"),
            ]);
            if (!isSale && !isManager) {
                console.log("[DAC Deposit Reminder] polling disabled for current role");
                return;
            }

            const domain = [
                ["order_state_custom", "=", "deposit"],
                ["user_id", "=", user.userId],
            ];
            const lockKey = `${DEPOSIT_POLL_LOCK_KEY}.${user.userId}`;

            const pollMissingDeposits = async () => {
                if (!claimDepositPollingSlot(lockKey)) {
                    return;
                }

                try {
                    const depositOrders = await orm.searchRead(
                        "sale.order",
                        domain,
                        [
                            "name",
                            "order_number",
                            "date",
                            "user_id",
                            "order_state_custom",
                            "total_deposit_paid",
                        ],
                        { order: "user_id, date asc, id asc" }
                    );
                    const unpaidOrders = depositOrders.filter(
                        (order) => order.total_deposit_paid <= 0
                    );
                    
                    if (!unpaidOrders.length) {
                        return;
                    }

                    const labels = unpaidOrders.map(depositReminderOrderCode);
                    let message = `Chưa nhận cọc: ${labels.join(", ")}`;
                    const unpaidOrderIds = unpaidOrders.map((order) => order.id);
                    currentUnpaidOrderIds = unpaidOrderIds;
                    notificationService.add(message, {
                        title: `Nhắc thu tiền cọc (${unpaidOrders.length} đơn)`,
                        type: "warning",
                        sticky: false,
                        autocloseDelay: 20_000,
                        className: "dac_deposit_reminder_notification",
                    });
                    
                } catch (error) {
                    console.error("[DAC Deposit Reminder] ORM polling failed", error);
                }
            };

            void pollMissingDeposits();
            window.setInterval(pollMissingDeposits, DEPOSIT_POLL_INTERVAL);
        };

        void setupDepositPolling();
    },
};

registry.category("services").add(
    "simple_notification",
    depositReminderNotificationService,
    { force: true }
);
