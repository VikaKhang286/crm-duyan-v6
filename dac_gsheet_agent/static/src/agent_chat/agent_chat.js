/** @odoo-module **/
import { Component, useState, useRef, onWillStart, onPatched } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { useDebounced } from "@web/core/utils/timing";

const MODEL = "dac.gsheet.agent.session";

const QUICK_PROMPTS = [
    "Đối chiếu file sheet với Odoo và liệt kê chênh lệch",
    "Tiến độ các đơn đang chọn trên sheet thế nào?",
    "Đồng bộ trạng thái từ sheet sang Odoo cho các đơn lệch",
    "Đơn nào có trên sheet mà chưa có trên Odoo?",
];

export class DacGsheetAgentChat extends Component {
    static template = "dac_gsheet_agent.AgentChat";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.messagesRef = useRef("messages");
        this.quickPrompts = QUICK_PROMPTS;
        this.state = useState({
            loading: true,
            busy: false,
            sessions: [],
            sources: [],
            current: null,
            input: "",
            orderQuery: "",
            orderResults: [],
            pickerOpen: false,
            rejectingId: null,
            rejectReason: "",
            sidebarOpen: true,
        });
        this.debouncedSearch = useDebounced(() => this.searchOrders(), 250);
        this._scrollPending = true;

