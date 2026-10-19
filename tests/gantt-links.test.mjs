import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const html = await readFile(path.join(root, "dist/public/gantt.html"), "utf8");

/* Extract the pure scheduling/tree functions from the shipped artifact
   and evaluate them against a minimal stub environment. */
function extract(name) {
  const re = new RegExp(`function ${name}\\(`, "");
  const m = re.exec(html);
  if (!m) throw new Error(`function ${name} not found in dist/public/gantt.html`);
  let depth = 0, i = m.index + `function ${name}(`.length - 1;
  for (; i < html.length; i++) {
    if (html[i] === "(") depth++;
    else if (html[i] === ")") { depth--; if (depth === 0) break; }
  }
  let j = html.indexOf("{", i);
  depth = 0;
  for (; j < html.length; j++) {
    if (html[j] === "{") depth++;
    else if (html[j] === "}") { depth--; if (depth === 0) break; }
  }
  return html.slice(m.index, j + 1);
}

const g = () => ({ value: "", innerHTML: "", textContent: "" });
const sandbox = {
  g,
  state: { rows: [], collapsed: {}, settings: { autoShift: true } },
  categoryFilterSet: new Set(),
  statusFilterSet: new Set(),
  assigneeFilterSet: new Set(),
  document: { getElementById: () => ({ value: "" }), querySelectorAll: () => [] },
};
sandbox.globalThis = sandbox;
const vm = await import("node:vm");
const ctx = vm.createContext(sandbox);

/* g/pd/fd/addDays/diffDays are declared in one const chain in the artifact */
const constChainStart = html.indexOf("const g=id=>");
assert.ok(constChainStart >= 0, "const helper chain not found in dist/public/gantt.html");
const constChainEnd = html.indexOf(",fru=", constChainStart);
assert.ok(constChainEnd > constChainStart, "end of const helper chain not found");
const constChain = html.slice(constChainStart, constChainEnd);
const src = [
  constChain,
  extract("chooseMax"), extract("getTask"), extract("shiftedStart"),
  extract("shiftedEnd"), extract("applyDependencies"), extract("visibleRows"),
].join("\n");
vm.runInContext(src, ctx);
const { state, applyDependencies, visibleRows } = sandbox;

let failed = 0;
function check(name, actual, expected) {
  try {
    assert.strictEqual(actual, expected);
    console.log(`PASS — ${name}`);
  } catch {
    failed++;
    console.log(`FAIL — ${name}: got ${JSON.stringify(actual)}, expected ${JSON.stringify(expected)}`);
  }
}

const task = (id, start, end, extra = {}) => ({
  id, type: "task", name: id, start, end, endMode: "date", duration: 1,
  durationType: "calendar", progress: 0, offset: 0, parentId: "", groupId: "",
  dependencyId: "", dependencyType: "FS", depLag: 0, status: "new",
  comments: [], changeHistory: [], ...extra,
});

/* --- 1. Dependency types and lag (no parent restrictions) --- */
const depCases = [
  ["FS", 0, "2026-05-11..2026-05-15"],
  ["FS", 3, "2026-05-14..2026-05-18"],
  ["FS", -2, "2026-05-09..2026-05-13"],
  ["SS", 0, "2026-05-01..2026-05-05"],
  ["SS", 3, "2026-05-04..2026-05-08"],
  ["SS", -2, "2026-04-29..2026-05-03"],
  ["FF", 0, "2026-05-06..2026-05-10"],
  ["FF", 3, "2026-05-09..2026-05-13"],
  ["SF", 0, "2026-04-27..2026-05-01"],
  ["SF", 3, "2026-04-30..2026-05-04"],
];
for (const [type, lag, expected] of depCases) {
  state.rows = [
    task("A", "2026-05-01", "2026-05-10"),
    task("B", "2026-06-01", "2026-06-05", { dependencyId: "A", dependencyType: type, depLag: lag }),
  ];
  applyDependencies();
  const b = state.rows.find((r) => r.id === "B");
  check(`${type} lag=${lag}`, `${b.start}..${b.end}`, expected);
}

/* --- 2. Milestone must not duplicate when both parentId and groupId are set --- */
state.rows = [
  { id: "G1", type: "group", name: "G", color: "blue" },
  task("P", "2026-05-01", "2026-05-10", { groupId: "G1" }),
  { id: "M1", type: "milestone", name: "M", date: "2026-05-05", color: "teal",
    parentId: "P", groupId: "G1", assigneeId: "", comment: "",
    lineStyle: "dashed", lineColor: "teal" },
];
const ids = visibleRows().map((r) => r.id);
check("веха с parentId+groupId встречается один раз", JSON.stringify(ids), JSON.stringify(["G1", "P", "M1"]));
check("веха с parentId+groupId не дублируется в списке", ids.filter((x) => x === "M1").length, 1);

/* --- 3. Milestone with only groupId stays under the group --- */
state.rows[2].parentId = "";
const ids2 = visibleRows().map((r) => r.id);
check("веха только с groupId — под группой", JSON.stringify(ids2), JSON.stringify(["G1", "P", "M1"]));

/* --- 4. Parent range still constrains a linked child (current semantics) --- */
state.rows = [
  task("A", "2026-04-01", "2026-04-10"),
  task("P", "2026-05-01", "2026-05-31"),
  task("B", "2026-05-10", "2026-05-14", { parentId: "P", dependencyId: "A", dependencyType: "SS", depLag: 5 }),
];
applyDependencies();
const b4 = state.rows.find((r) => r.id === "B");
check("ребёнок с SS+лаг вне родителя клампится к родителю (текущая семантика)",
  `${b4.start}..${b4.end}`, "2026-05-01..2026-05-05");

if (failed) {
  console.error(`\n${failed} check(s) failed`);
  process.exit(1);
}
console.log("\nGantt links & milestone dedup regression check passed");
