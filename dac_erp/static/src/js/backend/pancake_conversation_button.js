/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

function platformDisplay(rawValue) {
    const raw = (rawValue || "").trim();
    const value = raw.toLowerCase();
    if (value.includes("zalo")) {
        return { name: value.includes("oa") ? "Zalo OA" : "Zalo", icon: "fa-comment" };
    }
    if (value.includes("instagram")) return { name: "Instagram", icon: "fa-instagram" };
    if (value.includes("whatsapp")) return { name: "WhatsApp", icon: "fa-whatsapp" };
    if (value.includes("facebook") || value.includes("messenger")) {
        return { name: "Fanpage Facebook", icon: "fa-facebook" };
    }
    if (value.includes("tiktok")) return { name: "TikTok", icon: "fa-music" };
    return { name: raw || "Pancake", icon: "fa-comments" };
}

function toPlainText(html) {
    const parser = new DOMParser();
    const normalized = (html || "")
        .replace(/<br\s*\/?>/gi, "\n")
        .replace(/<\/(p|div|li|h[1-6])\s*>/gi, "\n");
    return parser.parseFromString(normalized, "text/html").body.textContent.trim();
}

class PancakeConversationDialog extends Component {
    static template = "dac_erp.PancakeConversationDialog";
    static components = { Dialog };
    static props = { close: Function, conversationId: Number };

    setup() {
        this.orm = useService("orm");
        this.state = useState({ loading: true, error: "", name: "", phone: "", platform: "", messages: [] });
        onWillStart(() => this.loadConversation());
    }

    async loadConversation() {
        try {
            const [conversation] = await this.orm.read(
                "page.fm.conversation",
                [this.props.conversationId],
                ["customer_name_fm", "phone", "platform_fm", "partner_id"]
            );
            if (!conversation) throw new Error("Không tìm thấy hội thoại Pancake.");
            let partner = {};
            if (conversation.partner_id?.[0]) {
                [partner] = await this.orm.read("res.partner", [conversation.partner_id[0]], ["name", "phone"]);
            }
            const foundMessages = await this.orm.searchRead(
                "page.fm.message",
                [["conversation_id", "=", this.props.conversationId]],
                ["content_html", "type_content", "sender_name_fm", "staff_name_fm", "staff_id_fm", "staff", "inserted_at_fm"],
                { order: "inserted_at_fm desc, id desc", limit: 100 }
            );
            this.state.name = conversation.customer_name_fm || partner.name || "Khách hàng";
            this.state.phone = conversation.phone || partner.phone || "";
            this.state.platform = platformDisplay(conversation.platform_fm).name;
            this.state.messages = foundMessages.reverse().map((message) => {
                const staffName = message.staff_name_fm || message.staff?.[1] || "";
                const isStaff = Boolean(staffName || message.staff_id_fm || message.staff?.[0]);
                const body = toPlainText(message.content_html) || message.type_content || "Tin nhắn";
                return {
                    sender: isStaff ? (staffName || "Sale") : (message.sender_name_fm || "Khách hàng"),
                    body,
                    isStaff,
                };
            });
        } catch (error) {
            this.state.error = error.message || "Không tải được hội thoại Pancake.";
        } finally {
            this.state.loading = false;
        }
    }
}

class PancakeConversationButton extends Component {
    static template = "dac_erp.PancakeConversationButton";
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this.dialog = useService("dialog");
        this.state = useState({ platform: { name: "Pancake", icon: "fa-comments" } });
        onWillStart(async () => {
            const conversationId = this.props.record.data[this.props.name];
            if (conversationId) {
                try {
                    const [conversation] = await this.orm.read("page.fm.conversation", [conversationId], ["platform_fm"]);
                    this.state.platform = platformDisplay(conversation?.platform_fm);
                } catch {
                    // Button remains available; dialog will show the access/load error.
                }
            }
        });
    }

    openConversation() {
        const conversationId = this.props.record.data[this.props.name];
        if (conversationId) {
            this.dialog.add(PancakeConversationDialog, { conversationId });
        }
    }
}

registry.category("fields").add("dac_pancake_conversation_button", {
    component: PancakeConversationButton,
    supportedTypes: ["integer"],
});
