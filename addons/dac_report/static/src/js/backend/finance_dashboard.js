/** @odoo-module **/
import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class FinanceDashboard extends Component {
  setup() {
    this.orm = useService("orm");
    this.action = useService("action");
    this.notification = useService("notification");
    this.state = useState({
      loading: true,
      data: {
        total_receivable: 0,
        receivable_count: 0,
        due_today: 0,
        due_today_customer_count: 0,
        overdue: 0,
        overdue_count: 0,
        collected_month: 0,
        collection_growth: 0,
        aging: [],
        collections: [],
        priority: [],
      },
    });
    onWillStart(() => this.loadData());
  }
  async loadData() {
    this.state.loading = true;
    try {
      Object.assign(
        this.state.data,
        await this.orm.call("sale.order", "dac_get_finance_dashboard", [], {
          company_id: false,
        }),
      );
    } catch (e) {
      console.error(e);
      this.notification.add("Không thể tải dữ liệu công nợ.", {
        type: "danger",
      });
    } finally {
      this.state.loading = false;
    }
  }
  formatCurrency(v) {
    return new Intl.NumberFormat("vi-VN", {
      style: "currency",
      currency: "VND",
      maximumFractionDigits: 0,
    }).format(Number(v || 0));
  }
  formatCompact(v) {
    const n = Number(v || 0);
    if (Math.abs(n) >= 1e9)
      return `${(n / 1e9).toLocaleString("vi-VN", { maximumFractionDigits: 1 })} tỷ`;
    if (Math.abs(n) >= 1e6)
      return `${(n / 1e6).toLocaleString("vi-VN", { maximumFractionDigits: 1 })}tr`;
    return new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 0 }).format(
      n,
    );
  }
  formatDate(v) {
    if (!v) return "—";
    const [y, m, d] = v.split("-");
    return `${d}/${m}/${y}`;
  }
  get maxAging() {
    return Math.max(
      1,
      ...this.state.data.aging.map((x) => Number(x.amount || 0)),
    );
  }
  get maxCollection() {
    return Math.max(
      1,
      ...this.state.data.collections.map((x) => Number(x.amount || 0)),
    );
  }
  agingWidth(v) {
    return `${Math.max(2, (Number(v || 0) / this.maxAging) * 100)}%`;
  }
  collectionHeight(v) {
    return v
      ? `${Math.max(12, (Number(v) / this.maxCollection) * 100)}%`
      : "4px";
  }
  openReceivables() {
    return this.action.doAction(
      "dac_erp.dac_account_move_custom_action_manager",
    );
  }
  openInvoice(id) {
    return this.action.doAction({
      type: "ir.actions.act_window",
      name: "Chi tiết công nợ",
      res_model: "account.move",
      res_id: id,
      views: [[false, "form"]],
      target: "current",
    });
  }
  createReceipt() {
    return this.action.doAction({
      type: "ir.actions.act_window",
      name: "Tạo phiếu thu",
      res_model: "account.payment",
      views: [[false, "form"]],
      target: "current",
      context: {
        default_payment_type: "inbound",
        default_partner_type: "customer",
      },
    });
  }
  recordPayment() {
    this.openReceivables();
    this.notification.add(
      "Chọn hóa đơn cần thu và dùng nút Ghi nhận thanh toán.",
      { type: "info" },
    );
  }
}
FinanceDashboard.template = "dac_report.FinanceDashboard";
registry.category("actions").add("dac_finance_dashboard", FinanceDashboard);
