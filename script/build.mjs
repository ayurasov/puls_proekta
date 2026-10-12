import assert from "node:assert/strict";
import { access, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const requiredArtifacts = [
  "dist/index.cjs",
  "dist/public/index.html",
  "dist/public/gantt.html",
];

for (const relativePath of requiredArtifacts) {
  try {
    await access(path.join(root, relativePath));
  } catch {
    throw new Error(`Required production artifact is missing: ${relativePath}`);
  }
}

const ganttPath = path.join(root, "dist/public/gantt.html");
let html = await readFile(ganttPath, "utf8");

function setZIndex(source, selector, value) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const rule = new RegExp(`(${escaped}\\{[^}]*?z-index:\\s*)-?\\d+`, "m");
  assert.match(source, rule, `${selector} must define z-index`);
  return source.replace(rule, `$1${value}`);
}

html = setZIndex(html, ".taskWrap", 10);
html = setZIndex(html, ".milestoneLine", 12);
html = setZIndex(html, ".milestoneLabel", 13);
html = setZIndex(html, ".todayLine", 14);

assert.match(
  html,
  /\.milestoneLine\{[^}]*pointer-events:none/,
  "milestone overlay must not block task interaction",
);
assert.match(
  html,
  /\.todayLine\{[^}]*pointer-events:none/,
  "today overlay must not block task interaction",
);

const previous = await readFile(ganttPath, "utf8");
if (html !== previous) {
  await writeFile(ganttPath, html, "utf8");
  console.log("Updated Gantt timeline layering in dist/public/gantt.html");
} else {
  console.log("Gantt production artifact is already up to date");
}

console.log("Validated tracked production artifacts (source tree is not included in this repository)");
