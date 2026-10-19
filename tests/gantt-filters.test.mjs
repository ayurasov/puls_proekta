import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const html = await readFile(path.join(root, "dist/public/gantt.html"), "utf8");

/* Extract the pure tree/filter functions from the shipped artifact
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

const sandbox = {
  g: () => ({ value: "" }),
  state: { rows: [], collapsed: {} },
  categoryFilterSet: new Set(),
  statusFilterSet: new Set(),
  assigneeFilterSet: new Set(),
  document: { getElementById: () => ({ value: "" }), querySelectorAll: () => [] },
};
sandbox.globalThis = sandbox;
const vm = await import("node:vm");
const ctx = vm.createContext(sandbox);

const constChainStart = html.indexOf("const g=id=>");
assert.ok(constChainStart >= 0, "const helper chain not found in dist/public/gantt.html");
const constChainEnd = html.indexOf(",fru=", constChainStart);
assert.ok(constChainEnd > constChainStart, "end of const helper chain not found");
const src = [
  html.slice(constChainStart, constChainEnd),
  extract("chooseMax"), extract("getTask"), extract("assigneeName"),
  extract("statusObj"), extract("visibleRows"), extract("taskPassesFilters"),
].join("\n");
vm.runInContext(src, ctx);
const { state, categoryFilterSet, statusFilterSet, assigneeFilterSet, visibleRows } = sandbox;
const ids = () => visibleRows().map((r) => r.id).join(",");

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
  assigneeId: "", category: "", comments: [], changeHistory: [], ...extra,
});
const resetFilters = () => { categoryFilterSet.clear(); statusFilterSet.clear(); assigneeFilterSet.clear(); };

/* Base fixture:
   G1 (group)
   ├─ T1 (new, cat X, ass A1)
   ├─ T2 (done, cat X, ass A2)
   └─ P  (inwork, cat Y, no assignee)
      └─ C  (blocked, cat X, ass A1)
   M1 (milestone, child of P and G1)
   S1 (standalone task, done) */
const base = () => [
  { id: "G1", type: "group", name: "G", color: "blue" },
  task("T1", "2026-05-01", "2026-05-05", { groupId: "G1", category: "X", assigneeId: "a1" }),
  task("T2", "2026-05-02", "2026-05-06", { groupId: "G1", category: "X", status: "done", assigneeId: "a2" }),
  task("P", "2026-05-03", "2026-05-20", { groupId: "G1", category: "Y", status: "inwork" }),
  task("C", "2026-05-04", "2026-05-08", { parentId: "P", category: "X", status: "blocked", assigneeId: "a1" }),
  { id: "M1", type: "milestone", name: "M", date: "2026-05-05", color: "teal",
    parentId: "P", groupId: "G1", assigneeId: "", comment: "", lineStyle: "dashed", lineColor: "teal" },
  task("S1", "2026-05-01", "2026-05-02", { status: "done" }),
];

/* --- no filters: everything visible --- */
resetFilters();
state.rows = base();
check("без фильтров видно всё", ids(), "G1,T1,T2,P,C,M1,S1");

/* --- single status --- */
statusFilterSet.add("blocked");
check("статус=Заблокирована: задача + цепочка предков", ids(), "G1,P,C,M1");
resetFilters();

/* --- multiple statuses (OR) --- */
statusFilterSet.add("new"); statusFilterSet.add("done");
check("статусы Новая+Завершена (ИЛИ)", ids(), "G1,T1,T2,S1");
resetFilters();

/* --- single assignee --- */
assigneeFilterSet.add("a1");
check("исполнитель a1: T1, C + предки", ids(), "G1,T1,P,C,M1");
resetFilters();

/* --- multiple assignees (OR) --- */
assigneeFilterSet.add("a1"); assigneeFilterSet.add("a2");
check("исполнители a1+a2 (ИЛИ)", ids(), "G1,T1,T2,P,C,M1");
resetFilters();

/* --- unassigned --- */
assigneeFilterSet.add("__none__");
check("Без исполнителя: P и S1 + предки, веха при видимом якоре", ids(), "G1,P,M1,S1");
resetFilters();

/* --- combined status AND assignee on the same task --- */
statusFilterSet.add("blocked"); assigneeFilterSet.add("a1");
check("статус И исполнитель совпадают на C", ids(), "G1,P,C,M1");
resetFilters();

/* --- neighbors: one passes status, other passes assignee, none passes both --- */
statusFilterSet.add("done"); assigneeFilterSet.add("a1");
check("статус done И исполнитель a1: никто не проходит оба", ids(), "");
resetFilters();

/* --- category + status + assignee (AND) --- */
categoryFilterSet.add("X");
statusFilterSet.add("inwork"); assigneeFilterSet.add("__none__");
check("категория X И inwork И без исполнителя: пусто", ids(), "");
resetFilters();
statusFilterSet.add("blocked"); assigneeFilterSet.add("__none__");
check("категория X И blocked И без исполнителя: пусто (C имеет a1)", ids(), "");
resetFilters(); categoryFilterSet.add("X");
check("категория X без статусного фильтра: задачи кат. X + предки", ids(), "G1,T1,T2,P,C,M1");
categoryFilterSet.clear(); categoryFilterSet.add("__none__");
check("Без категории: только задачи с пустой категорией (S1)", ids(), "S1");
resetFilters();

/* --- deep hierarchy: group → parent of another category → matching child --- */
state.rows = [
  { id: "G1", type: "group", name: "G", color: "blue" },
  task("P", "2026-05-01", "2026-05-20", { groupId: "G1", category: "Y", status: "done" }),
  task("C", "2026-05-02", "2026-05-06", { parentId: "P", category: "X", status: "blocked" }),
];
statusFilterSet.add("blocked");
check("родитель другой категории: ребёнок доступен, предки сохранены", ids(), "G1,P,C");
resetFilters();

/* --- ancestor chain uses the effective parent only (no unrelated group) --- */
state.rows = [
  { id: "G1", type: "group", name: "G1", color: "blue" },
  { id: "G2", type: "group", name: "G2", color: "blue" },
  task("P", "2026-05-01", "2026-05-20", { groupId: "G1", category: "Y" }),
  task("C", "2026-05-02", "2026-05-06", { parentId: "P", groupId: "G2", category: "X", status: "blocked" }),
];
statusFilterSet.add("blocked");
check("предок по parentId: C под P в G1, посторонняя G2 не видна", ids(), "G1,P,C");
resetFilters();
state.rows = base();

/* --- milestone with parentId+groupId appears once under filters --- */
state.rows = base();
statusFilterSet.add("blocked");
check("веха с parentId+groupId один раз при фильтрации", visibleRows().filter((r) => r.id === "M1").length, 1);
resetFilters();

/* --- collapsed group auto-expands when filters are active --- */
state.rows = base();
state.collapsed = { G1: true };
statusFilterSet.add("blocked");
check("свёрнутая группа разворачивается при активном фильтре", ids(), "G1,P,C,M1");
state.collapsed = {};
resetFilters();

if (failed) {
  console.error(`\n${failed} check(s) failed`);
  process.exit(1);
}
console.log("\nGantt status/assignee filter regression check passed");
