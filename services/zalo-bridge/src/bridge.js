import { timingSafeEqual } from 'node:crypto';
import { readFile, writeFile, rename, rm, mkdir, chmod } from 'node:fs/promises';
import { join } from 'node:path';

export function authorized(header, token) {
  const actual = Buffer.from(header || '');
  const expected = Buffer.from(`Bearer ${token}`);
  return Boolean(token && token.length >= 32 && actual.length === expected.length && timingSafeEqual(actual, expected));
}

export function normalizeMessage(message, historical = false) {
  if (message.type !== 0 || !message.threadId || !message.data?.msgId) return null;
  const content = message.data.content;
  const text = typeof content === 'string' ? content : `[Tin nhắn ${String(message.data.msgType || 'đính kèm').slice(0, 80)}]`;
  if (!text.trim()) return null;
  return {
    thread_id: String(message.threadId), message_id: String(message.data.msgId),
    text: text.slice(0, 12000), direction: message.isSelf ? 'out' : 'in',
    historical, timestamp: Number(message.data.ts) || Date.now(),
  };
}

// Kiểu chữ Zalo cho phép (giá trị TextStyle của zca-js). Chỉ nhận danh sách trắng, phạm vi phải nằm trong văn bản.
const STYLE_CODES = { bold: 'b' };

const MAX_MENTIONS = 30;
const USER_ID = /^\d{1,30}$/;

// Nhắc tên (@tag) chỉ dùng được trong nhóm. `pos`/`len` tính theo ký tự JavaScript (UTF-16), đúng như zca-js;
// mỗi đoạn phải bắt đầu bằng "@" trong văn bản và các đoạn không được chồng lên nhau.
export function buildMentions(payload) {
  if (payload.mentions === undefined || payload.mentions === null) return [];
  if (!Array.isArray(payload.mentions) || payload.mentions.length > MAX_MENTIONS || payload.thread_type !== 'group') throw new Error('invalid_payload');
  const mentions = payload.mentions.map((item) => {
    if (!item || typeof item.uid !== 'string' || !USER_ID.test(item.uid) || !Number.isInteger(item.pos) || !Number.isInteger(item.len)
      || item.pos < 0 || item.len < 2 || item.pos + item.len > payload.text.length || payload.text[item.pos] !== '@') throw new Error('invalid_payload');
    return { pos: item.pos, len: item.len, uid: item.uid };
  });
  const ordered = [...mentions].sort((a, b) => a.pos - b.pos);
  for (let index = 1; index < ordered.length; index += 1) {
    if (ordered[index].pos < ordered[index - 1].pos + ordered[index - 1].len) throw new Error('invalid_payload');
  }
  return mentions;
}

export function buildContent(payload, quote = null) {
  const mentions = buildMentions(payload);
  let styles = [];
  if (payload.styles !== undefined && payload.styles !== null) {
    if (!Array.isArray(payload.styles) || payload.styles.length > 20) throw new Error('invalid_payload');
    styles = payload.styles.map((item) => {
      const code = item && STYLE_CODES[item.style];
      if (!code || !Number.isInteger(item.start) || !Number.isInteger(item.len) || item.start < 0 || item.len < 1 || item.start + item.len > payload.text.length) throw new Error('invalid_payload');
      return { start: item.start, len: item.len, st: code };
    });
  }
  if (!styles.length && !mentions.length && !quote) return payload.text;
  return { msg: payload.text, ...(styles.length ? { styles } : {}), ...(mentions.length ? { mentions } : {}), ...(quote ? { quote } : {}) };
}

const MESSAGE_ID = /^\d{1,30}$/;
// Phản hồi gửi tin của Zalo là JSON thường nên msgId số lớn bị làm tròn theo double của JavaScript, còn echo giữ nguyên chuỗi.
// Khớp hai bên theo dạng đã làm tròn (cùng một phép tính); msgId nguyên bản vẫn được giữ để gửi cho Zalo làm khung trả lời.
export const idKey = (id) => { const text = String(id); return /^\d+$/.test(text) ? String(Number(text)) : text; };

