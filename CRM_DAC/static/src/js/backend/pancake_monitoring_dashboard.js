/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class PancakeMonitoringDashboard extends Component {
  setup() {
    this.orm = useService("orm");
    this.action = useService("action");
    this.notification = useService("notification");
    this.state = useState({ loading: true, data: null });
    onWillStart(() => this.loadData());
  }

  async loadData() {
    this.state.loading = true;
    try {
      this.state.data = await this.orm.call(
        "pancake.message.sync.job",
        "get_monitoring_dashboard_data",
        [],
      );
    } finally {
      this.state.loading = false;
    }
  }

  formatNumber(value) {
    return new Intl.NumberFormat("vi-VN").format(Number(value || 0));
  }

  formatTime(value) {
    if (!value) return "—";
    return new Date(`${value.replace(" ", "T")}Z`).toLocaleTimeString("vi-VN");
  }

  openLogs() {
    return this.action.doAction("CRM_DAC.action_pancake_message_sync_logs", { clearBreadcrumbs: true });
  }

  openJobs() {
    return this.action.doAction("CRM_DAC.action_pancake_message_sync_jobs", { clearBreadcrumbs: true });
  }

  scanToday() {
    return this.action.doAction("CRM_DAC.action_pancake_sync_dashboard", { clearBreadcrumbs: true });
  }

  async syncAll() {
    const result = await this.orm.call(
      "pancake.message.sync.job",
      "action_start_manual_full_sync",
      [],
    );
    this.notification.add(result.message || "Đã xếp hàng đồng bộ Pancake.", {
      type: result.status === "started" ? "success" : "info",
    });
    await this.loadData();
  }
}

PancakeMonitoringDashboard.template = "CRM_DAC.PancakeMonitoringDashboard";
registry
  .category("actions")
  .add("pancake_monitoring_dashboard", PancakeMonitoringDashboard);
