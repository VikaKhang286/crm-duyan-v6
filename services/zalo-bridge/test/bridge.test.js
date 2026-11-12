import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, readFile, stat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Bridge, authorized, normalizeMessage, matchThreads, buildContent, quoteFromEcho, idKey } from '../src/bridge.js';
import { Store } from '../src/store.js';

const TOKEN = 'test-secret-that-is-longer-than-thirty-two-characters';
const payload = { request_id: '1', account_id: 'assistant', thread_id: 'employee', text: 'Xin chào' };
function fixture(overrides = {}) {
  const store = new Store(':memory:');
  const bridge = new Bridge({ store, zalo: {}, dataDir: '/unused', odooUrl: 'https://odoo.invalid', token: TOKEN, ...overrides });
  bridge.accountId = 'assistant'; bridge.state = 'connected';
  return { bridge, store };
}

test('shared secret is required and compared exactly', () => {
  assert.equal(authorized(`Bearer ${TOKEN}`, TOKEN), true);
  for (const header of ['', 'Bearer wrong', `bearer ${TOKEN}`, `Bearer ${TOKEN}x`]) assert.equal(authorized(header, TOKEN), false);
  assert.equal(authorized('Bearer short', 'short'), false);
});

test('only direct messages are normalized; self echoes retain direction', () => {
  const message = { type: 0, threadId: '00123', isSelf: false, data: { msgId: '90071992547409931', content: 'Chào', ts: '1780000000000' } };
  assert.equal(normalizeMessage({ ...message, type: 1 }), null);
  assert.deepEqual(normalizeMessage(message), { thread_id: '00123', message_id: '90071992547409931', text: 'Chào', timestamp: 1780000000000, direction: 'in', historical: false });
  assert.equal(normalizeMessage({ ...message, isSelf: true }, true).direction, 'out');
  assert.equal(normalizeMessage({ ...message, data: { ...message.data, content: { href: 'https://private.invalid/image' }, msgType: 'chat.photo' } }).text, '[Tin nhắn chat.photo]');
});

