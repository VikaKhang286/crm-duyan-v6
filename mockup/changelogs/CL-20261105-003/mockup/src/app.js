import {
  seed,
  screens,
  roles,
  states,
  priorities,
  taskStates,
  roleViews,
} from "./data.js";
import { icon } from "./icons.js";
import { mockupRelease } from "./version.js";

const STORAGE = mockupRelease?.snapshot
  ? `dac-ui-mockup-${mockupRelease.id}`
  : "dac-ui-mockup-v1";
const SESSION = mockupRelease?.snapshot
  ? `dac-ui-mockup-session-${mockupRelease.id}`
  : "dac-ui-mockup-session-v1";
const DEMO_EMAIL = "demo@duyan.local";
const DEMO_PASSWORD = "demo";
const clone = (value) => JSON.parse(JSON.stringify(value));
const escape = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const money = (value) => new Intl.NumberFormat("vi-VN").format(value) + " đ";
const day = (value) =>
  value ? value.split("-").reverse().join("/") : "Chưa có hạn";
const today = "2026-11-05"; // Ngày tham chiếu của fixtures, không giả làm thời gian production.
let data;
try {
  data = JSON.parse(localStorage.getItem(STORAGE));
} catch {
  data = null;
}
if (
  !data ||
  !["orders", "tasks", "conversations", "notes"].every((k) =>
    Array.isArray(data[k]),
  )
)
  data = clone(seed);
let session;
try {
  session = JSON.parse(sessionStorage.getItem(SESSION));
} catch {
  session = null;
}
const ui = {
  signedIn: session?.signedIn === true,
  role: Object.hasOwn(roles, session?.role) ? session.role : "manager",
  search: "",
  filter: "all",
  period: "month",
  collapsed: false,
  mobile: false,
  empty: false,
  conversation: "C1",
  month: 10,
  year: 2026,
  selected: new Set(),
  taskFilter: "all",
  designFilter: "active",
  designSearch: "",
  calendarMode: "month",
  managerTab: "alerts",
};
function saveSession() {
  try {
    sessionStorage.setItem(
      SESSION,
      JSON.stringify({ signedIn: ui.signedIn, role: ui.role }),
    );
  } catch {
    /* The preview still works when browser storage is disabled. */
  }
}
const app = document.querySelector("#app");
const dialog = document.querySelector("#dialog");
let toastTimer;
function toast(message) {
  const el = document.querySelector("#toast");
  el.textContent = message;
  el.classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("visible"), 3200);
}
function save() {
  try {
    localStorage.setItem(STORAGE, JSON.stringify(data));
  } catch {
    toast(
      "Trình duyệt không lưu được dữ liệu mẫu. Thao tác vẫn dùng được trong phiên này.",
    );
  }
}
function reviewFor(task) {
  if (!task.designReview) {
    const example = seed.tasks.find((t) => t.id === task.id && t.state === task.state)?.designReview;
    task.designReview = example ? clone(example) : {
      status: ["done", "review"].includes(task.state) ? "pending" : task.state === "revision" ? "changes_requested" : "draft", comments: [], finalFiles: [],
    };
  }
  return task.designReview;
}
function previewFor(task) {
  const review = reviewFor(task);
  return review.comments.filter((c) => c.attachment?.kind === "preview" &&
    (c.round || 0) === (review.round || 0)).at(-1)?.attachment;
}
function designTaskActions(task) {
  const review = reviewFor(task);
  if (ui.role === "design") {
    if (task.state === "done") return badge(review.status === "approved" ? "Đã duyệt" : "Đã hoàn thành · Chờ Sale duyệt", review.status === "approved" ? "success" : "blue");
    return `<button class="btn primary" data-complete-design="${escape(task.id)}">${icon("check")}Hoàn thành task</button>`;
  }
  if ((ui.role === "sale" || ui.role === "manager") && review.status === "pending")
    return `<button class="btn primary" data-approve-design="${escape(task.id)}" ${!previewFor(task) ? 'disabled title="Cần có ảnh preview để duyệt"' : ""}>${icon("check")}Duyệt thiết kế</button>`;
  return badge(taskStates[task.state], task.state === "revision" ? "danger" : "neutral");
}
function completeDesignTask(id) {
  const task = data.tasks.find((t) => t.id === id && t.type === "design");
  if (!task || ui.role !== "design" || task.state === "done") return;
  const review = reviewFor(task);
  task.state = "done";
  review.status = "pending";
  review.submittedPreviewId = previewFor(task)?.id || null;
  review.comments.push({ id: `complete-${Date.now()}`, author: "Thiết kế mẫu", role: "design", time: "Vừa xong", text: "Đã hoàn thành task. Nhờ Sale kiểm tra và duyệt bản thiết kế." });
  save();
  render();
  toast("Đã hoàn thành task. Sale có thể xem và góp ý.");
}
function approveDesignTask(id) {
  const task = data.tasks.find((t) => t.id === id && t.type === "design");
  if (!task || !["sale", "manager"].includes(ui.role)) return;
  const review = reviewFor(task), preview = previewFor(task);
  if (review.status !== "pending" || !preview) return;
  review.status = "approved";
  review.approvedPreviewId = preview.id;
  task.state = "done";
  review.comments.push({ id: `approval-${Date.now()}`, author: "Sale mẫu", role: "sale", time: "Vừa xong", text: "Đã duyệt bản preview. Thiết kế gửi file chính thức giúp mình." });
  save();
  render();
  toast("Đã duyệt. Thiết kế có thể gửi file chính thức.");
}
async function submitDesignForm(form) {
  const task = data.tasks.find((t) => t.id === form.dataset.taskId && t.type === "design");
  const role = ui.role;
  if (!task || !["design", "sale", "manager"].includes(role)) return;
  const review = reviewFor(task);
  const isFinal = form.id === "design-final-form";
  if (isFinal && (role !== "design" || review.status !== "approved")) return;
  const text = form.elements.text?.value.trim() || "";
  const file = (isFinal ? form.elements.file : form.elements.preview)?.files[0];
  if (!isFinal && !text && !file) return toast("Nhập bình luận hoặc chọn ảnh preview.");
  if (isFinal && !file) return toast("Chọn file thiết kế để gửi.");
  let attachment;
  if (file) {
    if (role !== "design" || (!isFinal && review.status === "approved")) return;
    if (file.size > 1024 * 1024) return toast("Bản mockup nhận file tối đa 1 MB để lưu trong trình duyệt.");
    if (!isFinal && !["image/png", "image/jpeg", "image/webp", "image/gif"].includes(file.type)) return toast("Chọn ảnh PNG, JPG, WebP hoặc GIF.");
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      const url = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = reject;
        reader.readAsDataURL(file);
      });
      attachment = { id: `file-${Date.now()}`, name: file.name, url, kind: isFinal ? "final" : "preview" };
    } catch {
      button.disabled = false;
      return toast("Không đọc được file. Anh chọn lại file giúp tôi.");
    }
    if (!form.isConnected || ui.role !== role) return;
  }
  if (isFinal) {
    review.finalFiles.push({ ...attachment, previewId: review.approvedPreviewId });
    review.comments.push({ id: `delivery-${Date.now()}`, author: "Thiết kế mẫu", role: "design", time: "Vừa xong", text: `Đã gửi file thiết kế chính thức: ${file.name}` });
  } else {
    review.comments.push({ id: `comment-${Date.now()}`, author: role === "design" ? "Thiết kế mẫu" : "Sale mẫu", role, time: "Vừa xong", text, ...(attachment ? { attachment } : {}), round: review.round || 0 });
    if (role === "sale" || role === "manager") {
      if (review.status !== "changes_requested") review.round = (review.round || 0) + 1;
      task.state = "revision";
      review.status = "changes_requested";
      review.approvedPreviewId = null;
      review.submittedPreviewId = null;
    } else if (attachment && review.status === "pending") review.submittedPreviewId = attachment.id;
  }
  save();
  render();
  toast(isFinal ? "Đã lưu file thiết kế trong bản mockup." : role === "design" ? "Đã gửi bình luận / ảnh preview." : "Đã gửi góp ý. Task chuyển sang Cần chỉnh sửa.");
}
function route() {
  const [raw, id] = location.hash.slice(1).split("/");
  const screen = screens[raw] ? raw : "login";
  return { screen: !ui.signedIn && screen !== "login" ? "login" : screen, id };
}
function enterRole(role) {
  if (!ui.signedIn || !Object.hasOwn(roleViews, role)) return;
  ui.role = role;
  ui.selected.clear();
  ui.taskFilter = "all";
  ui.designFilter = "active";
  ui.designSearch = "";
  ui.empty = false;
  dialog.close();
  saveSession();
  go(roleViews[role].home);
}
function loginPage() {
  return `<main id="main" class="access-page" tabindex="-1">
    <section class="login-card" aria-labelledby="login-heading">
      <a class="access-brand" href="#login" aria-label="Duy An ERP"><img src="./assets/dac-logo.png" alt="DAC Since 2000"></a>
      <div class="login-intro"><span class="access-eyebrow">GIAO DIỆN MOCKUP</span><h1 id="login-heading">Đăng nhập</h1><p>Đăng nhập để chọn giao diện theo vai trò.</p></div>
      <form id="login-form" autocomplete="off">
        <label for="login-email">Email<input id="login-email" name="email" type="email" value="${DEMO_EMAIL}" placeholder="Email" required autocomplete="off" spellcheck="false"></label>
        <label for="login-password">Mật khẩu<input id="login-password" name="password" type="password" value="${DEMO_PASSWORD}" placeholder="Mật khẩu" required autocomplete="off"></label>
        <p id="login-error" class="login-error" role="alert" hidden></p>
        <button type="submit" class="btn login-submit">Đăng nhập</button>
      </form>
      <p class="demo-account">Tài khoản mẫu đã điền sẵn.<br><strong>${DEMO_EMAIL}</strong> · Mật khẩu: <strong>${DEMO_PASSWORD}</strong></p>
      <footer class="access-footer">DAC ERP <span>·</span> Bản duyệt giao diện</footer>
    </section>
  </main>`;
}
function rolesPage() {
  return `<main id="main" class="access-page role-access-page" tabindex="-1">
    <section class="role-picker" aria-labelledby="role-heading">
      <header class="role-picker-header"><img src="./assets/dac-logo.png" alt="DAC Since 2000"><span class="access-eyebrow">GIAO DIỆN MOCKUP</span></header>
      <div class="access-steps"><span>${icon("check")} Đã đăng nhập</span><i></i><strong>Chọn vai trò</strong></div>
      <h1 id="role-heading">Anh muốn xem giao diện nào?</h1><p class="role-picker-copy">Chọn một vai trò để mở không gian làm việc tương ứng.</p>
      <div class="role-grid">${Object.entries(roles)
        .map(
          ([key, label]) =>
            `<button type="button" class="role-card" data-enter-role="${key}" aria-label="Vào giao diện ${label}"><span class="role-card-icon ${key}">${icon(roleViews[key].icon)}</span><h2>${label}</h2><p>${escape(roleViews[key].description)}</p><span class="role-card-link">Vào giao diện ${icon("next")}</span></button>`,
        )
        .join("")}</div>
      <footer class="role-picker-footer"><button type="button" class="text-button" data-action="logout">${icon("back")} Quay lại đăng nhập</button><small>Có thể đổi vai trò trong lúc xem mockup.</small></footer>
    </section>
  </main>`;
}
function go(screen, id) {
  ui.search = "";
  ui.filter = "all";
  ui.mobile = false;
  const hash = "#" + screen + (id ? "/" + encodeURIComponent(id) : "");
  if (location.hash !== hash) history.pushState(null, "", hash);
  render();
  window.scrollTo(0, 0);
}
function orderById(id) {
  try {
    return (
      data.orders.find((o) => o.id === decodeURIComponent(id || "")) ||
      data.orders[0]
    );
  } catch {
    return data.orders[0];
  }
}
const badge = (label, type = "neutral") =>
  `<span class="badge ${type}"><span class="badge-dot"></span>${escape(label)}</span>`;
