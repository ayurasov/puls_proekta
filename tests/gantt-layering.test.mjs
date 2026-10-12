import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const html = readFileSync(new URL("../dist/public/gantt.html", import.meta.url), "utf8");

function zIndex(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const rule = html.match(new RegExp(`${escaped}\\{([^}]*)\\}`));
  assert.ok(rule, `CSS rule ${selector} must exist`);
  const value = rule[1].match(/z-index:\s*(-?\d+)/);
  assert.ok(value, `CSS rule ${selector} must define z-index`);
  return Number(value[1]);
}

const task = zIndex(".taskWrap");
const milestone = zIndex(".milestoneLine");
const label = zIndex(".milestoneLabel");

assert.ok(milestone > task, "milestone lines must render above task bars");
assert.ok(label > milestone, "milestone labels must render above milestone lines");
assert.match(html, /\.milestoneLine\{[^}]*pointer-events:none/, "milestone overlay must not block task interaction");

console.log("Gantt milestone layering regression check passed");