test('inbox survives restart and redelivered events are deduplicated', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'dac-zalo-'));
  try {
    const path = join(dir, 'queue.sqlite');
    let store = new Store(path);
    const event = { thread_id: 'e', message_id: 'm', direction: 'in', text: 'saved' };
    store.enqueue('a', event); store.enqueue('a', event); store.close();
    store = new Store(path); assert.equal(store.count(), 1);
    store.acknowledge(store.pending()); assert.equal(store.count(), 0); store.close();
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('callback failure, wrong account and missing acceptance never discard inbox', async () => {
  let status = 409; let accepted = true;
  const { bridge, store } = fixture({ fetchImpl: async () => ({ ok: status === 200, json: async () => ({ accepted }) }) });
  store.enqueue('assistant', { thread_id: 'e', message_id: 'm', direction: 'in', text: 'pending' });
  await bridge.flush(); assert.equal(store.count(), 1);
  status = 200; accepted = false; await bridge.flush(); assert.equal(store.count(), 1);
  accepted = true; await bridge.flush(); assert.equal(store.count(), 0); store.close();
});

test('queued messages from a mistaken login cannot block the current assistant account', async () => {
  const delivered = [];
  const { bridge, store } = fixture({ fetchImpl: async (_url, options) => {
    delivered.push(JSON.parse(options.body).account_id); return { ok: true, json: async () => ({ accepted: true }) };
  } });
  store.enqueue('mistaken-account', { thread_id: 'x', message_id: 'm1', direction: 'in', text: 'wrong account' });
  store.enqueue('assistant', { thread_id: 'y', message_id: 'm2', direction: 'in', text: 'current account' });
  await bridge.flush(); assert.deepEqual(delivered, ['assistant']); assert.equal(store.count(), 1); store.close();
});

test('send replay uses durable result and does not send twice', async () => {
  const { bridge, store } = fixture(); let calls = 0;
  bridge.api = { sendMessage: async () => { calls++; return { message: { msgId: 'message-1' } }; } };
  assert.deepEqual(await bridge.send(payload), { delivery: 'sent', message_id: 'message-1' });
  assert.equal((await bridge.send(payload)).delivery, 'sent'); assert.equal(calls, 1);
  assert.equal((await bridge.send({ text: payload.text, thread_id: payload.thread_id, account_id: payload.account_id, request_id: payload.request_id })).delivery, 'sent');
  await assert.rejects(bridge.send({ ...payload, text: 'different' }), /request_id_conflict/); store.close();
});

test('uncertain network result and crash reservations never automatically resend', async () => {
  const { bridge, store } = fixture(); let calls = 0;
  bridge.api = { sendMessage: async () => { calls++; throw new Error('timeout after delivery'); } };
  assert.equal((await bridge.send(payload)).delivery, 'uncertain');
  assert.equal((await bridge.send(payload)).delivery, 'uncertain'); assert.equal(calls, 1);
  store.reserve('2', { ...payload, request_id: '2' });
  assert.equal((await bridge.send({ ...payload, request_id: '2' })).delivery, 'uncertain'); assert.equal(calls, 1); store.close();
});

test('wrong assistant account and invalid payload cannot send', async () => {
  const { bridge, store } = fixture(); bridge.api = { sendMessage: async () => assert.fail('must not send') };
  await assert.rejects(bridge.send({ ...payload, account_id: 'another' }), /wrong_account/);
  await assert.rejects(bridge.send({ ...payload, text: 'x'.repeat(12001) }), /invalid_payload/);
  store.close();
});

test('QR login persists credentials privately and status never returns cookies', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'dac-zalo-qr-'));
  const store = new Store(':memory:');
  let stopped = false; const callbacks = {};
  const api = {
    getOwnId: () => 'assistant',
    getContext: () => ({ cookie: { toJSON: () => ({ cookies: [{ value: 'PRIVATE-COOKIE' }] }) }, imei: 'device', userAgent: 'ua' }),
    listener: { on: () => {}, onConnected: (cb) => { callbacks.connected = cb; }, onClosed: () => {}, onError: () => {},
      start: () => callbacks.connected(), requestOldMessages: () => {}, stop: () => { stopped = true; } },
  };
  let finish;
  const zalo = { loginQR: async (_options, cb) => {
    cb({ type: 0, data: { image: 'data:image/png;base64,aW1hZ2U=' }, actions: { abort: () => {} } });
    await new Promise((resolve) => { finish = resolve; });
    return api;
  } };
  const bridge = new Bridge({ zalo, store, dataDir: dir, odooUrl: 'https://odoo.invalid', token: TOKEN });
  try {
    bridge.startLogin(); assert.equal(bridge.status().qr_image, 'aW1hZ2U='); finish(); await bridge.loginTask;
    assert.equal(bridge.status().state, 'connected'); assert.equal(bridge.status().qr_image, null);
    assert.ok((await readFile(join(dir, 'credentials.json'), 'utf8')).includes('PRIVATE-COOKIE'));
    assert.equal((await stat(join(dir, 'credentials.json'))).mode & 0o777, 0o600);
    assert.ok(!JSON.stringify(bridge.status()).includes('PRIVATE-COOKIE'));
    await bridge.logout(); assert.ok(stopped); assert.equal(bridge.status().state, 'disconnected');
    await assert.rejects(readFile(join(dir, 'credentials.json')), { code: 'ENOENT' });
  } finally { store.close(); await rm(dir, { recursive: true, force: true }); }
});

test('a hung Zalo send is bounded and never automatically repeated', async () => {
  const { bridge, store } = fixture({ sendTimeoutMs: 5 }); let calls = 0;
  bridge.api = { sendMessage: () => { calls++; return new Promise(() => {}); } };
  assert.equal((await bridge.send(payload)).delivery, 'uncertain');
  assert.equal((await bridge.send(payload)).delivery, 'uncertain'); assert.equal(calls, 1); store.close();
});

