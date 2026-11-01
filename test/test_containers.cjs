const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const test = require("node:test");
const path = require("node:path");

const source = fs.readFileSync(path.join(__dirname, "../herdr-containers/mods/containers/main.js"), "utf8")
  .replace(/^import .*;\n/gm, "")
  .replace("export default class Containers", "class Containers") + "\nglobalThis.Containers = Containers;";

function app() {
  const values = new Map();
  const events = [];
  const timers = [];
  const openSpaces = [{ id: "one", machine: "dev" }, { id: "two", machine: "dev" }, { id: "other", machine: "else" }];
  const tree = { children: [
    { id: "sidebar", size: { px: 268 } },
    { id: "containers", size: { weight: 1 } },
    { id: "spaces", content: { mod: "spaces-containers" } },
  ] };
  const win = { active: true };
  let machineLayouts = [];
  let hcFn = async () => ({ code: 0, stdout: "{}" });
  let execFn = async () => ({ code: 0, stdout: "[]" });
  const overlays = [];
  // UI methods retain children, ids, clicks, tooltips, hovers and every other call.
  const element = () => {
    const node = { kids: [], calls: [], hovered: false };
    const proxy = new Proxy(node, {
      get(target, name) {
        if (name === "child") return (value) => { target.kids.push(value); return proxy; };
        if (name === "children") return (values) => { target.kids.push(...values); return proxy; };
        if (name === "when") return (condition, fn) => { if (condition) fn(proxy); return proxy; };
        if (name === "on_click") return (fn) => { target.click = fn; return proxy; };
        if (name === "id") return (value) => { target.name = value; return proxy; };
        if (name === "tooltip") return (value) => { target.tip = value; return proxy; };
        if (name === "hover") return () => { target.hovered = true; return proxy; };
        if (name in target) return target[name];
        return (...args) => { target.calls.push([name, ...args]); return proxy; };
      },
    });
    return proxy;
  };
  let currentProps = {};
  let next = () => new Promise(() => {});
  const context = {
    View: class {}, div: element, svg: (src) => { const node = element(); node.src = src; return node; },
    v_flex: element, h_flex: element,
    exec: (cmd, args) => execFn(cmd, args),
    hc: (args) => { events.push(["hc", ...args]); return hcFn(args); },
    machines: () => machineLayouts, spaces: () => openSpaces,
    run: (name, args) => {
      events.push([name, args]);
      if (name === "layout.resize") find(tree, args.id).size = "px" in args ? { px: args.px } : { weight: args.weight };
    },
    theme: () => new Proxy({}, { get: (_, key) => key }),
    layout: () => tree, props: () => currentProps, window: () => win,
    shared: (key) => values.get(key), setShared: (key, value) => { events.push(["shared", key, value]); values.set(key, value); },
    nextCommand: () => next(), openOverlay: (spec) => overlays.push(spec), closeOverlay: () => {},
    Date, console,
  };
  vm.runInNewContext(source, context);
  const cx = { notify() {}, stop_propagation() {}, spawn: (fn) => fn(cx), timer: { every: (ms, fn) => timers.push([ms, fn]) } };
  const view = new context.Containers();
  Object.assign(view, { rows: [], added: [], gone: [], state: {}, goneState: {}, pending: {}, removing: {}, taken: new Set(), watching: true });
  view.refresh = () => events.push(["refresh"]);
  return { view, values, events, timers, openSpaces, overlays, cx, tree, win,
    realRefresh: () => { delete view.refresh; },
    setProps: (p) => { currentProps = p; }, setNext: (fn) => { next = fn; },
    setMachines: (list) => { machineLayouts = list; }, setHc: (fn) => { hcFn = fn; }, setExec: (fn) => { execFn = fn; } };
}

function find(node, name) {
  if (node?.name === name || node?.id === name) return node;
  for (const child of node?.kids ?? node?.children ?? []) {
    const found = find(child, name);
    if (found) return found;
  }
  return null;
}

function labels(node) {
  if (typeof node === "string") return [node];
  return (node?.kids ?? []).flatMap(labels);
}

const plain = (value) => JSON.parse(JSON.stringify(value));
const settle = async () => { for (let i = 0; i < 20; i++) await new Promise((resolve) => setImmediate(resolve)); };
const hcCalls = (a) => plain(a.events.filter(([name]) => name === "hc"));

