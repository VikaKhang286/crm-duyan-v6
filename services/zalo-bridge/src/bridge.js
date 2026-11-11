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
  constructor({ zalo, store, dataDir, odooUrl, token, fetchImpl = fetch, sendTimeoutMs = 25000 }) {
    Object.assign(this, { zalo, store, dataDir, odooUrl, token, fetchImpl, sendTimeoutMs });
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
    api.listener.on('message', (message) => receive(message));
    api.listener.on('old_messages', (messages, type) => {
      if (type === 0) for (const message of messages) receive(message, true);
    });
    api.listener.onConnected(() => {
      if (this.api !== api) return;
      this.state = 'connected';
      // One bounded server-provided recent-history batch; no claim of a full Zalo archive export.
      api.listener.requestOldMessages(0);
    });
    api.listener.onClosed(() => { if (this.api === api) this.state = 'disconnected'; });
    api.listener.onError(() => { if (this.api === api) this.state = 'error'; });
    this.state = 'connected';
    api.listener.start();
    void this.loadAccountProfile(api);
  }
  async logout() {
    ++this.generation; this.abortQR?.(); this.api?.listener.stop();
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
  async searchThreads(query = '') {
    return { threads: matchThreads(await this.listThreads(), String(query).slice(0, 100)) };
  }
  async send(payload) {
    for (const field of ['request_id', 'account_id', 'thread_id', 'text']) {
      if (typeof payload[field] !== 'string' || !payload[field] || payload[field].length > (field === 'text' ? 12000 : 200)) throw new Error('invalid_payload');
    }
    if (payload.thread_type !== undefined && !['user', 'group'].includes(payload.thread_type)) throw new Error('invalid_payload');
    if (payload.account_id !== this.accountId) throw new Error('wrong_account');
    if (!this.api || this.state !== 'connected') throw new Error('not_connected');
    const previous = this.store.reserve(payload.request_id, payload);
    if (previous) return { delivery: previous.state === 'sent' ? 'sent' : 'uncertain', message_id: previous.message_id };
    let timer;
    try {
      const result = await Promise.race([
        this.api.sendMessage(payload.text, payload.thread_id, payload.thread_type === 'group' ? 1 : 0),
        new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('send_timeout')), this.sendTimeoutMs); }),
      ]);
      const id = result?.message?.msgId;
      if (!id) throw new Error('no_message_id');
      this.store.finish(payload.request_id, 'sent', String(id));
      return { delivery: 'sent', message_id: String(id) };
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