test('logout during credential persistence cannot resurrect the session', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'dac-zalo-logout-')); const store = new Store(':memory:');
  let started; let finish; const entered = new Promise((resolve) => { started = resolve; });
  const api = { getContext: () => ({ cookie: { toJSON: () => ({ cookies: [] }) }, imei: 'device', userAgent: 'ua' }), listener: { stop: () => {} } };
  const bridge = new Bridge({ zalo: { loginQR: async () => api }, store, dataDir: dir, odooUrl: 'http://odoo.invalid', token: TOKEN });
  const original = bridge.saveCredentials.bind(bridge);
  bridge.saveCredentials = async (credentials) => { started(); await new Promise((resolve) => { finish = resolve; }); await original(credentials); };
  try {
    bridge.startLogin(); const task = bridge.loginTask; await entered;
    await bridge.logout(); finish(); await task;
    assert.equal(bridge.api, null); assert.equal(bridge.state, 'disconnected');
    await assert.rejects(readFile(join(dir, 'credentials.json')), { code: 'ENOENT' });
  } finally { store.close(); await rm(dir, { recursive: true, force: true }); }
});

test('thread search lists friends and groups, folds Vietnamese accents and caches the result', async () => {
  let calls = 0;
  const api = {
    getAllFriends: async () => { calls += 1; return [{ userId: 'u1', displayName: 'Nguyễn Văn Kiệt' }, { userId: 'u2', zaloName: 'Lan' }]; },
    getAllGroups: async () => ({ gridVerMap: { g1: '1', g2: '1' } }),
    getGroupInfo: async (ids) => ({ gridInfoMap: Object.fromEntries(ids.map((id) => [id, { name: id === 'g1' ? 'Nhóm Sale Đơn hàng' : 'Thiết kế', totalMember: 7 }])) }),
  };
  const { bridge, store } = fixture(); bridge.api = api;
  const all = (await bridge.searchThreads('')).threads;
  assert.equal(all.length, 4);
  assert.deepEqual((await bridge.searchThreads('kiet')).threads.map((t) => t.thread_id), ['u1']);
  const groups = (await bridge.searchThreads('don hang')).threads;
  assert.deepEqual(groups, [{ thread_id: 'g1', thread_type: 'group', name: 'Nhóm Sale Đơn hàng', members: 7 }]);
  assert.equal(calls, 1, 'second and third search must reuse the cache');
  bridge.state = 'disconnected';
  await assert.rejects(bridge.searchThreads('x'), /not_connected/);
  store.close();
});

test('matchThreads limits results and matches an exact thread id', () => {
  const many = Array.from({ length: 80 }, (_, i) => ({ thread_id: `t${i}`, thread_type: 'user', name: `Khách ${i}` }));
  assert.equal(matchThreads(many, 'khach').length, 50);
  assert.deepEqual(matchThreads(many, 't7').map((t) => t.thread_id), ['t7']);
});

test('group messages are sent with the group thread type and never collide with user sends', async () => {
  const sent = [];
  const { bridge, store } = fixture();
  bridge.api = { sendMessage: async (text, thread, type) => { sent.push([thread, type]); return { message: { msgId: `m${sent.length}` } }; }, getUserInfo: async () => ({}) };
  assert.equal((await bridge.send({ ...payload, request_id: 'g', thread_id: 'G1', thread_type: 'group' })).delivery, 'sent');
  assert.equal((await bridge.send({ ...payload, request_id: 'u', thread_id: 'G1' })).delivery, 'sent');
  assert.deepEqual(sent, [['G1', 1], ['G1', 0]]);
  await assert.rejects(bridge.send({ ...payload, request_id: 'x', thread_type: 'channel' }), /invalid_payload/);
  store.close();
});