test("header and command collapse the block by its height", async () => {
  const a = app();
  a.view.rows = [
    { id: "dev", name: "dev", kind: "devcontainer", state: "running" },
    { id: "stopped", name: "stopped", kind: "devcontainer", state: "exited" },
    { id: "plain", name: "plain", kind: "container", state: "running" },
  ];
  let tree = a.view.render();
  assert.ok(find(tree, "container-plain"));
  assert.ok(find(tree, "container-stopped"));
  assert.equal(find(tree, "containers-settings"), null);
  assert.deepEqual(labels(find(tree, "containers-header")), ["Containers"]);
  assert.deepEqual(labels(find(tree, "containers-group-dev")), ["Dev containers", "2"]);
  assert.deepEqual(labels(find(tree, "containers-group-other")), ["Other containers", "1"]);
  a.values.set("containers.showOther", false);
  a.values.set("containers.showStopped", false);
  find(tree, "containers-header").click(null, a.cx);
  assert.deepEqual(plain(a.events.at(-1)), ["layout.resize", { id: "containers", px: 35 }]);
  tree = a.view.render();
  assert.equal(find(tree, "container-dev"), null);
  assert.deepEqual(labels(find(tree, "containers-header")), ["Containers"]);
  assert.equal(find(tree, "containers-header").kids[2].src, "mods/workspace-chrome/chevron-right.svg");
  find(tree, "containers-header").click(null, a.cx);
  assert.deepEqual(plain(a.events.at(-1)), ["layout.resize", { id: "containers", weight: 1 }]);
  tree = a.view.render();
  assert.ok(find(tree, "container-dev"));
  assert.equal(find(tree, "container-plain"), null);
  assert.equal(find(tree, "container-stopped"), null);
  // A divider dragged down to the minimum collapses it too.
  find(a.tree, "containers").size = { px: 35 };
  assert.equal(find(a.view.render(), "container-dev"), null);
  find(a.tree, "containers").size = { weight: 1 };

  a.setNext(async () => { a.setNext(() => new Promise(() => {})); return "toggle"; });
  a.view.init(null, a.cx);
  await settle();
  assert.deepEqual(plain(a.events.at(-1)), ["layout.resize", { id: "containers", px: 35 }]);
});

test("group counts follow visible rows and folded groups keep their counts", () => {
  const a = app();
  a.view.rows = [
    { id: "dev-a", name: "dev-a", kind: "devcontainer", state: "running", via: "via-a" },
    { id: "dev-b", name: "dev-b", kind: "devcontainer", state: "exited", via: "via-b" },
    { id: "dev-c", name: "dev-c", kind: "devcontainer", state: "running", via: "via-c" },
    { id: "other-a", name: "other-a", kind: "container", state: "running", via: "via-other-a" },
    { id: "other-b", name: "other-b", kind: "container", state: "running", via: "via-other-b" },
  ];
  a.view.added = [
    { name: "removed-dev", via: true, target: "via-c" },
    { name: "removed-other", via: true, target: "via-other-b" },
  ];
  a.view.gone = [{ name: "gone-a" }, { name: "gone-hidden" }];
  a.values.set("containers.removing", ["removed-dev", "removed-other", "gone-hidden"]);
  let tree = a.view.render();
  assert.deepEqual(labels(find(tree, "containers-group-dev")), ["Dev containers", "2"]);
  assert.deepEqual(labels(find(tree, "containers-group-other")), ["Other containers", "1"]);
  assert.deepEqual(labels(find(tree, "containers-group-gone")), ["Gone", "1"]);
  assert.equal(find(tree, "container-dev-c"), null);
  assert.equal(find(tree, "container-other-b"), null);
  assert.equal(find(tree, "forget-gone-hidden"), null);

  find(tree, "containers-group-dev").click(null, a.cx);
  assert.deepEqual([...a.values.get("containers.folded")], ["dev"]);
  tree = a.view.render();
  assert.deepEqual(labels(find(tree, "containers-group-dev")), ["Dev containers", "2"]);
  assert.equal(find(tree, "container-dev-a"), null);
  assert.equal(find(tree, "container-dev-b"), null);
  assert.equal(find(tree, "containers-group-dev").kids.at(-1).src, "mods/workspace-chrome/chevron-right.svg");

  find(tree, "containers-group-other").click(null, a.cx);
  find(a.view.render(), "containers-group-gone").click(null, a.cx);
  assert.deepEqual([...a.values.get("containers.folded")], ["dev", "other", "gone"]);
  tree = a.view.render();
  assert.deepEqual(labels(find(tree, "containers-group-gone")), ["Gone", "1"]);
  assert.equal(find(tree, "forget-gone-a"), null);

  a.values.set("containers.showStopped", false);
  a.values.set("containers.showOther", false);
  tree = a.view.render();
  assert.deepEqual(labels(find(tree, "containers-group-dev")), ["Dev containers", "1"]);
  assert.equal(find(tree, "containers-group-other"), null);
  assert.deepEqual(labels(find(tree, "containers-group-gone")), ["Gone", "1"]);
  find(tree, "containers-group-dev").click(null, a.cx);
  tree = a.view.render();
  assert.ok(find(tree, "container-dev-a"));
  assert.equal(find(tree, "container-dev-b"), null);
  assert.deepEqual([...a.values.get("containers.folded")], ["other", "gone"]);
});

