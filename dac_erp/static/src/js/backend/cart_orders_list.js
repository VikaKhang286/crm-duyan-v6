/** @odoo-module **/

import { registry } from "@web/core/registry";
import { ListController } from "@web/views/list/list_controller";
import { listView } from "@web/views/list/list_view";
import { onMounted, onPatched, onWillUnmount } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { user } from "@web/core/user";

const HERO_CLASS = "dac-cart-orders-hero";
const TOOLBAR_CLASS = "dac-cart-orders-toolbar";
const KANBAN_CLASS = "dac-cart-orders-kanban";
const BULK_ACTIONS_CLASS = "dac-cart-bulk-actions";
const KANBAN_STAGES = [
    ["quotation", "Báo giá"],
    ["deposit", "Thiết kế/Cọc"],
    ["production", "Sản xuất"],
    ["delivery", "Giao hàng"],
    ["completed", "Hoàn thành"],
];

function formatCurrency(value) {
    const amount = Number(value || 0);
    if (!amount) {
        return "0 ₫";
    }
    return `${new Intl.NumberFormat("vi-VN", {
        maximumFractionDigits: 0,
    }).format(amount)} ₫`;
}

function formatDate(value) {
    if (!value) return "—";
    if (value.toFormat) return value.toFormat("dd/MM/yyyy");
    const match = String(value).match(/^(\d{4})-(\d{2})-(\d{2})/);
    return match ? `${match[3]}/${match[2]}/${match[1]}` : String(value);
}

/**
 * A light presentation layer around the native Odoo list.  The standard
 * control panel and table remain in charge of searching, sorting, filtering
 * and opening records, while this controller supplies the cart-order
 * workspace header shown in the v6 prototype.
 */
export class CartOrdersListController extends ListController {
    setup() {
        super.setup();
        this.orm = useService("orm");
        this.action = useService("action");
        this.quickSale = useService("dac_quick_sale");
        this.cartStats = null;
        this.cartToolbarFilterGroupIds = { search: null, stage: null, sale: null };
        this.cartSearchTimer = null;
        this.cartViewMode = "list";
        this.cartKanbanRequest = 0;
        this.cartSelectionChangeHandler = () => {
            window.requestAnimationFrame(() => this.refreshCartBulkActions(this.rootRef?.el));
            window.setTimeout(() => this.refreshCartBulkActions(this.rootRef?.el), 0);
        };
        onMounted(async () => {
            this.mountCartOrdersHero();
            this.bindQuickSaleRows();
            await this.loadCartStats();
        });
        onPatched(() => {
            this.mountCartOrdersHero();
            this.bindQuickSaleRows();
        });
        onWillUnmount(() => this.removeCartOrdersHero());
    }

    openRecord(record) {
        return this.quickSale.open(record.resId);
    }

    isCartWorkspace() {
        const context = this.props?.context || this.model?.root?.context || {};
        return context.default_order_type === "cart";
    }

    isMyOrdersWorkspace() {
        const context = this.props?.context || this.model?.root?.context || {};
        return Boolean(context.dac_my_orders_workspace || context.search_default_my_orders);
    }

    getWorkspaceDomain() {
        return this.model?.root?.domain || (this.isCartWorkspace() ? [["order_type", "=", "cart"]] : []);
    }

    bindQuickSaleRows() {
        const rows = this.rootRef?.el?.querySelectorAll(".o_list_table tbody tr.o_data_row") || [];
        const records = this.model?.root?.records || [];
        rows.forEach((row, index) => {
            if (row.dataset.quickSaleBound || !records[index]?.resId) return;
            row.dataset.quickSaleBound = "true";
            row.classList.add("dac-quick-sale-row");
            row.addEventListener("click", (event) => {
                if (event.target.closest("a,button,input,select,textarea,.o-checkbox,.o_field_x2many")) return;
                event.preventDefault();
                event.stopPropagation();
                this.quickSale.open(records[index].resId);
            }, true);
        });
    }

    getCartStats() {
        if (this.cartStats) {
            return this.cartStats;
        }
        const records = this.model?.root?.records || [];
        const activeStates = new Set(["quotation", "deposit", "production", "delivery", "installation"]);
        const active = records.filter((record) => activeStates.has(record.data.order_state_custom)).length;
        const overdue = records.filter((record) => record.data.is_production_overdue).length;
        const outstanding = records.reduce(
            (total, record) => total + Number(record.data.remaining_amount_display || 0),
            0
        );
        const total = this.model?.root?.count ?? records.length;
        return { total, active, overdue, outstanding };
    }

