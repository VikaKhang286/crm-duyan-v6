/** @odoo-module **/
// Bảng số liệu khách liên hệ mới: form website + Pancake (Zalo/Messenger), lấy trực tiếp từ dac.new.contact.
import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const CHANNELS = [
    { key: "web", label: "Form website", color: "#f59e0b", icon: "fa-globe" },
    { key: "zalo", label: "Zalo", color: "#0068ff", icon: "fa-comment" },
    { key: "messenger", label: "Messenger", color: "#a855f7", icon: "fa-facebook" },
    { key: "other", label: "Kênh khác", color: "#94a3b8", icon: "fa-comments" },
];
const PERIODS = [
    { key: "today", label: "Hôm nay" },
    { key: "yesterday", label: "Hôm qua" },
    { key: "7d", label: "7 ngày" },
    { key: "30d", label: "30 ngày" },
    { key: "month", label: "Tháng này" },
    { key: "custom", label: "Tuỳ chọn" },
];
const BUCKET_LABELS = { hour: "giờ", day: "ngày", month: "tháng" };

function isoDate(date) {
    const pad = (n) => String(n).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

export class ContactDashboard extends Component {
    static template = "dac_pancake_remind.ContactDashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.channels = CHANNELS;
        this.periods = PERIODS;
        const today = new Date();
        const monthAgo = new Date(today.getTime() - 29 * 86400000);
        this.state = useState({
            period: "30d", data: null, loading: true, error: false,
            dateFrom: isoDate(monthAgo), dateTo: isoDate(today), showCustom: false,
        });
        onWillStart(() => this.load());
    }

    selectPeriod(period) {
        if (period === "custom") {
            // Chọn ngày trước rồi bấm "Xem"; chưa tải lại ngay
            this.state.showCustom = true;
            return;
        }
        this.state.showCustom = false;
        this.load(period);
    }

    applyCustom() {
        if (!this.state.dateFrom || !this.state.dateTo) {
            return;
        }
        this.load("custom");
    }

    async load(period = this.state.period) {
        this.state.loading = true;
        this.state.period = period;
        const args = period === "custom" ? [period, this.state.dateFrom, this.state.dateTo] : [period];
        try {
            this.state.data = await this.orm.call("dac.new.contact", "dashboard_data", args);
            this.state.error = false;
        } catch {
            this.state.error = true;
        }
        this.state.loading = false;
    }

    get delta() {
        const { total, previous } = this.state.data;
        if (!previous) {
            return total ? { text: "kỳ trước 0", up: true } : null;
        }
        const pct = Math.round(((total - previous) / previous) * 100);
        return { text: `${pct >= 0 ? "+" : ""}${pct}% so với kỳ trước (${previous})`, up: pct >= 0 };
    }

    share(key) {
        const total = this.state.data.total;
        return total ? Math.round((this.state.data.channels[key] / total) * 100) : 0;
    }

    get visibleChannels() {
        return CHANNELS.filter((c) => c.key !== "other" || this.state.data.channels.other);
    }

    get maxColumn() {
        return Math.max(1, ...this.state.data.series.map((row) => CHANNELS.reduce((sum, c) => sum + row[c.key], 0)));
    }

    get halfMax() {
        return Math.round(this.maxColumn / 2);
    }

    columnTotal(row) {
        return CHANNELS.reduce((sum, c) => sum + row[c.key], 0);
    }

    segmentStyle(row, channel) {
        return `height:${(row[channel.key] / this.maxColumn) * 100}%;background:${channel.color}`;
    }

    columnTitle(row) {
        const parts = CHANNELS.filter((c) => row[c.key]).map((c) => `${c.label}: ${row[c.key]}`);
        return `${row.label} — ${this.columnTotal(row)} khách${parts.length ? "\n" + parts.join("\n") : ""}`;
    }

    get bucketLabel() {
        return BUCKET_LABELS[this.state.data.bucket] || "ngày";
    }

    isActive(key) {
        return key === "custom" ? this.state.showCustom || this.state.period === "custom" : !this.state.showCustom && this.state.period === key;
    }

    showLabel(index) {
        const count = this.state.data.series.length;
        const step = count > 20 ? Math.ceil(count / 12) : 1;
        return index % step === 0 || index === count - 1;
    }

    get maxHour() {
        return Math.max(1, ...this.state.data.hours);
    }

    hourStyle(value) {
        return `height:${Math.max(value ? 4 : 0, (value / this.maxHour) * 100)}%`;
    }

    get maxPage() {
        return Math.max(1, ...this.state.data.pages.map((p) => p.count));
    }

    channelOf(key) {
        return CHANNELS.find((c) => c.key === key) || CHANNELS[3];
    }

    openContacts(channel = null) {
        const data = this.state.data;
        const domain = [["counts_as_new", "=", true], ["contact_at", ">=", data.start], ["contact_at", "<", data.end]];
        if (channel === "web") {
            domain.push(["source", "=", "web_lead"]);
        } else if (channel === "zalo") {
            domain.push(["source", "=", "pancake"], ["platform", "ilike", "zalo"]);
        } else if (channel === "messenger") {
            domain.push(["source", "=", "pancake"], ["platform", "in", ["facebook", "messenger"]]);
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Khách mới liên hệ",
            res_model: "dac.new.contact",
            views: [[false, "list"], [false, "pivot"], [false, "graph"], [false, "form"]],
            domain,
        });
    }

    openWaits() {
        this.action.doAction("dac_pancake_remind.remind_wait_action", {
            additionalContext: { search_default_waiting: 1 },
        });
    }

    openRow(row) {
        if (row.url) {
            window.open(row.url, row.url.startsWith("http") ? "_blank" : "_self");
        }
    }
}

registry.category("actions").add("dac_contact_dashboard", ContactDashboard);
