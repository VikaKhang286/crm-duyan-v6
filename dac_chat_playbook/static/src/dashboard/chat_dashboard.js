/** @odoo-module **/
// Trung tâm Chatbot AI: tổng quan phân tích chat, đánh giá nhân viên, quy tắc chờ duyệt, system prompt, thiết lập.
import { Component, onMounted, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";

const OUTCOME_TONE = {
    chot_don: "is-good", cho_khach: "is-info", dang_trao_doi: "is-info",
    khach_im_lang: "is-warn", mat_khach: "is-bad", khong_lien_quan: "is-muted",
};
const BOT_TONE = { mo_dau: "is-good", mot_phan: "is-info", khong: "is-warn" };

export class ChatDashboard extends Component {
    static template = "dac_chat_playbook.ChatDashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        this.state = useState({ data: null, loading: true, error: false, busy: {}, openStaff: null, showSummary: false, reviewing: false });
        this.timer = null;
        onWillStart(() => this.load());
        onMounted(() => {
            this.timer = setInterval(() => {
                if (this.state.data?.running?.length && !document.hidden) {
                    this.load(true);
                }
            }, 10000);
        });
        onWillUnmount(() => clearInterval(this.timer));
    }

    async load(silent = false) {
        if (!silent) {
            this.state.loading = true;
        }
        try {
            this.state.data = await this.orm.call("dac.chat.analysis", "dashboard_data", []);
            this.state.error = false;
        } catch {
            this.state.error = true;
        }
        this.state.loading = false;
    }

    // ── Hiển thị ────────────────────────────────────────────────────────────
    num(value, unit = "") {
        if (value === null || value === undefined || value === false) {
            return "–";
        }
        const text = Number.isInteger(value) ? value.toLocaleString("vi-VN") : Number(value).toLocaleString("vi-VN", { maximumFractionDigits: 1 });
        return text + unit;
    }

    outcomeTone(key) {
        return OUTCOME_TONE[key] || "is-info";
    }

    botTone(key) {
        return BOT_TONE[key] || "is-info";
    }

    scoreTone(score) {
        if (!score) {
            return "is-muted";
        }
        return score >= 4 ? "is-good" : score >= 3 ? "is-info" : "is-bad";
    }

    maxCount(rows) {
        return Math.max(1, ...rows.map((r) => r.count));
    }

    barWidth(count, max) {
        return Math.max(3, Math.round((100 * count) / max));
    }

    toggleSummary() {
        this.state.showSummary = !this.state.showSummary;
    }

    toggleStaff(id) {
        this.state.openStaff = this.state.openStaff === id ? null : id;
    }

    // ── Điều hướng ──────────────────────────────────────────────────────────
    async openRun() {
        await this.action.doAction(
            { type: "ir.actions.act_window", name: "Chạy phân tích mới", res_model: "dac.chat.run.wizard", views: [[false, "form"]], target: "new" },
            { onClose: () => this.load(true) }
        );
    }

    openDemo() {
        this.action.doAction("dac_chat_playbook.chat_demo_action");
    }

    async reviewRules() {
        this.state.reviewing = true;
        try {
            const result = await this.orm.call("dac.chat.rule", "action_ai_review", [[]]);
            this.notification.add(result?.params?.message || "Đã xếp lượt chạy nền.", { title: "AI đang tối ưu bộ quy tắc", type: "info" });
            await this.load(true);
        } finally {
            this.state.reviewing = false;
        }
    }

    async openSettings() {
        const action = await this.orm.call("dac.chat.config", "action_open", []);
        await this.action.doAction(action);
    }

    openAnalysis(id) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "dac.chat.analysis", res_id: id, views: [[false, "form"]] });
    }

    openAnalyses() {
        this.action.doAction("dac_chat_playbook.chat_analysis_action");
    }

    openRules() {
        this.action.doAction("dac_chat_playbook.chat_rule_action");
    }

    openPrompts() {
        this.action.doAction("dac_chat_playbook.chat_prompt_action");
    }

    openKnowledge() {
        this.action.doAction("dac_chat_playbook.chat_knowledge_action");
    }

    openPrompt(id) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "dac.chat.prompt", res_id: id, views: [[false, "form"]] });
    }

    async openInsights(field, value) {
        const ai = this.state.data?.ai;
        if (!ai) {
            return;
        }
        const action = await this.orm.call("dac.chat.analysis", "dashboard_open_insights", [ai.id, field || null, value === undefined ? null : value]);
        await this.action.doAction(action);
    }

    stepAction(step) {
        const map = { settings: () => this.openSettings(), run: () => this.openRun(), rules: () => this.openRules(), prompts: () => this.openPrompts() };
        map[step.cta]?.();
    }

    // ── Thao tác ────────────────────────────────────────────────────────────
    async ruleAction(rule, method) {
        this.state.busy[rule.id] = true;
        try {
            await this.orm.call("dac.chat.rule", method, [[rule.id]]);
            this.notification.add(method === "action_approve" ? `Đã duyệt: ${rule.name}` : `Đã bỏ: ${rule.name}`, {
                type: method === "action_approve" ? "success" : "info",
            });
            await this.load(true);
        } finally {
            delete this.state.busy[rule.id];
        }
    }

    async testPrompt(id) {
        const action = await this.orm.call("dac.chat.prompt", "action_test", [[id]]);
        await this.action.doAction(action);
    }

    activatePrompt(prompt) {
        this.dialog.add(ConfirmationDialog, {
            title: `Kích hoạt bộ "${prompt.label}"`,
            body: `Bản "${prompt.draft.name}" sẽ thành bản đang dùng của bộ này; bản cũ (nếu có) chuyển sang "Cũ" và các quy tắc đề xuất trong bản này được duyệt. Nên bấm "Thử" trước khi kích hoạt.`,
            confirmLabel: "Kích hoạt",
            confirm: async () => {
                await this.orm.call("dac.chat.prompt", "action_activate", [[prompt.draft.id]]);
                this.notification.add(`Đã kích hoạt bộ "${prompt.label}"`, { type: "success" });
                await this.load(true);
            },
            cancel: () => {},
        });
    }
}

registry.category("actions").add("dac_chat_dashboard", ChatDashboard);