    async loadCartStats() {
        try {
            const baseDomain = this.getWorkspaceDomain();
            const activeStates = ["quotation", "deposit", "production", "delivery", "installation"];
            const [total, active, orders] = await Promise.all([
                this.orm.searchCount("sale.order", baseDomain),
                this.orm.searchCount("sale.order", [...baseDomain, ["order_state_custom", "in", activeStates]]),
                this.orm.searchRead("sale.order", baseDomain,
                    ["remaining_amount_display", "order_state_custom", "production_deadline", "production_done"],
                    { limit: 2000 }),
            ]);
            const today = new Date().toISOString().slice(0, 10);
            this.cartStats = {
                total,
                active,
                overdue: orders.filter((order) => order.order_state_custom === "production"
                    && order.production_deadline && !order.production_done
                    && order.production_deadline < today).length,
                outstanding: orders.reduce((sum, order) => sum + Number(order.remaining_amount_display || 0), 0),
            };
            this.mountCartOrdersHero();
        } catch (error) {
            console.warn("Unable to load cart order summary", error);
        }
    }

    mountCartOrdersHero() {
        // ListController stores its DOM root in rootRef (unlike the form
        // controller, which exposes `el` directly).
        const root = this.rootRef?.el;
        if (!root) {
            return;
        }
        root.classList.add("dac-cart-orders-list");
        let hero = root.querySelector(`.${HERO_CLASS}`);
        if (!hero) {
            hero = document.createElement("section");
            hero.className = HERO_CLASS;
            hero.innerHTML = `
                <div class="dac-cart-orders-heading">
                    <div>
                        <div class="dac-cart-orders-breadcrumb">DAC Bán hàng &amp; CRM <span>/</span> Bán hàng</div>
                        <h1>${this.isCartWorkspace() ? "Đơn hàng Xe đẩy" : "Đơn hàng"}</h1>
                        <p>Quản lý đơn theo 5 bước: Báo giá → Thiết kế/Cọc → Sản xuất → Giao hàng → Hoàn thành.</p>
                    </div>
                    <div class="dac-cart-orders-actions">
                        <button type="button" class="btn dac-cart-orders-export"><i class="fa fa-download"></i> Xuất Excel</button>
                        <button type="button" class="btn dac-cart-orders-create"><i class="fa fa-plus"></i> Tạo đơn mới</button>
                    </div>
                </div>
                <div class="dac-cart-orders-stats">
                    <article><div><small>Tất cả đơn</small><strong data-stat="total">0</strong></div></article>
                    <article><div><small>Đang hoạt động</small><strong data-stat="active">0</strong></div></article>
                    <article><div><small>Quá hạn</small><strong data-stat="overdue" class="overdue">0</strong></div></article>
                    <article><div><small>Tổng công nợ</small><strong data-stat="outstanding">0 ₫</strong></div></article>
                </div>`;
            const controlPanel = root.querySelector(".o_control_panel");
            if (controlPanel) {
                controlPanel.before(hero);
            } else {
                this.el.prepend(hero);
            }
            hero.querySelector(".dac-cart-orders-create")?.addEventListener("click", () => this.openNewCartOrder());
            hero.querySelector(".dac-cart-orders-export")?.addEventListener("click", () => this.onDirectExportData());
        }
        this.mountCartToolbar(root);
        this.decorateCustomerCells(root);
        this.renderCartKanban(root);
        const stats = this.getCartStats();
        hero.querySelector('[data-stat="total"]').textContent = stats.total;
        hero.querySelector('[data-stat="active"]').textContent = stats.active;
        hero.querySelector('[data-stat="overdue"]').textContent = stats.overdue;
        hero.querySelector('[data-stat="outstanding"]').textContent = formatCurrency(stats.outstanding);
    }

