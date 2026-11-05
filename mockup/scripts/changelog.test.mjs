import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { cp, mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

test("each handoff keeps its own frozen mockup, archive and Git version", async () => {
  const fixture = await mkdtemp(path.join(tmpdir(), "dac-changelog-test-"));
  try {
    const root = path.join(fixture, "mockup");
    await mkdir(path.join(root, "scripts"), { recursive: true });
    await mkdir(path.join(root, "src"));
    await mkdir(path.join(root, "assets"));
    for (const name of [
      "changelog.mjs",
      "pack-changelog.mjs",
      "check.mjs",
      "serve.mjs",
    ]) {
      await cp(
        fileURLToPath(new URL(name, import.meta.url)),
        path.join(root, "scripts", name),
      );
    }
    await writeFile(path.join(root, "package.json"), '{"type":"module"}');
    await writeFile(
      path.join(root, "index.html"),
      '<h1>Original snapshot</h1><script type="module" src="./src/app.js"></script>',
    );
    await writeFile(
      path.join(root, "src", "app.js"),
      'export const demo = "original";',
    );
    await writeFile(
      path.join(root, "src", "version.js"),
      "export const mockupRelease = null;",
    );
    await writeFile(
      path.join(root, "src", "data.js"),
      'export const seed={orders:[],tasks:[]}; export const states={}; export const screens={login:{sources:[]}}; export const roles={manager:"Quản lý"};',
    );
    const git = (...args) =>
      execFileSync("git", args, { cwd: fixture, encoding: "utf8" }).trim();
    git("init", "-q");
    git("config", "user.name", "Mockup Test");
    git("config", "user.email", "mockup@example.test");
    git("remote", "add", "origin", "git@github.com:example/mockup.git");
    git("add", ".");
    git("commit", "-qm", "fixture");
    const cli = (...args) =>
      spawnSync(
        process.execPath,
        [path.join(root, "scripts", "changelog.mjs"), ...args],
        { encoding: "utf8", cwd: fixture },
      );
    const ok = (...args) => {
      const result = cli(...args);
      assert.equal(result.status, 0, result.stderr || result.stdout);
      return result;
    };
    ok(
      "new",
      "--title",
      "Yêu cầu thứ nhất",
      "--request",
      "Chỉnh giao diện",
      "--date",
      "2026-11-05",
    );
    const id = "CL-20261105-001";
    const entry = path.join(root, "changelogs", id);
    assert.notEqual(
      cli("finalize", id).status,
      0,
      "unfinished changes cannot be sealed",
    );
    assert.equal(
      await readFile(path.join(root, "src/version.js"), "utf8"),
      "export const mockupRelease = null;",
    );
    const change = {
      title: "Thay đổi mẫu",
      request: "Yêu cầu minh họa",
      before: ["Trước"],
      after: ["Sau"],
      screens: ["login"],
      roles: ["manager"],
      acceptanceCriteria: ["Mở được giao diện"],
      implementationNotes: ["Kiểm tra quyền thật"],
      validation: ["Kiểm tra bộ dữ liệu thử"],
      knownLimits: ["Mockup"],
    };
    await writeFile(path.join(entry, "change.json"), JSON.stringify(change));
    ok("finalize", id);
    ok("verify", id);
    const frozenVersion = await readFile(
      path.join(entry, "mockup/src/version.js"),
      "utf8",
    );
    assert.match(
      frozenVersion,
      /"snapshot": true/,
      "frozen previews use separate browser storage",
    );
    assert.doesNotMatch(
      await readFile(path.join(root, "src/version.js"), "utf8"),
      /"snapshot": true/,
      "editable preview keeps existing browser drafts",
    );
    assert.notEqual(
      cli("finalize", id).status,
      0,
      "sealed changes cannot be overwritten",
    );
    const frozenIndex = await readFile(
      path.join(entry, "mockup/index.html"),
      "utf8",
    );
    await writeFile(path.join(root, "index.html"), "<h1>Next session</h1>");
    ok("verify", id);
    assert.equal(
      await readFile(path.join(entry, "mockup/index.html"), "utf8"),
      frozenIndex,
      "new edits leave previous snapshot intact",
    );
    const report = await readFile(path.join(entry, "CHANGELOG.md"));
    await writeFile(path.join(entry, "CHANGELOG.md"), "accidental edit");
    assert.notEqual(
      cli("verify", id).status,
      0,
      "modified changelogs are detected",
    );
    await writeFile(path.join(entry, "CHANGELOG.md"), report);
    await writeFile(
      path.join(entry, "mockup/unexpected.txt"),
      "accidental addition",
    );
    assert.notEqual(
      cli("verify", id).status,
      0,
      "unexpected snapshot files are detected",
    );
    await rm(path.join(entry, "mockup/unexpected.txt"));
    ok("verify", id);
    ok("pack", id);
    const archive = path.join(root, "exports", `${id}.zip`);
    execFileSync("python3", [
      "-c",
      "import sys,zipfile,pathlib; z=zipfile.ZipFile(sys.argv[1]); assert z.testzip() is None; assert z.read(sys.argv[2]+'/CHANGELOG.md')==pathlib.Path(sys.argv[3]).read_bytes(); assert sys.argv[2]+'/mockup/src/version.js' in z.namelist(); assert not any('exports/' in n or '.git/' in n for n in z.namelist())",
      archive,
      id,
      path.join(entry, "CHANGELOG.md"),
    ]);
    const firstArchive = await readFile(archive);
    ok("pack", id);
    assert.deepEqual(
      await readFile(archive),
      firstArchive,
      "packing the same sealed version is reproducible",
    );
    assert.notEqual(
      cli("tag", id).status,
      0,
      "tags require a committed handoff",
    );
    git("add", "mockup");
    git("commit", "-qm", "sealed handoff");
    ok("tag", id);
    ok("tag", id);
    const pinned = git("rev-parse", `refs/tags/mockup-${id}^{commit}`);
    ok(
      "new",
      "--title",
      "Yêu cầu thứ hai",
      "--request",
      "Phiên mới",
      "--date",
      "2026-11-05",
    );
    const second = JSON.parse(
      await readFile(
        path.join(root, "changelogs/CL-20261105-002/manifest.json"),
        "utf8",
      ),
    );
    assert.equal(second.previousChangelogId, id);
    git("add", "mockup");
    git("commit", "-qm", "next session");
    assert.notEqual(
      cli("tag", id).status,
      0,
      "an existing Git version is never moved to a later commit",
    );
    assert.equal(git("rev-parse", `refs/tags/mockup-${id}^{commit}`), pinned);
    ok("verify", "all");
  } finally {
    await rm(fixture, { recursive: true, force: true });
  }
});