test("only inactive container rows and their x buttons hover", () => {
  const a = app();
  a.view.rows = [{ id: "dev-id", name: "dev", kind: "devcontainer", state: "running", via: "via", has_feature: true }];
  a.view.added = [{ name: "dev", via: true, target: "via", state: "connected" }];
  a.view.gone = [{ name: "old" }];
  const tree = a.view.render();
  for (const id of ["containers-header", "containers-group-dev", "containers-group-gone"]) {
    assert.ok(find(tree, id), id);
    assert.equal(find(tree, id).hovered, false, id);
  }
  assert.equal(find(tree, "containers-header").kids.at(-1).hovered, false);
  assert.equal(find(tree, "container-dev-id").hovered, true);
  assert.equal(find(tree, "disconnect-action-dev-id").hovered, true);
  a.openSpaces[0].active = true;
  assert.equal(find(a.view.render(), "container-dev-id").hovered, false);
});

test("group headers match the Agents group header; the section title has no count", () => {
  const a = app();
  a.view.rows = [{ id: "dev-id", name: "dev", kind: "devcontainer", state: "running", via: "via" }];
  const tree = a.view.render();
  const group = find(tree, "containers-group-dev");
  const geometry = ["px", "pt", "h", "flex_none", "gap", "items_center", "text_size", "font_medium"];
  assert.deepEqual(group.calls.filter(([name]) => geometry.includes(name)),
    [["px", 8], ["pt", 8], ["h", 30], ["flex_none"], ["gap", 6], ["items_center"], ["text_size", 11], ["font_medium"]]);
  assert.deepEqual(group.kids[0].calls, [["truncate"], ["text_color", "dim"]]);
  assert.deepEqual(group.kids[1].calls, [["text_color", "dim"]]);
  const title = find(tree, "containers-header").kids[0];
  assert.deepEqual([title.calls, title.kids], [[["text_size", 11.5], ["font_medium"], ["text_color", "dim"]], ["Containers"]]);
});

test("Disconnect confirms inline, closes its spaces, then removes the machine once they're gone", async () => {
  const a = app();
  const row = { id: "dev-id", name: "dev", kind: "devcontainer", state: "running", via: "via" };
  a.view.rows = [row];
  a.view.added = [{ name: "dev", via: true, target: "via" }];
  const button = find(a.view.render(), "disconnect-action-dev-id");
  assert.equal(button.tip, "Disconnect machine");
  button.click(null, a.cx);
  assert.equal(a.view.state[row.id], "disconnect-confirm");
  let tree = a.view.render();
  assert.equal(a.events.length, 0);
  assert.ok(labels(tree).includes("Close 2 spaces and disconnect?"));
  find(tree, "cancel-dev-id").click(null, a.cx);
  assert.equal(a.view.state[row.id], undefined);
  find(a.view.render(), "disconnect-action-dev-id").click(null, a.cx);
  find(a.view.render(), "disconnect-action-dev-id").click(null, a.cx); // collapse confirmation
  assert.notEqual(a.view.state[row.id], "disconnect-confirm");
  find(a.view.render(), "disconnect-action-dev-id").click(null, a.cx);
  find(a.view.render(), "disconnect-dev-id").click(null, a.cx);
  await settle();
  assert.deepEqual(plain(a.events), [
    ["shared", "containers.removing", ["dev"]],
    ["space.close", { id: "one", confirmed: true }],
    ["space.close", { id: "two", confirmed: true }],
  ]);
  tree = a.view.render();
  assert.ok(labels(tree).includes("Disconnecting…"));
  assert.equal(find(tree, "disconnect-action-dev-id"), null);
  // The app dropped the spaces; their kills still get a moment.
  a.openSpaces.splice(0, 2);
  a.view.checkPending(a.cx);
  assert.deepEqual(hcCalls(a), []);
  a.view.removing["dev-id"].at -= 2000;
  a.view.checkPending(a.cx);
  await settle();
  assert.deepEqual(plain(a.events.slice(3)), [["hc", "machine", "rm", "dev"], ["refresh"]]);
});

