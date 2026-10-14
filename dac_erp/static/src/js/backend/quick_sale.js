/** @odoo-module **/

import { Component, reactive, useState, onMounted, onPatched } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { ListController } from "@web/views/list/list_controller";
import { listView } from "@web/views/list/list_view";

const quickSaleService = {
    dependencies: ["orm", "action", "notification"],
    start(env, { orm, action, notification }) {
        const state = reactive({ open: false, loading: false, order: null, error: null });
        return {
            state,
            async open(orderId) {
                if (!orderId) return;
                state.open = true;
                state.loading = true;
                state.error = null;
                state.order = null;
                try {
                    state.order = await orm.call("sale.order", "dac_get_quick_sale", [orderId]);
                    if (!state.order) throw new Error("Không tìm thấy đơn hàng.");
                } catch (error) {
                    state.error = error?.message || "Không thể tải thông tin đơn hàng.";
                } finally {
                    state.loading = false;
                }
            },
            close() {
                state.open = false;
            },
            async openFull() {
                const id = state.order?.id;
                if (!id) return;
                state.open = false;
                return action.doAction({
                    type: "ir.actions.act_window", res_model: "sale.order", res_id: id,
                    views: [[false, "form"]], target: "current",
                });
            },
            async recordPayment() {
                const id = state.order?.id;
                if (!id) return;
                try {
                    const result = await orm.call("sale.order", "action_open_deposit_payment", [[id]]);
                    return action.doAction(result);
                } catch (error) {
                    notification.add(error?.message || "Không thể mở ghi nhận thanh toán", { type: "danger" });
                }
            },
            async createTask() {
                const id = state.order?.id;
                if (!id) return;
                try {
                    const result = await orm.call("sale.order", "action_quick_create_task", [[id]]);
                    return action.doAction(result);
                } catch (error) {
                    notification.add(error?.message || "Không thể tạo task", { type: "danger" });
                }
            },
            openConversation() {
                const id = state.order?.conversation_id;
                if (!id) {
                    notification.add("Đơn hàng chưa liên kết hội thoại", { type: "warning" });
                    return;
                }
                state.open = false;
                return action.doAction({
                    type: "ir.actions.act_window", res_model: "page.fm.conversation", res_id: id,
                    views: [[false, "form"]], target: "current",
                });
            },
        };
    },
};
registry.category("services").add("dac_quick_sale", quickSaleService);

export class QuickSaleDrawer extends Component {
    setup() {
        this.quickSale = useService("dac_quick_sale");
        this.state = useState(this.quickSale.state);
    }
    get stages() {
        return [
            ["quotation", "Báo giá"], ["deposit", "Thiết kế/Cọc"],
            ["production", "Sản xuất"], ["delivery", "Giao hàng"], ["completed", "Hoàn thành"],
        ];
    }
    stageStatus(key) {
        const current = this.state.order?.state === "installation" ? "delivery" : this.state.order?.state;
        const order = this.stages.map(([value]) => value);
        const currentIndex = order.indexOf(current);
        const index = order.indexOf(key);
        return index < currentIndex ? "done" : index === currentIndex ? "active" : "pending";
    }
    formatMoney(value) {
        return `${new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 0 }).format(Number(value || 0))}đ`;
    }
    onBackdrop(ev) {
        if (ev.target === ev.currentTarget) this.quickSale.close();
    }
}
QuickSaleDrawer.template = "dac_erp.QuickSaleDrawer";
QuickSaleDrawer.props = {};
registry.category("main_components").add("DacQuickSaleDrawer", { Component: QuickSaleDrawer });

export class QuickSaleListController extends ListController {
    setup() {
        super.setup();
        this.quickSale = useService("dac_quick_sale");
        onMounted(() => this.bindQuickSaleRows());
        onPatched(() => this.bindQuickSaleRows());
    }
    openRecord(record) {
        return this.quickSale.open(record.resId);
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
}
registry.category("views").add("dac_quick_sale_list", {
    ...listView,
    Controller: QuickSaleListController,
});