// Dữ liệu cần để Zalo hiện "trả lời tin nhắn" cho một tin văn bản do chính tài khoản đã gửi (lấy từ echo của tin đó).
export function quoteFromEcho(message) {
  const data = message?.data;
  if (!message?.isSelf || !message.threadId || !data?.msgId || !data.cliMsgId || typeof data.content !== 'string') return null;
  return {
    threadId: String(message.threadId), msgId: idKey(data.msgId),
    body: { msgId: String(data.msgId), content: data.content, msgType: data.msgType || 'webchat', propertyExt: data.propertyExt,
      cliMsgId: String(data.cliMsgId), ts: String(data.ts), ttl: Number(data.ttl) || 0 },
  };
}

const DUPLICATE_CONNECTION = 3000; // CloseReason.DuplicateConnection của zca-js
const THREAD_CACHE_MS = 5 * 60 * 1000;
const THREAD_LIMIT = 50;
const fold = (value) => String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/đ/g, 'd').replace(/Đ/g, 'D').toLowerCase();

export function matchThreads(threads, query, limit = THREAD_LIMIT) {
  const needle = fold(query).trim();
  return threads
    .filter((thread) => !needle || fold(thread.name).includes(needle) || thread.thread_id === needle)
    .sort((a, b) => fold(a.name).localeCompare(fold(b.name)))
    .slice(0, limit);
}

