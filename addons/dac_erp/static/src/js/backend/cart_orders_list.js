/** @odoo-module **/

import { registry } from "@web/core/registry";
import { ListController } from "@web/views/list/list_controller";
import { listView } from "@web/views/list/list_view";
import { onMounted, onPatched, onWillUnmount } from "@odoo/owl";

const HERO_CLASS = "dac-cart-orders-hero";

function formatCurrency(value) {
    const amount = Number(value || 0);
    if (!amount) {
        return "0 ₫";
    }
    return `${new Intl.NumberFormat("vi-VN", {
        maximumFractionDigits: 0,
    }).format(amount)} ₫`;
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
        onMounted(() => this.mountCartOrdersHero());
        onPatched(() => this.mountCartOrdersHero());
        onWillUnmount(() => this.removeCartOrdersHero());
    }

    getCartStats() {
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
                        <h1>Đơn hàng Xe đẩy</h1>
                        <p>Quản lý đơn theo 5 bước: Báo giá, Thiết kế/Cọc, Sản xuất, Giao hàng và Hoàn thành.</p>
                    </div>
                    <div class="dac-cart-orders-actions">
                        <button type="button" class="btn dac-cart-orders-export">Xuất Excel</button>
                        <button type="button" class="btn dac-cart-orders-create">Tạo đơn mới</button>
                    </div>
                </div>
                <div class="dac-cart-orders-stats">
                    <article><span class="dac-cart-stat-icon dac-cart-stat-icon--blue">▣</span><div><small>Tất cả đơn</small><strong data-stat="total">0</strong></div></article>
                    <article><span class="dac-cart-stat-icon dac-cart-stat-icon--yellow">◷</span><div><small>Đang hoạt động</small><strong data-stat="active">0</strong></div></article>
                    <article><span class="dac-cart-stat-icon dac-cart-stat-icon--red">!</span><div><small>Quá hạn</small><strong data-stat="overdue">0</strong></div></article>
                    <article><span class="dac-cart-stat-icon dac-cart-stat-icon--purple">₫</span><div><small>Tổng công nợ</small><strong data-stat="outstanding">0 ₫</strong></div></article>
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
        const stats = this.getCartStats();
        hero.querySelector('[data-stat="total"]').textContent = stats.total;
        hero.querySelector('[data-stat="active"]').textContent = stats.active;
        hero.querySelector('[data-stat="overdue"]').textContent = stats.overdue;
        hero.querySelector('[data-stat="outstanding"]').textContent = formatCurrency(stats.outstanding);
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
        const root = this.rootRef?.el;
        root?.querySelector(`.${HERO_CLASS}`)?.remove();
        root?.classList.remove("dac-cart-orders-list");
    }
}

registry.category("views").add("dac_cart_orders_list", {
    ...listView,
    Controller: CartOrdersListController,
});