    mountCartToolbar(root) {
        let toolbar = root.querySelector(`.${TOOLBAR_CLASS}`);
        const renderer = root.querySelector(".o_list_renderer");
        if (!renderer) {
            return;
        }
        if (!toolbar) {
            toolbar = document.createElement("div");
            toolbar.className = TOOLBAR_CLASS;
            toolbar.innerHTML = `
                <div class="dac-cart-toolbar-filters">
                    <label class="dac-cart-toolbar-search">
                        <i class="fa fa-search"></i>
                        <input type="search" placeholder="Tìm mã đơn, khách hàng, SĐT..." aria-label="Tìm đơn hàng"/>
                    </label>
                    <select class="dac-cart-stage-select" aria-label="Giai đoạn">
                        <option value="">Tất cả giai đoạn</option>
                        <option value="quotation">Báo giá</option>
                        <option value="deposit">Thiết kế / Cọc</option>
                        <option value="production">Sản xuất</option>
                        <option value="delivery">Giao hàng</option>
                        <option value="completed">Hoàn thành</option>
                    </select>
                    <select class="dac-cart-sale-select" aria-label="Sale"><option value="">Tất cả Sale</option></select>
                    <button type="button" class="dac-cart-toolbar-filter">Bộ lọc</button>
                    <div class="${BULK_ACTIONS_CLASS}" hidden>
                        <button type="button" class="dac-cart-bulk-actions-toggle" aria-expanded="false" aria-haspopup="true">
                            <i class="fa fa-ellipsis-v" aria-hidden="true"></i>
                            <span>Tác vụ</span>
                            <span class="dac-cart-bulk-actions-count" aria-label="Số đơn đã chọn">0</span>
                        </button>
                    </div>
                </div>
                <div class="dac-cart-view-toggle" aria-label="Kiểu hiển thị">
                    <button type="button" class="active">Danh sách</button>
                    <button type="button">Kanban</button>
                </div>`;
            renderer.before(toolbar);
            const searchInput = toolbar.querySelector('input[type="search"]');
            searchInput?.addEventListener("input", (event) => {
                window.clearTimeout(this.cartSearchTimer);
                const query = event.currentTarget.value.trim();
                this.cartSearchTimer = window.setTimeout(() => {
                    const domain = query ? [
                        "|", "|", "|",
                        ["order_number", "ilike", query],
                        ["name", "ilike", query],
                        ["partner_id", "ilike", query],
                        ["phone", "ilike", query],
                    ] : [];
                    this.applyToolbarFilter("search", domain, query || "Tìm đơn hàng");
                }, 250);
            });
            toolbar.querySelector(".dac-cart-toolbar-filter")?.addEventListener("click", () => {
                root.querySelector(".o_control_panel .o_searchview_dropdown_toggler")?.click();
            });
            toolbar.querySelector(".dac-cart-bulk-actions-toggle")?.addEventListener("click", () => {
                this.openNativeCartActions(root, toolbar.querySelector(".dac-cart-bulk-actions-toggle"));
            });
            root.addEventListener("change", this.cartSelectionChangeHandler);
            root.addEventListener("input", this.cartSelectionChangeHandler, true);
            root.addEventListener("click", this.cartSelectionChangeHandler, true);
            toolbar.querySelector(".dac-cart-stage-select")?.addEventListener("change", (event) => {
                const value = event.currentTarget.value;
                this.applyToolbarFilter("stage", value ? [["order_state_custom", "=", value]] : [], event.currentTarget.selectedOptions[0]?.textContent || "Giai đoạn");
            });
            toolbar.querySelector(".dac-cart-sale-select")?.addEventListener("change", (event) => {
                const value = Number(event.currentTarget.value || 0);
                this.applyToolbarFilter("sale", value ? [["user_id", "=", value]] : [], event.currentTarget.selectedOptions[0]?.textContent || "Sale");
            });
            toolbar.querySelectorAll(".dac-cart-view-toggle button").forEach((button) => {
                button.addEventListener("click", () => this.setCartViewMode(button.textContent.trim() === "Kanban" ? "kanban" : "list"));
            });
            this.loadSaleOptions(toolbar);
        } else if (toolbar.nextElementSibling !== renderer) {
            renderer.before(toolbar);
        }
        this.refreshCartBulkActions(root);
    }

    getSelectedCartOrders(root) {
        // The CheckBox rendered by Odoo's ListRenderer exposes the current
        // selection in the row selector input. Read the rendered list rows so
        // this remains correct even when the controller model is paged.
        return Array.from(root?.querySelectorAll(
            ".o_list_renderer tbody tr.o_data_row_selected"
        ) || []);
    }

