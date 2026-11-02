import { DatabaseSync } from 'node:sqlite';
import { createHash } from 'node:crypto';
import { mkdirSync, chmodSync } from 'node:fs';
import { dirname } from 'node:path';

export class Store {
  constructor(path) {
    if (path !== ':memory:') mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
    this.db = new DatabaseSync(path);
    if (path !== ':memory:') chmodSync(path, 0o600);
    this.db.exec(`PRAGMA journal_mode=WAL;
      CREATE TABLE IF NOT EXISTS inbox (key TEXT PRIMARY KEY, account TEXT NOT NULL, body TEXT NOT NULL, created INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS outbox (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, state TEXT NOT NULL, message_id TEXT);`);
  }
  enqueue(account, event) {
    const key = `${account}:${event.thread_id}:${event.direction}:${event.message_id}`;
    if (this.db.prepare('SELECT count(*) AS n FROM inbox').get().n >= 10000) throw new Error('spool_full');
    this.db.prepare('INSERT OR IGNORE INTO inbox VALUES (?, ?, ?, ?)').run(key, account, JSON.stringify(event), Date.now());
  }
  pending(account = null) {
    const first = account ? { account } : this.db.prepare('SELECT account FROM inbox ORDER BY created, rowid LIMIT 1').get();
    if (!first) return [];
    return this.db.prepare('SELECT key, account, body FROM inbox WHERE account=? ORDER BY created, rowid LIMIT 50').all(first.account);
  }
  acknowledge(rows) {
    this.db.exec('BEGIN');
    try {
      for (const row of rows) this.db.prepare('DELETE FROM inbox WHERE key=?').run(row.key);
      this.db.exec('COMMIT');
    } catch (error) { this.db.exec('ROLLBACK'); throw error; }
  }
  count() { return this.db.prepare('SELECT count(*) AS n FROM inbox').get().n; }
  reserve(id, payload) {
    const fingerprint = createHash('sha256').update(JSON.stringify([payload.account_id, payload.thread_id, payload.text])).digest('hex');
    const previous = this.db.prepare('SELECT * FROM outbox WHERE id=?').get(id);
    if (previous) {
      if (previous.fingerprint !== fingerprint) throw new Error('request_id_conflict');
      return previous;
    }
    this.db.prepare('INSERT INTO outbox VALUES (?, ?, ?, NULL)').run(id, fingerprint, 'sending');
    return null;
  }
  finish(id, state, messageId = null) {
    this.db.prepare('UPDATE outbox SET state=?, message_id=? WHERE id=?').run(state, messageId, id);
  }
  close() { this.db.close(); }
}
