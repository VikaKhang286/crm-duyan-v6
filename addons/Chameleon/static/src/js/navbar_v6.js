/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { NavBar } from "@web/webclient/navbar/navbar";
import { BurgerMenu } from "@web/webclient/burger_menu/burger_menu";
import { useService } from "@web/core/utils/hooks";
import { onMounted, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";

const DAC_ROLE_LABELS = {
    manager: "Manager",
    sale_all: "Sale",
    sale: "Sale",
    design: "Thiết kế",
    production: "Sản xuất",
    design_production: "Thiết kế + Sản xuất",
    full_stack: "Full-stack",
};

patch(BurgerMenu.prototype, {
    setup() {
        super.setup(...arguments);
        const orm = useService("orm");
        this.dacIdentity = useState({ roleLabel: "" });
        onWillStart(async () => {
            try {
                const [currentUser] = await orm.read(
                    "res.users",
                    [this.user.userId],
                    ["dac_role"]
                );
                this.dacIdentity.roleLabel = DAC_ROLE_LABELS[currentUser?.dac_role] || "";
            } catch {
                this.dacIdentity.roleLabel = "";
            }
        });
    },
});

patch(NavBar.prototype, {
    setup() {
        super.setup(...arguments);
        this.dacSearchOrm = useService("orm");
        this.dacSearchAction = useService("action");
        this.dacSearchInput = useRef("dacSearchInput");
        this.dacSearchState = useState({
            open: false,
            query: "",
            category: "all",
            results: [],
            loading: false,
            activeIndex: 0,
        });
        this._dacSearchTimer = null;
        this._dacSearchRequest = 0;
        this._dacSearchKeydown = (event) => {
            if ((event.ctrlKey || event.metaKey) && event.key.toLocaleLowerCase() === "k") {
                event.preventDefault();
                event.stopImmediatePropagation();
                this.openDacGlobalSearch();
            } else if (event.key === "Escape" && this.dacSearchState.open) {
                event.preventDefault();
                this.closeDacGlobalSearch();
            }
        };
        onMounted(() => document.addEventListener("keydown", this._dacSearchKeydown, true));
        onWillUnmount(() => {
            document.removeEventListener("keydown", this._dacSearchKeydown, true);
            clearTimeout(this._dacSearchTimer);
        });
    },

    get dacSearchCategories() {
        return [
            ["all", "Tất cả"],
            ["order", "Đơn hàng"],
            ["customer", "Khách hàng"],
            ["conversation", "Hội thoại"],
            ["task", "Công việc"],
            ["action", "Hành động"],
        ];
    },

    get dacSearchActions() {
        const actions = [
            { key: "action-new-order", kind: "action", icon: "+", title: "Tạo đơn mới", subtitle: "Tạo nhanh đơn bán hàng / xe đẩy", typeLabel: "Hành động", actionKey: "new_order" },
            { key: "action-orders", kind: "action", icon: "ĐH", title: "Mở danh sách đơn hàng", subtitle: "Xem và quản lý tất cả đơn hàng", typeLabel: "Hành động", actionKey: "orders" },
            { key: "action-customers", kind: "action", icon: "KH", title: "Mở danh sách khách hàng", subtitle: "Tra cứu hồ sơ khách hàng", typeLabel: "Hành động", actionKey: "customers" },
            { key: "action-conversations", kind: "action", icon: "HT", title: "Mở danh sách hội thoại", subtitle: "Theo dõi hội thoại khách hàng", typeLabel: "Hành động", actionKey: "conversations" },
            { key: "action-tasks", kind: "action", icon: "CV", title: "Mở danh sách công việc", subtitle: "Theo dõi công việc đang xử lý", typeLabel: "Hành động", actionKey: "tasks" },
        ];
        const query = this.dacSearchState.query.trim().toLocaleLowerCase();
        if (!query) return actions;
        return actions.filter((item) => `${item.title} ${item.subtitle}`.toLocaleLowerCase().includes(query));
    },

    get dacSearchItems() {
        const category = this.dacSearchState.category;
        if (category === "action") return this.dacSearchActions;
        if (category === "all") return [...this.dacSearchState.results, ...this.dacSearchActions].slice(0, 24);
        return this.dacSearchState.results;
    },

    openDacGlobalSearch() {
        this.dacSearchState.open = true;
        this.dacSearchState.activeIndex = 0;
        requestAnimationFrame(() => this.dacSearchInput.el?.focus());
        this.loadDacGlobalSearch();
    },

    closeDacGlobalSearch() {
        this.dacSearchState.open = false;
    },

    closeDacSearchFromBackdrop(event) {
        if (event.target === event.currentTarget) this.closeDacGlobalSearch();
    },

    onDacSearchInput(event) {
        this.dacSearchState.query = event.target.value;
        this.dacSearchState.activeIndex = 0;
        clearTimeout(this._dacSearchTimer);
        this._dacSearchTimer = setTimeout(() => this.loadDacGlobalSearch(), 180);
    },

    selectDacSearchCategory(category) {
        this.dacSearchState.category = category;
        this.dacSearchState.activeIndex = 0;
        this.loadDacGlobalSearch();
        requestAnimationFrame(() => this.dacSearchInput.el?.focus());
    },

    async loadDacGlobalSearch() {
        if (!this.dacSearchState.open || this.dacSearchState.category === "action") {
            this.dacSearchState.results = [];
            return;
        }
        const request = ++this._dacSearchRequest;
        this.dacSearchState.loading = true;
        try {
            const results = await this.dacSearchOrm.call("res.users", "dac_global_search", [
                this.dacSearchState.query,
                this.dacSearchState.category,
                6,
            ]);
            if (request === this._dacSearchRequest) this.dacSearchState.results = results;
        } catch (error) {
            console.warn("DAC global search failed", error);
            if (request === this._dacSearchRequest) this.dacSearchState.results = [];
        } finally {
            if (request === this._dacSearchRequest) this.dacSearchState.loading = false;
        }
    },

    onDacSearchKeydown(event) {
        const items = this.dacSearchItems;
        if (event.key === "ArrowDown") {
            event.preventDefault();
            this.dacSearchState.activeIndex = items.length ? (this.dacSearchState.activeIndex + 1) % items.length : 0;
        } else if (event.key === "ArrowUp") {
            event.preventDefault();
            this.dacSearchState.activeIndex = items.length ? (this.dacSearchState.activeIndex - 1 + items.length) % items.length : 0;
        } else if (event.key === "Enter" && items.length) {
            event.preventDefault();
            this.openDacSearchItem(items[this.dacSearchState.activeIndex] || items[0]);
        }
    },

    async openDacSearchItem(item) {
        this.closeDacGlobalSearch();
        if (item.resId) {
            return this.dacSearchAction.doAction({
                type: "ir.actions.act_window",
                res_model: item.model,
                res_id: item.resId,
                views: [[false, "form"]],
                target: "current",
            });
        }
        const actionMap = {
            new_order: { res_model: "sale.order", views: [[false, "form"]] },
            orders: { res_model: "sale.order", views: [[false, "list"], [false, "form"]] },
            customers: { res_model: "res.partner", views: [[false, "kanban"], [false, "list"], [false, "form"]] },
            conversations: { res_model: "page.fm.conversation", views: [[false, "list"], [false, "form"]] },
            tasks: { res_model: "dac.work.task", views: [[false, "kanban"], [false, "list"], [false, "form"]] },
        };
        const config = actionMap[item.actionKey];
        if (config) return this.dacSearchAction.doAction({ type: "ir.actions.act_window", target: "current", ...config });
    },

    toggleDacSidebar() {
        document.body.classList.toggle("dac-v6-sidebar-collapsed");
    },

    refreshDacPage() {
        window.location.reload();
    },

    filterDacMenu(event) {
        const query = event.target.value.trim().toLocaleLowerCase();
        const shell = event.target.closest(".dac_v6_shell");
        for (const item of shell?.querySelectorAll(".dac_v6_section_item") || []) {
            item.hidden = Boolean(query) && !item.textContent.toLocaleLowerCase().includes(query);
        }
        for (const group of shell?.querySelectorAll(".dac_v6_section_group") || []) {
            const children = [...group.querySelectorAll(".dac_v6_section_item")];
            group.hidden = Boolean(query) && children.length > 0 && children.every((item) => item.hidden);
        }
    },

    openDacMessages(event) {
        const shell = event.currentTarget.closest(".dac_v6_shell");
        const messageIcon = shell?.querySelector(".o_menu_systray .fa-comments");
        messageIcon?.closest("button, .dropdown-toggle")?.click();
    },

    openDacActivities(event) {
        const shell = event.currentTarget.closest(".dac_v6_shell");
        const activityIcon = shell?.querySelector(".o_menu_systray .fa-clock-o");
        activityIcon?.closest("button, .dropdown-toggle")?.click();
    },
});