test("Forget closes first too, and removes after 5 s even if a space stays", async () => {
  const a = app();
  a.view.forget({ name: "dev" }, a.cx);
  assert.deepEqual(plain(a.events), [
    ["shared", "containers.removing", ["dev"]],
    ["space.close", { id: "one", confirmed: true }],
    ["space.close", { id: "two", confirmed: true }],
  ]);
  a.view.removing.dev.at -= 4900;
  a.view.checkPending(a.cx);
  assert.deepEqual(hcCalls(a), []);
  a.view.removing.dev.at -= 200;
  a.view.checkPending(a.cx);
  await settle();
  assert.deepEqual(hcCalls(a), [["hc", "machine", "rm", "dev"]]);
});

test("removeMachine cancels pending opens for its row and machine", async () => {
  const a = app();
  a.view.state = { "dev-id": "connecting", "other-id": "connecting" };
  a.view.pending = {
    "dev-id": { machine: "dev", row: { suggested_name: "dev" }, at: Date.now() - 21000 },
    "other-id": { machine: "dev", row: { suggested_name: "dev" }, at: Date.now() - 21000 },
  };
  a.view.added = [{ name: "dev", state: "connected" }];
  a.setMachines([{ name: "dev", layout: "loading" }]);
  a.openSpaces.splice(0, 2);
  a.view.disconnect({ id: "dev-id" }, { name: "dev" }, a.cx);
  assert.deepEqual(Object.keys(a.view.pending), []);
  assert.equal(a.view.state["other-id"], undefined);
  assert.equal(a.view.state["dev-id"], "disconnecting");
  await settle();
  a.view.checkPending(a.cx);
  assert.equal(a.view.state["dev-id"], "disconnecting");
  assert.ok(!a.events.some(([name]) => name === "space.new"));
  a.setMachines([]);
  a.view.machineListAt = Date.now() + 1;
  a.view.added = [];
  a.view.checkPending(a.cx);
  assert.equal(a.view.state["dev-id"], undefined);
});

test("a pending open outlives a stale machine list and drops on a fresh one", () => {
  const a = app();
  a.view.pending["dev-id"] = { machine: "dev", row: { suggested_name: "dev" }, at: Date.now() };
  a.view.state["dev-id"] = "connecting";
  a.view.machineListAt = Date.now() - 1000; // fetched before the add returned
  a.view.checkPending(a.cx);
  assert.ok(a.view.pending["dev-id"]);
  assert.equal(a.view.state["dev-id"], "connecting");
  a.view.machineListAt = Date.now() + 1;
  a.view.checkPending(a.cx);
  assert.equal(a.view.pending["dev-id"], undefined);
  assert.equal(a.view.state["dev-id"], undefined);
  assert.ok(!a.events.some(([name]) => name === "space.new"));
});

test("refresh coalesces: calls during a look run one more look after it", async () => {
  const a = app();
  a.realRefresh();
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  let looks = 0;
  a.setExec(async (_cmd, args) => {
    if (args[0] === "ls" && ++looks === 1) await gate;
    return { code: 0, stdout: "[]" };
  });
  a.setHc(async () => ({ code: 0, stdout: '{"machines":[]}' }));
  a.view.refresh(a.cx);
  a.view.refresh(a.cx);
  a.view.refresh(a.cx);
  assert.equal(looks, 1);
  release();
  await settle();
  assert.equal(looks, 2);
  assert.equal(a.view.refreshing, false);
});

test("an add that returns during a look still opens its space", async () => {
  const a = app();
  a.realRefresh();
  const row = { id: "dev-id", name: "dev", kind: "devcontainer", state: "running", via: "via", suggested_name: "dev", workspace: "/w" };
  const gates = [];
  a.setExec(async () => ({ code: 0, stdout: JSON.stringify([row]) }));
  a.setHc(async (args) => {
    if (args[1] === "add") return { code: 0, stdout: '{"name":"dev"}' };
    const n = gates.length;
    await new Promise((resolve) => gates.push(resolve));
    return { code: 0, stdout: JSON.stringify({ machines: n ? [{ name: "dev", via: true, target: "via", state: "connected" }] : [] }) };
  });
  a.openSpaces.length = 0;
  a.view.refresh(a.cx); // a poll starts before the add
  a.view.open(row, a.cx);
  await settle();
  gates[0]();
  await settle();
  a.view.checkPending(a.cx); // the poll's list predates the add
  assert.ok(a.view.pending["dev-id"]);
  gates[1]();
  await settle();
  a.setMachines([{ name: "dev", layout: "ready" }]);
  a.view.checkPending(a.cx);
  assert.deepEqual(plain(a.events.at(-1)), ["space.new", { name: "dev", folder: "/w", machine: "dev" }]);
});

