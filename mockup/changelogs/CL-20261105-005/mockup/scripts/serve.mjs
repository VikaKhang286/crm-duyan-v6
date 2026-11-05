import http from "node:http";
import { readFile, stat } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = fileURLToPath(new URL("../", import.meta.url));
const port = Number(process.env.PORT || 4173);
const mime = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".woff2": "font/woff2",
  ".json": "application/json; charset=utf-8",
  ".md": "text/plain; charset=utf-8",
};
const server = http.createServer(async (req, res) => {
  try {
    const name = decodeURIComponent(
      new URL(req.url, "http://localhost").pathname,
    );
    const relative =
      name === "/" ? "index.html" : name.replace(/\/$/, "/index.html").slice(1);
    // Serve only the preview, never repository files or local secrets.
    if (
      !(
        relative === "index.html" ||
        /^(src|assets)\//.test(relative) ||
        /^changelogs\/CL-\d{8}-\d{3,}\/(?:CHANGELOG\.md|manifest\.json|change\.json|mockup\/(?:index\.html|(?:src|assets)\/.+))$/.test(
          relative,
        )
      )
    )
      throw new Error("Not found");
    const target = path.resolve(root, relative);
    if (!target.startsWith(root) || !(await stat(target)).isFile())
      throw new Error("Not found");
    res.writeHead(200, {
      "Content-Type": mime[path.extname(target)] || "application/octet-stream",
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    });
    res.end(await readFile(target));
  } catch {
    res.writeHead(404);
    res.end("Not found");
  }
});
server.listen(port, "127.0.0.1", () =>
  console.log(`DAC mockup: http://localhost:${port}`),
);
server.on("error", (e) => {
  console.error(e.message);
  process.exitCode = 1;
});