test('bold styles are sent as zca-js styled content; plain text stays a plain string', async () => {
  const sent = [];
  const { bridge, store } = fixture();
  bridge.api = { sendMessage: async (content) => { sent.push(content); return { message: { msgId: `m${sent.length}` } }; }, getUserInfo: async () => ({}) };
  const text = 'Khách yêu cầu tư vấn\n📞 0909';
  await bridge.send({ ...payload, request_id: 's1', text, styles: [{ start: 0, len: 20, style: 'bold' }] });
  await bridge.send({ ...payload, request_id: 's2', text: 'không định dạng' });
  await bridge.send({ ...payload, request_id: 's3', text: 'rỗng', styles: [] });
  assert.deepEqual(sent[0], { msg: text, styles: [{ start: 0, len: 20, st: 'b' }] });
  assert.equal(sent[1], 'không định dạng');
  assert.equal(sent[2], 'rỗng');
  store.close();
});

test('invalid styles are rejected before anything is reserved or sent', async () => {
  const { bridge, store } = fixture();
  let calls = 0;
  bridge.api = { sendMessage: async () => { calls += 1; return { message: { msgId: 'x' } }; } };
  const bad = [
    [{ start: 0, len: 5, style: 'red' }], [{ start: -1, len: 2, style: 'bold' }], [{ start: 0, len: 0, style: 'bold' }],
    [{ start: 0, len: 99, style: 'bold' }], [{ start: 1.5, len: 2, style: 'bold' }], 'bold', [null], Array(21).fill({ start: 0, len: 1, style: 'bold' }),
  ];
  for (const [index, styles] of bad.entries()) {
    await assert.rejects(bridge.send({ ...payload, request_id: `bad${index}`, text: 'xin chào', styles }), /invalid_payload/);
  }
  assert.equal(calls, 0);
  assert.equal(store.reserve('bad0', payload), null, 'a rejected request id is still free to use');
  assert.equal(buildContent({ text: 'abc' }), 'abc');
  store.close();
});

test('mentions are sent with the group message and validated before anything is reserved', async () => {
  const sent = [];
  const { bridge, store } = fixture();
  bridge.api = { sendMessage: async (content, thread, type) => { sent.push([content, type]); return { message: { msgId: `m${sent.length}` } }; }, getUserInfo: async () => ({}) };
  const text = 'Khách yêu cầu\n📞 0909\n@An @Bình Phạm';
  const at = text.indexOf('@An');
  const mentions = [{ pos: at, len: 3, uid: '111' }, { pos: at + 4, len: 10, uid: '222' }];
  const styles = [{ start: 0, len: 13, style: 'bold' }];
  assert.equal((await bridge.send({ ...payload, request_id: 'm1', thread_id: 'G1', thread_type: 'group', text, styles, mentions })).delivery, 'sent');
  assert.deepEqual(sent[0], [{ msg: text, styles: [{ start: 0, len: 13, st: 'b' }], mentions }, 1]);
  const bad = [
    [{ pos: at, len: 3, uid: '1x1' }], [{ pos: at, len: 3, uid: 111 }], [{ pos: 0, len: 3, uid: '1' }], [{ pos: at, len: 1, uid: '1' }],
    [{ pos: at, len: 99, uid: '1' }], [{ pos: -1, len: 3, uid: '1' }], [{ pos: 1.5, len: 3, uid: '1' }], 'x', [null],
    [{ pos: at, len: 3, uid: '1' }, { pos: at + 1, len: 3, uid: '2' }], Array(31).fill({ pos: at, len: 3, uid: '1' }),
  ];
  for (const [index, list] of bad.entries()) {
    await assert.rejects(bridge.send({ ...payload, request_id: `bm${index}`, thread_id: 'G1', thread_type: 'group', text, mentions: list }), /invalid_payload/);
  }
  await assert.rejects(bridge.send({ ...payload, request_id: 'bmu', text, mentions }), /invalid_payload/, 'mentions only work in groups');
  assert.equal(sent.length, 1);
  assert.equal(buildContent({ text: 'abc', thread_type: 'group', mentions: [] }), 'abc');
  store.close();
});