test("refresh: a connected machine is never gone; Docker answers for the rest", async () => {
  const a = app();
  a.realRefresh();
  const list = [
    { name: "hand", via: true, target: "hc-devcontainer /w/hand", state: "connected" },
    { name: "deleted", via: true, target: "hc-devcontainer '/w/deleted'", state: "unreachable" },
    { name: "stopped", via: true, target: "hc-devcontainer --container 'web'", state: "unreachable" },
    { name: "unsure", via: true, target: "hc-devcontainer --container 'db'", state: "unreachable" },
    { name: "box", via: false, target: "box", state: "unreachable" },
  ];
  const codes = { "hc-devcontainer '/w/deleted'": 1, "hc-devcontainer --container 'web'": 0 };
  const asked = [];
  a.setHc(async () => ({ code: 0, stdout: JSON.stringify({ machines: list }) }));
  a.setExec(async (_cmd, args) => {
    if (args[0] === "ls") return { code: 0, stdout: "[]" };
    asked.push(args[1]);
    return { code: codes[args[1]] ?? 2, stdout: "", stderr: "" };
  });
  a.view.refresh(a.cx);
  await settle();
  assert.deepEqual(asked.sort(), [list[1].target, list[2].target, list[3].target].sort());
  assert.deepEqual(plain(a.view.gone.map((m) => m.name)), ["deleted"]);
  const tree = a.view.render();
  assert.deepEqual(labels(find(tree, "containers-group-gone")), ["Gone", "1"]);
  // A stopped plain container with a machine keeps a row to Start or Disconnect it.
  assert.ok(labels(find(tree, "container-web")).includes("Stopped"));
  assert.ok(find(tree, "start-web"));
  assert.ok(find(tree, "disconnect-action-web"));
  assert.equal(find(tree, "container-db"), null);
});

test("Docker down publishes disconnected machines once and labels saved rows", async () => {
  const a = app();
  a.realRefresh();
  const saved = [
    { name: "dev", via: true, target: "hc-devcontainer '/work'", state: "unreachable" },
    { name: "plain", via: true, target: "hc-devcontainer --container 'web'", state: "connected" },
    { name: "ssh", via: false, target: "ssh box", state: "unreachable" },
  ];
  a.setHc(async () => ({ code: 0, stdout: JSON.stringify({ machines: saved }) }));
  a.setExec(async () => ({ code: 1, stderr: "Cannot connect to the Docker daemon", stdout: "" }));
  a.view.refresh(a.cx);
  await settle();
  assert.deepEqual(plain(a.values.get("containers.down")), ["dev"]);
  assert.deepEqual(labels(a.view.render()).filter((s) => s === "Docker isn't running"), ["Docker isn't running"]);
  assert.ok(labels(find(a.view.render(), "containers-group-gone")).includes("Saved machines"));
  assert.ok(labels(a.view.render()).includes("Disconnected"));
  const writes = a.events.filter(([name, key]) => name === "shared" && key === "containers.down").length;
  a.view.refresh(a.cx);
  await settle();
  assert.equal(a.events.filter(([name, key]) => name === "shared" && key === "containers.down").length, writes);
  saved[0].state = "connected";
  a.view.refresh(a.cx);
  await settle();
  assert.deepEqual(plain(a.values.get("containers.down")), []);
});

test("only the app's own spawn failure reads as a missing helper", async () => {
  const a = app();
  a.realRefresh();
  a.setHc(async () => ({ code: 0, stdout: '{"machines":[]}' }));
  a.setExec(async () => { throw new Error("cannot run hc-devcontainer: No such file or directory (os error 2)"); });
  a.view.refresh(a.cx);
  await settle();
  assert.equal(a.view.error, "hc-devcontainer is not installed (put it on PATH)");
  const docker = "hc-devcontainer: cannot run docker: [Errno 2] No such file or directory: 'docker'";
  a.setExec(async () => ({ code: 1, stdout: "", stderr: `${docker}\n` }));
  a.view.refresh(a.cx);
  await settle();
  assert.equal(a.view.error, docker);
});

