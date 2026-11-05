import { readFile, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { screens } from "../src/data.js";
const root = fileURLToPath(new URL("../", import.meta.url));
const sourceIndex = process.argv.indexOf("--source-root");
if (sourceIndex >= 0 && !process.argv[sourceIndex + 1])
  throw Error("--source-root requires a directory");
const sourceRoot =
  sourceIndex < 0
    ? path.resolve(root, "..")
    : path.resolve(process.argv[sourceIndex + 1]);
const baselineFile = path.join(root, "baseline.json");
const common = [
  "addons/home_menu/static/src/xml/backend/navbar.xml",
  "addons/home_menu/static/src/css/backend/home_menu.css",
  "addons/Chameleon/static/src/scss/backend_modern.scss",
  "addons/dac_erp/static/src/css/backend/zzz_modern_backend.css",
];
const files = [
  ...new Set([...common, ...Object.values(screens).flatMap((s) => s.sources)]),
].sort();
const hashes = {};
for (const name of files)
  hashes[name] = createHash("sha256")
    .update(await readFile(path.join(sourceRoot, name)))
    .digest("hex");
if (process.argv[2] === "update") {
  const prior = await readFile(baselineFile, "utf8")
    .then(JSON.parse)
    .catch((e) => {
      if (e.code === "ENOENT") return {};
      throw e;
    });
  const commit = execFileSync("git", ["rev-parse", "HEAD"], {
    cwd: sourceRoot,
    encoding: "utf8",
  }).trim();
  const dirty = execFileSync("git", ["status", "--porcelain", "--", ...files], {
    cwd: sourceRoot,
    encoding: "utf8",
  }).trim();
  await writeFile(
    baselineFile,
    JSON.stringify(
      {
        ...prior,
        sourceCommit: commit,
        sourceHasUncommittedChanges: Boolean(dirty),
        sourceRecordedAt: new Date().toISOString(),
        files: hashes,
      },
      null,
      2,
    ) + "\n",
  );
  console.log(
    `Recorded ${files.length} source fingerprints. This records source only; review the UI before claiming it matches.`,
  );
} else if (process.argv[2] === "check") {
  const baseline = JSON.parse(await readFile(baselineFile, "utf8"));
  const changed = files.filter((name) => baseline.files[name] !== hashes[name]);
  if (changed.length) {
    console.error(
      "Odoo UI source changed; review the matching mockup screens:\n" +
        changed.map((s) => `  ${s}`).join("\n"),
    );
    process.exitCode = 1;
  } else
    console.log(
      `Baseline matches ${files.length} local Odoo UI source files. Live production parity is a separate visual check.`,
    );
} else
  throw Error(
    "Usage: node scripts/baseline.mjs check|update [--source-root PATH]",
  );