test('group members: names from the group info, missing names looked up, self excluded, cached, sorted without accents', async () => {
  let infoCalls = 0; const looked = [];
  const api = {
    getGroupInfo: async (id) => { infoCalls += 1; return { gridInfoMap: { [id]: {
      currentMems: [{ id: '111', dName: 'Đặng Văn B' }, { id: '222', dName: '', zaloName: 'An' }],
      memberIds: ['111', '222', '333', '999', 'khong-hop-le'], memVerList: ['444_9', '111_1'] } } }; },
    getGroupMembersInfo: async (ids) => { looked.push(ids); return { profiles: { 333: { displayName: 'Lan Phạm' }, 444: { zaloName: 'Công' } } }; },
  };
  const { bridge, store } = fixture(); bridge.api = api; bridge.accountId = '999';
  const items = await bridge.listGroupMembers('5001');
  assert.deepEqual(items.map((m) => m.name), ['An', 'Công', 'Đặng Văn B', 'Lan Phạm']);
  assert.deepEqual(looked, [['333', '444']]);
  assert.deepEqual(await bridge.listGroupMembers('5001'), items);
  assert.equal(infoCalls, 1, 'second call must use the cache');
  await assert.rejects(bridge.listGroupMembers('../x'), /invalid_payload/);
  bridge.api = { getGroupInfo: async () => ({ gridInfoMap: {} }) };
  await assert.rejects(bridge.listGroupMembers('6001'), /not_found/);
  bridge.state = 'disconnected';
  await assert.rejects(bridge.listGroupMembers('5001'), /not_connected/);
  store.close();
});

function fakeListener() {
  const handlers = {};
  return { handlers, starts: [], stops: 0,
    on() {}, requestOldMessages() {},
    onConnected(cb) { handlers.connected = cb; }, onClosed(cb) { handlers.closed = cb; }, onError(cb) { handlers.error = cb; },
    start(options) { this.starts.push(options); }, stop() { this.stops += 1; } };
}
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

test('a closed Zalo connection is reopened automatically with growing waits; duplicate-connection waits longer', async () => {
  const listener = fakeListener();
  const { bridge, store } = fixture({ reconnectBaseMs: 10, reconnectMaxMs: 80 });
  const api = { listener, getOwnId: () => 'assistant', getUserInfo: async () => ({ changed_profiles: {} }), getContext: () => ({}) };
  await bridge.attach(api);
  assert.deepEqual(listener.starts, [{ retryOnClose: true }]);
  listener.handlers.closed(1006, 'abnormal');
  assert.equal(bridge.state, 'disconnected');
  await wait(40);
  assert.equal(listener.starts.length, 2, 'reopened after the first wait');
  listener.handlers.closed(1006, 'abnormal again');
  await wait(15);
  assert.equal(listener.starts.length, 2, 'second wait is longer than the first');
  await wait(30);
  assert.equal(listener.starts.length, 3);
  listener.handlers.connected();
  assert.equal(bridge.state, 'connected');
  assert.equal(bridge.reconnectAttempts, 0, 'a successful connection resets the backoff');
  listener.handlers.closed(3000, 'duplicate');
  await wait(40);
  assert.equal(listener.starts.length, 3, 'duplicate connection waits ten times longer, so we do not fight another session');
  await wait(100);
  assert.equal(listener.starts.length, 4);
  store.close();
});

