/** @odoo-module **/
import {
  Component,
  useState,
  useRef,
  onWillStart,
  onMounted,
  onPatched,
  onWillUnmount,
} from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { registry } from "@web/core/registry";

const _DESIGN_H = "dac_design_list_h";
const _DESIGN_READ_NOTIFICATIONS = "dac_design_read_notifications";

function loadReadNotifications() {
  try {
    const value = JSON.parse(localStorage.getItem(_DESIGN_READ_NOTIFICATIONS) || "[]");
    return Array.isArray(value) ? value : [];
  } catch {
    return [];
  }
}

class DesignDashboard extends Component {
  setup() {
    this.orm = useService("orm");
    this.action = useService("action");
    this.menu = useService("menu");
    this.notification = useService("notification");
    this.state = useState({ loading: true, data: null, error: null, filter: "total", search: "", readNotifications: loadReadNotifications(), notificationTab: "all" });
    this.designScrollRef = useRef("designScroll");

    onWillStart(async () => {
      await this.reload();
    });

    this._applyViewportTweaks = this._applyViewportTweaks.bind(this);
    this._ensureViewportReady = this._ensureViewportReady.bind(this);
    this._restoreViewportTweaks = this._restoreViewportTweaks.bind(this);

    onMounted(() => {
      if (!this.menu.getCurrentApp()) {
        const designMenu = this.menu
          .getAll()
          .find((menu) => menu.xmlid === "dac_report.menu_dashboards_manage_design");
        if (designMenu) this.menu.setCurrentMenu(designMenu);
      }
      document.body.classList.add("dac-dashboard-open", "dac-compact");
      const act =
        this.el?.closest?.(".o_action") || document.querySelector(".o_action");
      if (act) {
        this._hostAction = act;
        act.classList.add("dac-host");
      }
      this._raf1 = requestAnimationFrame(this._ensureViewportReady);
      this._refreshTimer = setInterval(() => this._silentReload(), 30_000);
    });

    onPatched(() => {
      const h = localStorage.getItem(_DESIGN_H);
      if (h && this.designScrollRef.el) {
        this._setScrollH(this.designScrollRef.el, parseInt(h, 10));
      }
    });

    onWillUnmount(() => {
      if (this._raf1) cancelAnimationFrame(this._raf1);
      if (this._raf2) cancelAnimationFrame(this._raf2);
      if (this._retryTimer) clearTimeout(this._retryTimer);
      if (this._refreshTimer) clearInterval(this._refreshTimer);
      window.removeEventListener("resize", this._applyViewportTweaks);
      if (this._hostAction) this._hostAction.classList.remove("dac-host");
      document.body.classList.remove("dac-dashboard-open", "dac-compact");
      this._restoreViewportTweaks();
    });
  }

  _setScrollH(el, h) {
    el.style.setProperty("height", h + "px", "important");
    el.style.setProperty("max-height", h + "px", "important");
    el.style.setProperty("min-height", "0", "important");
  }

  startResizeDesign(ev) {
    this._startDrag(ev, this.designScrollRef.el, _DESIGN_H);
  }

