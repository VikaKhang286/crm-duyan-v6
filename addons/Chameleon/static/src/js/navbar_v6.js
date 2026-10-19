/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { NavBar } from "@web/webclient/navbar/navbar";
import { BurgerMenu } from "@web/webclient/burger_menu/burger_menu";
import { useService } from "@web/core/utils/hooks";
import { user } from "@web/core/user";
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
const DAC_ACTIVE_SIDEBAR_MENU_STORAGE_KEY = "dac_v6_active_sidebar_xmlid";

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
        this.dacMenuService = useService("menu");
        this.dacSearchInput = useRef("dacSearchInput");
        this.dacSearchState = useState({
            open: false,
            query: "",
            category: "all",
            results: [],
            loading: false,
            activeIndex: 0,
        });
        this.dacActivityState = useState({ open: false, loading: false, items: [] });
        let activeMenuXmlid = null;
        try {
            activeMenuXmlid = window.localStorage.getItem(DAC_ACTIVE_SIDEBAR_MENU_STORAGE_KEY);
        } catch {
            // The sidebar still works when browser storage is unavailable.
        }
        this.dacSidebarState = useState({ collapsedGroups: {}, activeMenuXmlid, userSelectedMenu: false });
        this.dacRole = null;
        onWillStart(async () => {
            try {
                const [currentUser] = await this.dacSearchOrm.read(
                    "res.users", [user.userId], ["dac_role"]
                );
                this.dacRole = currentUser?.dac_role || null;
            } catch {
                this.dacRole = null;
            }
        });
        this.toggleDacMenuGroup = this.toggleDacMenuGroup.bind(this);
        this.onDacSidebarMenuSelection = this.onDacSidebarMenuSelection.bind(this);
        this._dacSearchTimer = null;
        this._dacSearchRequest = 0;
        this._dacSearchKeydown = (event) => {
            if ((event.ctrlKey || event.metaKey) && event.key.toLocaleLowerCase() === "k") {
                event.preventDefault();
                event.stopImmediatePropagation();
                this.openDacGlobalSearch();
            } else if (event.key === "Escape" && (this.dacSearchState.open || this.dacActivityState.open)) {
                event.preventDefault();
                this.closeDacGlobalSearch();
                this.closeDacActivities();
            }
        };
        onMounted(() => document.addEventListener("keydown", this._dacSearchKeydown, true));
        onMounted(() => {
            this._syncDacSidebarMenu(true);
            this._dacSidebarRouteChange = () => this._syncDacSidebarMenu();
            window.addEventListener("hashchange", this._dacSidebarRouteChange);
            window.addEventListener("popstate", this._dacSidebarRouteChange);
        });
        onWillUnmount(() => {
            document.removeEventListener("keydown", this._dacSearchKeydown, true);
            window.removeEventListener("hashchange", this._dacSidebarRouteChange);
            window.removeEventListener("popstate", this._dacSidebarRouteChange);
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
        if (item.actionKey === "orders") {
            window.location.assign("/odoo/action-555");
            return;
        }
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

    isDacMenuGroupCollapsed(menu) {
        return Boolean(this.dacSidebarState.collapsedGroups[menu.id]);
    },

    toggleDacMenuGroup(menu, event) {
        event.preventDefault();
        this.dacSidebarState.collapsedGroups[menu.id] = !this.isDacMenuGroupCollapsed(menu);
    },

    isDacSidebarMenuActive(menu) {
        return this.getDacSidebarActiveMenuXmlid() === menu.xmlid;
    },

    getDacSidebarActiveMenuXmlid() {
        if (this.dacSidebarState.userSelectedMenu && this.dacSidebarState.activeMenuXmlid) {
            return this.dacSidebarState.activeMenuXmlid;
        }
        const params = new URLSearchParams(window.location.hash.slice(1));
        const action = window.location.pathname.match(/\/action-(\d+)/)?.[1]
            || params.get("action");
        const actionXmlids = {
            "dac_report.dac_sale_dashboard_action": "dac_report.dac_sale_dashboard_menu_root",
            "611": "dac_report.dac_sale_dashboard_menu_root",
            "dac_erp.dac_production_dashboard_action": "dac_report.menu_dashboards_manage_production",
            "552": "dac_report.menu_dashboards_manage_production",
            "dac_erp.dac_design_dashboard_action": "dac_report.menu_dashboards_manage_design",
            "551": "dac_report.menu_dashboards_manage_design",
            "home_menu.home_menu_action": "home_menu.home_dashboard_menu",
            "402": "home_menu.home_dashboard_menu",
        };
        // A recognized route identifies the active landing menu. For all
        // other menu actions, keep the XML ID selected by the sidebar click.
        return actionXmlids[action] || this.dacSidebarState.activeMenuXmlid || null;
    },

    async _syncDacSidebarMenu(initialLoad = false) {
        // Only derive a menu from the route when loading/navigating into a
        // known dashboard. Other actions retain the menu explicitly selected.
        const roleXmlids = {
            manager: "home_menu.home_dashboard_menu",
            sale: "dac_report.dac_sale_dashboard_menu_root",
            sale_all: "dac_report.dac_sale_dashboard_menu_root",
            design: "dac_report.menu_dashboards_manage_design",
            production: "dac_report.menu_dashboards_manage_production",
            design_production: "dac_report.menu_dashboards_manage_production",
            full_stack: "dac_report.dac_sale_dashboard_menu_root",
        };
        const roleXmlid = roleXmlids[this.dacRole];
        const params = new URLSearchParams(window.location.hash.slice(1));
        const routeAction = window.location.pathname.match(/\/action-(\d+)/)?.[1]
            || params.get("action");
        const landingActions = {
            manager: "402",
            sale: "611",
            sale_all: "611",
            design: "551",
            production: "552",
            design_production: "552",
            full_stack: "611",
        };
        const isRoleLanding = routeAction === landingActions[this.dacRole];
        if (initialLoad && isRoleLanding) {
            this.dacSidebarState.userSelectedMenu = false;
        }
        const routeXmlid = isRoleLanding ? roleXmlid : this.getDacSidebarActiveMenuXmlid();
        const xmlid = routeXmlid ||
            (window.location.pathname === "/odoo/" ? roleXmlid : null);
        if (!xmlid) return;
        const allMenus = [];
        const visit = (items) => items.forEach((item) => { allMenus.push(item); visit(item.childrenTree || []); });
        visit(this.dacMenuService.getApps());
        const menu = allMenus.find((item) => item.xmlid === xmlid);
        if (menu) {
            this.dacSidebarState.activeMenuXmlid = menu.xmlid;
            try { window.localStorage.setItem(DAC_ACTIVE_SIDEBAR_MENU_STORAGE_KEY, menu.xmlid); } catch {}
        }
    },

    onDacSidebarMenuSelection(menu) {
        this.dacSidebarState.activeMenuXmlid = menu.xmlid;
        this.dacSidebarState.userSelectedMenu = true;
        try {
            window.localStorage.setItem(DAC_ACTIVE_SIDEBAR_MENU_STORAGE_KEY, menu.xmlid);
        } catch {
            // Keeping the active state in memory is sufficient as a fallback.
        }
        return this.onNavBarDropdownItemSelection(menu);
    },

    dacSidebarMenuIcon(menu) {
        const icons = {
           "home_menu.home_dashboard_menu": "fa-home",
           "dac_erp.menu_dac_my_tasks": "fa-clock-o",
            "dac_report.dac_sale_dashboard_menu_root": "fa-bar-chart",
            "CRM_DAC.menu_pancake_message_dashboard_main": "fa-comments",
            "dac_report.dac_sale_conversation_queue_menu": "fa-commenting",
            "dac_report.dac_sale_orders_menu_root": "fa-list",
            "dac_report.dac_task_calendar_menu_sale": "fa-calendar",
            "dac_report.dac_res_partner_customer_menu_sale": "fa-users",
            "dac_report.dac_manager_dashboard_menu": "fa-tachometer",
            "dac_report.menu_dashboards_manage_design": "fa-pencil-square-o",
            "dac_report.menu_dashboards_manage_production": "fa-industry",
            "dac_erp.menu_dac_task_calendar_design": "fa-calendar-o",
            "dac_erp.menu_dac_task_calendar_production": "fa-calendar-check-o",
            "dac_report.dac_finance_dashboard_menu": "fa-line-chart",
            "dac_report.dac_account_move_sale_menu": "fa-file-text-o",
            "dac_report.dac_account_payment_sale_menu": "fa-credit-card",
            "dac_erp.dac_sale_manage_users_menu_root": "fa-user-circle-o",
            "dac_erp.menu_sale_order_line_import": "fa-upload",
        };
        if (icons[menu.xmlid]) {
            return icons[menu.xmlid];
        }
        const name = (menu.name || "").toLocaleLowerCase();
        if (name.includes("đơn hàng")) return "fa-shopping-cart";
        if (name.includes("khách hàng")) return "fa-users";
        if (name.includes("hội thoại") || name.includes("tin nhắn")) return "fa-comments";
        if (name.includes("lịch")) return "fa-calendar";
        if (name.includes("báo cáo") || name.includes("dashboard")) return "fa-bar-chart";
        if (name.includes("công nợ") || name.includes("thanh toán")) return "fa-credit-card";
        if (name.includes("cấu hình") || name.includes("quản trị")) return "fa-cog";
        return "fa-bookmark-o";
    },

    dacAppIcon(app) {
        const icons = {
            "home_menu.home_root": "fa-home",
            "dac_erp.dac_sale_crm_root": "fa-bar-chart",
            "dac_erp.dac_operations_root": "fa-cube",
            "dac_erp.dac_finance_root": "fa-plus-square-o",
            "dac_attendance.menu_dac_attendance_root": "fa-user-o",
            "dac_erp.dac_system_settings_root": "fa-cog",
        };
        if (icons[app.xmlid]) return icons[app.xmlid];
        const name = (app.name || "").toLocaleLowerCase();
        if (name.includes("home")) return "fa-home";
        if (name.includes("bán hàng") || name.includes("crm")) return "fa-bar-chart";
        if (name.includes("vận hành") || name.includes("sản xuất")) return "fa-cube";
        if (name.includes("tài chính") || name.includes("công nợ")) return "fa-plus-square-o";
        if (name.includes("chấm công")) return "fa-user-o";
        if (name.includes("hệ thống") || name.includes("cấu hình") || name.includes("cài đặt")) return "fa-cog";
        if (name.includes("trợ giúp") || name.includes("help")) return "fa-question";
        if (name.includes("ứng dụng")) return "fa-th-large";
        return "fa-bookmark-o";
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

    async openDacActivities() {
        this.dacActivityState.open = true;
        this.dacActivityState.loading = true;
        try {
            this.dacActivityState.items = await this.dacSearchOrm.call("res.users", "dac_activity_summary", []);
        } catch (error) {
            console.warn("DAC activity summary failed", error);
            this.dacActivityState.items = [];
        } finally {
            this.dacActivityState.loading = false;
        }
    },

    closeDacActivities() {
        this.dacActivityState.open = false;
    },

    closeDacActivitiesFromBackdrop(event) {
        if (event.target === event.currentTarget) this.closeDacActivities();
    },

    openDacActivityItem(item) {
        this.closeDacActivities();
        const actionIds = {
            production: 552,
            delivery: 555,
            design: 551,
        };
        const actionId = actionIds[item.key];
        if (actionId) return this.dacSearchAction.doAction(actionId);
    },
});
