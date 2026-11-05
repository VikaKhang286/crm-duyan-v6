import { access, readdir, readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { seed, screens, states } from "../src/data.js";
const root = fileURLToPath(new URL("../", import.meta.url));
for (const directory of ["src", "scripts"]) {
  for (const name of await readdir(path.join(root, directory))) {
    if (/\.(mjs|js)$/.test(name))
      execFileSync(process.execPath, [
        "--check",
        path.join(root, directory, name),
      ]);
  }
}
const ids = seed.orders.map((o) => o.id);
if (ids.length !== new Set(ids).size) throw Error("Duplicate order IDs");
for (const order of seed.orders)
  if (!states[order.state] || order.paid > order.total)
    throw Error(`Invalid fixture: ${order.id}`);
for (const task of seed.tasks)
  if (!ids.includes(task.order)) throw Error(`Broken order link: ${task.id}`);
for (const screen of Object.values(screens))
  for (const source of screen.sources)
    await access(path.resolve(root, "..", source));
const html = await readFile(path.join(root, "index.html"), "utf8");
for (const [, asset] of html.matchAll(/(?:src|href)="\.\/([^"#]+)"/g))
  await access(path.join(root, asset));
console.log(
  `Checked JS syntax, static entry points, ${ids.length} demo orders, linked tasks, and ${Object.keys(screens).length} source mappings.`,
);