const stateBadge = (state) => badge(states[state], state);
const avatar = (name, type = "") =>
  `<span class="avatar ${type}">${escape(name?.slice(0, 1) || "D")}</span>`;
const btn = (label, action, glyph = "", primary = false, extra = "") =>
  `<button type="button" class="btn ${primary ? "primary" : ""}" data-action="${action}" ${extra}>${glyph ? icon(glyph) : ""}${escape(label)}</button>`;
const navBtn = (key, label) =>
  `<button type="button" class="nav-item ${route().screen === key || (key === "orders" && route().screen === "order") || (key === "design" && route().screen === "task") ? "active" : ""}" data-route="${key}" ${route().screen === key ? 'aria-current="page"' : ""}>${icon(screens[key].icon)}<span>${escape(label || screens[key].title)}</span></button>`;
function title(
  name,
  subtitle = "",
  actions = "",
  breadcrumb = "DAC Sản xuất & Vận hành",
) {
  return `<header class="page-heading"><div><div class="breadcrumb">${escape(breadcrumb)}</div><h1>${escape(name)}</h1>${subtitle ? `<p>${escape(subtitle)}</p>` : ""}</div><div class="actions">${actions}</div></header>`;
}
function panel(name, subtitle, body, cls = "", actions = "") {
  return `<section class="panel ${cls}"><header class="panel-heading"><div><h2>${name}</h2>${subtitle ? `<p>${escape(subtitle)}</p>` : ""}</div>${actions}</header>${body}</section>`;
}
function empty(heading, copy, glyph = "factory") {
  return `<div class="empty">${icon(glyph)}<strong>${escape(heading)}</strong><p>${escape(copy)}</p></div>`;
}
function kpi(label, value, hint, type = "", action = "") {
  return `<${action ? 'button type="button"' : "div"} class="kpi ${type}" ${action ? `data-action="${action}"` : ""}><span>${escape(label)}</span><strong>${escape(value)}</strong><small>${escape(hint)}</small></${action ? "button" : "div"}>`;
}
function filteredOrders() {
  const query = ui.search.toLocaleLowerCase("vi");
  return (ui.empty ? [] : data.orders).filter(
    (o) =>
      (ui.filter === "all" || o.state === ui.filter) &&
      (!query ||
        [o.id, o.customer, o.product, o.sale]
          .join(" ")
          .toLocaleLowerCase("vi")
          .includes(query)),
  );
}
function periodOrders() {
  return (ui.empty ? [] : data.orders).filter((o) => {
    const [date, month, year] = o.date.split(" ")[0].split("/");
    const dateISO = `${year}-${month}-${date}`;
    return ui.period === "today"
      ? dateISO === today
      : ui.period === "week"
        ? dateISO >= "2026-11-02" && dateISO <= "2026-11-08"
        : ui.period === "last"
          ? dateISO.startsWith("2026-10")
          : dateISO.startsWith("2026-11");
  });
}
function visibleTasks() {
  return (ui.empty ? [] : data.tasks).filter(
    (t) =>
      ((ui.role !== "design" && ui.role !== "production") ||
        t.type === ui.role) &&
      (ui.taskFilter === "all" || t.state === ui.taskFilter),
  );
}
function sidebar() {
  const current = route().screen;
  const group = ["sales", "manager", "conversations"].includes(current)
    ? "sales"
    : current === "attendance"
      ? "attendance"
      : current === "payments"
        ? "payments"
        : "operations";
  const rail = [
    { key: "sales", route: "sales", name: "Bán hàng", icon: "chart" },
    {
      key: "operations",
      route: ui.role === "production" ? "production" : "design",
      name: "Vận hành",
      icon: "box",
    },
    { key: "attendance", route: "attendance", name: "Chấm công", icon: "user" },
    { key: "payments", route: "payments", name: "Kế toán", icon: "wallet" },
  ];
  const nav =
    group === "sales"
      ? `<h3>Bán hàng & CRM</h3><div class="nav-label">TỔNG QUAN</div>${navBtn("sales")}${ui.role === "manager" ? navBtn("manager") : ""}<div class="nav-label">KHÁCH HÀNG</div>${navBtn("conversations")}${navBtn("orders")}`
      : group === "operations"
        ? `<h3>${ui.role === "design" ? "Thiết kế" : ui.role === "production" ? "Sản xuất" : "Vận Hành"}</h3><div class="nav-label">ĐIỀU PHỐI</div>${ui.role !== "production" ? navBtn("design") : ""}${ui.role !== "design" ? navBtn("production") : ""}${navBtn("orders")}<div class="nav-label">LỊCH LÀM VIỆC</div>${navBtn("tasks")}${navBtn("calendar", ui.role === "production" ? "Lịch sản xuất" : "Lịch thiết kế")}`
        : group === "attendance"
          ? `<h3>Chấm công</h3><div class="nav-label">NHÂN SỰ</div>${navBtn("attendance", "Chấm công của tôi")}`
          : `<h3>Kế toán</h3><div class="nav-label">THU CHI</div>${navBtn("payments")}${navBtn("orders")}`;
  return `<aside class="sidebar ${ui.collapsed ? "collapsed" : ""} ${ui.mobile ? "mobile-open" : ""}" aria-label="Điều hướng chính"><div class="brand"><img src="./assets/dac-logo.png" alt="DAC Since 2000"><div><strong>Duy An ERP</strong><small>ERP / CRM · v6</small></div><button class="icon-btn collapse" data-action="collapse" aria-label="Thu gọn menu">${icon("menu")}</button></div><div class="sidebar-tools"><button class="sidebar-search" data-action="search">${icon("search")}<span>Tìm kiếm</span><kbd>Ctrl K</kbd></button><button class="icon-btn" data-action="refresh" aria-label="Làm mới">${icon("refresh")}</button><button class="icon-btn" data-route="calendar" aria-label="Lịch làm việc">${icon("clock")}</button></div><div class="sidebar-body"><nav class="rail" aria-label="Ứng dụng">${rail.map((r) => `<button class="rail-item ${group === r.key ? "active" : ""}" data-route="${r.route}" title="${r.name}">${icon(r.icon)}<span>${r.name}</span></button>`).join("")}</nav><nav class="nav-tree" aria-label="Chức năng">${nav}</nav></div><button class="sidebar-expand icon-btn" data-action="collapse" aria-label="Mở rộng menu">${icon("next")}</button><footer class="sidebar-footer"><div class="attendance-mini ${data.checkedIn ? "" : "out"}"><div><strong><i></i>${data.checkedIn ? "ĐANG LÀM VIỆC" : "SẴN SÀNG CHẤM CÔNG"}</strong><small>${data.checkedIn ? "07:53 · Đang trong ca làm việc" : "Thao tác chấm công mẫu"}</small></div><button data-action="checkin">${data.checkedIn ? "Check out" : "Check in"}</button></div><div class="profile">${avatar(roles[ui.role])}<div><strong>${ui.role === "manager" ? "Duy An" : roles[ui.role] + " mẫu"}</strong><small>${escape(roles[ui.role])}</small></div><button class="icon-btn" data-action="profile" aria-label="Thông tin phiên mẫu">${icon("settings")}</button></div></footer></aside>`;
}
function toolbar() {
  return `<div class="mockup-toolbar"><div class="mockup-label"><span></span><strong>MOCKUP</strong><span class="toolbar-copy">Dữ liệu mẫu</span></div><label class="toolbar-screen"><span>Màn hình</span><select id="screen-select" aria-label="Chọn màn hình">${Object.entries(
    screens,
  )
    .filter(([k]) => !["order", "task", "login", "roles"].includes(k))
    .map(
      ([k, s]) =>
        `<option value="${k}" ${route().screen === k || (route().screen === "order" && k === "orders") || (route().screen === "task" && k === "design") ? "selected" : ""}>${escape(s.title)}</option>`,
    )
    .join(
      "",
    )}</select></label><label class="toolbar-role"><span>Vai trò</span><select id="role-select" aria-label="Chọn vai trò">${Object.entries(
    roles,
  )
    .map(
      ([k, v]) =>
        `<option value="${k}" ${ui.role === k ? "selected" : ""}>${v}</option>`,
    )
    .join(
      "",
    )}</select></label><button class="toolbar-button ${ui.empty ? "selected" : ""}" data-action="empty">${ui.empty ? "Hiện dữ liệu" : "Xem trạng thái trống"}</button><button class="toolbar-button" data-action="handoff">${icon("file")}<span>Bàn giao</span></button><button class="toolbar-button logout-button" data-action="logout" aria-label="Đăng xuất" title="Đăng xuất">${icon("back")}<span>Đăng xuất</span></button></div>`;
}
function dashboard(type) {
  if (type === "design") return designDashboard();
  const design = type === "design";
  const orders = (ui.empty ? [] : data.orders).filter((o) => o.state === type);
  const tasks = (ui.empty ? [] : data.tasks).filter((t) => t.type === type);
  const active = tasks.filter((t) => t.state !== "done");
  const late = active.filter((t) => t.deadline < today).length;
  const soon = active.filter(
    (t) => t.deadline >= today && t.deadline <= "2026-11-07",
  ).length;
  const stats = `<div class="kpi-grid">${kpi(design ? "Tổng việc" : "Đơn trong xưởng", design ? tasks.length : orders.length, `${tasks.filter((t) => t.state === "doing").length} đang xử lý`)}${kpi("Quá hạn", late, design ? "Cần ưu tiên" : "Cần can thiệp", "danger", "overdue")}${kpi(design ? "Sắp đến hạn" : "Sắp hoàn thành", soon, "Trong 48 giờ")}${kpi(design ? "Hoàn thành tháng" : "Đúng hạn tháng", design ? tasks.filter((t) => t.state === "done").length : "—", design ? "Trong tháng 11" : "Chưa có đơn hoàn thành", "success")}</div>`;
  const list = orders.length
    ? `<div class="priority-list">${orders
        .sort((a, b) => a.deadline.localeCompare(b.deadline))
        .map(
          (o, i) =>
            `<button class="priority-row" data-order="${escape(o.id)}"><span class="priority-number ${o.deadline < today ? "late" : ""}">${o.deadline < today ? "!" : i + 1}</span><span><strong>${escape(o.id)}</strong><small>${o.deadline < today ? "Quá hạn" : "Deadline " + day(o.deadline)} · còn ${tasks.filter((t) => t.order === o.id && t.state !== "done").length} task</small></span>${icon("next")}</button>`,
        )
        .join("")}</div>`
    : empty(
        design ? "Chưa có việc thiết kế" : "Chưa có đơn trong xưởng",
        design
          ? "Công việc được giao sẽ xuất hiện tại đây."
          : "Đơn chuyển sang trạng thái Sản xuất sẽ xuất hiện tại đây.",
      );
  const count = (key) => tasks.filter((t) => t.state === key).length;
  const percent = (key) =>
    tasks.length ? (count(key) / tasks.length) * 100 : 0;
  const reviewEnd = percent("review");
  const doingEnd = reviewEnd + percent("doing");
  const todoEnd = doingEnd + percent("todo");
  const donutBackground = tasks.length
    ? `conic-gradient(#4e80ed 0 ${reviewEnd}%, #f9c741 ${reviewEnd}% ${doingEnd}%, #f6a52c ${doingEnd}% ${todoEnd}%, #53ac7c ${todoEnd}% 100%)`
    : "#e9edf4";
  const chart = design
    ? `<div class="distribution"><div class="donut" style="background:${donutBackground}"><div><strong>${tasks.length}</strong><small>task</small></div></div><div class="legend">${[
        ["doing", "Đang thiết kế", "yellow"],
        ["review", "Chờ duyệt", "blue"],
        ["todo", "Chưa thực hiện", "orange"],
        ["done", "Sẵn sàng SX", "green"],
      ]
        .map(
          ([key, label, color]) =>
            `<div><span><i class="legend-dot ${color}"></i>${label}</span><b>${count(key)}</b></div>`,
        )
        .join("")}</div></div>`
    : orders.length
      ? `<div class="workloads">${["Tổ sản xuất A", "Tổ sản xuất B"]
          .map((name) => {
            const total = orders.filter((o) => o.production === name).length;
            return `<div><div><strong>${name}</strong><span>${total} đơn</span></div><div class="progress"><i style="width:${Math.min(total * 35, 100)}%"></i></div></div>`;
          })
          .join("")}</div>`
      : empty(
          "Chưa có dữ liệu tải xưởng",
          "Phân công nhân sự để theo dõi tiến độ theo tổ.",
          "chart",
        );
  return (
    title(
      `Dashboard ${design ? "Thiết kế" : "Sản xuất"}`,
      design
        ? "Góc nhìn vai trò Thiết kế: deadline, ưu tiên, và task cần hoàn thành."
        : "Góc nhìn vai trò Sản xuất: đơn trong xưởng, tiến độ, deadline và việc bị chậm.",
      btn(design ? "Lịch thiết kế" : "Lịch sản xuất", "calendar", "calendar") +
        btn(
          design ? "Task thiết kế" : "Cập nhật tiến độ",
          design ? "tasks" : "refresh",
          design ? "plus" : "refresh",
          true,
        ),
      `DAC Sản xuất & Vận hành / ${design ? "Thiết kế" : "Sản xuất"}`,
    ) +
    stats +
    `<div class="dashboard-columns">${panel(design ? "Việc thiết kế ưu tiên" : "Đơn đang sản xuất", design ? "Bấm đơn để mở trang chi tiết." : "Xếp theo deadline gần nhất.", list)}${panel(design ? "Phân bổ trạng thái" : "Tải xưởng", design ? `${tasks.length} task thiết kế trong tháng` : "Tiến độ theo tổ sản xuất", chart)}</div>`
  );
}
function taskDeadline(task) {
  if (!task.deadline) return badge("Chưa có hạn");
  if (task.state === "done") return badge("Đã hoàn thành", "success");
  if (task.deadline < today) return badge("Quá hạn", "danger");
  if (task.deadline === today) return badge("Đến hạn hôm nay", "yellow");
  return "";
}
function designDashboard() {
  const tasks = (ui.empty ? [] : data.tasks).filter((t) => t.type === "design");
  const active = tasks.filter((t) => t.state !== "done");
  const count = (key) => tasks.filter((t) => key === "review" ? reviewFor(t).status === "pending" : t.state === key).length;
  const overdue = (t) => t.state !== "done" && t.deadline && t.deadline < today;
  const query = ui.designSearch.trim().toLocaleLowerCase("vi");
  const list = tasks.filter((t) => {
    const order = data.orders.find((o) => o.id === t.order);
    const match = ui.designFilter === "all" ||
      (ui.designFilter === "active" ? t.state !== "done" :
        ui.designFilter === "overdue" ? overdue(t) : ui.designFilter === "review" ? reviewFor(t).status === "pending" : t.state === ui.designFilter);
    return match && (!query || [t.name, t.order, order?.customer, order?.product]
      .join(" ").toLocaleLowerCase("vi").includes(query));
  }).sort((a, b) => Number(a.state === "done") - Number(b.state === "done") ||
    (a.deadline || "9999").localeCompare(b.deadline || "9999"));
  const metric = (label, value, hint, key, color = "") =>
    `<button class="kpi ${color} ${ui.designFilter === key ? "design-kpi-active" : ""}" data-design-filter="${key}" aria-pressed="${ui.designFilter === key}"><span>${label}</span><strong>${value}</strong><small>${hint}</small></button>`;
  const filters = [["active", "Cần làm", active.length], ["todo", "Chưa thực hiện", count("todo")],
    ["doing", "Đang làm", count("doing")], ["revision", "Cần chỉnh sửa", count("revision")], ["review", "Chờ duyệt", count("review")],
    ["done", "Hoàn thành", count("done")], ["all", "Tất cả", tasks.length]];
  const rows = list.map((t) => {
    const o = data.orders.find((order) => order.id === t.order);
    return `<button class="design-task-row ${overdue(t) ? "is-overdue" : ""}" data-task="${escape(t.id)}">
      <span class="design-task-icon">${icon("pen")}</span>
      <span class="design-task-copy"><strong>${escape(t.name)}</strong><span>${escape(t.order)} · ${escape(o?.product || "Chưa có sản phẩm")}</span><small>${icon("user")}${escape(o?.customer || "Chưa có khách hàng")}</small></span>
      <span class="design-task-status">${badge(taskStates[t.state], t.state === "revision" ? "danger" : t.state === "review" ? "blue" : t.state === "done" ? "success" : "neutral")}</span>
      <span class="design-task-deadline"><strong>${day(t.deadline)}</strong>${taskDeadline(t)}</span>${icon("next")}</button>`;
  }).join("");
  const revisionTasks = tasks.filter((t) => t.state === "revision");
  const revisions = panel(`${icon("pen")}Thiết kế cần chỉnh sửa`, "Task tự chuyển vào đây khi Sale gửi góp ý.",
    revisionTasks.map((t) => {
      const o = data.orders.find((order) => order.id === t.order);
      const feedback = reviewFor(t).comments.filter((c) => ["sale", "manager"].includes(c.role)).at(-1);
      return `<button class="design-revision-row" data-task="${escape(t.id)}"><div><strong>${escape(t.name)}</strong><small>${escape(t.order)} · ${escape(o?.customer)}</small><p>${escape(feedback?.text || "Sale có yêu cầu chỉnh sửa thiết kế.")}</p></div>${badge("Cần chỉnh sửa", "danger")}${icon("next")}</button>`;
    }).join("") || '<p class="design-revision-empty">Hiện không có task cần chỉnh sửa.</p>', "design-revision-panel", badge(`${revisionTasks.length} task`, revisionTasks.length ? "danger" : "neutral"));
  return title("Công việc thiết kế", "Các task được giao cho bạn, ưu tiên hạn hoàn thành gần nhất.",
    btn("Lịch thiết kế", "calendar", "calendar"), "DAC / Nhân viên Thiết kế") +
    `<div class="kpi-grid design-kpis">${metric("Cần làm", active.length, "Task chưa hoàn thành", "active")}${metric("Quá hạn", tasks.filter(overdue).length, "Cần ưu tiên xử lý", "overdue", "danger")}${metric("Chờ duyệt", count("review"), "Đã gửi bản thiết kế", "review")}${metric("Hoàn thành", count("done"), "Task đã hoàn tất", "done", "success")}${metric("Cần chỉnh sửa", count("revision"), "Sale có góp ý mới", "revision", "danger")}</div>` +
    revisions + panel("Task thiết kế của tôi", "Bấm vào task để xem yêu cầu và thông tin đơn hàng.",
      `<div class="design-list-tools"><div class="design-filters" role="group" aria-label="Lọc task thiết kế">${filters.map(([key, label, total]) => `<button data-design-filter="${key}" class="${ui.designFilter === key ? "active" : ""}" aria-pressed="${ui.designFilter === key}">${label}<span>${total}</span></button>`).join("")}</div>
      <label class="design-search">${icon("search")}<input id="design-search" type="search" placeholder="Tìm task, mã đơn, khách hàng..." aria-label="Tìm task thiết kế" value="${escape(ui.designSearch)}"></label></div>
      <div class="design-list-heading"><span>Công việc / Khách hàng</span><span>Trạng thái</span><span>Hạn hoàn thành</span></div>
      ${rows || empty(tasks.length ? "Không có task phù hợp" : "Chưa có task thiết kế", tasks.length ? "Thử đổi từ khóa hoặc bộ lọc." : "Task được giao sẽ xuất hiện tại đây.", "pen")}
      <footer class="design-list-footer">${list.length} / ${tasks.length} task</footer>`, "design-task-panel");
}
function designBrief(o) {
  return panel(`${icon("file")}Mô tả thiết kế`, "",
    `<div class="design-brief-body">${escape(o.description?.trim() || "Chưa có mô tả thiết kế. Liên hệ Sale phụ trách để bổ sung yêu cầu.")}</div>`, "design-brief");
}
function orderLines(o) {
  if (Array.isArray(o.lines)) return o.lines;
  const example = seed.orders.find((item) => item.id === o.id);
  if (example?.lines && ["product", "material", "size", "quantity"].every((key) => o[key] === example[key])) return example.lines;
  return [{ product: o.product, description: o.description, material: o.material, size: o.size, quantity: o.quantity }];
}
function designOrderInfo(o) {
  const info = (label, value) => `<div><dt>${label}</dt><dd>${escape(value ?? "Chưa có thông tin")}</dd></div>`;
  const lines = orderLines(o);
  return panel(`${icon("box")}Thông tin đơn hàng`, "",
    `<dl class="design-order-fields">${info("Mã đơn hàng", o.id)}${info("Tên khách hàng", o.customer)}${info("Sale phụ trách", o.sale)}${info("Hạn hoàn thành đơn", day(o.deadline))}</dl>
    <div class="design-lines"><h3>Các dòng sản phẩm <span>${lines.length}</span></h3><div class="table-wrap"><table><thead><tr><th>Sản phẩm / Nội dung</th><th>Chất liệu</th><th>Kích thước</th><th>Số lượng</th></tr></thead><tbody>${lines.map((line) => `<tr><td><strong>${escape(line.product)}</strong><p>${escape(line.description || "")}</p></td><td>${escape(line.material)}</td><td>${escape(line.size)}</td><td>${escape(line.quantity)}</td></tr>`).join("") || '<tr><td colspan="4">Chưa có dòng sản phẩm.</td></tr>'}</tbody></table></div></div>`, "design-order-info", stateBadge(o.state));
}
function designReviewPanel(task) {
  const review = reviewFor(task);
  const status = { draft: "Chưa gửi duyệt", pending: "Chờ Sale duyệt", approved: "Sale đã duyệt", changes_requested: "Cần chỉnh sửa" };
  const canReview = ui.role === "sale" || ui.role === "manager";
  const canUpload = ui.role === "design" && review.status !== "approved";
  const comments = review.comments.map((comment) => {
    const file = comment.attachment;
    const previewURL = file?.kind === "preview" && (/^data:image\/(png|jpeg|webp|gif);base64,/.test(file.url || "") || file.url === "./assets/design-preview-demo.svg") ? file.url : null;
    return `<article class="design-comment">${avatar(comment.author, comment.role === "sale" ? "teal" : "")}<div><header><strong>${escape(comment.author)}</strong><small>${escape(comment.time)}</small></header>${comment.text ? `<p>${escape(comment.text)}</p>` : ""}${previewURL ? `<a class="design-preview" href="${escape(previewURL)}" target="_blank" rel="noopener"><img src="${escape(previewURL)}" alt="${escape(file.name)}"><span>${icon("file")}${escape(file.name)} · Xem ảnh</span></a>` : ""}</div></article>`;
  }).join("");
  return panel(`${icon("chat")}Trao đổi & duyệt thiết kế`, "Gửi ảnh preview để Sale kiểm tra trước khi bàn giao file thiết kế.",
    `<ol class="design-review-steps"><li class="${previewFor(task) ? "complete" : ""}">1. Gửi ảnh preview</li><li class="${review.status === "approved" ? "complete" : ""}">2. Sale duyệt</li><li class="${review.status === "approved" && review.finalFiles.some((f) => f.previewId === review.approvedPreviewId) ? "complete" : ""}">3. Gửi file thiết kế</li></ol>
    <div class="design-review-status">${badge(status[review.status], review.status === "changes_requested" ? "danger" : review.status === "approved" ? "success" : "blue")}${canReview ? designTaskActions(task) : ""}</div>
    ${review.status === "pending" && !previewFor(task) ? '<p class="design-awaiting-preview">Chưa có ảnh preview của lần thiết kế này. Thiết kế bổ sung ảnh để Sale duyệt.</p>' : ""}
    <div class="design-comment-list">${comments || '<p class="design-no-comments">Chưa có trao đổi. Gửi ảnh preview đầu tiên tại đây.</p>'}</div>
    <form id="design-comment-form" data-task-id="${escape(task.id)}" class="design-comment-form"><label for="design-comment">${canReview ? "Góp ý cho Thiết kế" : "Bình luận"}<textarea id="design-comment" name="text" placeholder="${canReview ? "Nhập góp ý; task sẽ tự chuyển sang Cần chỉnh sửa..." : "Nhập nội dung gửi cho Sale..."}"></textarea></label>
    ${canUpload ? '<label class="design-file-label" for="preview-file">Ảnh preview thiết kế<input id="preview-file" name="preview" type="file" accept="image/png,image/jpeg,image/webp,image/gif"><small>PNG, JPG, WebP, GIF · Tối đa 1 MB/ảnh trong bản mockup.</small></label>' : ""}
    <div class="design-comment-submit"><small>${canReview ? "Gửi góp ý sẽ đưa task về Cần chỉnh sửa." : "Gửi ảnh preview, sau đó bấm Hoàn thành task."}</small><button class="btn" type="submit">${icon("send")}Gửi ${canReview ? "góp ý" : "bình luận / preview"}</button></div></form>
    <section class="design-final-files"><div><h3>${icon("file")}File thiết kế chính thức</h3><p>${review.status === "approved" ? "Bản preview đã được Sale duyệt. Có thể gửi file thiết kế." : "Mở sau khi Sale duyệt ảnh preview."}</p></div>
    ${review.finalFiles.map((file) => `<a class="design-file-download" href="${escape(file.url)}" download="${escape(file.name)}">${icon("file")}${escape(file.name)}${file.previewId !== review.approvedPreviewId ? '<small>Phiên bản trước</small>' : ""}</a>`).join("")}
    ${review.status === "approved" && ui.role === "design" ? `<form id="design-final-form" data-task-id="${escape(task.id)}"><label for="final-file">Chọn file thiết kế<input id="final-file" name="file" type="file" required><small>Tối đa 1 MB/file trong bản mockup.</small></label><button class="btn primary" type="submit">${icon("send")}Gửi file thiết kế</button></form>` : ""}</section>`, "design-review-panel");
}
function designTaskPage() {
  const task = data.tasks.find((t) => t.id === route().id && t.type === "design");
  const o = task && data.orders.find((order) => order.id === task.order);
  if (!task || !o) return title("Không tìm thấy task thiết kế", "Task hoặc đơn hàng liên quan không còn tồn tại.", btn("Về công việc thiết kế", "design-home", "back"));
  return `<div class="design-detail"><button class="design-back" data-route="${ui.role === "design" ? "design" : "orders"}">${icon("back")}${ui.role === "design" ? "Công việc thiết kế" : "Danh sách đơn hàng"}</button>
    ${title(task.name, `${o.id} · ${o.customer}`, "", "DAC / Thiết kế / Chi tiết task")}
    <section class="design-task-summary"><div>${badge(taskStates[task.state], task.state === "revision" ? "danger" : task.state === "done" ? "success" : "neutral")}<span>${icon("calendar")}Hạn task: <strong>${day(task.deadline)}</strong></span></div>${ui.role === "design" ? designTaskActions(task) : ""}</section>
    ${designBrief(o)}${designReviewPanel(task)}${designOrderInfo(o)}</div>`;
}
function salesDashboard(manager = false) {
  const orders = periodOrders();
  const completed = orders.filter((o) => o.state === "done");
  const total = completed.reduce((s, o) => s + o.total, 0);
  const debt = orders.reduce((s, o) => s + o.total - o.paid, 0);
  const chats = ui.empty ? [] : data.conversations;
  const tickets = (items) =>
    items.length
      ? `<div class="ticket-list">${items.map((o) => `<button class="ticket ${o.deadline < today ? "late" : ""}" data-order="${escape(o.id)}"><div><strong>${escape(o.customer)}</strong>${o.priority !== "normal" ? badge(priorities[o.priority], o.priority === "urgent" ? "danger" : "blue") : ""}</div><p>Số ĐH: <b>${escape(o.id)}</b></p><small>${stateBadge(o.state)} <span>Hạn ${day(o.deadline)}</span></small></button>`).join("")}</div>`
      : empty(
          "Không có dữ liệu",
          "Đơn hàng phù hợp sẽ xuất hiện tại đây.",
          "cart",
        );
  return (
    title(
      manager ? "Dashboard Quản lý" : "Dashboard Bán hàng",
      "Tổng quan doanh thu, hội thoại và tiến độ đơn hàng.",
      btn("Làm mới", "refresh", "refresh"),
      "DAC Bán Hàng & CRM",
    ) +
    `<div class="periods">${[
      ["today", "Hôm nay"],
      ["week", "Tuần này"],
      ["month", "Tháng này"],
      ["last", "Tháng trước"],
    ]
      .map(
        ([key, label]) =>
          `<button class="pill ${ui.period === key ? "active" : ""}" data-period="${key}">${label}</button>`,
      )
      .join(
        "",
      )}<span class="period-note">Kỳ dữ liệu mẫu: 11/2026</span></div><div class="kpi-grid revenue">${kpi("DOANH THU KỲ NÀY", money(total), "Mục tiêu mẫu: " + money(150000000))}${kpi("ĐƠN ĐÃ CHỐT", orders.filter((o) => o.state !== "quote").length, "Đơn mẫu trong kỳ")}${kpi("DỰ KIẾN", money(orders.filter((o) => o.state === "quote").reduce((s, o) => s + o.total, 0)), "Báo giá đang mở")}${kpi("CÔNG NỢ", money(debt), "Số tiền còn phải thu")}</div><div class="urgent-strip"><strong>ϟ Cần làm ngay</strong><button data-route="conversations">${badge(`${chats.filter((c) => c.unread).length} tin cần trả lời`, "danger")}</button><button data-filter="quote">${badge(`${orders.filter((o) => o.state === "quote").length} báo giá cần chốt`, "blue")}</button><button data-route="production">${badge(`${orders.filter((o) => o.state === "production").length} đơn sản xuất`, "yellow")}</button></div><div class="sales-columns">${panel(`TIN NHẮN CẦN XỬ LÝ (${chats.length})`, "", `<div class="ticket-list">${chats.map((c) => `<button class="ticket" data-conversation="${c.id}"><div><strong>${escape(c.customer)}</strong>${icon("chat")}</div><p class="text-danger">${escape(c.tag)}</p><small>${escape(c.messages.at(-1)?.text)}</small></button>`).join("") || empty("Không có dữ liệu", "Hội thoại mẫu sẽ hiện tại đây.", "chat")}</div>`)}${panel(`BÁO GIÁ CẦN CHỐT (${orders.filter((o) => o.state === "quote").length})`, "", tickets(orders.filter((o) => o.state === "quote")))}${panel(`ĐƠN ĐANG THEO DÕI (${orders.filter((o) => !["quote", "done"].includes(o.state)).length})`, "", tickets(orders.filter((o) => !["quote", "done"].includes(o.state))))}</div>`
  );
}
function managerDashboard() {
  const orders = periodOrders();
  const late = orders.filter((o) => o.deadline < today && o.state !== "done");
  const sum = (items) => items.reduce((n, o) => n + o.total, 0);
  const stage = (key, label) => {
    const items = orders.filter((o) => o.state === key);
    return `<button class="pipeline-stage ${key}" data-filter="${key}"><span>${label}</span><strong>${items.length}</strong><small>~ ${money(sum(items))}</small></button>`;
  };
  const alertBody =
    ui.managerTab === "alerts"
      ? late
          .map(
            (o) =>
              `<button class="manager-alert" data-order="${escape(o.id)}">${badge("Quá hạn", "danger")}<span><strong>${escape(o.id)}</strong><small>${escape(o.customer)}</small></span>${icon("next")}</button>`,
          )
          .join("") ||
        empty(
          "Không có cảnh báo nào",
          "Đơn chậm hoặc nghẽn luồng sẽ hiện tại đây.",
          "check",
        )
      : `<div class="workloads"><div><div><strong>Kinh doanh · Sale mẫu</strong><span>${orders.length} đơn hàng</span></div><div class="progress"><i style="width:${Math.min(orders.length * 10, 100)}%"></i></div></div><div><div><strong>Thiết kế</strong><span>${orders.filter((o) => o.state === "design").length} đơn</span></div></div><div><div><strong>Sản xuất</strong><span>${orders.filter((o) => o.state === "production").length} đơn</span></div></div></div>`;
  return (
    title(
      "Điều phối CRM",
      "Ưu tiên việc cần can thiệp, đơn chậm và công nợ trong ngày",
      `<button class="btn ${ui.period === "week" ? "primary" : ""}" data-period="week">Tuần này</button><button class="btn ${ui.period === "month" ? "primary" : ""}" data-period="month">Tháng 11 / 2026</button>${btn("Xuất báo cáo", "export-orders", "file", true)}`,
      "DAC Bán Hàng & CRM / Quản lý",
    ) +
    `<div class="kpi-grid manager-kpis">${kpi("Việc cần can thiệp", late.length, "Cảnh báo và nghẽn luồng", "danger")}${kpi("Công nợ cần thu", money(orders.reduce((n, o) => n + o.total - o.paid, 0)), "Chưa thanh toán")}${kpi("Đơn đang theo dõi", orders.filter((o) => o.state !== "done").length, "Chưa hoàn thành")}${kpi("Doanh thu đã chốt", money(sum(orders.filter((o) => o.state === "done"))), "Đơn hoàn thành trong kỳ", "success")}</div><div class="dashboard-columns manager-columns">${panel("Đơn hàng theo trạng thái", "", `<div class="pipeline"><h3>Đầu phễu</h3><div class="pipeline-row">${stage("quote", "Báo giá")}${stage("design", "Đặt cọc")}</div><h3>Thực thi</h3><div class="pipeline-execution">${stage("production", "Sản xuất")}<div>${stage("installation", "Thi công")}${stage("delivery", "Giao hàng")}</div></div><h3>Hoàn tất</h3><div class="pipeline-row">${stage("payment", "Thu tiền")}${stage("done", "Hoàn thành")}</div></div>`)}${panel("Việc cần can thiệp hôm nay", "", `<div class="manager-tabs"><button data-manager-tab="alerts" class="${ui.managerTab === "alerts" ? "active" : ""}">Đơn chậm / nghẽn ${late.length}</button><button data-manager-tab="performance" class="${ui.managerTab === "performance" ? "active" : ""}">Hiệu suất</button></div>${alertBody}`)}</div>`
  );
}
function ordersPage() {
  const orders = filteredOrders();
  return `<div class="list-control"><div class="list-title"><h1>Đơn hàng${ui.role === "design" ? " - Thiết kế" : ui.role === "production" ? " - Sản xuất" : ""}</h1>${icon("settings")}</div><div class="list-search">${icon("search")}<input id="order-search" type="search" placeholder="Tìm kiếm..." value="${escape(ui.search)}" aria-label="Tìm đơn hàng"><select id="order-filter" aria-label="Lọc trạng thái"><option value="all">Tất cả trạng thái</option>${Object.entries(
    states,
  )
    .map(
      ([k, v]) =>
        `<option value="${k}" ${ui.filter === k ? "selected" : ""}>${v}</option>`,
    )
    .join(
      "",
    )}</select></div><span class="result-count">${orders.length ? "1–" + orders.length : "0"} / ${orders.length}</span>${ui.role === "design" ? "" : btn("Mới", "new-order", "plus", true)}</div>${ui.selected.size ? `<div class="bulk-toolbar">${ui.selected.size} đơn được chọn ${btn("Xuất đơn đã chọn", "export-orders", "file")}</div>` : ""}<div class="table-wrap"><table class="order-table"><thead><tr><th><input type="checkbox" id="select-all" aria-label="Chọn tất cả đơn" ${orders.length && orders.every((o) => ui.selected.has(o.id)) ? "checked" : ""}></th>${["Mã đơn", "Ngày đơn hàng", "Khách hàng", "Sale phụ trách", "Người thiết kế", "Người sản xuất", "Trạng thái", "Ưu tiên", "Hạn hoàn thành", ...(ui.role === "design" ? [] : ["Tổng tiền"])].map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${orders.map((o) => `<tr><td><input type="checkbox" data-select="${escape(o.id)}" aria-label="Chọn ${escape(o.id)}" ${ui.selected.has(o.id) ? "checked" : ""}></td><td><button class="table-link" data-order="${escape(o.id)}">${escape(o.id)}</button></td><td>${escape(o.date)}</td><td><button class="customer-link" data-order="${escape(o.id)}">${escape(o.customer)}</button></td><td>${avatar(o.sale, "teal")}${escape(o.sale)}</td><td>${avatar(o.designer)}${escape(o.designer)}</td><td>${escape(o.production)}</td><td>${stateBadge(o.state)}</td><td>${badge(priorities[o.priority], o.priority === "normal" ? "neutral" : "yellow")}</td><td>${day(o.deadline)}</td>${ui.role === "design" ? "" : `<td class="numeric">${money(o.total)}</td>`}</tr>`).join("") || '<tr><td colspan="11"><div class="empty"><strong>Không tìm thấy đơn hàng</strong><p>Thử từ khóa khác hoặc đổi bộ lọc.</p></div></td></tr>'}</tbody></table></div><div class="list-footnote">Dữ liệu mẫu · Bấm mã đơn để xem chi tiết.</div>`;
}
function orderPage() {
  const o = orderById(route().id);
  if (!o)
    return empty(
      "Chưa có đơn hàng",
      "Tạo một đơn mẫu để xem chi tiết.",
      "cart",
    );
  if (ui.role === "design") return `<div class="design-detail"><button class="design-back" data-route="orders">${icon("back")}Danh sách đơn hàng</button>${title(o.id, o.customer, "", "DAC / Thiết kế / Đơn hàng")}${designBrief(o)}${data.tasks.filter((t) => t.order === o.id && t.type === "design").map((t) => `<div class="design-order-task-link"><strong>${escape(t.name)}</strong><button class="btn" data-task="${escape(t.id)}">${icon("chat")}Xem trao đổi & bản thiết kế</button></div>`).join("")}${designOrderInfo(o)}</div>`;
  const steps = ["quote", "design", "production", "delivery", "done"];
  const current = steps.indexOf(
    ["installation", "payment"].includes(o.state) ? "delivery" : o.state,
  );
  const field = (glyph, content) =>
    `<div class="info-field">${icon(glyph)}<span>${content}</span></div>`;
  const tasks = data.tasks.filter((t) => t.order === o.id);
  const notes = data.notes.filter((n) => n.order === o.id);
  return `<header class="order-heading"><div><div class="order-name"><button class="icon-btn" data-route="orders" aria-label="Quay lại đơn hàng">${icon("back")}</button><h1>${escape(o.id)} · ${escape(o.customer)}</h1></div><div class="badges">${stateBadge(o.state)}${badge(priorities[o.priority])}</div></div><ol class="steps">${steps.map((s, i) => `<li class="${i < current ? "complete" : i === current ? "current" : ""}"><button data-state="${s}" data-id="${escape(o.id)}" aria-label="Chuyển sang ${states[s]}"><span>${i < current ? "✓" : i + 1}</span><small>${states[s]}</small></button></li>`).join("")}</ol>${btn("Sửa", "edit-order", "pen")}</header><div class="order-grid"><div class="order-left">${panel(`${icon("user")} Khách hàng`, "", `<div class="customer-grid">${field("user", escape(o.customer))}${field("phone", escape(o.phone))}${field("pin", escape(o.address))}${field("link", '<span class="text-blue">Hội thoại khách hàng mẫu</span>')}</div>`)}<div class="totals"><div><span>Tạm tính</span><b>${money(o.total - o.shipping)}</b></div><div><span>Phí vận chuyển</span><b>${money(o.shipping)}</b></div><div><span>Tổng</span><b>${money(o.total)}</b></div><div class="remaining"><strong>Còn thanh toán</strong><b>${money(o.total - o.paid)}</b></div></div></div><div class="order-right"><section class="description"><div><h2>Mô tả đơn hàng</h2><button class="text-button" data-action="edit-order">Xem chi tiết</button></div><p>${escape(o.description || "Chưa có mô tả. Bấm để bổ sung yêu cầu thiết kế.")}</p><small>File minh họa: không có tài liệu khách hàng thật</small></section>${panel(`${icon("box")} Nghiệp vụ`, "", `<div class="customer-grid">${field("user", escape(o.sale))}${field("pen", escape(o.designer))}${field("factory", escape(o.production))}${field("clock", day(o.deadline))}</div>`)}${panel(
    "Task của tôi",
    "",
    `<div class="my-tasks"><div class="print-link"><span>Link in ấn</span><input id="print-link" placeholder="Dán link in ấn..." aria-label="Link in ấn" value="${escape(o.printLink || "")}"></div>${
      tasks
        .map(
          (t) =>
            `<div class="task-mini"><div><button class="table-link" ${t.type === "design" ? `data-task="${escape(t.id)}"` : `data-order="${escape(t.order)}"`}>${escape(t.name)}</button>${t.type === "design" ? designTaskActions(t) : `<select data-task-state="${t.id}" aria-label="Trạng thái ${escape(t.name)}">${Object.entries(
              taskStates,
            )
              .map(
                ([k, v]) =>
                  `<option value="${k}" ${t.state === k ? "selected" : ""}>${v}</option>`,
              )
              .join(
                "",
              )}</select>`}</div><small>Nhận: ${escape(t.type === "design" ? o.designer : o.production)}　Sale giao: ${escape(o.sale)}　Hạn: ${day(t.deadline)}</small></div>`,
        )
        .join("") || '<p class="muted">Chưa có task được giao.</p>'
    }</div>`,
  )}</div></div><div class="product-layout">${panel("Sản phẩm", "", `<div class="table-wrap product-table"><table><thead><tr>${["SẢN PHẨM", "NỘI DUNG", "CHẤT LIỆU", "KÍCH THƯỚC", "SỐ LƯỢNG"].map((s) => `<th>${s}</th>`).join("")}</tr></thead><tbody><tr><td><button class="table-link" data-action="product">${escape(o.product)}</button></td><td>${escape(o.description)}</td><td>${escape(o.material)}</td><td>${escape(o.size)}</td><td>${o.quantity}</td></tr></tbody></table></div>`, "", `<div class="actions">${btn("In báo giá", "print", "file")} ${badge("1 dòng", "blue")}</div>`)}${panel("Trò chuyện nội bộ", "Trong đơn hàng", `<div class="internal-notes">${notes.map((n) => `<p><b>${escape(n.author)}</b><br>${escape(n.text)}</p>`).join("") || "<small>Chưa có trao đổi nội bộ.</small>"}<form id="note-form"><label for="note-text">Ghi chú cho nhóm</label><textarea id="note-text" name="text" placeholder="Nhập trao đổi cho Sale, Thiết kế, Sản xuất..." required></textarea><button class="btn primary" type="submit">${icon("send")}Gửi ghi chú</button></form></div>`)}</div>`;
}
function tasksPage() {
  const tasks = visibleTasks();
  return (
    title(
      "Việc của tôi",
      "Theo dõi công việc được giao và tiến độ thực hiện.",
      btn("Lịch làm việc", "calendar", "calendar"),
      "DAC Sản xuất & Vận hành / Công việc",
    ) +
    `<div class="task-filters"><label>Trạng thái <select id="task-filter"><option value="all">Tất cả</option>${Object.entries(
      taskStates,
    ).filter(([key]) => key !== "revision" || ui.role !== "production")
      .map(
        ([k, v]) =>
          `<option value="${k}" ${ui.taskFilter === k ? "selected" : ""}>${v}</option>`,
      )
      .join(
        "",
      )}</select></label><span class="muted">${ui.role === "design" ? "Bấm Hoàn thành task; góp ý từ Sale sẽ chuyển task sang Cần chỉnh sửa." : "Task thiết kế dùng nút hoàn thành và luồng Sale duyệt."}</span></div><div class="kanban">${Object.entries(
      taskStates,
    ).filter(([key]) => key !== "revision" || ui.role !== "production")
      .map(([key, label]) => {
        const list = tasks.filter((t) => t.state === key);
        return `<section class="kanban-column" data-drop="${key}"><header><span class="column-dot ${key}"></span><h2>${label}</h2><span>${list.length}</span></header><div class="kanban-cards">${
          list
            .map((t) => {
              const o = orderById(t.order);
              return `<article class="kanban-card" draggable="${t.type !== "design"}" ${t.type !== "design" ? `data-drag="${t.id}"` : ""}><button class="card-title" ${t.type === "design" ? `data-task="${escape(t.id)}"` : `data-order="${escape(t.order)}"`}>${escape(t.name)}</button><p>${escape(t.order)} · ${escape(o.customer)}</p><div class="task-meta">${badge(t.type === "design" ? "Thiết kế" : "Sản xuất", t.type === "design" ? "blue" : "yellow")}${t.deadline < today && t.state !== "done" ? badge("Quá hạn", "danger") : ""}</div><small>${icon("calendar")}${day(t.deadline)}</small>${t.type === "design" ? designTaskActions(t) : `<select data-task-state="${t.id}" aria-label="Trạng thái ${escape(t.name)}">${Object.entries(
                taskStates,
              )
                .map(
                  ([s, l]) =>
                    `<option value="${s}" ${key === s ? "selected" : ""}>${l}</option>`,
                )
                .join("")}</select>`}</article>`;
            })
            .join("") || '<div class="kanban-empty">Chưa có công việc</div>'
        }</div></section>`;
      })
      .join("")}</div>`
  );
}
function calendarPage() {
  const first = new Date(Date.UTC(ui.year, ui.month, 1));
  const offset = (first.getUTCDay() + 6) % 7;
  const count = new Date(Date.UTC(ui.year, ui.month + 1, 0)).getUTCDate();
  const tasks = visibleTasks().filter((t) =>
    t.deadline.startsWith(
      `${ui.year}-${String(ui.month + 1).padStart(2, "0")}`,
    ),
  );
  const list = `<div class="calendar-agenda">${
    tasks
      .sort((a, b) => a.deadline.localeCompare(b.deadline))
      .map(
        (t) =>
          `<button ${t.type === "design" ? `data-task="${escape(t.id)}"` : `data-order="${escape(t.order)}"`}><span>${day(t.deadline)}</span><div><strong>${escape(t.order)} · ${escape(t.name)}</strong><small>${escape(taskStates[t.state])}</small></div>${icon("next")}</button>`,
      )
      .join("") ||
    empty(
      "Không có công việc trong tháng",
      "Dùng mũi tên để đổi tháng.",
      "calendar",
    )
  }</div>`;
  return (
    title(
      "Lịch làm việc",
      "Công việc theo hạn hoàn thành.",
      btn("Việc của tôi", "tasks", "board"),
    ) +
    `<section class="panel calendar-panel"><header class="calendar-control"><div class="actions">${btn("", "prev-month", "back")}${btn("Hôm nay", "this-month")}${btn("", "next-month", "next")}</div><h2>Tháng ${ui.month + 1} năm ${ui.year}</h2><div class="segmented"><button data-calendar-mode="month" class="${ui.calendarMode === "month" ? "active" : ""}">Tháng</button><button data-calendar-mode="list" class="${ui.calendarMode === "list" ? "active" : ""}">Danh sách</button></div></header><div class="${ui.calendarMode === "month" ? "month-view" : "hidden"}"><div class="weekdays">${["Thứ hai", "Thứ ba", "Thứ tư", "Thứ năm", "Thứ sáu", "Thứ bảy", "Chủ nhật"].map((s) => `<span>${s}</span>`).join("")}</div><div class="calendar-grid">${Array.from(
      { length: Math.ceil((offset + count) / 7) * 7 },
      (_, i) => {
        const d = i - offset + 1;
        const valid = d > 0 && d <= count;
        const date = `${ui.year}-${String(ui.month + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
        return `<div class="calendar-day ${!valid ? "outside" : ""} ${date === today ? "today" : ""}">${
          valid
            ? `<span>${d}</span>${tasks
                .filter((t) => t.deadline === date)
                .map(
                  (t) =>
                    `<button class="calendar-event ${t.type}" ${t.type === "design" ? `data-task="${escape(t.id)}"` : `data-order="${escape(t.order)}"`}>${escape(t.order)} · ${escape(t.name)}</button>`,
                )
                .join("")}`
            : ""
        }</div>`;
      },
    ).join(
      "",
    )}</div></div><div class="${ui.calendarMode === "list" ? "" : "mobile-agenda"}">${list}</div></section>`
  );
}
function conversationsPage() {
  const chats = (ui.empty ? [] : data.conversations).filter(
    (c) =>
      !ui.search ||
      [c.customer, c.channel, c.tag]
        .join(" ")
        .toLocaleLowerCase("vi")
        .includes(ui.search.toLocaleLowerCase("vi")),
  );
  const c = chats.find((c) => c.id === ui.conversation) || chats[0];
  return (
    title(
      "Hội thoại Pancake",
      "Quản lý hội thoại và chăm sóc khách hàng đa kênh.",
      btn("Làm mới", "refresh", "refresh"),
      "DAC Bán Hàng & CRM / Hội thoại",
    ) +
    `<section class="inbox"><aside class="conversation-list"><div class="conversation-search">${icon("search")}<input id="chat-search" placeholder="Tìm hội thoại..." aria-label="Tìm hội thoại" value="${escape(ui.search)}"></div>${chats.map((c) => `<button class="conversation ${c.id === (ui.conversation || chats[0]?.id) ? "active" : ""}" data-chat="${c.id}">${avatar(c.customer)}<span><strong>${escape(c.customer)}${c.unread ? '<i class="unread-dot"></i>' : ""}</strong><small>${escape(c.messages.at(-1)?.text)}</small><em>${escape(c.channel)} · ${escape(c.tag)}</em></span></button>`).join("")}</aside>${c ? `<div class="chat-pane"><header><div>${avatar(c.customer)}<div><strong>${escape(c.customer)}</strong><small>${escape(c.channel)} · Hội thoại mẫu</small></div></div>${badge(c.tag, "blue")}</header><div class="messages" id="messages">${c.messages.map((m) => `<div class="message ${m.from}"><p>${escape(m.text)}</p><small>${escape(m.time)}</small></div>`).join("")}</div><form id="message-form" data-conversation-id="${c.id}"><input name="text" placeholder="Nhập tin nhắn mẫu..." aria-label="Tin nhắn" required autocomplete="off"><button class="btn primary" type="submit">${icon("send")}Gửi</button></form><small class="chat-disclaimer">Tin nhắn được lưu trong trình duyệt để thử giao diện.</small></div>` : empty("Chưa có hội thoại", "Thử đổi từ khóa hoặc bật dữ liệu mẫu.", "chat")}</section>`
  );
}
function attendancePage() {
  return (
    title(
      "Chấm công của tôi",
      "Theo dõi ca làm việc, giờ công và yêu cầu nghỉ phép.",
      btn("Tạo yêu cầu nghỉ", "leave", "plus", true),
      "DAC Nhân sự / Chấm công",
    ) +
    `<div class="kpi-grid">${kpi("Ngày công tháng", ui.empty ? 0 : 4, "Tháng 11/2026")}${kpi("Tổng giờ làm", ui.empty ? "0h" : "32h 15m", "Giờ công mẫu")}${kpi("Đi muộn", 0, "Trong tháng")}${kpi("Phép còn lại", ui.empty ? 0 : 8, "Ngày phép", "success")}</div><div class="dashboard-columns">${panel("Ca làm việc hôm nay", "Thứ năm, 05/11/2026", `<div class="checkin-card"><div class="attendance-circle ${data.checkedIn ? "working" : ""}">${icon("clock")}</div><h2>${data.checkedIn ? "Đang trong ca làm việc" : "Chưa vào ca"}</h2><p>08:00 – 17:00 · Văn phòng</p>${btn(data.checkedIn ? "Check out" : "Check in", "checkin", "clock", true)}<small>Thao tác mô phỏng chấm công.</small></div>`)}${panel("Lịch sử chấm công", "Dữ liệu minh họa", ui.empty ? empty("Chưa có dữ liệu", "Lịch sử chấm công mẫu sẽ hiện tại đây.", "clock") : `<div class="attendance-history">${["05/11/2026", "04/11/2026", "03/11/2026", "02/11/2026"].map((d, i) => `<div><strong>${d}</strong><span>07:53</span><span>${i === 0 && data.checkedIn ? "Đang làm việc" : "17:05"}</span>${badge("Đúng giờ", "success")}</div>`).join("")}</div>`)}</div>`
  );
}
function paymentsPage() {
  const orders = ui.empty ? [] : data.orders.filter((o) => o.paid > 0);
  return (
    title(
      "Thanh toán",
      "Theo dõi các khoản thanh toán theo đơn hàng.",
      btn("Xuất dữ liệu mẫu", "export-orders", "file"),
      "DAC Kế toán / Thanh toán",
    ) +
    `<section class="panel"><div class="table-wrap"><table><thead><tr><th>Phiếu thu</th><th>Khách hàng</th><th>Đơn hàng</th><th>Ngày</th><th>Số tiền</th><th>Trạng thái</th></tr></thead><tbody>${orders.map((o, i) => `<tr><td>PT-MAU-${String(i + 1).padStart(3, "0")}</td><td>${escape(o.customer)}</td><td><button class="table-link" data-order="${escape(o.id)}">${escape(o.id)}</button></td><td>${escape(o.date.split(" ")[0])}</td><td>${money(o.paid)}</td><td>${badge("Đã ghi nhận", "success")}</td></tr>`).join("") || '<tr><td colspan="6">Chưa có thanh toán mẫu.</td></tr>'}</tbody></table></div></section>`
  );
}
function content() {
  switch (route().screen) {
    case "sales":
      return salesDashboard();
    case "manager":
      return managerDashboard();
    case "orders":
      return ordersPage();
    case "order":
      return orderPage();
    case "tasks":
      return tasksPage();
    case "task":
      return designTaskPage();
    case "calendar":
      return calendarPage();
    case "conversations":
      return conversationsPage();
    case "attendance":
      return attendancePage();
    case "payments":
      return paymentsPage();
    default:
      return dashboard(route().screen);
  }
}
function render() {
  const screen = route().screen;
  if (screen === "login" || screen === "roles") {
    dialog.close();
    document.title = `${screens[screen].title} · DAC Mockup`;
    app.innerHTML = screen === "login" ? loginPage() : rolesPage();
    return;
  }
  const focused = document.activeElement?.id;
  const position = document.activeElement?.selectionStart;
  document.title = `${screens[route().screen].title} · DAC Mockup`;
  app.innerHTML =
    toolbar() +
    sidebar() +
    `<button class="mobile-overlay ${ui.mobile ? "visible" : ""}" data-action="mobile" aria-label="Đóng menu"></button><main id="main" class="main ${ui.collapsed ? "expanded" : ""}" tabindex="-1"><div class="mobile-header"><button class="icon-btn" data-action="mobile" aria-label="Mở menu">${icon("menu")}</button><img src="./assets/dac-logo.png" alt="DAC"><strong>${escape(screens[route().screen].title)}</strong></div><div class="page-content ${["orders"].includes(route().screen) ? "flush" : ""}">${content()}</div></main>`;
  if (focused) {
    const el = document.getElementById(focused);
    if (el) {
      el.focus();
      if (typeof position === "number" && ["text", "search"].includes(el.type))
        el.setSelectionRange(position, position);
    }
  }
}
function modal(name, html) {
  dialog.innerHTML = `<header><h2 id="dialog-title">${escape(name)}</h2><button class="icon-btn" data-action="close-dialog" aria-label="Đóng">${icon("close")}</button></header>${html}`;
  if (!dialog.open) dialog.showModal();
}
function exportJSON(value, name) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function editOrder(create = false) {
  const o = create
    ? {
        customer: "",
        phone: "",
        address: "",
        product: "",
        material: "",
        size: "",
        quantity: 1,
        total: 0,
        shipping: 0,
        paid: 0,
        state: "quote",
        priority: "normal",
        deadline: "2026-11-07",
        description: "",
      }
    : orderById(route().id);
  modal(
    create ? "Tạo đơn hàng mẫu" : `Chỉnh sửa ${o.id}`,
    `<form id="order-form" data-create="${create}"><div class="form-grid">${[
      ["customer", "Khách hàng", "text"],
      ["phone", "Số điện thoại", "text"],
      ["address", "Địa chỉ", "text"],
      ["product", "Sản phẩm", "text"],
      ["material", "Chất liệu", "text"],
      ["size", "Kích thước", "text"],
      ["quantity", "Số lượng", "number"],
      ["total", "Tổng tiền (đ)", "number"],
      ["shipping", "Phí vận chuyển (đ)", "number"],
      ["paid", "Đã thanh toán (đ)", "number"],
      ["deadline", "Hạn hoàn thành", "date"],
    ]
      .map(
        ([key, label, type]) =>
          `<label>${label}<input name="${key}" type="${type}" value="${escape(o[key])}" ${["customer", "product"].includes(key) ? "required" : ""} ${type === "number" ? `min="${key === "quantity" ? 1 : 0}"` : ""}></label>`,
      )
      .join("")}<label>Trạng thái<select name="state">${Object.entries(states)
      .map(
        ([k, v]) =>
          `<option value="${k}" ${o.state === k ? "selected" : ""}>${v}</option>`,
      )
      .join(
        "",
      )}</select></label><label>Ưu tiên<select name="priority">${Object.entries(
      priorities,
    )
      .map(
        ([k, v]) =>
          `<option value="${k}" ${o.priority === k ? "selected" : ""}>${v}</option>`,
      )
      .join(
        "",
      )}</select></label></div><label>Mô tả đơn hàng<textarea name="description">${escape(o.description)}</textarea></label><footer class="dialog-footer"><button type="button" class="btn" data-action="close-dialog">Hủy</button><button type="submit" class="btn primary">Lưu đơn mẫu</button></footer></form>`,
  );
}
function handoff() {
  const screen = route().screen;
  const releaseInfo = mockupRelease
    ? `<div class="handoff-screen"><small>Bản bàn giao gần nhất</small><h3>${escape(mockupRelease.id)}</h3><code>${escape(mockupRelease.version)}</code><p>${escape(mockupRelease.title)}</p>${mockupRelease.changelogUrl ? `<a class="text-blue" href="${escape(mockupRelease.changelogUrl)}" target="_blank" rel="noopener">Đọc changelog và mở đúng phiên bản trên GitHub</a>` : ""}</div>`
    : "";
  modal(
    "Bàn giao giao diện",
    `<p class="dialog-copy"><strong>Duy An</strong> chỉnh mockup và duyệt giao diện. <strong>Khang</strong> triển khai vào Odoo, kiểm tra nghiệp vụ và deploy production.</p>${releaseInfo}<div class="handoff-screen"><small>Màn hình đang xem</small><h3>${escape(screens[screen].title)}</h3><p>File Odoo tham chiếu:</p>${screens[screen].sources.map((s) => `<code>${escape(s)}</code>`).join("")}</div><form id="handoff-form"><label for="handoff-note">Yêu cầu thay đổi cho Khang</label><textarea id="handoff-note" name="text" placeholder="Mô tả điều muốn thay đổi, trạng thái cần có và tiêu chí duyệt..." required></textarea><div class="dialog-footer"><button type="submit" class="btn primary">Lưu ghi chú mẫu</button><button class="btn" type="button" data-action="export-handoff">${icon("file")}Tải ghi chú (JSON)</button></div></form><div class="handoff-notes">${data.notes
      .filter((n) => n.screen === screen && !n.order)
      .map((n) => `<p>${escape(n.text)}</p>`)
      .join(
        "",
      )}</div><p class="muted dialog-copy">Ghi chú lưu trong trình duyệt. Khi chỉnh xong, yêu cầu Codex chốt changelog và gói mockup tương ứng để gửi Khang. File JSON tải ở đây dùng để chuyển các ghi chú đó cho Codex.</p>`,
  );
}
const actions = {
  "choose-role": () => go("roles"),
  logout: () => {
    ui.signedIn = false;
    ui.mobile = false;
    dialog.close();
    saveSession();
    go("login");
  },
  collapse: () => {
    if (innerWidth < 900) ui.mobile = false;
    else ui.collapsed = !ui.collapsed;
    render();
  },
  mobile: () => {
    ui.mobile = !ui.mobile;
    render();
  },
  refresh: () => {
    render();
    toast("Đã làm mới dữ liệu mẫu.");
  },
  calendar: () => go("calendar"),
  "design-home": () => go("design"),
  tasks: () => go("tasks"),
  empty: () => {
    ui.empty = !ui.empty;
    render();
  },
  checkin: () => {
    data.checkedIn = !data.checkedIn;
    save();
    render();
    toast(data.checkedIn ? "Đã check in mẫu." : "Đã check out mẫu.");
  },
  handoff,
  "close-dialog": () => dialog.close(),
  "new-order": () => editOrder(true),
  "edit-order": () => editOrder(),
  "export-orders": () =>
    exportJSON(
      data.orders.filter((o) => !ui.selected.size || ui.selected.has(o.id)),
      "dac-don-hang-mau.json",
    ),
  "export-handoff": () =>
    exportJSON(
      {
        project: "DAC ERP mockup",
        latestHandoff: mockupRelease,
        screen: route().screen,
        sources: screens[route().screen].sources,
        notes: data.notes,
        orders: data.orders,
        tasks: data.tasks,
      },
      `dac-ghi-chu-${mockupRelease?.id || "draft"}.json`,
    ),
  "prev-month": () => {
    if (--ui.month < 0) {
      ui.month = 11;
      ui.year--;
    }
    render();
  },
  "next-month": () => {
    if (++ui.month > 11) {
      ui.month = 0;
      ui.year++;
    }
    render();
  },
  "this-month": () => {
    ui.month = 10;
    ui.year = 2026;
    render();
  },
  search: () =>
    modal(
      "Tìm kiếm màn hình",
      `<div class="quick-links">${Object.entries(screens)
        .filter(([k]) => k !== "order")
        .map(
          ([k, s]) =>
            `<button class="btn" data-route="${k}">${icon(s.icon)}${escape(s.title)}</button>`,
        )
        .join("")}</div>`,
    ),
  overdue: () => {
    go("tasks");
    ui.taskFilter = "all";
    toast("Danh sách công việc: thẻ quá hạn có nhãn đỏ.");
  },
  product: () => {
    const o = orderById(route().id);
    modal(
      "Thông tin sản phẩm",
      `<dl class="product-detail">${[
        ["Sản phẩm", o.product],
        ["Nội dung", o.description || "Chưa có"],
        ["Chất liệu", o.material],
        ["Kích thước", o.size],
        ["Số lượng", o.quantity],
      ]
        .map(([k, v]) => `<dt>${k}</dt><dd>${escape(v)}</dd>`)
        .join("")}</dl>${ui.role === "design" ? "" : btn("Chỉnh sửa đơn mẫu", "edit-order", "pen", true)}`,
    );
  },
  print: () => window.print(),
  leave: () =>
    modal(
      "Yêu cầu nghỉ phép mẫu",
      '<form id="leave-form"><label>Ngày nghỉ<input type="date" name="date" required value="2026-11-06"></label><label>Lý do<textarea name="reason" required></textarea></label><div class="dialog-footer"><button class="btn primary" type="submit">Tạo yêu cầu mẫu</button></div></form>',
    ),
  profile: () =>
    modal(
      "Phiên mockup",
      `<p class="dialog-copy">Vai trò đang xem: <strong>${roles[ui.role]}</strong>. Bộ chọn vai trò mô phỏng giao diện; quyền thật do Odoo kiểm tra khi Khang triển khai.</p><p class="dialog-copy">Dữ liệu tự lưu trên trình duyệt này. Anh có thể đưa về bộ dữ liệu ban đầu.</p><div class="actions">${btn("Chọn lại vai trò", "choose-role", "user")}${btn("Đăng xuất", "logout", "back")}${btn("Khôi phục dữ liệu mẫu", "reset", "refresh")}</div>`,
    ),
  reset: () => {
    data = clone(seed);
    save();
    ui.selected.clear();
    ui.empty = false;
    dialog.close();
    render();
    toast("Đã khôi phục dữ liệu mẫu.");
  },
};
document.addEventListener("click", (event) => {
  const el = event.target.closest("button,[data-period]");
  if (!el) return;
  if (el.dataset.enterRole) {
    enterRole(el.dataset.enterRole);
  } else if (el.dataset.route) {
    dialog.close();
    go(el.dataset.route);
  } else if (el.dataset.order) {
    dialog.close();
    go("order", el.dataset.order);
  } else if (el.dataset.task) {
    dialog.close();
    go("task", el.dataset.task);
  } else if (el.dataset.designFilter) {
    ui.designFilter = el.dataset.designFilter;
    render();
  } else if (el.dataset.completeDesign) {
    completeDesignTask(el.dataset.completeDesign);
  } else if (el.dataset.approveDesign) {
    approveDesignTask(el.dataset.approveDesign);
  } else if (el.dataset.conversation) {
    ui.conversation = el.dataset.conversation;
    go("conversations");
  } else if (el.dataset.chat) {
    ui.conversation = el.dataset.chat;
    const c = data.conversations.find((c) => c.id === ui.conversation);
    c.unread = false;
    save();
    render();
  } else if (el.dataset.state) {
    const o = data.orders.find((o) => o.id === el.dataset.id);
    o.state = el.dataset.state;
    save();
    render();
    toast("Đã đổi trạng thái đơn mẫu.");
  } else if (el.dataset.filter) {
    go("orders");
    ui.filter = el.dataset.filter;
    render();
  } else if (el.dataset.period) {
    ui.period = el.dataset.period;
    render();
  } else if (el.dataset.managerTab) {
    ui.managerTab = el.dataset.managerTab;
    render();
  } else if (el.dataset.calendarMode) {
    ui.calendarMode = el.dataset.calendarMode;
    render();
  } else if (el.dataset.action) actions[el.dataset.action]?.();
});
document.addEventListener("input", (event) => {
  if (event.target.id === "design-search") {
    ui.designSearch = event.target.value;
    render();
  }
  if (["order-search", "chat-search"].includes(event.target.id)) {
    ui.search = event.target.value;
    render();
  }
});
document.addEventListener("change", (event) => {
  const el = event.target;
  if (el.id === "screen-select") go(el.value);
  else if (el.id === "role-select") {
    enterRole(el.value);
  } else if (el.id === "order-filter") {
    ui.filter = el.value;
    render();
  } else if (el.id === "task-filter") {
    ui.taskFilter = el.value;
    render();
  } else if (el.id === "select-all") {
    for (const o of filteredOrders()) {
      if (el.checked) ui.selected.add(o.id);
      else ui.selected.delete(o.id);
    }
    render();
  } else if (el.dataset.select) {
    if (el.checked) ui.selected.add(el.dataset.select);
    else ui.selected.delete(el.dataset.select);
    render();
  } else if (el.dataset.taskState) {
    data.tasks.find((t) => t.id === el.dataset.taskState).state = el.value;
    save();
    render();
    toast("Đã cập nhật công việc mẫu.");
  } else if (el.id === "print-link") {
    orderById(route().id).printLink = el.value;
    save();
    toast("Đã lưu link mẫu.");
  }
});
document.addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.target;
  if (["design-comment-form", "design-final-form"].includes(form.id)) {
    void submitDesignForm(form);
    return;
  }
  const values = Object.fromEntries(new FormData(form));
  if (form.id === "login-form") {
    if (
      values.email?.trim() !== DEMO_EMAIL ||
      values.password !== DEMO_PASSWORD
    ) {
      const error = document.querySelector("#login-error");
      error.textContent = `Dùng tài khoản mẫu ${DEMO_EMAIL} và mật khẩu ${DEMO_PASSWORD}.`;
      error.hidden = false;
      return;
    }
    ui.signedIn = true;
    saveSession();
    go("roles");
  } else if (form.id === "order-form") {
    for (const key of ["quantity", "total", "shipping", "paid"])
      values[key] = Number(values[key]);
    if (values.paid > values.total || values.shipping > values.total) {
      toast(
        "Tiền đã thanh toán và phí vận chuyển không được lớn hơn tổng tiền.",
      );
      return;
    }
    if (form.dataset.create === "true") {
      const id =
        "DEMO-" +
        String(
          Math.max(
            0,
            ...data.orders.map((o) => Number(o.id.replace("DEMO-", "")) || 0),
          ) + 1,
        ).padStart(3, "0");
      data.orders.unshift({
        ...values,
        id,
        date: "05/11/2026 10:00",
        sale: "Sale mẫu",
        designer: "Thiết kế mẫu",
        production: "Tổ sản xuất A",
      });
      save();
      dialog.close();
      ui.empty = false;
      go("order", id);
    } else {
      Object.assign(orderById(route().id), values);
      save();
      dialog.close();
      render();
    }
    toast("Đã lưu đơn mẫu trong trình duyệt.");
  } else if (form.id === "message-form") {
    if (!values.text.trim()) return;
    data.conversations
      .find((c) => c.id === form.dataset.conversationId)
      .messages.push({ from: "team", text: values.text, time: "Vừa xong" });
    save();
    render();
    document.querySelector("#messages")?.scrollTo(0, 100000);
  } else if (form.id === "note-form" || form.id === "handoff-form") {
    if (!values.text.trim()) return;
    data.notes.push({
      screen: route().screen,
      ...(form.id === "note-form" ? { order: orderById(route().id).id } : {}),
      author: "Duy An",
      text: values.text,
    });
    save();
    if (form.id === "handoff-form") handoff();
    else render();
    toast("Đã lưu ghi chú mẫu.");
  } else if (form.id === "leave-form") {
    data.notes.push({
      screen: "attendance",
      author: "Duy An",
      text: `Yêu cầu nghỉ mẫu ${values.date}: ${values.reason}`,
    });
    save();
    dialog.close();
    toast("Đã lưu yêu cầu nghỉ mẫu trong trình duyệt.");
  }
});
document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    if (ui.signedIn && !["login", "roles"].includes(route().screen))
      actions.search();
  }
});
document.addEventListener("dragstart", (event) => {
  const el = event.target.closest("[data-drag]");
  if (el) event.dataTransfer.setData("text/plain", el.dataset.drag);
});
document.addEventListener("dragover", (event) => {
  if (event.target.closest("[data-drop]")) event.preventDefault();
});
document.addEventListener("drop", (event) => {
  const col = event.target.closest("[data-drop]");
  if (!col) return;
  event.preventDefault();
  const task = data.tasks.find(
    (t) => t.id === event.dataTransfer.getData("text/plain"),
  );
  if (task) {
    if (task.type === "design") return;
    task.state = col.dataset.drop;
    save();
    render();
  }
});
window.addEventListener("hashchange", () => {
  ui.search = "";
  ui.mobile = false;
  render();
});
render();
