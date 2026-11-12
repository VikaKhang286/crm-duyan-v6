/** @odoo-module **/

import { registry } from "@web/core/registry";

const READ_KEY = "dac_design_read_notifications";

function readSet() {
  try {
    return new Set(JSON.parse(localStorage.getItem(READ_KEY) || "[]"));
  } catch {
    return new Set();
  }
}

function saveSet(read) {
  localStorage.setItem(READ_KEY, JSON.stringify([...read]));
}

function relativeTime(value) {
  if (!value) return "Mới cập nhật";
  const time = new Date(`${String(value).replace(" ", "T")}Z`).getTime();
  const minutes = Math.max(0, Math.floor((Date.now() - time) / 60000));
  if (!Number.isFinite(minutes)) return "Mới cập nhật";
  if (minutes < 60) return `${Math.max(1, minutes)} phút`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)} giờ`;
  return `${Math.floor(minutes / 1440)} ngày`;
}

const designWorkspaceHeader = {
  dependencies: ["orm", "action"],
  start(env, { orm, action }) {
    // Do not await boot here. A header failure must never delay Odoo startup.
    window.setTimeout(async () => {
      try {
        if (!await orm.call("sale.order", "dac_is_design_workspace_user", [])) return;

        let header;
        let panel;
        let data = {};
        let unreadOnly = false;
        const read = readSet();

        const getItems = () => (data.design_notifications || []).map((item, index) => ({
          ...item,
          key: item.key || `design-notification-${index}`,
          time: relativeTime(item.notification_at),
        }));
        const unread = () => getItems().filter((item) => !read.has(item.key)).length;
        const close = () => {
          panel?.remove();
          panel = null;
          header?.querySelector(".dac-design-global-bell")?.classList.remove("is-active");
        };

        const renderPanel = () => {
          if (!panel) return;
          panel.replaceChildren();
          const head = document.createElement("div");
          head.className = "dac-design-global-notification-head";
          const title = document.createElement("strong");
          title.textContent = "Thông báo";
          const controls = document.createElement("div");
          const done = document.createElement("button");
          done.type = "button";
          done.innerHTML = '<i class="fa fa-check"></i>';
          done.title = "Đánh dấu tất cả đã đọc";
          done.onclick = () => { getItems().forEach((item) => read.add(item.key)); saveSet(read); render(); };
          const x = document.createElement("button");
          x.type = "button";
          x.innerHTML = '<i class="fa fa-times"></i>';
          x.title = "Đóng";
          x.onclick = close;
          controls.append(done, x);
          head.append(title, controls);

          const tabs = document.createElement("div");
          tabs.className = "dac-design-global-notification-tabs";
          [[false, "Tất cả"], [true, "Chưa đọc"]].forEach(([value, label]) => {
            const button = document.createElement("button");
            button.type = "button";
            button.className = unreadOnly === value ? "is-active" : "";
            button.textContent = label;
            if (value && unread()) {
              const count = document.createElement("b");
              count.textContent = unread();
              button.appendChild(count);
            }
            button.onclick = () => { unreadOnly = value; renderPanel(); };
            tabs.appendChild(button);
          });

          const list = document.createElement("div");
          list.className = "dac-design-global-notification-list";
          const visible = unreadOnly ? getItems().filter((item) => !read.has(item.key)) : getItems();
          if (!visible.length) {
            const empty = document.createElement("p");
            empty.className = "dac-design-global-notification-empty";
            empty.textContent = "Đã đọc hết thông báo";
            list.appendChild(empty);
          }
          visible.forEach((item) => {
            const row = document.createElement("button");
            row.type = "button";
            row.className = `dac-design-global-notification ${read.has(item.key) ? "is-read" : "is-unread"}`;
            const icon = document.createElement("span");
            icon.className = `dac-design-global-notification-icon ${item.alert ? "is-alert" : (item.kind || "order")} ${item.avatar_tone || ""}`;
            if (item.initials) {
              icon.textContent = item.initials;
            } else {
              const glyph = document.createElement("i");
              glyph.className = item.alert ? "fa fa-exclamation-triangle" : "fa fa-cube";
              icon.appendChild(glyph);
            }
            const copy = document.createElement("span");
            copy.className = "dac-design-global-notification-copy";
            const itemTitle = document.createElement("strong");
            itemTitle.textContent = item.title || "Cập nhật thiết kế";
            const detail = document.createElement("span");
            detail.className = "dac-design-global-notification-detail";
            const excerpt = document.createElement("span");
            excerpt.className = "dac-design-global-notification-excerpt";
            excerpt.textContent = `“${item.detail || ""}”`;
            const timestamp = document.createElement("time");
            timestamp.textContent = `· ${item.time}`;
            detail.append(excerpt, timestamp);
            copy.append(itemTitle, detail);
            const dot = document.createElement("i");
            dot.className = "dac-design-global-notification-dot";
            row.append(icon, copy, dot);
            row.onclick = async () => {
              read.add(item.key);
              saveSet(read);
              close();
              if (item.open_task && item.order_id) {
                const result = await orm.call("sale.order", "action_open_design_collaboration", [[item.order_id]]);
                await action.doAction(result, { clearBreadcrumbs: true });
              } else if (item.order_id) {
                await action.doAction({ type: "ir.actions.act_window", res_model: "sale.order", res_id: item.order_id, views: [[false, "form"]], target: "current" });
              }
            };
            list.appendChild(row);
          });
          panel.append(head, tabs, list);
        };

        const render = () => {
          const host = document.querySelector(".o_action_manager");
          if (!host) return;
          if (!header || !header.isConnected || header.parentElement !== host) {
            close();
            header = document.createElement("div");
            header.className = "dac-design-global-header";
            const label = document.createElement("span");
            label.textContent = "Không gian Thiết kế";
            const bell = document.createElement("button");
            bell.type = "button";
            bell.className = "dac-design-global-bell";
            bell.innerHTML = '<i class="fa fa-bell-o"></i>';
            bell.onclick = async () => {
              if (panel) return close();
              // Match the fixed app rail: no background polling or remounting.
              // Refresh only when the user explicitly asks to see notifications.
              try { data = await orm.call("sale.order", "dac_get_dashboard_design", []); } catch { /* keep last successful data */ }
              render();
              panel = document.createElement("aside");
              panel.className = "dac-design-global-notification-popover";
              bell.classList.add("is-active");
              header.appendChild(panel);
              renderPanel();
            };
            header.append(label, bell);
            host.prepend(header);
          }
          const bell = header.querySelector(".dac-design-global-bell");
          bell.querySelector("b")?.remove();
          if (unread()) {
            const badge = document.createElement("b");
            badge.textContent = unread();
            bell.appendChild(badge);
          }
          renderPanel();
        };

        const reload = async () => {
          try { data = await orm.call("sale.order", "dac_get_dashboard_design", []); } catch { data = {}; }
          render();
        };
        await reload();
        document.addEventListener("click", (event) => {
          if (panel && header && !header.contains(event.target)) close();
        }, true);
        // Odoo replaces the action-manager node during SPA navigation. Restore
        // the header only after that specific DOM replacement; no polling/RPC.
        new MutationObserver(() => {
          const host = document.querySelector(".o_action_manager");
          if (host && (!header || !header.isConnected || header.parentElement !== host)) render();
        }).observe(document.body, { childList: true, subtree: true });
      } catch {
        // The workspace header is optional. Keep the backend fully usable.
      }
    }, 0);
    return {};
  },
};

registry.category("services").add("dac_design_workspace_header", designWorkspaceHeader);
