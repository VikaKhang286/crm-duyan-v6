/** @odoo-module **/

import { FormController } from "@web/views/form/form_controller";
import { patch } from "@web/core/utils/patch";
import { onMounted, onPatched, onWillUnmount } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { user } from "@web/core/user";

const ACTIVE_STATES = ["draft", "in_progress"];

function isHome(controller) {
    return controller.props?.resModel === "home.menu";
}

function dateKey(date) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
}

function many2oneName(value) {
    return value?.display_name || value?.name || value?.[1] || "—";
}

function initials(name) {
    return name.split(/\s+/).filter(Boolean).slice(-2).map((part) => part[0]).join("").toUpperCase() || "DA";
}

patch(FormController.prototype, {
    setup() {
        super.setup(...arguments);
        this.dacHomeOrm = useService("orm");
        this.dacHomeAction = useService("action");
        this.dacHomeLoaded = false;
        onMounted(() => this.mountDacHomeDashboard());
        onPatched(() => this.mountDacHomeDashboard());
        onWillUnmount(() => this.cleanupDacHomeDashboard());
    },

    mountDacHomeDashboard() {
        if (!isHome(this)) return;
        const root = this.rootRef?.el;
        const dashboard = root?.querySelector(".dac-home-dashboard");
        if (!dashboard) return;
        root.closest(".o_action_manager")?.classList.add("dac-home-dashboard-active");
        const button = dashboard.querySelector(".dac-home-quick-task");
        if (button && !button.dataset.bound) {
            button.dataset.bound = "true";
            button.addEventListener("click", () => this.openDacQuickTask());
        }
        if (!this.dacHomeLoaded) {
            this.dacHomeLoaded = true;
            this.loadDacHomeData(dashboard);
        }
    },

    async loadDacHomeData(dashboard) {
        const now = new Date();
        const today = dateKey(now);
        const dayStart = `${today} 00:00:00`;
        const dayEnd = `${today} 23:59:59`;
        const myDomain = [["assigned_user_id", "=", user.userId], ["state", "in", ACTIVE_STATES]];
        try {
            const [currentUser, myTasks, todayTasks, overdue, approvals, watchedOrders, schedule, recentTasks, recentOrders] = await Promise.all([
                this.dacHomeOrm.read("res.users", [user.userId], ["name", "partner_id"]),
                this.dacHomeOrm.searchCount("dac.work.task", myDomain),
                this.dacHomeOrm.searchCount("dac.work.task", [...myDomain, ["deadline", ">=", dayStart], ["deadline", "<=", dayEnd]]),
                this.dacHomeOrm.searchCount("dac.work.task", [...myDomain, ["deadline", "<", now.toISOString().slice(0, 19).replace("T", " ")]]),
                this.dacHomeOrm.searchCount("dac.work.task", [["assigned_user_id", "=", user.userId], ["state", "=", "draft"], ["priority", "in", ["high", "urgent"]]]),
                this.dacHomeOrm.searchCount("sale.order", [["user_id", "=", user.userId], ["order_state_custom", "not in", ["completed", "cancel"]], ["is_priority_today", "=", true]]),
                this.dacHomeOrm.searchRead("dac.work.task", [...myDomain, ["deadline", ">=", dayStart], ["deadline", "<=", dayEnd]], ["name", "deadline", "order_id", "task_type"], { limit: 6, order: "deadline asc" }),
                this.dacHomeOrm.searchRead("dac.work.task", [], ["name", "assigned_user_id", "order_id", "write_date"], { limit: 3, order: "write_date desc" }),
                this.dacHomeOrm.searchRead("sale.order", [["order_type", "=", "cart"]], ["name", "order_number", "user_id", "partner_id", "amount_total", "write_date"], { limit: 3, order: "write_date desc" }),
            ]);
            const userData = currentUser[0] || {};
            dashboard.querySelector("[data-home-user]").textContent = (userData.name || "bạn").split(/\s+/).slice(-1)[0];
            const values = { my_tasks: myTasks, today_tasks: todayTasks, overdue, approvals, orders: watchedOrders };
            for (const [key, value] of Object.entries(values)) dashboard.querySelector(`[data-home-stat="${key}"]`).textContent = value;
            let notifications = 0;
            const partnerId = userData.partner_id?.id || userData.partner_id?.[0];
            if (partnerId) {
                try {
                    notifications = await this.dacHomeOrm.searchCount("mail.notification", [["res_partner_id", "=", partnerId], ["is_read", "=", false]]);
                } catch (_) { /* access rights differ by role */ }
            }
            dashboard.querySelector('[data-home-stat="notifications"]').textContent = notifications;
            dashboard.querySelector("[data-home-date]").textContent = now.toLocaleDateString("vi-VN");
            this.renderDacSchedule(dashboard, schedule);
            const activities = [
                ...recentTasks.map((item) => ({
                    date: item.write_date,
                    title: `${many2oneName(item.assigned_user_id)} cập nhật task`,
                    detail: `${item.name}${item.order_id ? ` · ${many2oneName(item.order_id)}` : ""}`,
                    person: many2oneName(item.assigned_user_id),
                })),
                ...recentOrders.map((item) => ({
                    date: item.write_date,
                    title: `${many2oneName(item.user_id)} cập nhật đơn ${item.order_number || item.name}`,
                    detail: `${many2oneName(item.partner_id)} · ${new Intl.NumberFormat("vi-VN").format(item.amount_total || 0)}đ`,
                    person: many2oneName(item.user_id),
                })),
            ].sort((a, b) => String(b.date).localeCompare(String(a.date))).slice(0, 3);
            this.renderDacActivities(dashboard, activities, now);
        } catch (error) {
            console.warn("Unable to load home dashboard", error);
            dashboard.querySelector("[data-home-schedule]").innerHTML = '<div class="dac-home-empty">Chưa thể tải lịch hôm nay.</div>';
            dashboard.querySelector("[data-home-activity]").innerHTML = '<div class="dac-home-empty">Chưa thể tải hoạt động gần đây.</div>';
        }
    },

    renderDacSchedule(dashboard, tasks) {
        const container = dashboard.querySelector("[data-home-schedule]");
        container.replaceChildren();
        if (!tasks.length) {
            const empty = document.createElement("div");
            empty.className = "dac-home-empty";
            empty.textContent = "Không có lịch cần xử lý hôm nay.";
            container.append(empty);
            return;
        }
        for (const task of tasks) {
            const row = document.createElement("div");
            row.className = "dac-home-schedule-row";
            const time = document.createElement("time");
            time.textContent = task.deadline ? task.deadline.slice(11, 16) : "—";
            const dot = document.createElement("i");
            const copy = document.createElement("div");
            const title = document.createElement("strong");
            title.textContent = task.name;
            const detail = document.createElement("span");
            detail.textContent = task.order_id ? many2oneName(task.order_id) : "Công việc cá nhân";
            copy.append(title, detail);
            row.append(time, dot, copy);
            container.append(row);
        }
    },

    renderDacActivities(dashboard, activities, now) {
        const container = dashboard.querySelector("[data-home-activity]");
        container.replaceChildren();
        for (const activity of activities) {
            const row = document.createElement("div");
            row.className = "dac-home-activity-row";
            const avatar = document.createElement("span");
            avatar.textContent = initials(activity.person);
            const copy = document.createElement("div");
            const title = document.createElement("strong");
            title.textContent = activity.title;
            const detail = document.createElement("span");
            const changedAt = new Date(String(activity.date).replace(" ", "T") + "Z");
            const minutes = Math.max(1, Math.round((now - changedAt) / 60000));
            detail.textContent = `${activity.detail} · ${minutes < 60 ? `${minutes} phút trước` : changedAt.toLocaleDateString("vi-VN")}`;
            copy.append(title, detail);
            row.append(avatar, copy);
            container.append(row);
        }
    },

    openDacQuickTask() {
        return this.dacHomeAction.doAction({
            type: "ir.actions.act_window",
            name: "Tạo task nhanh",
            res_model: "dac.work.task",
            views: [[false, "form"]],
            target: "current",
            context: { default_is_personal_reminder: true, default_assigned_user_id: user.userId },
        });
    },

    cleanupDacHomeDashboard() {
        if (!isHome(this)) return;
        this.rootRef?.el?.closest(".o_action_manager")?.classList.remove("dac-home-dashboard-active");
    },
});