    refreshCartBulkActions(root) {
        const wrap = root?.querySelector(`.${BULK_ACTIONS_CLASS}`);
        const toggle = wrap?.querySelector(".dac-cart-bulk-actions-toggle");
        const count = wrap?.querySelector(".dac-cart-bulk-actions-count");
        if (!wrap || !toggle || !count) return;

        const selected = this.getSelectedCartOrders(root);
        wrap.hidden = selected.length === 0;
        root?.classList.toggle("dac-cart-has-selection", selected.length > 0);
        count.textContent = String(selected.length);
        count.setAttribute("aria-label", `${selected.length} đơn đã chọn`);
    }

    openNativeCartActions(root, customButton) {
        const nativeButton = root?.querySelector('.o_cp_action_menus button[data-hotkey="u"]');
        if (!nativeButton || !customButton) return;
        const rect = customButton.getBoundingClientRect();
        Object.assign(nativeButton.style, {
            position: "fixed",
            left: `${rect.left}px`,
            top: `${rect.top}px`,
            width: `${rect.width}px`,
            height: `${rect.height}px`,
            opacity: "0",
            pointerEvents: "none",
            zIndex: "1050",
        });
        nativeButton.click();
    }

    setCartViewMode(mode) {
        this.cartViewMode = mode;
        this.renderCartKanban(this.rootRef?.el);
    }

    async renderCartKanban(root) {
        if (!root) return;
        const renderer = root.querySelector(".o_list_renderer");
        const toolbar = root.querySelector(`.${TOOLBAR_CLASS}`);
        if (!renderer || !toolbar) return;
        toolbar.querySelectorAll(".dac-cart-view-toggle button").forEach((button) => {
            button.classList.toggle("active", (button.textContent.trim() === "Kanban") === (this.cartViewMode === "kanban"));
        });
        let board = root.querySelector(`.${KANBAN_CLASS}`);
        renderer.classList.toggle("d-none", this.cartViewMode === "kanban");
        if (this.cartViewMode !== "kanban") {
            board?.remove();
            return;
        }
        board?.remove();
        board = document.createElement("div");
        board.className = KANBAN_CLASS;
        // `root.records` only contains the current pager page.  Fetch against
        // the list's effective domain so Kanban counts/cards stay identical to
        // the list view and to the stage/sale filters.
        const request = ++this.cartKanbanRequest;
        let records;
        try {
            records = await this.orm.searchRead(
                "sale.order",
                this.getWorkspaceDomain(),
                ["cart_order_code", "partner_id", "user_id", "production_deadline", "amount_total", "order_state_custom"],
                { limit: 2000 }
            );
        } catch (error) {
            console.warn("Unable to load cart Kanban records", error);
            records = (this.model?.root?.records || []).map((record) => ({ id: record.resId, ...record.data }));
        }
        if (request !== this.cartKanbanRequest || this.cartViewMode !== "kanban") {
            return;
        }
        for (const [stage, label] of KANBAN_STAGES) {
            const stageRecords = records.filter((record) => {
                const value = record.order_state_custom;
                return value === stage || (stage === "delivery" && value === "installation");
            });
            const column = document.createElement("section");
            column.className = "dac-cart-kanban-column";
            const heading = document.createElement("header");
            const title = document.createElement("strong");
            title.textContent = label;
            const count = document.createElement("span");
            count.textContent = stageRecords.length;
            heading.append(title, count);
            column.append(heading);
            for (const record of stageRecords) {
                const card = document.createElement("button");
                card.type = "button";
                card.className = "dac-cart-kanban-card";
                const partner = record.partner_id;
                const sale = record.user_id;
                const deadline = record.production_deadline;
                const partnerName = partner?.display_name || partner?.name || partner?.[1] || "—";
                const saleName = sale?.display_name || sale?.name || sale?.[1] || "—";
                const dateText = formatDate(deadline);
                for (const [className, text] of [
                    ["dac-cart-kanban-code", record.cart_order_code || "—"],
                    ["dac-cart-kanban-customer", partnerName],
                    ["dac-cart-kanban-total", formatCurrency(record.amount_total)],
                    ["dac-cart-kanban-meta", `${dateText} · ${saleName}`],
                ]) {
                    const line = document.createElement("span");
                    line.className = className;
                    line.textContent = text;
                    card.append(line);
                }
                card.addEventListener("click", () => this.quickSale.open(record.id));
                column.append(card);
            }
            board.append(column);
        }
        renderer.before(board);
    }

