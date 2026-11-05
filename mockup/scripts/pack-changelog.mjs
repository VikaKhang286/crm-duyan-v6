import { mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import { deflateRawSync } from "node:zlib";
import path from "node:path";

// Small standard ZIP writer: no system zip executable or npm dependencies needed.
const crcTable = Array.from({ length: 256 }, (_, n) => {
  for (let i = 0; i < 8; i++) n = n & 1 ? 0xedb88320 ^ (n >>> 1) : n >>> 1;
  return n >>> 0;
});
function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) crc = crcTable[(crc ^ byte) & 255] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}
async function filesIn(directory, prefix = "") {
  const files = [];
  for (const entry of (await readdir(directory, { withFileTypes: true })).sort(
    (a, b) => a.name.localeCompare(b.name),
  )) {
    const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (entry.isSymbolicLink()) throw Error("Cannot package symbolic links");
    if (entry.isDirectory())
      files.push(
        ...(await filesIn(path.join(directory, entry.name), relative)),
      );
    else if (entry.isFile()) files.push(relative);
  }
  return files;
}
export async function pack(directory, output, id) {
  const selected = [
    "CHANGELOG.md",
    "change.json",
    "manifest.json",
    ...(await filesIn(path.join(directory, "mockup"))).map(
      (file) => `mockup/${file}`,
    ),
  ];
  if (selected.length > 65535) throw Error("Too many files for a standard ZIP");
  const locals = [],
    central = [];
  let offset = 0;
  for (const relative of selected) {
    const bytes = await readFile(path.join(directory, relative));
    const compressed = deflateRawSync(bytes);
    const name = Buffer.from(`${id}/${relative}`, "utf8");
    const checksum = crc32(bytes);
    const header = Buffer.alloc(30);
    header.writeUInt32LE(0x04034b50, 0);
    header.writeUInt16LE(20, 4);
    header.writeUInt16LE(0x800, 6);
    header.writeUInt16LE(8, 8);
    header.writeUInt16LE(33, 12); // 1980-01-01; file contents determine the release.
    header.writeUInt32LE(checksum, 14);
    header.writeUInt32LE(compressed.length, 18);
    header.writeUInt32LE(bytes.length, 22);
    header.writeUInt16LE(name.length, 26);
    const entry = Buffer.alloc(46);
    entry.writeUInt32LE(0x02014b50, 0);
    entry.writeUInt16LE(20, 4);
    entry.writeUInt16LE(20, 6);
    entry.writeUInt16LE(0x800, 8);
    entry.writeUInt16LE(8, 10);
    entry.writeUInt16LE(33, 14);
    entry.writeUInt32LE(checksum, 16);
    entry.writeUInt32LE(compressed.length, 20);
    entry.writeUInt32LE(bytes.length, 24);
    entry.writeUInt16LE(name.length, 28);
    entry.writeUInt32LE(offset, 42);
    locals.push(header, name, compressed);
    central.push(entry, name);
    offset += header.length + name.length + compressed.length;
  }
  const directoryBytes = Buffer.concat(central);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(selected.length, 8);
  end.writeUInt16LE(selected.length, 10);
  end.writeUInt32LE(directoryBytes.length, 12);
  end.writeUInt32LE(offset, 16);
  await mkdir(path.dirname(output), { recursive: true });
  await writeFile(output, Buffer.concat([...locals, directoryBytes, end]));
}