test("Repair adds under a spare name before it removes the old machine", async () => {
  const a = app();
  const row = { id: "dev-id", name: "dev", kind: "devcontainer", state: "running", via: "via", suggested_name: "dev" };
  const saved = { name: "dev", via: true, target: "via", state: "unreachable", why: "hc is not installed on via" };
  const add = ["hc", "machine", "add", "--name", "dev-repair", "--via", "via", "--install", "--json"];
  a.setHc(async (args) => (args[1] === "add" ? { code: 1, stdout: '{"error":{"message":"cannot reach via"}}' } : { code: 0, stdout: "{}" }));
  a.view.repair(row, saved, a.cx);
  await settle();
  assert.deepEqual(hcCalls(a), [add]);
  assert.equal(a.view.state["dev-id"], "cannot reach via");
  a.events.length = 0;
  a.setHc(async (args) => ({ code: 0, stdout: args[1] === "add" ? '{"name":"dev-repair","id":"new-core"}' : "{}" }));
  a.view.repair(row, saved, a.cx);
  await settle();
  assert.deepEqual(hcCalls(a), [add, ["hc", "machine", "rm", "dev"], ["hc", "machine", "rename", "dev-repair", "dev"]]);
  assert.equal(a.view.pending["dev-id"].core, "new-core");
  assert.equal(a.view.state["dev-id"], "connecting");
});

test("a new machine skips names already taken", async () => {
  const a = app();
  a.view.taken = new Set(["api", "api-2"]);
  a.setHc(async () => ({ code: 0, stdout: '{"name":"api-3"}' }));
  a.view.open({ id: "x", name: "api", kind: "devcontainer", state: "running", via: "via", suggested_name: "api" }, a.cx);
  await settle();
  assert.deepEqual(hcCalls(a), [["hc", "machine", "add", "--name", "api-3", "--via", "via", "--install", "--json"]]);
  assert.equal(a.view.pending.x.machine, "api-3");
});

test("the rail draws icons only: no x, no Gone section, messages in the tooltip", () => {
  const a = app();
  find(a.tree, "sidebar").size = { px: 44 };
  a.view.rows = [{ id: "dev-id", name: "dev", kind: "devcontainer", state: "running", via: "via", has_feature: true }];
  a.view.added = [{ name: "dev", via: true, target: "via", state: "connected" }];
  a.view.gone = [{ name: "old" }];
  a.view.state["dev-id"] = "connecting";
  const tree = a.view.render();
  assert.ok(find(tree, "container-dev-id").calls.some(([name]) => name === "justify_center"));
  assert.equal(find(tree, "disconnect-action-dev-id"), null);
  assert.equal(find(tree, "containers-group-gone"), null);
  assert.equal(find(tree, "container-dev-id").tip, "Connecting…");
  assert.ok(!labels(tree).includes("Connecting…"));
});

test("connected machine opens after five seconds despite stale layout", () => {
  const a = app();
  const row = { suggested_name: "dev", workspace: "/work" };
  a.view.added = [{ name: "dev", state: "connected" }];
  a.view.pending["dev-id"] = { machine: "dev", row, at: Date.now() - 5100 };
  a.view.state["dev-id"] = "connecting";
  a.setMachines([{ name: "dev", layout: "loading" }]);
  a.openSpaces.splice(0, 2);
  a.view.checkPending(a.cx);
  assert.deepEqual(plain(a.events), [["space.new", { name: "dev", folder: "/work", machine: "dev" }]]);
  assert.equal(a.view.pending["dev-id"], undefined);
  assert.equal(a.view.state["dev-id"], undefined);
});

test("removal waits for fresh machine list and app layout, then clears shared state", () => {
  const a = app();
  a.setMachines([{ name: "dev", layout: "loading" }]);
  a.view.state["dev-id"] = "disconnecting";
  a.values.set("containers.removing", ["dev"]);
  a.view.removing["dev-id"] = { machine: "dev", state: a.view.state, closing: [], started: true,
    at: Date.now() - 1000, removedAt: Date.now() - 500 };
  a.view.machineListAt = Date.now();
  a.view.checkPending(a.cx);
  assert.equal(a.view.state["dev-id"], "disconnecting");
  a.setMachines([]);
  a.view.added = [{ name: "dev" }];
  a.view.checkPending(a.cx);
  assert.equal(a.view.state["dev-id"], "disconnecting");
  a.view.added = [];
  a.view.machineListAt = a.view.removing["dev-id"].removedAt - 1;
  a.view.checkPending(a.cx);
  assert.equal(a.view.state["dev-id"], "disconnecting");
  a.view.machineListAt = Date.now();
  a.view.checkPending(a.cx);
  assert.equal(a.view.state["dev-id"], undefined);
  assert.equal(a.view.removing["dev-id"], undefined);
  assert.equal(a.values.get("containers.removing").length, 0);
});

