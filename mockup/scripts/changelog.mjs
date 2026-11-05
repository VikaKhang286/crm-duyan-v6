import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import {
  cp,
  lstat,
  mkdir,
  readFile,
  readdir,
  rm,
  writeFile,
} from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
const repository = path.resolve(root, "..");
const history = path.join(root, "changelogs");
const idPattern = /^CL-\d{8}-\d{3,}$/;
const command = process.argv[2];
const args = process.argv.slice(3);
const json = (value) => JSON.stringify(value, null, 2) + "\n";
const digest = (value) => createHash("sha256").update(value).digest("hex");
const readJSON = async (file) => JSON.parse(await readFile(file, "utf8"));
const git = (...values) =>
  execFileSync("git", values, { cwd: repository, encoding: "utf8" }).trim();
const dateVN = () =>
  new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Ho_Chi_Minh",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
const option = (name) => {
  const index = args.indexOf(name);
  if (index < 0) return undefined;
  if (!args[index + 1] || args[index + 1].startsWith("--"))
    throw Error(`${name} requires a value`);
  return args[index + 1];
};
const folder = (id) => {
  if (!idPattern.test(id || ""))
    throw Error("Expected a changelog ID such as CL-20261105-001");
  return path.join(history, id);
};
async function records() {
  const names = await readdir(history).catch((error) => {
    if (error.code === "ENOENT") return [];
    throw error;
  });
  const items = [];
  for (const name of names.filter((name) => idPattern.test(name)).sort()) {
    items.push(await readJSON(path.join(history, name, "manifest.json")));
  }
  return items.sort(
    (a, b) =>
      b.id.slice(3, 11).localeCompare(a.id.slice(3, 11)) ||
      Number(b.id.slice(12)) - Number(a.id.slice(12)),
  );
}
async function writeIndex() {
  const items = await records();
  const lines = items.map(
    (item) =>
      `| [${item.id}](${item.id}/CHANGELOG.md) | ${item.date} | ${item.title.replaceAll("|", "\\|").replaceAll("\n", " ")} | ${item.status === "draft" ? "Đang chỉnh" : "Sẵn sàng bàn giao"} | \`${item.mockupVersion}\` |`,
  );
  await writeFile(
    path.join(history, "README.md"),
    `# Changelog bàn giao mockup\n\nMỗi mã là một yêu cầu hoặc phiên làm mockup. Gửi Khang file CHANGELOG.md cùng phiên bản mockup của đúng mã đó.\n\n| Mã changelog | Ngày (Việt Nam) | Nội dung | Trạng thái | Phiên bản mockup |\n| --- | --- | --- | --- | --- |\n${lines.join("\n")}\n\nXem [quy trình và lệnh sử dụng](../docs/CHANGELOG_WORKFLOW.md). Bản đã chốt giữ nguyên nội dung và snapshot; yêu cầu tiếp theo dùng mã mới.\n`,
  );
}
async function walk(directory, prefix = "") {
  const files = [];
  for (const name of (await readdir(directory)).sort()) {
    const full = path.join(directory, name);
    const relative = prefix ? `${prefix}/${name}` : name;
    const stat = await lstat(full);
    if (stat.isSymbolicLink())
      throw Error(`Symbolic links are not allowed in snapshots: ${relative}`);
    if (stat.isDirectory()) files.push(...(await walk(full, relative)));
    else if (stat.isFile()) files.push(relative);
  }
  return files;
}
async function snapshotHashes(directory) {
  const hashes = {};
  for (const relative of await walk(path.join(directory, "mockup"))) {
    hashes[`mockup/${relative}`] = digest(
      await readFile(path.join(directory, "mockup", relative)),
    );
  }
  for (const name of ["CHANGELOG.md", "change.json"])
    hashes[name] = digest(await readFile(path.join(directory, name)));
  return Object.fromEntries(
    Object.entries(hashes).sort(([a], [b]) => a.localeCompare(b)),
  );
}
function repositoryURL() {
  const remote = git("remote", "get-url", "origin");
  const match = remote.match(
    /^(?:git@github\.com:|https:\/\/github\.com\/)([\w.-]+\/[\w.-]+?)(?:\.git)?$/,
  );
  return match ? `https://github.com/${match[1]}` : null;
}
function validateChange(change) {
  for (const name of ["title", "request"])
    if (typeof change[name] !== "string" || !change[name].trim())
      throw Error(`change.json needs ${name}`);
  for (const name of ["before", "after", "acceptanceCriteria", "validation"]) {
    if (
      !Array.isArray(change[name]) ||
      !change[name].length ||
      change[name].some((value) => typeof value !== "string" || !value.trim())
    )
      throw Error(`Fill change.json.${name} before finalizing`);
  }
  for (const name of [
    "screens",
    "roles",
    "implementationNotes",
    "knownLimits",
  ]) {
    if (
      !Array.isArray(change[name]) ||
      change[name].some((value) => typeof value !== "string" || !value.trim())
    )
      throw Error(`change.json.${name} must be an array of text`);
  }
  if (/\b(?:TODO|TBD)\b|\[ĐIỀN[^\]]*\]/i.test(JSON.stringify(change)))
    throw Error("Finish changelog placeholders before finalizing");
}
function markdown(change, manifest) {
  const bullets = (items) =>
    items.length
      ? items.map((item) => `- ${item}`).join("\n")
      : "Chưa có yêu cầu bổ sung.";
  const link = manifest.repositoryUrl
    ? `[${manifest.mockupVersion}](${manifest.repositoryUrl}/tree/${manifest.mockupVersion}/mockup/changelogs/${manifest.id}/mockup)`
    : `\`${manifest.mockupVersion}\``;
  return `# ${manifest.id} — ${change.title}\n\n| Thông tin | Giá trị |\n| --- | --- |\n| Ngày | ${manifest.date} (Việt Nam) |\n| Người yêu cầu | Duy An |\n| Người triển khai Odoo | Khang |\n| Trạng thái | ${manifest.status === "draft" ? "Đang chỉnh mockup" : "Sẵn sàng bàn giao; chờ Duy An duyệt triển khai"} |\n| Phiên bản mockup | ${link} |\n| Bản trước | ${manifest.previousChangelogId || "Bản nền đầu tiên"} |\n\n## Yêu cầu của Duy An\n\n${change.request}\n\n## Trước thay đổi\n\n${bullets(change.before)}\n\n## Mockup đã thay đổi\n\n${bullets(change.after)}\n\n## Màn hình và vai trò\n\n- Màn hình: ${change.screens.join(", ") || "Công cụ/quy trình mockup"}.\n- Vai trò: ${change.roles.join(", ") || "Không đổi vai trò"}.\n\n## Tiêu chí nghiệm thu để Khang triển khai\n\n${change.acceptanceCriteria.map((item) => `- [ ] ${item}`).join("\n")}\n\n## Hướng triển khai Odoo\n\n${bullets(change.implementationNotes)}\n\nFile Odoo tham chiếu (xác minh trên nhánh triển khai):\n\n${manifest.odooSources?.length ? manifest.odooSources.map((file) => `- \`${file}\``).join("\n") : "Không yêu cầu sửa addon trong lần bàn giao này."}\n\n## Đã kiểm tra\n\n${bullets(change.validation)}\n\n## Giới hạn và phần còn cần làm\n\n${bullets(change.knownLimits)}\n\n## Mở đúng phiên bản mockup\n\n1. Dùng thư mục \`mockup/\` đi cùng changelog này hoặc tải đúng Git tag \`${manifest.mockupVersion}\` sau khi Codex đã push.\n2. Trong thư mục snapshot \`mockup/\`, chạy \`npm run dev\`; không cần npm install hoặc Odoo. Nếu đang chạy bản khác ở cổng 4173, dùng \`PORT=4174 npm run dev\`.\n3. Đăng nhập bằng tài khoản mẫu điền sẵn, rồi chọn vai trò cần đối chiếu.\n4. So sánh tiêu chí ở trên; ghi commit/PR triển khai vào file \`IMPLEMENTATION.md\` riêng khi bắt đầu đưa vào Odoo.\n\nSnapshot chỉ chứa mã và dữ liệu mẫu đã chốt. Ghi chú/dữ liệu sửa trong localStorage không tự đi vào snapshot; Codex phải đưa yêu cầu cần bàn giao vào changelog trước khi chốt. Thao tác và quyền trong mockup cần Khang triển khai, kiểm tra trên Odoo.\n`;
}
async function create() {
  const title = option("--title");
  const request = option("--request");
  const date = option("--date") || dateVN();
  if (!title?.trim() || !request?.trim())
    throw Error(
      "Usage: new --title TITLE --request REQUEST [--date YYYY-MM-DD]",
    );
  if (
    !/^\d{4}-\d{2}-\d{2}$/.test(date) ||
    new Date(`${date}T00:00:00Z`).toISOString().slice(0, 10) !== date
  )
    throw Error("Invalid date");
  await mkdir(history, { recursive: true });
  const prior = await records();
  const prefix = `CL-${date.replaceAll("-", "")}-`;
  let sequence =
    Math.max(
      0,
      ...prior
        .filter((item) => item.id.startsWith(prefix))
        .map((item) => Number(item.id.slice(prefix.length))),
    ) + 1;
  let id, directory;
  for (;;) {
    id = prefix + String(sequence).padStart(3, "0");
    directory = folder(id);
    try {
      await mkdir(directory);
      break;
    } catch (error) {
      if (error.code !== "EEXIST") throw error;
      sequence++;
    }
  }
  const previous = prior.find((item) => item.status === "ready_for_handoff");
  const manifest = {
    schemaVersion: 1,
    id,
    title,
    date,
    timeZone: "Asia/Ho_Chi_Minh",
    createdAt: new Date().toISOString(),
    status: "draft",
    mockupVersion: `mockup-${id}`,
    previousChangelogId: previous?.id || null,
    repositoryUrl: repositoryURL(),
  };
  const change = {
    title,
    request,
    before: [],
    after: [],
    screens: [],
    roles: [],
    acceptanceCriteria: [],
    implementationNotes: [],
    validation: [],
    knownLimits: [],
  };
  await writeFile(path.join(directory, "change.json"), json(change));
  await writeFile(path.join(directory, "manifest.json"), json(manifest));
  await writeFile(
    path.join(directory, "CHANGELOG.md"),
    markdown(change, manifest),
  );
  await writeIndex();
  console.log(
    json({
      id,
      status: manifest.status,
      directory,
      next: "Fill change.json, then run change:finalize with this ID.",
    }),
  );
}
async function finalize(id) {
  const directory = folder(id);
  const manifest = await readJSON(path.join(directory, "manifest.json"));
  if (manifest.status !== "draft")
    throw Error(
      `${id} is already sealed; create a new changelog instead of overwriting it`,
    );
  const change = await readJSON(path.join(directory, "change.json"));
  validateChange(change);
  const { screens, roles } = await import(
    pathToFileURL(path.join(root, "src/data.js"))
  );
  for (const key of change.screens)
    if (!Object.hasOwn(screens, key)) throw Error(`Unknown screen: ${key}`);
  for (const key of change.roles)
    if (!Object.hasOwn(roles, key)) throw Error(`Unknown role: ${key}`);
  execFileSync(process.execPath, [path.join(root, "scripts/check.mjs")], {
    cwd: root,
    stdio: "inherit",
  });
  const sealed = {
    ...manifest,
    title: change.title,
    status: "ready_for_handoff",
    finalizedAt: new Date().toISOString(),
    sourceBaseCommit: git("rev-parse", "HEAD"),
    sourceBranch: git("branch", "--show-current"),
    sourceChanges: git(
      "status",
      "--porcelain",
      "--",
      "mockup/src",
      "mockup/assets",
      "mockup/index.html",
      "mockup/scripts/serve.mjs",
    ),
    odooSources: [
      ...new Set(change.screens.flatMap((key) => screens[key].sources)),
    ],
    browserDraftsIncluded: false,
  };
  const release = {
    id,
    version: sealed.mockupVersion,
    title: change.title,
    date: sealed.date,
    changelogUrl: sealed.repositoryUrl
      ? `${sealed.repositoryUrl}/blob/${sealed.mockupVersion}/mockup/changelogs/${id}/CHANGELOG.md`
      : null,
  };
  const versionFile = path.join(root, "src/version.js");
  const oldVersion = await readFile(versionFile);
  const temporary = path.join(directory, ".snapshot-pending");
  try {
    await writeFile(
      versionFile,
      `// Generated when Codex seals a changelog.\nexport const mockupRelease = ${json(release).trim()};\n`,
    );
    await rm(temporary, { recursive: true, force: true });
    await mkdir(path.join(temporary, "scripts"), { recursive: true });
    for (const name of ["index.html", "src", "assets"])
      await cp(path.join(root, name), path.join(temporary, name), {
        recursive: true,
      });
    await writeFile(
      path.join(temporary, "src/version.js"),
      `// Frozen release: browser drafts are isolated from the editable mockup.\nexport const mockupRelease = ${json({ ...release, snapshot: true }).trim()};\n`,
    );
    await cp(
      path.join(root, "scripts/serve.mjs"),
      path.join(temporary, "scripts/serve.mjs"),
    );
    await writeFile(
      path.join(temporary, "package.json"),
      json({
        name: `dac-mockup-${id.toLowerCase()}`,
        version: "1.0.0",
        private: true,
        type: "module",
        scripts: {
          dev: "node scripts/serve.mjs",
          start: "node scripts/serve.mjs",
        },
        engines: { node: ">=20" },
      }),
    );
    await rm(path.join(directory, "mockup"), { recursive: true, force: true });
    await cp(temporary, path.join(directory, "mockup"), { recursive: true });
    await writeFile(
      path.join(directory, "CHANGELOG.md"),
      markdown(change, sealed),
    );
    sealed.files = await snapshotHashes(directory);
    sealed.contentSha256 = digest(json(sealed.files));
    await writeFile(path.join(directory, "manifest.json"), json(sealed));
  } catch (error) {
    await writeFile(versionFile, oldVersion);
    throw error;
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
  await writeIndex();
  console.log(
    json({
      id,
      status: sealed.status,
      version: sealed.mockupVersion,
      changelog: path.join(directory, "CHANGELOG.md"),
      snapshot: path.join(directory, "mockup"),
      contentSha256: sealed.contentSha256,
      next: "Verify and pack; commit, then create and push the version tag.",
    }),
  );
}
async function verify(id) {
  const directory = folder(id);
  const manifest = await readJSON(path.join(directory, "manifest.json"));
  if (manifest.status !== "ready_for_handoff")
    throw Error(`${id} is not sealed`);
  const files = await snapshotHashes(directory);
  if (
    json(files) !== json(manifest.files) ||
    digest(json(files)) !== manifest.contentSha256
  )
    throw Error(
      `${id}: changelog or frozen snapshot has changed; restore it and create a new ID`,
    );
  return manifest;
}
async function tag(id) {
  const manifest = await verify(id);
  const relative = `mockup/changelogs/${id}/manifest.json`;
  const committed = git("show", `HEAD:${relative}`);
  if (
    committed !==
    (await readFile(path.join(folder(id), "manifest.json"), "utf8")).trim()
  )
    throw Error("Commit the sealed changelog before creating its version tag");
  const committedFiles = git(
    "status",
    "--porcelain",
    "--",
    `mockup/changelogs/${id}`,
  );
  if (committedFiles)
    throw Error("Commit all files in the sealed changelog first");
  let existing;
  try {
    existing = git(
      "rev-parse",
      "--quiet",
      "--verify",
      `refs/tags/${manifest.mockupVersion}^{commit}`,
    );
  } catch {
    existing = null;
  }
  if (existing && existing !== git("rev-parse", "HEAD"))
    throw Error(
      "The version tag already points to another commit; tags are never moved",
    );
  if (!existing)
    git("tag", "-a", manifest.mockupVersion, "-m", `${id}: ${manifest.title}`);
  console.log(
    json({
      id,
      tag: manifest.mockupVersion,
      commit: git("rev-parse", "HEAD"),
      next: `git push origin HEAD refs/tags/${manifest.mockupVersion}`,
    }),
  );
}
try {
  if (command === "new") await create();
  else if (command === "finalize") await finalize(args[0]);
  else if (command === "verify") {
    const items =
      args[0] && args[0] !== "all"
        ? [{ id: args[0] }]
        : (await records()).filter(
            (item) => item.status === "ready_for_handoff",
          );
    for (const item of items) {
      await verify(item.id);
      console.log(`Verified ${item.id}: changelog and snapshot unchanged.`);
    }
  } else if (command === "tag") await tag(args[0]);
  else if (command === "pack") {
    const manifest = await verify(args[0]);
    const { pack } = await import("./pack-changelog.mjs");
    const output = path.join(root, "exports", `${manifest.id}.zip`);
    await pack(folder(manifest.id), output, manifest.id);
    console.log(
      json({
        id: manifest.id,
        version: manifest.mockupVersion,
        archive: output,
      }),
    );
  } else throw Error("Usage: changelog.mjs new|finalize|verify|pack|tag ...");
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