        onWillStart(async () => {
            const ctx = this.props.action?.context || {};
            const data = await this.orm.call(MODEL, "ui_bootstrap", [], {
                order_ids: ctx.default_order_ids || [],
                source_ids: ctx.default_source_ids || [],
            });
            this.state.sessions = data.sessions;
            this.state.sources = data.available_sources;
            this.state.current = data.current;
            this.state.loading = false;
        });
        onPatched(() => {
            if (this._scrollPending && this.messagesRef.el) {
                this.messagesRef.el.scrollTop = this.messagesRef.el.scrollHeight;
                this._scrollPending = false;
            }
        });
    }

    // ---------------------------------------------------------------- helpers
    get current() {
        return this.state.current;
    }
    get pendingCount() {
        return (this.current?.proposals || []).filter((p) => p.state === "pending").length;
    }
    proposalsFor(messageId) {
        return (this.current?.proposals || []).filter((p) => p.message_id === messageId);
    }
    get orphanProposals() {
        const ids = new Set((this.current?.messages || []).map((m) => m.id));
        return (this.current?.proposals || []).filter((p) => !ids.has(p.message_id));
    }
    isSourceSelected(id) {
        return (this.current?.sources || []).some((s) => s.id === id);
    }
    _setCurrent(payload) {
        this.state.current = payload;
        this._scrollPending = true;
    }
    async _call(method, args = [], kwargs = {}) {
        try {
            return await this.orm.call(MODEL, method, args, kwargs);
        } catch (e) {
            const msg = e?.data?.message || e?.message || "Có lỗi xảy ra";
            this.notification.add(msg, { type: "danger" });
            throw e;
        }
    }

    // ---------------------------------------------------------------- sessions
    async selectSession(id) {
        const data = await this._call("ui_bootstrap", [], { session_id: id });
        this.state.sessions = data.sessions;
        this._setCurrent(data.current);
    }
    async newSession() {
        const data = await this._call("ui_new_session", [], {
            order_ids: [],
            source_ids: (this.current?.sources || []).map((s) => s.id),
        });
        this.state.sessions = data.sessions;
        this._setCurrent(data.current);
    }
    async deleteSession(id, ev) {
        ev.stopPropagation();
        if (!window.confirm("Xoá phiên chat này?")) {
            return;
        }
        this.state.sessions = await this.orm.call(MODEL, "ui_delete", [[id]]);
        if (this.current?.id === id) {
            if (this.state.sessions.length) {
                await this.selectSession(this.state.sessions[0].id);
            } else {
                await this.newSession();
            }
        }
    }

    // ---------------------------------------------------------------- context
    async _saveContext(orderIds, sourceIds) {
        const payload = await this._call("ui_set_context", [[this.current.id], orderIds, sourceIds]);
        this._setCurrent(payload);
    }
    async searchOrders() {
        this.state.orderResults = await this._call("ui_search_orders", [], {
            query: this.state.orderQuery,
        });
    }
    onOrderQuery(ev) {
        this.state.orderQuery = ev.target.value;
        this.state.pickerOpen = true;
        this.debouncedSearch();
    }
    async openPicker() {
        this.state.pickerOpen = true;
        await this.searchOrders();
    }
    closePicker() {
        setTimeout(() => (this.state.pickerOpen = false), 200);
    }
    async addOrder(order) {
        const ids = this.current.orders.map((o) => o.id);
        if (!ids.includes(order.id)) {
            ids.push(order.id);
        }
        this.state.orderQuery = "";
        this.state.pickerOpen = false;
        await this._saveContext(ids, this.current.sources.map((s) => s.id));
    }
    async removeOrder(id) {
        await this._saveContext(
            this.current.orders.filter((o) => o.id !== id).map((o) => o.id),
            this.current.sources.map((s) => s.id)
        );
    }
    async toggleSource(id) {
        let ids = this.current.sources.map((s) => s.id);
        ids = ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id];
        await this._saveContext(this.current.orders.map((o) => o.id), ids);
    }
    openOrder(id) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "sale.order",
            res_id: id,
            views: [[false, "form"]],
            target: "new",
        });
    }
    openSources() {
        this.action.doAction("dac_gsheet_agent.action_gsheet_source");
    }
    openRules() {
        this.action.doAction("dac_gsheet_agent.action_gsheet_rule");
    }

    // ---------------------------------------------------------------- chat
    onKeydown(ev) {
        if (ev.key === "Enter" && !ev.shiftKey) {
            ev.preventDefault();
            this.send();
        }
    }
    useQuickPrompt(text) {
        this.state.input = text;
        this.send();
    }
    async send() {
        const text = this.state.input.trim();
        if (!text || this.state.busy) {
            return;
        }
        this.state.input = "";
        this.state.busy = true;
        this.current.messages.push({ id: `tmp-${Date.now()}`, role: "user", content: text, time: "" });
        this._scrollPending = true;
        try {
            const payload = await this._call("ui_send", [[this.current.id], text]);
            this._setCurrent(payload);
            this.state.sessions = await this.orm.call(MODEL, "ui_list_sessions", []);
        } catch {
            this.current.messages.pop();
            this.state.input = text;
        } finally {
            this.state.busy = false;
        }
    }

    // ---------------------------------------------------------------- proposals
    async proposalAction(id, action) {
        const reason = action === "reject" ? this.state.rejectReason : null;
        const payload = await this._call("ui_proposal_action", [[this.current.id], id, action, reason]);
        this.state.rejectingId = null;
        this.state.rejectReason = "";
        this.state.current = payload;
        const p = payload.proposals.find((x) => x.id === id);
        if (p?.state === "failed") {
            this.notification.add(p.error, { type: "danger", title: "Không áp dụng được" });
        } else if (p?.state === "applied") {
            this.notification.add("Đã áp dụng", { type: "success" });
        }
    }
    startReject(id) {
        this.state.rejectingId = id;
        this.state.rejectReason = "";
    }
    async applyAll() {
        if (!window.confirm(`Duyệt toàn bộ ${this.pendingCount} đề xuất?`)) {
            return;
        }
        this.state.current = await this._call("ui_apply_all", [[this.current.id]]);
    }
    async reflect() {
        this.state.busy = true;
        try {
            this._setCurrent(await this._call("ui_reflect", [[this.current.id]]));
        } finally {
            this.state.busy = false;
        }
    }
}

registry.category("actions").add("dac_gsheet_agent.chat", DacGsheetAgentChat);
