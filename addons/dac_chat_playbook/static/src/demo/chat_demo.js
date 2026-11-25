/** @odoo-module **/
// Huấn luyện & kiểm thử: nhắn thử với một bản system prompt, góp ý từng câu bot trả lời, cho Agent tối ưu prompt từ góp ý.
// Xem câu trả lời, prompt gửi đi, token và chi phí tham khảo. Không gửi gì cho khách.
import { Component, onPatched, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";
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
            options: null, promptId: false, at: false, preset: "now", customerName: "Chị Lan", channel: "zalo", gender: "unknown", input: "",
            messages: [], last: null, sending: false, tab: "turn",
            feedback: { items: [], pending: 0 }, fbFor: null, fbText: "", optimizing: null, improved: null, replaying: false,
            samples: null, sampleFilter: "", sampleId: null,
            totals: { turns: 0, in: 0, out: 0, seconds: 0 },
        });
        onWillStart(async () => {
            const options = await this.orm.call("dac.chat.prompt", "demo_options", []);
            this.state.options = options;
            const wanted = this.props.action?.params?.prompt_id || this.props.action?.context?.prompt_id;
            this.state.promptId = options.prompts.some((p) => p.id === wanted) ? wanted : options.default_prompt_id;
            this.state.at = options.presets[0]?.at || false;
            await this.loadFeedback();
        });
        onWillUnmount(() => clearTimeout(this.pollTimer));
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
        this.loadFeedback();
    }

    // ── Góp ý → Agent tối ưu prompt ─────────────────────────────────────────
    async loadFeedback() {
        if (!this.state.promptId) {
            return;
        }
        this.state.feedback = await this.orm.call("dac.chat.prompt", "demo_feedback", [this.state.promptId]);
    }

    openFeedback(index) {
        this.state.fbFor = index;
        this.state.fbText = "";
    }

    cancelFeedback() {
        this.state.fbFor = null;
        this.state.fbText = "";
    }

    /** Lượt chứa tin `index`: các tin khách liền trước + các tin bot của lượt (cũ → mới). */
    turnOf(index) {
        const msgs = this.state.messages;
        let i = index;
        const bot = [];
        while (i >= 0 && msgs[i].role !== "customer") {
            if (msgs[i].role === "bot" && !msgs[i].blocked) {
                bot.unshift(msgs[i].text);
            }
            i--;
        }
        const customer = [];
        while (i >= 0 && msgs[i].role === "customer") {
            customer.unshift(msgs[i].text);
            i--;
        }
        return { customer, bot };
    }

    async saveFeedback(index) {
        const text = this.state.fbText.trim();
        const m = this.state.messages[index];
        if (!text || !m) {
            return;
        }
        const turn = this.turnOf(index);
        this.state.feedback = await this.orm.call("dac.chat.prompt", "demo_feedback_add",
            [this.state.promptId, text, turn.customer.join("\n"), turn.bot.join("\n")]);
        m.feedback = text;
        this.cancelFeedback();
    }

    onFeedbackKeydown(ev, index) {
        if (ev.key === "Enter" && !ev.shiftKey) {
            ev.preventDefault();
            this.saveFeedback(index);
        } else if (ev.key === "Escape") {
            this.cancelFeedback();
        }
    }

    async toggleFeedback(item) {
        this.state.feedback = await this.orm.call("dac.chat.prompt", "demo_feedback_toggle", [item.id, this.state.promptId]);
    }

    async deleteFeedback(item) {
        this.state.feedback = await this.orm.call("dac.chat.prompt", "demo_feedback_delete", [item.id, this.state.promptId]);
    }

    async optimize() {
        if (this.state.optimizing) {
            return;
        }
        const result = await this.orm.call("dac.chat.prompt", "demo_optimize", [this.state.promptId]);
        this.state.improved = null;
        this.state.optimizing = { id: result.id, from: this.state.promptId, started: Date.now(), seconds: 0 };
        this.poll();
    }

    async poll() {
        const job = this.state.optimizing;
        if (!job) {
            return;
        }
        job.seconds = Math.round((Date.now() - job.started) / 1000);
        const status = await this.orm.call("dac.chat.prompt", "demo_prompt_state", [job.id]);
        if (status.state === "draft") {
            this.state.options = await this.orm.call("dac.chat.prompt", "demo_options", []);
            this.state.optimizing = null;
            this.state.improved = { id: job.id, name: status.name, note: status.note, model: status.model, seconds: job.seconds };
            await this.loadFeedback();
            return;
        }
        if (status.state !== "queued") {
            this.state.optimizing = null;
            this.notification.add(`Cải tiến lỗi: ${status.error || "không rõ"}`, { type: "danger", sticky: true });
            return;
        }
        this.pollTimer = setTimeout(() => this.poll(), 3000);
    }

    /** Chuyển sang bản cải tiến và gửi lại đúng các tin khách của phiên vừa thử để so sánh. */
    // ── Mẫu khách thật ──────────────────────────────────────────────────────
    async loadSamples() {
        if (!this.state.samples) {
            this.state.samples = await this.orm.call("dac.chat.prompt", "demo_samples", []);
        }
    }

    async openSamples() {
        this.state.tab = "samples";
        await this.loadSamples();
    }

    get filteredSamples() {
        const q = (this.state.sampleFilter || "").toLowerCase().trim();
        const list = this.state.samples || [];
        return q ? list.filter((s) => (s.name + " " + s.messages.join(" ")).toLowerCase().includes(q)) : list;
    }

    async useSample(sample) {
        if (this.state.sending) {
            return;
        }
        this.reset();
        this.state.sampleId = sample.id;
        this.state.customerName = sample.name;
        this.state.at = sample.at;
        this.state.preset = "custom";
        this.state.channel = sample.channel || "zalo";
        this.state.gender = sample.gender || "unknown";
        await this.sendTexts(sample.messages);
    }

    async tryImproved() {
        const improved = this.state.improved;
        if (!improved) {
            return;
        }
        const customers = this.state.messages.filter((m) => m.role === "customer").map((m) => m.text).slice(0, 8);
        this.state.promptId = improved.id;
        this.state.improved = null;
        this.reset();
        await this.loadFeedback();
        this.state.replaying = true;
        try {
            for (const text of customers) {
                await this.sendTexts([text]);
            }
        } finally {
            this.state.replaying = false;
        }
        this.notification.add(customers.length ? "Đã gửi lại các tin khách với bản cải tiến. Góp ý tiếp nếu chưa ổn." : "Đã chuyển sang bản cải tiến.",
            { type: "success" });
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
        if (!text) {
            return;
        }
        await this.sendTexts([text]);
    }

    /** Gửi một lượt khách (một hoặc nhiều tin liền nhau) rồi lấy câu bot trả lời. */
    async sendTexts(texts) {
        if (!texts.length || this.state.sending || !this.state.promptId) {
            return;
        }
        texts.forEach((text) => this.state.messages.push({ role: "customer", text }));
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
            const result = await this.orm.call("dac.chat.prompt", "demo_reply", [this.state.promptId, history, this.state.at, this.state.customerName, this.state.channel, this.state.gender]);
            const usage = result.usage || {};
            const meta = { in: usage.in || 0, out: usage.out || 0, seconds: result.seconds, model: result.model };
            result.messages.forEach((m, i) => {
                this.state.messages.push({ role: "bot", text: m, meta: i === result.messages.length - 1 ? meta : null });
            });
            (result.blocked || []).forEach((m) => this.state.messages.push({ role: "bot", text: m, blocked: true }));
            const lastBot = [...this.state.messages].reverse().find((m) => m.role === "bot");
            if (lastBot) {
                lastBot.turnEnd = true; // góp ý gắn với cả lượt trả lời, icon hiện ở tin cuối của lượt
            }
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
