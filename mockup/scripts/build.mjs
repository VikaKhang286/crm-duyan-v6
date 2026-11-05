import { cp, mkdir, readFile, readdir, rm } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
const root = fileURLToPath(new URL("../", import.meta.url));
const dist = path.join(root, "dist");
await rm(dist, { recursive: true, force: true });
await mkdir(dist, { recursive: true });
for (const name of ["index.html", "src", "assets"])
  await cp(path.join(root, name), path.join(dist, name), { recursive: true });
const history = path.join(root, "changelogs");
for (const id of await readdir(history).catch((error) => {
  if (error.code === "ENOENT") return [];
  throw error;
})) {
  if (!/^CL-\d{8}-\d{3,}$/.test(id)) continue;
  const manifest = JSON.parse(
    await readFile(path.join(history, id, "manifest.json"), "utf8"),
  );
  if (manifest.status === "ready_for_handoff")
    await cp(path.join(history, id), path.join(dist, "changelogs", id), {
      recursive: true,
    });
}
console.log("Built mockup/dist: static files only, ready for GitHub Pages.");
