/** @odoo-module **/
// Demo chatbot: nhắn thử với một bản system prompt, xem câu trả lời, prompt gửi đi, token và chi phí tham khảo. Không gửi gì cho khách.
import { Component, onPatched, onWillStart, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const VN_OFFSET = 7 * 3600 * 1000; // Việt Nam không đổi giờ mùa hè

function utcToLocalInput(utc) {
    if (!utc) {
        return "";
    }
    const date = new Date(Date.parse(utc.replace(" ", "T") + "Z") + VN_OFFSET);
    return date.toISOString().slice(0, 16);
}

function localInputToUtc(value) {
    if (!value) {
        return false;
    }
    const date = new Date(Date.parse(value + ":00Z") - VN_OFFSET);
    return date.toISOString().slice(0, 19).replace("T", " ");
}

export class ChatDemo extends Component {
    static template = "dac_chat_playbook.ChatDemo";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.chatRef = useRef("chat");
        this.inputRef = useRef("input");
        this.state = useState({
            options: null, promptId: false, at: false, preset: "now", customerName: "Chị Lan", input: "",
            messages: [], last: null, sending: false, tab: "turn",
            totals: { turns: 0, in: 0, out: 0, seconds: 0 },
        });
        onWillStart(async () => {
            const options = await this.orm.call("dac.chat.prompt", "demo_options", []);
            this.state.options = options;
            const wanted = this.props.action?.params?.prompt_id || this.props.action?.context?.prompt_id;
            this.state.promptId = options.prompts.some((p) => p.id === wanted) ? wanted : options.default_prompt_id;
            this.state.at = options.presets[0]?.at || false;
        });
        onPatched(() => {
            const el = this.chatRef.el;
            if (el && this.scrollPending) {
                el.scrollTop = el.scrollHeight;
                this.scrollPending = false;
            }
        });
    }

    // ── Hiển thị ────────────────────────────────────────────────────────────
    get prompt() {
        return this.state.options?.prompts.find((p) => p.id === this.state.promptId);
    }

    get atLocal() {
        return utcToLocalInput(this.state.at);
    }

    num(value) {
        return Number(value || 0).toLocaleString("vi-VN");
    }

    cost(tokensIn, tokensOut) {
        const p = this.state.options?.pricing || { in: 0, out: 0, rate: 0 };
        const usd = (tokensIn * p.in + tokensOut * p.out) / 1e6;
        return { usd: usd.toLocaleString("vi-VN", { maximumFractionDigits: 4 }), vnd: Math.round(usd * p.rate).toLocaleString("vi-VN") };
    }

    get collectedRows() {
        const collected = this.state.last?.collected || {};
        return Object.entries(collected).filter(([, v]) => v);
    }

    // ── Điều khiển ──────────────────────────────────────────────────────────
    setTab(tab) {
        this.state.tab = tab;
    }

    onPrompt(ev) {
        this.state.promptId = parseInt(ev.target.value);
    }

    pickPreset(preset) {
        this.state.preset = preset.key;
        this.state.at = preset.at;
    }

    onTime(ev) {
        this.state.preset = "custom";
        this.state.at = localInputToUtc(ev.target.value);
    }

    reset() {
        this.state.messages = [];
        this.state.last = null;
        this.state.totals = { turns: 0, in: 0, out: 0, seconds: 0 };
    }

    async sample() {
        const sample = await this.orm.call("dac.chat.prompt", "demo_sample", []);
        if (!sample?.text) {
            this.notification.add("Không tìm được tin khách thật gần đây.", { type: "warning" });
            return;
        }
        this.reset();
        this.state.customerName = sample.customer_name || "Khách";
        this.state.at = sample.at;
        this.state.preset = "custom";
        this.state.input = sample.text;
    }

    onKeydown(ev) {
        if (ev.key === "Enter" && !ev.shiftKey) {
            ev.preventDefault();
            this.send();
        }
    }

    async send() {
        const text = this.state.input.trim();
        if (!text || this.state.sending || !this.state.promptId) {
            return;
        }
        this.state.messages.push({ role: "customer", text });
        this.state.input = "";
        if (this.inputRef.el) {
            this.inputRef.el.value = ""; // giá trị ảo không đổi ("" → "") nên OWL không tự xoá ô nhập
        }
        this.state.sending = true;
        this.scrollPending = true;
        const history = this.state.messages
            .filter((m) => (m.role === "customer" || m.role === "bot") && !m.blocked)
            .map((m) => [m.role, m.text]);
        try {
            const result = await this.orm.call("dac.chat.prompt", "demo_reply", [this.state.promptId, history, this.state.at, this.state.customerName]);
            const usage = result.usage || {};
            const meta = { in: usage.in || 0, out: usage.out || 0, seconds: result.seconds, model: result.model };
            result.messages.forEach((m, i) => {
                this.state.messages.push({ role: "bot", text: m, meta: i === result.messages.length - 1 ? meta : null });
            });
            (result.blocked || []).forEach((m) => this.state.messages.push({ role: "bot", text: m, blocked: true }));
            if (result.handoff) {
                this.state.messages.push({ role: "note", text: `Chuyển nhân viên: ${result.handoff_reason || "bot báo cần người xử lý"}` });
            }
            this.state.last = result;
            const t = this.state.totals;
            t.turns += 1;
            t.in += meta.in;
            t.out += meta.out;
            t.seconds += result.seconds || 0;
        } catch (error) {
            this.state.messages.push({ role: "note", text: "Lỗi khi gọi AI. Thử lại hoặc đổi model.", error: true });
            throw error;
        } finally {
            this.state.sending = false;
            this.scrollPending = true;
        }
    }

    async copy(text) {
        try {
            await navigator.clipboard.writeText(text || "");
            this.notification.add("Đã chép vào bộ nhớ tạm.", { type: "success" });
        } catch {
            this.notification.add("Trình duyệt không cho chép tự động.", { type: "warning" });
        }
    }
}

registry.category("actions").add("dac_chat_demo", ChatDemo);