test("only shipped Spaces makes a finished removal wait out its 15 s cache", () => {
  const a = app();
  const start = () => {
    a.view.state["dev-id"] = "disconnecting";
    a.view.removing["dev-id"] = { machine: "dev", state: a.view.state, closing: [], started: true,
      at: Date.now() - 1000, removedAt: Date.now() - 500 };
    a.view.machineListAt = Date.now();
  };
  start();
  a.view.checkPending(a.cx);
  assert.equal(a.view.state["dev-id"], undefined);
  find(a.tree, "spaces").content.mod = "spaces";
  start();
  a.view.checkPending(a.cx);
  assert.equal(a.view.state["dev-id"], "disconnecting");
  a.view.removing["dev-id"].removedAt = Date.now() - 16001;
  a.view.checkPending(a.cx);
  assert.equal(a.view.state["dev-id"], undefined);
});

test("failed machine rm restores the row and shows the error", async () => {
  const a = app();
  const row = { id: "dev-id", name: "dev", kind: "devcontainer", state: "running", via: "via" };
  a.view.rows = [row];
  a.view.added = [{ name: "dev", via: true, target: "via" }];
  a.values.set("containers.removing", ["else"]);
  a.openSpaces.splice(0, 2);
  a.setHc(async () => ({ code: 1, stderr: "permission denied", stdout: "" }));
  a.view.disconnect(row, a.view.added[0], a.cx);
  await settle();
  assert.deepEqual(plain(a.values.get("containers.removing")), ["else"]);
  assert.equal(a.view.state[row.id], "permission denied");
  assert.ok(find(a.view.render(), "disconnect-action-dev-id"));
});

test("removing hides gone machines and other rows for that machine", () => {
  const a = app();
  a.view.rows = [{ id: "dev-id", name: "dev", kind: "devcontainer", state: "running", via: "via" }];
  a.view.added = [{ name: "dev", via: true, target: "via" }];
  a.view.gone = [{ name: "dev" }];
  a.values.set("containers.removing", ["dev"]);
  assert.equal(find(a.view.render(), "container-dev-id"), null);
  assert.equal(find(a.view.render(), "forget-dev"), null);
  a.view.removing["dev-id"] = { machine: "dev", target: "via", state: a.view.state, closing: [], started: true, at: Date.now() };
  a.view.added = [];
  assert.equal(find(a.view.render(), "container-dev-id"), null);
  a.view.state["dev-id"] = "disconnecting";
  const tree = a.view.render();
  assert.ok(find(tree, "container-dev-id"));
  assert.equal(find(tree, "disconnect-action-dev-id"), null);
});

test("removal shared state expires after 30 seconds", () => {
  const a = app();
  a.values.set("containers.removing", ["dev"]);
  a.view.state["dev-id"] = "disconnecting";
  a.view.removing["dev-id"] = { machine: "dev", state: a.view.state, closing: [], started: true, at: Date.now() - 31000 };
  a.setMachines([{ name: "dev", layout: "loading" }]);
  a.view.checkPending(a.cx);
  assert.equal(a.values.get("containers.removing").length, 0);
  assert.equal(a.view.state["dev-id"], undefined);
});

test("tooltips sit on divs: the Feature hint on the icon, Loading on the dot", () => {
  const a = app();
  const row = { id: "dev-id", name: "dev", kind: "devcontainer", state: "running", has_feature: false, via: "via" };
  a.view.rows = [row];
  let tree = a.view.render();
  assert.equal(find(tree, "container-icon-dev-id").tip, "Rebuilds need Repair - add the herdr-core Feature");
  assert.ok(!labels(tree).includes("Rebuilds need Repair - add the herdr-core Feature"));
  a.view.added = [{ name: "dev", via: true, target: "via", state: "unreachable", why: "hc is not installed" }];
  tree = a.view.render();
  assert.ok(find(tree, "repair-dev-id"));
  assert.equal(labels(tree).filter((value) => value === "Rebuilds need Repair - add the herdr-core Feature").length, 1);
  a.view.added = [{ name: "dev", via: true, target: "via", state: "connected" }];
  a.setMachines([{ name: "dev", layout: "loading" }]);
  assert.equal(find(a.view.render(), "status-dev-id").tip, "Loading…");
});

test("polls only while shown or waiting, and looks again when it shows", () => {
  const a = app();
  a.view.init(null, a.cx);
  const poll = a.timers.find(([ms]) => ms === 5000)[1];
  a.events.length = 0;
  poll(a.cx);
  assert.equal(a.events.length, 1);
  a.win.active = false;
  a.view.checkPending(a.cx);
  a.events.length = 0;
  poll(a.cx);
  assert.equal(a.events.length, 0);
  a.view.pending.x = { machine: "x", row: {}, at: Date.now() };
  poll(a.cx);
  assert.equal(a.events.length, 1);
  delete a.view.pending.x;
  a.events.length = 0;
  a.win.active = true;
  a.view.checkPending(a.cx);
  assert.deepEqual(plain(a.events), [["refresh"]]);
  find(a.tree, "containers").size = { px: 35 };
  a.view.checkPending(a.cx);
  a.events.length = 0;
  poll(a.cx);
  assert.equal(a.events.length, 0);
});