  _startDrag(ev, scrollEl, storageKey) {
    if (!scrollEl) return;
    const startY = ev.clientY;
    const startH = scrollEl.getBoundingClientRect().height;
    const handle = ev.currentTarget;
    handle.classList.add("is-dragging");
    document.body.style.userSelect = "none";

    const onMove = (e) => {
      const newH = Math.max(60, startH + (e.clientY - startY));
      this._setScrollH(scrollEl, newH);
    };

    const onUp = (e) => {
      handle.classList.remove("is-dragging");
      document.body.style.userSelect = "";
      const finalH = Math.max(60, startH + (e.clientY - startY));
      this._setScrollH(scrollEl, finalH);
      localStorage.setItem(storageKey, String(finalH));
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }

  async _silentReload() {
    try {
      const data = await this.orm.call("sale.order", "dac_get_dashboard_design", []);
      if (data) this.state.data = data;
    } catch (e) {
      console.error("[design_dashboard] silent reload failed:", e);
    }
  }

  async reload() {
    this.state.loading = true;
    this.state.error = null;
    try {
      const data = await this.orm.call(
        "sale.order",
        "dac_get_dashboard_design",
        []
      );
      this.state.data = data || { task_stats: {}, design_tasks: [] };
    } catch (e) {
      this.state.error = (e && e.message) || String(e);
      console.error(e);
    } finally {
      this.state.loading = false;
    }
  }

  get taskStats() {
    return this.state.data?.task_stats || {};
  }
  get designTasks() {
    return this.state.data?.design_tasks || [];
  }
  get designOrderCount() {
    return this.designTasks.length;
  }
  get todoCount() {
    return this.designTasks.filter((task) =>
      task.design_review_state === 'in_progress' ||
      task.design_review_state === 'revision'
    ).length;
  }
  get notStartedCount() {
    return this.designTasks.filter((task) =>
      task.design_review_state === 'in_progress' && task.state === 'draft'
    ).length;
  }
  get inProgressCount() {
    return this.designTasks.filter((task) =>
      task.design_review_state === 'in_progress' && task.state === 'in_progress'
    ).length;
  }
  get completedDesignTasks() {
    return this.designTasks.filter((task) =>
      ['pending', 'approved', 'done'].includes(task.design_review_state)
    );
  }
  get completedDesignOrderCount() {
    return new Set(this.completedDesignTasks.map((task) => task.order_id).filter(Boolean)).size;
  }
  get designDistribution() {
    return this.state.data?.design_distribution || {};
  }
  get distributionStyle() {
    const data = this.designDistribution;
    const total = (data.designing || 0) + (data.revision || 0) + (data.ready || 0);
    if (!total) return "conic-gradient(#eef1f5 0 100%)";
    const a = (data.designing || 0) * 100 / total;
    const b = a + (data.revision || 0) * 100 / total;
    return `conic-gradient(#f4c440 0 ${a}%, #efa53a ${a}% ${b}%, #51aa72 ${b}% 100%)`;
  }
  get overdueCount() {
    return this.designTasks.filter((t) => t.is_overdue).length;
  }

  getTaskStatusLabel(task) {
    if (task.is_overdue) return 'Quá hạn';
    if (task.design_review_state === 'pending') return 'Chờ duyệt';
    if (['approved', 'done'].includes(task.design_review_state)) return 'Thiết kế xong';
    if (task.design_review_state === 'revision') return 'Cần chỉnh sửa';
    if (task.state === 'draft') return 'Cần làm';
    return 'Đang xử lý';
  }

  getTaskStatusClass(task) {
    if (task.is_overdue) return 'is-overdue';
    if (task.design_review_state === 'pending') return 'is-waiting';
    if (['approved', 'done'].includes(task.design_review_state)) return 'is-ready';
    if (task.design_review_state === 'revision') return 'is-revision';
    if (task.state === 'draft') return 'is-todo';
    return 'is-ready';
  }

  formatTaskDate(value) {
    return value ? String(value).split(" ")[0] : "Chưa đặt";
  }

  setFilter(key) {
    this.state.filter = key;
  }

  _applyFilter(tasks) {
    const f = this.state.filter;
    let result = tasks;
    if (f === 'overdue') result = tasks.filter((task) => task.is_overdue);
    // "Cần làm" là nhóm gộp: task mới/đang làm và task cần chỉnh sửa,
    // bao gồm cả task quá hạn để không làm mất dữ liệu khỏi danh sách.
    if (f === 'in_progress') result = tasks.filter((task) =>
      task.design_review_state === 'in_progress' ||
      task.design_review_state === 'revision'
    );
    if (f === 'draft') result = tasks.filter((task) =>
      task.design_review_state === 'in_progress' && task.state === 'draft'
    );
    if (f === 'working') result = tasks.filter((task) =>
      task.design_review_state === 'in_progress' && task.state === 'in_progress'
    );
    if (f === 'revision') result = tasks.filter((task) => task.design_review_state === 'revision');
    if (f === 'waiting') result = tasks.filter((task) => task.design_review_state === 'pending');
    if (f === 'done') result = this.completedDesignTasks;
    const search = this.state.search.trim().toLowerCase();
    if (search) {
      result = result.filter((task) => [task.name, task.order_name, task.order_number, task.order_title, task.partner_name]
        .filter(Boolean).join(" ").toLowerCase().includes(search));
    }
    return result;
  }

  get filteredDesignTasks() {
    return this._applyFilter(this.designTasks);
  }

  get filterLabel() {
    return { total: 'Tất cả', in_progress: 'Cần làm', draft: 'Chưa thực hiện', working: 'Đang làm', overdue: 'Quá hạn', waiting: 'Chờ Sale duyệt', done: 'Thiết kế xong', revision: 'Cần chỉnh sửa' }[this.state.filter] || 'Tất cả';
  }

  get activeFilterLabel() {
    return this.filterLabel;
  }

  get taskListDescription() {
    return this.state.filter === 'in_progress'
      ? 'Bao gồm task mới và task cần chỉnh sửa.'
      : 'Danh sách task theo nhóm thống kê đang chọn.';
  }

  get notificationItems() {
    return (this.state.data?.design_notifications || []).map((item) => ({
      ...item,
      time: this.formatNotificationTime(item.notification_at),
    }));
  }

  formatNotificationTime(value) {
    if (!value) return "Mới cập nhật";
    const timestamp = new Date(`${value.replace(" ", "T")}Z`).getTime();
    if (Number.isNaN(timestamp)) return "Mới cập nhật";
    const minutes = Math.max(0, Math.floor((Date.now() - timestamp) / 60000));
    if (minutes < 60) return `${Math.max(1, minutes)} phút`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours} giờ`;
    return `${Math.floor(hours / 24)} ngày`;
  }

  get unreadNotificationCount() {
    return this.notificationItems.filter((item) => !this.isNotificationRead(item.key)).length;
  }

  get visibleNotificationItems() {
    return this.state.notificationTab === "unread"
      ? this.notificationItems.filter((item) => !this.isNotificationRead(item.key))
      : this.notificationItems;
  }

  setNotificationTab(tab) {
    this.state.notificationTab = tab;
  }

  isNotificationRead(key) {
    return this.state.readNotifications.includes(key);
  }

  markNotificationRead(key) {
    if (!this.isNotificationRead(key)) {
      // Gán mảng mới để OWL render lại đồng thời badge và tab "Chưa đọc".
      this.state.readNotifications = [...this.state.readNotifications, key];
      localStorage.setItem(
        _DESIGN_READ_NOTIFICATIONS,
        JSON.stringify(this.state.readNotifications)
      );
    }
  }

  async openNotification(item) {
    if (!item) return;
    this.markNotificationRead(item.key);
    if (item.order_id) {
      const action = await this.orm.call(
        "sale.order",
        "action_open_design_collaboration",
        [[item.order_id]]
      );
      await this.action.doAction(action, { clearBreadcrumbs: true });
      return;
    }
    await this.openTaskOrOrder(item.task);
  }

  markAllNotificationsRead() {
    this.state.readNotifications = [
      ...new Set([...this.state.readNotifications, ...this.notificationItems.map((item) => item.key)]),
    ];
    localStorage.setItem(
      _DESIGN_READ_NOTIFICATIONS,
      JSON.stringify(this.state.readNotifications)
    );
  }

  async openTaskOrOrder(task) {
    if (!task || !task.id) return;
    if (task.order_id) {
      const action = await this.orm.call(
        "sale.order", "action_open_design_collaboration", [[task.order_id]]
      );
      await this.action.doAction(action, { clearBreadcrumbs: true });
    } else {
      await this.action.doAction({
        type: "ir.actions.act_window",
        res_model: "dac.work.task",
        res_id: task.id,
        target: "current",
        views: [[false, "form"]],
      }, { clearBreadcrumbs: true });
    }
  }

  async markTaskDone(task) {
    if (!task || !task.id) return;
    try {
      await this.orm.write("dac.work.task", [task.id], { state: "done" });
      await this.reload();
    } catch (e) {
      this.notification.add(
        (e && e.message) || "Không thể cập nhật task",
        { type: "danger" }
      );
    }
  }

  openDesignCalendar() {
    return this.action.doAction("dac_erp.action_dac_task_calendar_design", { clearBreadcrumbs: true });
  }

  openCreateDesignTask() {
    return this.action.doAction({
      type: "ir.actions.act_window",
      name: "Task thiết kế",
      res_model: "dac.work.task",
      target: "new",
      views: [[false, "form"]],
      context: { default_task_type: "design" },
    }, { clearBreadcrumbs: true });
  }

  // ==== viewport helpers ====
  _ensureViewportReady() {
    let v = this.el?.classList?.contains("dac-viewport")
      ? this.el
      : document.querySelector(".dac-viewport");
    if (!v) {
      this._retryTimer = setTimeout(this._ensureViewportReady, 0);
      return;
    }
    this._viewport = v;
    this._applyViewportTweaks();
    this._raf2 = requestAnimationFrame(this._applyViewportTweaks);
    window.addEventListener("resize", this._applyViewportTweaks, { passive: true });
  }
  _applyViewportTweaks() {
    const v = this._viewport || document.querySelector(".dac-viewport");
    if (!v) return;
    this._bak = this._bak || new Map();
    const setImp = (node, prop, value) => {
      if (!node) return;
      if (!this._bak.has(node)) this._bak.set(node, {});
      const rec = this._bak.get(node);
      if (!(prop in rec)) rec[prop] = node.style.getPropertyValue(prop);
      node.style.setProperty(prop, value, "important");
    };
    const act = v.closest?.(".o_action") || document.querySelector(".o_action");
    const content = act?.querySelector(".o_content");
    if (content) {
      setImp(content, "padding", "0");
      setImp(content, "overflow", "auto");
      setImp(content, "min-height", "0");
    }
    const ctrl = act?.querySelector(
      ".o_controller_with_control_panel, .o_view_controller"
    );
    if (ctrl) setImp(ctrl, "padding", "0");
    setImp(v, "position", "static");
    setImp(v, "overflow", "visible");
    const cx = v.querySelector(".container-xxl");
    if (cx) {
      setImp(cx, "padding-left", "0");
      setImp(cx, "padding-right", "0");
      setImp(cx, "margin-left", "auto");
      setImp(cx, "margin-right", "auto");
    }
  }
  _restoreViewportTweaks() {
    if (!this._bak) return;
    this._bak.forEach((styles, node) => {
      if (!node) return;
      Object.entries(styles).forEach(([prop, value]) => {
        if (value) node.style.setProperty(prop, value);
        else node.style.removeProperty(prop);
      });
    });
    this._bak.clear();
  }
}

DesignDashboard.template = "dac_erp.DesignDashboard";
registry.category("actions").add("dac_design_dashboard", DesignDashboard);
