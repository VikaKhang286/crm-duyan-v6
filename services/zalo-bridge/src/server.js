import { createServer } from 'node:http';
import { join } from 'node:path';
import { mkdirSync, chmodSync } from 'node:fs';
import { Zalo } from 'zca-js';
import { Store } from './store.js';
import { Bridge, authorized } from './bridge.js';

process.umask(0o077);
const token = process.env.ZALO_BRIDGE_TOKEN;
const odooUrl = process.env.ODOO_URL;
const dataDir = process.env.DATA_DIR || './data';
if (!token || token.length < 32 || !odooUrl || !/^https?:\/\//.test(odooUrl)) throw new Error('Set ODOO_URL and ZALO_BRIDGE_TOKEN (at least 32 characters).');
mkdirSync(dataDir, { recursive: true, mode: 0o700 }); chmodSync(dataDir, 0o700);
const store = new Store(join(dataDir, 'queue.sqlite'));
const bridge = new Bridge({
  zalo: new Zalo({ selfListen: true, checkUpdate: false, logging: false }),
  store, dataDir, odooUrl: odooUrl.replace(/\/$/, ''), token,
});
const server = createServer(async (req, res) => {
  const respond = (status, data) => { res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }); res.end(JSON.stringify(data)); };
  if (req.method === 'GET' && req.url === '/health') return respond(200, { alive: true });
  if (!authorized(req.headers.authorization, token)) return respond(401, { error: 'unauthorized' });
  try {
    if (req.method === 'GET' && req.url === '/status') return respond(200, bridge.status());
    if (req.method !== 'POST') return respond(404, { error: 'not_found' });
    let text = ''; let bytes = 0;
    for await (const chunk of req) { bytes += chunk.length; if (bytes > 1024 * 1024) return respond(413, { error: 'payload_too_large' }); text += chunk; }
    const body = JSON.parse(text || '{}');
    if (req.url === '/login') { bridge.startLogin(); return respond(202, bridge.status()); }
    if (req.url === '/logout') { await bridge.logout(); return respond(200, bridge.status()); }
    if (req.url === '/send') return respond(200, await bridge.send(body));
    return respond(404, { error: 'not_found' });
  } catch (error) {
    const known = ['invalid_payload', 'wrong_account', 'not_connected', 'request_id_conflict'];
    respond(error instanceof SyntaxError ? 400 : 409, { error: known.includes(error.message) ? error.message : 'request_failed' });
  }
});
server.requestTimeout = 35000;
await bridge.restore();
const timer = setInterval(() => { void bridge.flush(); }, 2000);
server.listen(Number(process.env.PORT || 8095), process.env.HOST || '127.0.0.1');
for (const signal of ['SIGTERM', 'SIGINT']) process.on(signal, () => {
  clearInterval(timer); bridge.api?.listener.stop(); server.close(() => { store.close(); process.exit(0); });
});