export class Bridge {
  constructor({ zalo, store, dataDir, odooUrl, token, fetchImpl = fetch, sendTimeoutMs = 25000, reconnectBaseMs = 30000, reconnectMaxMs = 900000 }) {
    Object.assign(this, { zalo, store, dataDir, odooUrl, token, fetchImpl, sendTimeoutMs, reconnectBaseMs, reconnectMaxMs });
    this.state = 'disconnected'; this.api = null; this.qr = null; this.accountId = null;
    this.accountName = null; this.accountAvatar = null;
    this.loginTask = null; this.flushing = false; this.generation = 0; this.abortQR = null;
  }
  status() {
    return { state: this.state, account_id: this.accountId, account_name: this.accountName,
      account_avatar: this.accountAvatar, qr_image: this.qr, pending_events: this.store.count() };
  }
  async saveCredentials(credentials) {
    await mkdir(this.dataDir, { recursive: true, mode: 0o700 });
    await chmod(this.dataDir, 0o700);
    const target = join(this.dataDir, 'credentials.json');
    await writeFile(`${target}.tmp`, JSON.stringify(credentials), { mode: 0o600 });
    await rename(`${target}.tmp`, target);
    await chmod(target, 0o600);
  }
  async restore() {
    try {
      const raw = await readFile(join(this.dataDir, 'credentials.json'), 'utf8');
      await this.attach(await this.zalo.login(JSON.parse(raw)));
    } catch (error) {
      this.state = error.code === 'ENOENT' ? 'disconnected' : 'error';
    }
  }
  startLogin() {
    if (this.loginTask || this.api) return;
    const generation = ++this.generation;
    this.state = 'waiting_qr';
    const task = (async () => {
      try {
        const api = await this.zalo.loginQR({}, (event) => {
          if (generation !== this.generation) { event.actions?.abort?.(); return; }
          if (event.type === 0) {
            this.qr = event.data.image.replace(/^data:image\/png;base64,/, '');
            this.state = 'waiting_qr'; this.abortQR = event.actions.abort;
          } else if (event.type === 1) {
            this.qr = null; this.state = 'error'; event.actions.abort();
          } else if (event.type === 2) {
            this.qr = null; this.state = 'scanned';
          } else if (event.type === 3) {
            this.qr = null; this.state = 'error'; event.actions.abort();
          }
        });
        if (generation !== this.generation) { api.listener.stop(); return; }
        const context = api.getContext();
        await this.saveCredentials({ cookie: context.cookie.toJSON()?.cookies || [], imei: context.imei, userAgent: context.userAgent });
        if (generation !== this.generation) {
          api.listener.stop();
          await rm(join(this.dataDir, 'credentials.json'), { force: true });
          await rm(join(this.dataDir, 'credentials.json.tmp'), { force: true });
          return;
        }
        await this.attach(api);
      } catch { if (generation === this.generation) { this.state = 'error'; this.qr = null; } }
      finally {
        if (this.loginTask === task) this.loginTask = null;
        if (generation === this.generation) this.abortQR = null;
      }
    })();
    this.loginTask = task;
  }
  async restartLogin() {
    await this.logout();
    const previous = this.loginTask;
    if (previous) {
      await Promise.race([previous, new Promise((resolve) => setTimeout(resolve, 8000))]);
      if (this.loginTask) throw new Error('login_in_progress');
    }
    this.startLogin();
  }
  async loadAccountProfile(api) {
    try {
      if (typeof api.getUserInfo !== 'function') return;
      const result = await api.getUserInfo(this.accountId);
      if (this.api !== api) return;
      const profiles = result?.changed_profiles || {};
      const profile = profiles[this.accountId] || profiles[`${this.accountId}_0`]
        || Object.values(profiles).find((item) => String(item?.userId || item?.uid || '') === this.accountId);
      if (!profile) return;
      this.accountName = String(profile.displayName || profile.zaloName || profile.zalo_name || '').slice(0, 160) || null;
      const avatarUrl = profile.avatar;
      if (typeof avatarUrl !== 'string' || !avatarUrl) return;
      const parsed = new URL(avatarUrl);
      if (parsed.protocol !== 'https:') return;
      const response = await fetch(parsed, { redirect: 'error', signal: AbortSignal.timeout(8000) });
      const contentType = response.headers.get('content-type') || '';
      const declaredSize = Number(response.headers.get('content-length') || 0);
      if (!response.ok || !contentType.toLowerCase().startsWith('image/') || declaredSize > 1024 * 1024) return;
      const reader = response.body?.getReader();
      if (!reader) return;
      const chunks = []; let size = 0;
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        size += value.length;
        if (size > 1024 * 1024) { await reader.cancel(); return; }
        chunks.push(Buffer.from(value));
      }
      if (size && this.api === api) this.accountAvatar = Buffer.concat(chunks).toString('base64');
    } catch { /* Profile lookup is optional; keep the live connection status. */ }
  }
  async attach(api) {
    this.api = api; this.accountId = String(api.getOwnId()); this.qr = null;
    const receive = (message, historical = false) => {
      if (this.api !== api) return;
      const event = normalizeMessage(message, historical);
      if (!event) return;
      try { this.store.enqueue(this.accountId, event); }
      catch { this.state = 'error'; console.error('Zalo inbox persistence failed; operator action required.'); }
    };
    api.listener.on('message', (message) => {
      receive(message);
      if (this.api !== api) return;
      try {
        const sent = quoteFromEcho(message);
        if (sent) this.store.rememberQuote(sent.threadId, sent.msgId, sent.body);
      } catch { /* Không lưu được dữ liệu trả lời thì tin nhắc nhở vẫn gửi, chỉ không có khung trích dẫn. */ }
    });
    api.listener.on('old_messages', (messages, type) => {
      if (type === 0) for (const message of messages) receive(message, true);
    });
    api.listener.onConnected(() => {
      if (this.api !== api) return;
      this.state = 'connected'; this.reconnectAttempts = 0;
      // One bounded server-provided recent-history batch; no claim of a full Zalo archive export.
      api.listener.requestOldMessages(0);
    });
    api.listener.onClosed((code) => {
      if (this.api !== api) return;
      this.state = 'disconnected';
      this.scheduleReconnect(api, code);
    });
    api.listener.onError(() => { if (this.api === api) this.state = 'error'; });
    this.state = 'connected';
    api.listener.start({ retryOnClose: true }); // zca-js tự thử lại các mã đóng tạm thời; mã còn lại do scheduleReconnect
    void this.loadAccountProfile(api);
  }
  // Zalo đóng kết nối (mạng rớt, hoặc "có kết nối khác đang mở" - mã 3000): tự nối lại với thời gian chờ tăng dần.
  // Mã 3000 chờ lâu hơn để không giành phiên liên tục với người đang dùng Zalo bằng cùng tài khoản.
  scheduleReconnect(api, code) {
    const attempt = this.reconnectAttempts = (this.reconnectAttempts || 0) + 1;
    const base = code === DUPLICATE_CONNECTION ? this.reconnectBaseMs * 10 : this.reconnectBaseMs;
    const delay = Math.min(base * 2 ** Math.min(attempt - 1, 4), this.reconnectMaxMs);
    clearTimeout(this.reconnectTimer);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      if (this.api !== api) return; // đã đăng xuất hoặc đổi tài khoản
      try { api.listener.start({ retryOnClose: true }); }
      catch { this.scheduleReconnect(api, 0); }
    }, delay);
    this.reconnectTimer.unref?.();
  }
  async logout() {
    ++this.generation; this.abortQR?.(); clearTimeout(this.reconnectTimer); this.reconnectTimer = null; this.api?.listener.stop();
    this.api = null; this.accountId = null; this.accountName = null; this.accountAvatar = null;
    this.qr = null; this.state = 'disconnected';
    // Keep a cancelled login task reserved until it settles, preventing a new login racing its cleanup.
    await rm(join(this.dataDir, 'credentials.json'), { force: true });
    await rm(join(this.dataDir, 'credentials.json.tmp'), { force: true });
  }
  async flush() {
    if (this.flushing) return;
    this.flushing = true;
    try {
      const rows = this.store.pending(this.accountId);
      if (!rows.length) return;
      const response = await this.fetchImpl(`${this.odooUrl}/dac_zalo/bridge/events`, {
        method: 'POST', redirect: 'error', signal: AbortSignal.timeout(30000),
        headers: { Authorization: `Bearer ${this.token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ account_id: rows[0].account, events: rows.map((row) => JSON.parse(row.body)) }),
      });
      if (response.ok && (await response.json()).accepted === true) this.store.acknowledge(rows);
    } catch { /* Keep the persisted batch for retry; do not log message bodies or tokens. */ }
    finally { this.flushing = false; }
  }
  // Friends + groups of the logged-in account, cached to avoid hammering Zalo (unofficial API).
  async listThreads() {
    if (!this.api || this.state !== 'connected') throw new Error('not_connected');
    const api = this.api;
    if (this.threadCache?.api === api && Date.now() - this.threadCache.at < THREAD_CACHE_MS) return this.threadCache.items;
    const items = [];
    const friends = await api.getAllFriends();
    for (const friend of Array.isArray(friends) ? friends : []) {
      const id = String(friend?.userId || '');
      if (!id) continue;
      items.push({ thread_id: id, thread_type: 'user', name: String(friend.displayName || friend.zaloName || id).slice(0, 160), members: null });
    }
    const groupIds = Object.keys((await api.getAllGroups())?.gridVerMap || {});
    for (let index = 0; index < groupIds.length; index += 50) {
      const info = await api.getGroupInfo(groupIds.slice(index, index + 50));
      for (const [id, group] of Object.entries(info?.gridInfoMap || {})) {
        items.push({ thread_id: String(id), thread_type: 'group', name: String(group?.name || id).slice(0, 160), members: Number(group?.totalMember) || null });
      }
    }
    if (this.api === api) this.threadCache = { api, at: Date.now(), items };
    return items;
  }
  // Thành viên một nhóm (trừ chính tài khoản trợ lý), cache 5 phút như danh sách hội thoại.
  async listGroupMembers(threadId) {
    if (!USER_ID.test(String(threadId))) throw new Error('invalid_payload');
    if (!this.api || this.state !== 'connected') throw new Error('not_connected');
    const api = this.api;
    this.memberCache ||= new Map();
    const cached = this.memberCache.get(threadId);
    if (cached && cached.api === api && Date.now() - cached.at < THREAD_CACHE_MS) return cached.items;
    const group = (await api.getGroupInfo(threadId))?.gridInfoMap?.[threadId];
    if (!group) throw new Error('not_found');
    const names = new Map();
    for (const member of Array.isArray(group.currentMems) ? group.currentMems : []) {
      const id = String(member?.id || '');
      if (id) names.set(id, String(member.dName || member.zaloName || '').trim());
    }
    const ids = new Set([...names.keys()]);
    for (const id of Array.isArray(group.memberIds) ? group.memberIds : []) ids.add(String(id));
    for (const version of Array.isArray(group.memVerList) ? group.memVerList : []) ids.add(String(version).split('_')[0]);
    ids.delete(String(this.accountId));
    for (const id of [...ids]) if (!USER_ID.test(id)) ids.delete(id);
    const missing = [...ids].filter((id) => !names.get(id));
    for (let index = 0; index < missing.length; index += 50) {
      const profiles = (await api.getGroupMembersInfo(missing.slice(index, index + 50)))?.profiles || {};
      for (const [id, profile] of Object.entries(profiles)) names.set(String(id), String(profile?.displayName || profile?.zaloName || '').trim());
    }
    const items = [...ids]
      .map((id) => ({ uid: id, name: (names.get(id) || id).slice(0, 160) }))
      .sort((a, b) => fold(a.name).localeCompare(fold(b.name)));
    if (this.api === api) this.memberCache.set(threadId, { api, at: Date.now(), items });
    return items;
  }
  async searchThreads(query = '') {
    return { threads: matchThreads(await this.listThreads(), String(query).slice(0, 100)) };
  }
  async send(payload) {
    for (const field of ['request_id', 'account_id', 'thread_id', 'text']) {
      if (typeof payload[field] !== 'string' || !payload[field] || payload[field].length > (field === 'text' ? 12000 : 200)) throw new Error('invalid_payload');
    }
    if (payload.thread_type !== undefined && !['user', 'group'].includes(payload.thread_type)) throw new Error('invalid_payload');
    if (payload.quote_message_id !== undefined && payload.quote_message_id !== null
      && (typeof payload.quote_message_id !== 'string' || !MESSAGE_ID.test(payload.quote_message_id))) throw new Error('invalid_payload');
    const wantsQuote = typeof payload.quote_message_id === 'string';
    const saved = wantsQuote ? this.store.quote(payload.thread_id, idKey(payload.quote_message_id)) : null;
    const quote = saved ? { ...saved, uidFrom: payload.account_id } : null;
    const content = buildContent(payload, quote); // kiểm tra kiểu chữ trước khi giữ chỗ request_id
    if (payload.account_id !== this.accountId) throw new Error('wrong_account');
    if (!this.api || this.state !== 'connected') throw new Error('not_connected');
    const previous = this.store.reserve(payload.request_id, payload);
    if (previous) return { delivery: previous.state === 'sent' ? 'sent' : 'uncertain', message_id: previous.message_id };
    let timer;
    try {
      const result = await Promise.race([
        this.api.sendMessage(content, payload.thread_id, payload.thread_type === 'group' ? 1 : 0),
        new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('send_timeout')), this.sendTimeoutMs); }),
      ]);
      const id = result?.message?.msgId;
      if (!id) throw new Error('no_message_id');
      this.store.finish(payload.request_id, 'sent', String(id));
      return { delivery: 'sent', message_id: String(id), ...(wantsQuote ? { quoted: Boolean(quote) } : {}) };
    } catch {
      // A failed network call may already have delivered; never automatically send it twice.
      const api = this.api;
      if (api === this.api && typeof api.getUserInfo === 'function') {
        try { await api.getUserInfo(this.accountId); }
        catch { if (this.api === api && this.state === 'connected') this.state = 'error'; }
      }
      this.store.finish(payload.request_id, 'uncertain');
      return { delivery: 'uncertain', message_id: null, connection_state: this.state };
    } finally { clearTimeout(timer); }
  }
}