test("on Windows the helper runs through cmd, which finds hc-devcontainer.cmd", async () => {
  const a = app();
  a.realRefresh();
  a.win.platform = "windows";
  const calls = [];
  const saved = [{ name: "gone", via: true, target: "hc-devcontainer 'c:\\w\\gone'", state: "unreachable" }];
  a.setHc(async () => ({ code: 0, stdout: JSON.stringify({ machines: saved }) }));
  a.setExec(async (cmd, args) => { calls.push([cmd, ...args]); return { code: args[3] === "ls" ? 0 : 1, stdout: "[]" }; });
  a.view.refresh(a.cx);
  await settle();
  a.view.start({ id: "dev-id", name: "dev", kind: "devcontainer", state: "exited" }, a.cx);
  await settle();
  assert.deepEqual(calls, [
    ["cmd", "/d", "/c", "hc-devcontainer", "ls", "--all", "--json"],
    ["cmd", "/d", "/c", "hc-devcontainer", "exists", "hc-devcontainer 'c:\\w\\gone'"],
    ["cmd", "/d", "/c", "hc-devcontainer", "start", "dev-id"],
  ]);
  delete a.win.platform;
  calls.length = 0;
  a.view.refresh(a.cx);
  await settle();
  assert.deepEqual(calls[0], ["hc-devcontainer", "ls", "--all", "--json"]);
});

test("on Windows a helper cmd can't find reads as missing, and Docker Desktop down as Docker down", async () => {
  const a = app();
  a.realRefresh();
  a.win.platform = "windows";
  a.setHc(async () => ({ code: 0, stdout: '{"machines":[]}' }));
  // cmd exits 1 for a command it can't find, in words of the system's language: where's code says which.
  let whereCode = 1;
  const calls = [];
  a.setExec(async (cmd, args) => {
    calls.push([cmd, ...args]);
    if (cmd === "where") return { code: whereCode, stdout: "", stderr: "" };
    return { code: 1, stdout: "", stderr: "'hc-devcontainer' n'est pas reconnu…\r\n" };
  });
  a.view.refresh(a.cx);
  await settle();
  assert.equal(a.view.error, "hc-devcontainer is not installed (put it and hc-devcontainer.cmd on PATH)");
  assert.deepEqual(calls.at(-1), ["where", "hc-devcontainer.cmd"]);
  whereCode = 0;
  a.view.refresh(a.cx);
  await settle();
  assert.equal(a.view.error, "'hc-devcontainer' n'est pas reconnu…");
  // The helper's own errors never need the look.
  calls.length = 0;
  a.setExec(async (cmd, args) => {
    calls.push([cmd, ...args]);
    return { code: 1, stdout: "", stderr: "hc-devcontainer: error during connect: Get \"http://%2F%2F.%2Fpipe%2FdockerDesktopLinuxEngine/v1.47/containers/json\": open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified.\n" };
  });
  a.view.refresh(a.cx);
  await settle();
  assert.equal(a.view.error, "Docker isn't running");
  assert.equal(calls.filter(([cmd]) => cmd === "where").length, 0);
});

test("on Windows core's refusal of --via machines is explained on Connect and Repair", async () => {
  const a = app();
  a.win.platform = "windows";
  const refused = { code: 1, stdout: JSON.stringify({ error: { message: "bad command \"hc-devcontainer 'c:\\\\w'\": one line, run by sh here" } }) };
  const explained = "This Herdr Core refuses --via machines on Windows, so it can't connect containers; see the README's Windows section";
  a.setHc(async () => refused);
  const row = { id: "dev-id", name: "dev", kind: "devcontainer", state: "running", via: "hc-devcontainer 'c:\\w'", suggested_name: "dev" };
  a.view.open(row, a.cx);
  await settle();
  assert.equal(a.view.state["dev-id"], explained);
  a.view.repair(row, { name: "dev", via: true, target: row.via, state: "unreachable" }, a.cx);
  await settle();
  assert.equal(a.view.state["dev-id"], explained);
  // Elsewhere core's own words stay.
  delete a.win.platform;
  a.view.open(row, a.cx);
  await settle();
  assert.match(a.view.state["dev-id"], /run by sh here/);
});
