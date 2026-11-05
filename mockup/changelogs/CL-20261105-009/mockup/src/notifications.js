// Shared notification extension for the design overview and notification surfaces.
// No network connections, push permissions or production data.
const graphemes = (value) => Array.from(
  new Intl.Segmenter("vi", { granularity: "grapheme" }).segment(String(value || "")),
  (part) => part.segment,
);

export function shortTaskName(name) {
  const letters = graphemes(name);
  return letters.length > 20 ? letters.slice(0, 17).join("").trimEnd() + "..." : letters.join("");
}

export function relativeNotificationTime(timestamp, now = Date.now()) {
  const minutes = Math.max(0, Math.floor((now - timestamp) / 60000));
  if (!minutes) return "Vừa xong";
  if (minutes < 60) return `${minutes} phút`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} giờ`;
  return `${Math.floor(hours / 24)} ngày`;
}

export function designNotifications({ data, tasks, orders, reviewFor, today, now = Date.now() }) {
  const items = [];
  const read = new Set(data.notificationRead || []);
  const base = data.notificationStartedAt || now;
  const assigned = new Set(tasks.map((task) => task.id));
  const orderIds = new Set(orders.map((order) => order.id));
  const representedComments = new Set();
  for (const example of data.notificationExamples || []) {
    if (example.task && !assigned.has(example.task)) continue;
    if (example.order && !orderIds.has(example.order)) continue;
    if (example.commentId) {
      const task = tasks.find((task) => task.id === example.task);
      if (!task || !reviewFor(task).comments.some((comment) => comment.id === example.commentId)) continue;
    }
    if (example.commentId) representedComments.add(example.commentId);
    items.push({ ...example, createdAt: base - example.minutesAgo * 60000 });
  }
  for (const task of tasks) {
    const review = reviewFor(task);
    for (const comment of review.comments) {
      if (comment.role === "design" || representedComments.has(comment.id)) continue;
      const approved = comment.id?.startsWith("approval-") || comment.id?.startsWith("comment-approve-");
      items.push({
        id: `exchange:${task.id}:${comment.id}`, kind: approved ? "approved" : "message",
        task: task.id, order: task.order, actor: comment.author, commentId: comment.id,
        text: comment.text || (comment.attachment ? "Đã gửi một tệp đính kèm." : "Có trao đổi mới."),
        createdAt: comment.notificationAt || base - 35 * 60000,
      });
    }
    if (task.state !== "done" && task.deadline && task.deadline < today) {
      items.push({ id: `overdue:${task.id}:${task.deadline}`, kind: "overdue", task: task.id, order: task.order,
        text: `Hạn ${task.deadline.split("-").reverse().slice(0, 2).join("/")}. Cần ưu tiên xử lý.`, createdAt: base - 20 * 60000 });
    }
  }
  for (const order of orders) {
    const task = tasks.find((task) => task.order === order.id);
    items.push({ id: `assigned:${order.id}`, kind: "assigned", task: task?.id, order: order.id,
      text: `Có đơn thiết kế mới ${order.id} được giao cho bạn.`,
      createdAt: order.notificationAt || base - (order.date?.startsWith("05/11/2026") ? 12 : 180) * 60000 });
  }
  return items.filter((item, index) => items.findIndex((other) => other.id === item.id) === index)
    .map((item) => ({ ...item, read: read.has(item.id) }))
    .sort((a, b) => b.createdAt - a.createdAt || a.id.localeCompare(b.id));
}

export function notificationRows(items, { escape, icon, taskName, tasks, now = Date.now() }) {
  return items.map((item) => {
    const task = tasks.find((task) => task.id === item.task);
    const fullName = task ? taskName(task) : item.order;
    const taskLabel = `<strong class="notification-task" title="${escape(fullName)}">${escape(shortTaskName(fullName))}</strong>`;
    const actor = item.actor ? `<strong class="notification-actor">${escape(item.actor)}</strong>` : "";
    const avatar = item.actor ? `<span class="notification-avatar" aria-hidden="true">${escape(item.actor.trim().split(/\s+/).map((word) => graphemes(word)[0]).slice(-2).join(""))}</span>` :
      `<span class="notification-system-icon ${item.kind}" aria-hidden="true">${icon(item.kind === "overdue" ? "alert" : "box")}</span>`;
    let lead = `${actor} đã gửi tin nhắn tại task ${taskLabel}`;
    if (item.kind === "approved") lead = `${actor} đã duyệt ${taskLabel}`;
    if (item.kind === "assigned") lead = `Bạn có đơn thiết kế mới ${taskLabel}`;
    if (item.kind === "overdue") lead = `${taskLabel} đã trễ hạn`;
    const body = item.kind === "assigned" ? `Đơn ${item.order} vừa được giao cho bạn.` : item.text;
    const snippet = body ? `: “${body}”` : "";
    return `<button type="button" class="notification-card ${item.read ? "is-read" : "is-unread"}" data-notification="${escape(item.id)}" aria-label="${escape(item.actor ? item.actor + ': ' : '')}${escape(fullName)}. ${escape(body)}. ${item.read ? "Đã đọc" : "Chưa đọc"}">
      ${avatar}<span class="notification-copy"><span class="notification-text"><span class="notification-lead">${lead}</span><span class="notification-snippet" data-full="${escape(snippet)}">${escape(snippet)}</span></span><time class="notification-time" datetime="${new Date(item.createdAt).toISOString()}">${relativeNotificationTime(item.createdAt, now)}</time></span><span class="notification-unread-dot" aria-hidden="true"></span></button>`;
  }).join("");
}

export function fitNotificationSnippets(root = document) {
  for (const block of root.querySelectorAll(".notification-text")) {
    const snippet = block.querySelector(".notification-snippet");
    const letters = graphemes(snippet.dataset.full);
    snippet.textContent = letters.join("");
    if (!block.clientWidth) continue;
    const limit = Math.ceil(parseFloat(getComputedStyle(block).lineHeight) * 3);
    if (block.scrollHeight <= limit) continue;
    let low = 0, high = letters.length;
    while (low < high) {
      const middle = Math.ceil((low + high) / 2);
      snippet.textContent = letters.slice(0, middle).join("").trimEnd() + "...";
      if (block.scrollHeight <= limit) low = middle;
      else high = middle - 1;
    }
    snippet.textContent = letters.slice(0, low).join("").trimEnd() + "...";
  }
}
