import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, readFile, stat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Bridge, authorized, normalizeMessage, matchThreads } from '../src/bridge.js';
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
