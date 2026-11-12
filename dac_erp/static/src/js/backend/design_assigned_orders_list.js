/** @odoo-module **/

import { onMounted, onPatched } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { ListController } from "@web/views/list/list_controller";
import { listView } from "@web/views/list/list_view";

/** Design-only list: an assigned order is an entry point to its task. */
export class DesignAssignedOrdersListController extends ListController {
    setup() {
        super.setup();
        this.orm = useService("orm");
        this.action = useService("action");
        onMounted(() => this.bindDesignRows());
        onPatched(() => this.bindDesignRows());
    }

    async openDesignTask(orderId) {
        if (!orderId) return;
        const action = await this.orm.call(
            "sale.order", "action_open_design_collaboration", [[orderId]]
        );
        return this.action.doAction(action, { clearBreadcrumbs: true });
    }

    openRecord(record) {
        return this.openDesignTask(record.resId);
    }

    bindDesignRows() {
        const rows = this.rootRef?.el?.querySelectorAll(".o_list_table tbody tr.o_data_row") || [];
        const records = this.model?.root?.records || [];
        rows.forEach((row, index) => {
            const orderId = records[index]?.resId;
            if (row.dataset.dacDesignTaskBound || !orderId) return;
            row.dataset.dacDesignTaskBound = "1";
            row.addEventListener("click", (event) => {
                if (event.target.closest("a,button,input,select,textarea,.o-checkbox,.o_field_x2many")) return;
                event.preventDefault();
                event.stopPropagation();
                event.stopImmediatePropagation?.();
                this.openDesignTask(orderId);
            }, true);
        });
    }
}

registry.category("views").add("dac_design_assigned_orders_list", {
    ...listView,
    Controller: DesignAssignedOrdersListController,
});