test('logout cancels a pending reconnect and a replaced account is never reopened', async () => {
  const first = fakeListener(); const second = fakeListener();
  const { bridge, store } = fixture({ reconnectBaseMs: 10, reconnectMaxMs: 20 });
  const make = (listener) => ({ listener, getOwnId: () => 'assistant', getUserInfo: async () => ({ changed_profiles: {} }), getContext: () => ({}) });
  await bridge.attach(make(first));
  first.handlers.closed(1006, 'x');
  await bridge.logout();
  await wait(40);
  assert.equal(first.starts.length, 1, 'no reconnect after logout');
  const replaced = fakeListener();
  await bridge.attach(make(replaced));
  replaced.handlers.closed(1006, 'x');
  await bridge.attach(make(second));
  await wait(40);
  assert.equal(replaced.starts.length, 1, 'the old account listener is not reopened');
  store.close();
});

test('a reminder can reply to an earlier message of this account: quote data comes from the Zalo echo, and falls back to plain when unknown', async () => {
  const sent = [];
  const { bridge, store } = fixture();
  bridge.api = { sendMessage: async (content) => { sent.push(content); return { message: { msgId: `m${sent.length}` } }; }, getUserInfo: async () => ({}) };
  const echo = { isSelf: true, threadId: 'G1', type: 1, data: { uidFrom: '0', msgId: '700123', cliMsgId: '1780000000001', ts: '1780000000002', msgType: 'webchat', content: 'Khách yêu cầu', propertyExt: { color: 0 }, ttl: 0 } };
  assert.equal(quoteFromEcho({ ...echo, isSelf: false }), null, 'only our own messages');
  assert.equal(quoteFromEcho({ ...echo, data: { ...echo.data, content: { href: 'x' } } }), null, 'only text messages');
  const saved = quoteFromEcho(echo);
  assert.equal(saved.msgId, '700123');
  store.rememberQuote(saved.threadId, saved.msgId, saved.body);
  const text = '@An Bạn đã liên hệ khách chưa ạ';
  const base = { ...payload, request_id: 'q1', thread_id: 'G1', thread_type: 'group', text, mentions: [{ pos: 0, len: 3, uid: '111' }] };
  const first = await bridge.send({ ...base, quote_message_id: '700123' });
  assert.deepEqual(first, { delivery: 'sent', message_id: 'm1', quoted: true });
  assert.deepEqual(sent[0].quote, { msgId: '700123', content: 'Khách yêu cầu', msgType: 'webchat', propertyExt: { color: 0 }, cliMsgId: '1780000000001', ts: '1780000000002', ttl: 0, uidFrom: 'assistant' });
  assert.equal(sent[0].msg, text);
  assert.deepEqual(sent[0].mentions, [{ pos: 0, len: 3, uid: '111' }]);
  // msgId lớn: phản hồi gửi tin đã bị làm tròn nhưng vẫn tìm ra đúng tin, và Zalo nhận msgId nguyên bản
  const big = quoteFromEcho({ ...echo, data: { ...echo.data, msgId: '7123456789012345678', cliMsgId: '1780000000009' } });
  store.rememberQuote(big.threadId, big.msgId, big.body);
  assert.notEqual(String(Number('7123456789012345678')), '7123456789012345678', 'the premise: a JS number cannot hold this id');
  await bridge.send({ ...base, request_id: 'qbig', text: 'Nhắc', mentions: undefined, quote_message_id: String(Number('7123456789012345678')) });
  assert.equal(sent[1].quote.msgId, '7123456789012345678');
  assert.equal(idKey('abc'), 'abc');
  const unknown = await bridge.send({ ...base, request_id: 'q2', text: 'Nhắc lại', mentions: undefined, quote_message_id: '999' });
  assert.deepEqual(unknown, { delivery: 'sent', message_id: 'm3', quoted: false });
  assert.equal(sent[2], 'Nhắc lại', 'unknown original: still reminds, without the quote frame');
  assert.deepEqual(await bridge.send({ ...payload, request_id: 'q3' }), { delivery: 'sent', message_id: 'm4' });
  for (const bad of ['abc', 12, '', '1'.repeat(31)]) {
    await assert.rejects(bridge.send({ ...payload, request_id: 'q4', quote_message_id: bad }), /invalid_payload/);
  }
  store.close();
});