    async applyToolbarFilter(kind, domain, description) {
        const searchModel = this.env.searchModel;
        const previousGroupId = this.cartToolbarFilterGroupIds[kind];
        searchModel.blockNotification = true;
        try {
            if (previousGroupId) {
                searchModel.deactivateGroup(previousGroupId);
                this.cartToolbarFilterGroupIds[kind] = null;
            }
            if (domain.length) {
                const groupId = searchModel.nextGroupId;
                await searchModel.splitAndAddDomain(domain);
                this.cartToolbarFilterGroupIds[kind] = groupId;
            }
        } finally {
            searchModel.blockNotification = false;
        }
        await searchModel._notify();
    }

    async loadSaleOptions(toolbar) {
        const select = toolbar.querySelector(".dac-cart-sale-select");
        if (!select || select.dataset.loaded) return;
        if (this.isMyOrdersWorkspace()) {
            let saleName = "Sale của tôi";
            try {
                const [currentUser] = await this.orm.read("res.users", [user.userId], ["name"]);
                saleName = currentUser?.name || saleName;
            } catch (error) {
                console.warn("Unable to load current sale name", error);
            }
            select.innerHTML = "";
            const option = document.createElement("option");
            option.value = String(user.userId);
            option.textContent = saleName;
            select.append(option);
            select.disabled = true;
            select.dataset.loaded = "true";
            return;
        }
        select.dataset.loaded = "loading";
        try {
            const users = await this.orm.searchRead(
                "res.users", [["active", "=", true], ["share", "=", false]],
                ["name", "dac_role"], { limit: 200, order: "name" }
            );
            for (const user of users.filter((item) => item.dac_role)) {
                const option = document.createElement("option");
                option.value = user.id;
                option.textContent = user.name;
                select.append(option);
            }
            select.dataset.loaded = "true";
        } catch (error) {
            select.dataset.loaded = "error";
            console.warn("Unable to load DAC sale users", error);
        }
    }

    decorateCustomerCells(root) {
        for (const cell of root.querySelectorAll('.o_list_table td[name="partner_id"]')) {
            if (cell.querySelector(".dac-cart-customer-avatar")) continue;
            const link = cell.querySelector(".dac-cart-customer > a");
            const name = link?.textContent?.trim();
            if (!name) continue;
            const initials = name.split(/\s+/).filter(Boolean).slice(-2).map((part) => part[0]).join("").toUpperCase();
            const avatar = document.createElement("span");
            avatar.className = "dac-cart-customer-avatar";
            avatar.textContent = initials;
            cell.querySelector(".dac-cart-customer")?.prepend(avatar);
        }
    }

    openNewCartOrder() {
        const root = this.rootRef?.el;
        const nativeNewButton = root?.querySelector(".o_list_button_add");
        if (nativeNewButton) {
            nativeNewButton.click();
            return;
        }
        const headerButton = root?.querySelector('button[name*="action_open_new_sale_order_url"]');
        headerButton?.click();
    }

    removeCartOrdersHero() {
        window.clearTimeout(this.cartSearchTimer);
        const root = this.rootRef?.el;
        root?.removeEventListener("change", this.cartSelectionChangeHandler);
        root?.removeEventListener("input", this.cartSelectionChangeHandler, true);
        root?.removeEventListener("click", this.cartSelectionChangeHandler, true);
        root?.querySelector('.o_cp_action_menus button[data-hotkey="u"]')?.removeAttribute("style");
        root?.querySelector(`.${HERO_CLASS}`)?.remove();
        root?.querySelector(`.${TOOLBAR_CLASS}`)?.remove();
        root?.querySelector(`.${KANBAN_CLASS}`)?.remove();
        root?.classList.remove("dac-cart-orders-list");
        root?.classList.remove("dac-cart-has-selection");
    }
}

registry.category("views").add("dac_cart_orders_list", {
    ...listView,
    Controller: CartOrdersListController,
});
