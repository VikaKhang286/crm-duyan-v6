/** @odoo-module **/
// Theo dõi tiến độ đợt phân tích chat: thanh %, 4 bước, nhật ký; tự tải lại bản ghi mỗi 5 giây khi đang chạy.
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { Component, onMounted, onWillUnmount } from "@odoo/owl";

const RUNNING = ["queued", "ai"];
const COORD_ROUNDS = 8;

export class ChatProgressField extends Component {
    static template = "dac_chat_playbook.ChatProgressField";
    static props = { ...standardFieldProps };

    setup() {
        this.timer = null;
        onMounted(() => {
            this.timer = setInterval(() => this.refresh(), 5000);
        });
        onWillUnmount(() => clearInterval(this.timer));
    }

    get data() {
        return this.props.record.data;
    }

    get pct() {
        return this.data[this.props.name] || 0;
    }

    get running() {
        return RUNNING.includes(this.data.state);
    }

    get barClass() {
        if (this.data.state === "failed") {
            return "progress-bar bg-danger";
        }
        if (this.data.state === "done") {
            return "progress-bar bg-success";
        }
        return "progress-bar progress-bar-striped progress-bar-animated";
    }

    get logLines() {
        return (this.data.progress_log || "").split("\n").filter(Boolean).reverse().slice(0, 20);
    }

    get steps() {
        const d = this.data;
        const st = d.state;
        const total = d.ai_batches_total || 0;
        const done = d.ai_batches_done || 0;
        const hasAi = total > 0;
        const make = (key, label, isDone, isActive) => ({
            key,
            label,
            cls: isActive ? "fw-bold text-primary" : isDone ? "text-success" : "text-muted",
            icon: isDone ? "fa fa-check-circle" : isActive ? "fa fa-circle-o-notch fa-spin" : "fa fa-circle-o",
        });
        return [
            make("stats", "1. Thống kê toàn bộ tin", st === "ai" || st === "done", st === "queued"),
            make("read", `2. AI đọc & gán nhãn (${done}/${total} lô)`, hasAi && done >= total && (st === "ai" || st === "done"),
                st === "ai" && done < total),
            make("coord", `3. Agent điều phối (vòng ${d.coord_rounds || 0}/${COORD_ROUNDS}) + rà soát quy tắc`, hasAi && (d.ai_reduced || st === "done"),
                st === "ai" && hasAi && done >= total && !d.ai_reduced),
            make("prompt", "4. Viết 3 bộ system prompt", hasAi && st === "done", st === "ai" && d.ai_reduced),
        ];
    }

    async refresh() {
        const record = this.props.record;
        if (!this.running || record.dirty || record.isNew) {
            return;
        }
        try {
            await record.load();
        } catch {
            // mất mạng tạm thời: lần sau thử lại
        }
    }
}

registry.category("fields").add("dac_chat_progress", {
    component: ChatProgressField,
    supportedTypes: ["integer"],
});
