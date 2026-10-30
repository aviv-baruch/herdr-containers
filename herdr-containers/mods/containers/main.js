// Docker containers, ready to open as spaces from the sidebar.

import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { exec, hc, machines, spaces, run, theme, layout, shared, setShared, nextCommand, window } from "herdr";

const icon = (name) => `mods/workspace-chrome/${name}.svg`;
const setting = (key, fallback) => shared(`containers.${key}`) ?? fallback;
const removingNames = () => new Set(shared("containers.removing") ?? []);
const safe = (p) => p.catch((e) => ({ code: 1, stderr: String(e), stdout: "" }));
const visibleRows = (rows) => rows.filter((r) =>
  (r.kind === "devcontainer" || setting("showOther", true))
  && (r.kind !== "devcontainer" || r.state === "running" || r.state === "paused" || setting("showStopped", true)));
// The container a saved plain-container via names (docker names never hold a quote).
const plainName = (target) => target.match(/^hc-devcontainer --container '([^']+)'$/)?.[1];

function find(node, id) {
  if (node?.id === id) return node;
  for (const child of node?.children ?? []) {
    const found = find(child, id);
    if (found) return found;
  }
  return null;
}

const narrow = () => (find(layout(), "sidebar")?.size?.px ?? Infinity) < 160;
// Collapsed goes by the block's height, as the rail goes by the sidebar's width.
const isCollapsed = () => (find(layout(), "containers")?.size?.px ?? Infinity) <= 35;

// hc's names: at most 32, with the helper's -2 scheme.
function freeName(base, taken) {
  let name = base;
  for (let i = 2; taken.has(name); i++) name = `${base.slice(0, 31 - String(i).length)}-${i}`;
  return name;
}

function errorText(r) {
  try {
    const e = JSON.parse(r.stdout || r.stderr).error;
    if (e?.message) return e.message;
  } catch {}
  return (r.stderr || r.stdout || "it didn't answer").trim();
}

export default class Containers extends View {
  init(_props, cx) {
    this.rows = null;
    this.added = [];
    this.taken = new Set(); // every machine name, temporary ones too
    this.error = null;
    this.machineError = null;
    this.state = {}; // id -> action state or error text
    this.pending = {}; // id -> { machine, at, row }
    this.removing = {}; // row id or machine name -> wait for the app's machine layout to disappear
    // In-flight removals belong to this view; a previous app session cannot finish them.
    if ((shared("containers.removing") ?? []).length) setShared("containers.removing", []);
    this.gone = [];
    this.goneState = {};
    this.watching = !isCollapsed() && window()?.active !== false;
    this.refresh(cx);
    // Timers run while collapsed and in the background: poll only when shown, or while waiting.
    cx.timer.every(5000, (cx) => {
      if (this.watching || Object.keys(this.pending).length || Object.keys(this.removing).length) this.refresh(cx);
    });
    cx.timer.every(250, (cx) => this.checkPending(cx));
    cx.spawn(async (cx) => {
      for (;;) {
        if (await nextCommand() === "toggle") this.toggleCollapse();
        cx.notify();
      }
    });
  }

  toggleCollapse() {
    run("layout.resize", { id: "containers", ...(isCollapsed() ? { weight: 1 } : { px: 35 }) });
  }

  // One look at a time; a call meanwhile looks once more after it.
  refresh(cx) {
    if (this.refreshing) {
      this.again = true;
      return;
    }
    this.refreshing = true;
    cx.spawn(async (cx) => {
      try {
        do {
          this.again = false;
          await this.load();
          cx.notify();
        } while (this.again);
      } finally {
        this.refreshing = false;
      }
    });
  }

  async load() {
    const machineListStartedAt = Date.now();
    const [listed, saved] = await Promise.all([
      safe(exec("hc-devcontainer", ["ls", "--all", "--json"])),
      safe(hc(["machine", "ls", "--json"])),
    ]);
    let rows = null;
    try {
      if (listed.code !== 0) throw new Error(errorText(listed));
      rows = JSON.parse(listed.stdout);
      if (!Array.isArray(rows)) throw new Error("invalid container list");
      this.error = null;
    } catch (e) {
      rows = null;
      const message = String(e.message ?? e);
      // The app's own spawn failure (mods.rs run_blocking), not the helper's about docker.
      this.error = /^(Error: )?cannot run hc-devcontainer:/.test(message)
        ? "hc-devcontainer is not installed (put it on PATH)"
        : /Cannot connect to the Docker daemon/i.test(message) ? "Docker isn't running" : message;
    }
    try {
      if (saved.code !== 0) throw new Error(errorText(saved));
      const all = JSON.parse(saved.stdout).machines;
      if (!Array.isArray(all)) throw new Error("invalid machine list");
      this.taken = new Set(all.map((m) => m.name));
      this.added = all.filter((m) => !m.temporary);
      const down = this.added.filter((m) => m.via && /^hc-devcontainer(?:\s|$)/.test(m.target) && m.state !== "connected")
        .map((m) => m.name).sort();
      if (JSON.stringify(down) !== JSON.stringify(shared("containers.down") ?? [])) setShared("containers.down", down);
      this.machineListAt = machineListStartedAt;
      this.machineError = null;
    } catch (e) {
      this.machineError = String(e.message ?? e);
    }
    const gone = [];
    if (rows && !this.machineError) {
      // A connected machine is never gone; Docker answers for the rest, whatever their via's form.
      const checked = await Promise.all(this.missing(rows).filter((m) => m.state !== "connected")
        .map(async (m) => [m, (await safe(exec("hc-devcontainer", ["exists", m.target]))).code]));
      for (const [m, code] of checked) {
        const name = code === 0 && plainName(m.target);
        if (code === 1) gone.push(m);
        // ls leaves out stopped plain containers; one with a machine still gets its row.
        else if (name) rows.push({ id: name, name, kind: "container", state: "exited", via: m.target, image: "", suggested_name: m.name });
      }
    }
    this.rows = rows ?? [];
    this.gone = gone;
    for (const row of this.rows) {
      if (row.state !== "paused" && this.state[row.id]?.startsWith("Unpause it first")) delete this.state[row.id];
    }
  }

  missing(rows) {
    return this.added.filter((m) => m.via && /^hc-devcontainer(?:\s|$)/.test(m.target) && !rows.some((r) => r.via === m.target));
  }

  saved(row) {
    return this.added.find((m) => m.via && m.target === row.via && !removingNames().has(m.name));
  }

  open(row, cx, confirmed = false) {
    if (row.state === "paused") {
      this.state[row.id] = `Unpause it first (docker unpause ${row.name})`;
      cx.notify();
      return;
    }
    if (row.state !== "running") return;
    if (["connecting", "disconnecting"].includes(this.state[row.id])) return;
    const saved = this.saved(row);
    const existing = saved && spaces().find((s) => s.machine === saved.name);
    if (existing) return run("space.select", { id: existing.id });
    if (!saved && row.kind === "container" && !confirmed) {
      if (this.state[row.id] === "confirm") delete this.state[row.id];
      else this.state[row.id] = "confirm";
      cx.notify();
      return;
    }
    if (!saved && this.machineError) {
      this.state[row.id] = this.machineError;
      cx.notify();
      return;
    }
    this.state[row.id] = "connecting";
    cx.notify();
    if (saved) {
      this.pending[row.id] = { machine: saved.name, at: Date.now(), row };
      this.checkPending(cx);
      return;
    }
    cx.spawn(async (cx) => {
      const wanted = freeName(row.suggested_name, this.taken);
      const r = await safe(hc(["machine", "add", "--name", wanted, "--via", row.via, "--install", "--json"]));
      if (r.code !== 0) this.state[row.id] = errorText(r);
      else {
        let name = wanted;
        try { name = JSON.parse(r.stdout).name || name; } catch {}
        this.taken.add(name);
        // Keep the pending name visible until machine ls catches up on refresh.
        if (!this.added.some((m) => m.name === name)) this.added.push({ name, state: "adding", via: true, target: row.via });
        this.pending[row.id] = { machine: name, at: Date.now(), row };
        this.refresh(cx);
      }
      cx.notify();
    });
  }

  start(row, cx) {
    if (this.state[row.id] === "starting") return;
    this.state[row.id] = "starting";
    cx.notify();
    cx.spawn(async (cx) => {
      const r = await safe(exec("hc-devcontainer", ["start", row.id]));
      if (r.code === 0) {
        delete this.state[row.id];
        this.refresh(cx);
      } else this.state[row.id] = errorText(r);
      cx.notify();
    });
  }

  repair(row, saved, cx) {
    if (this.state[row.id] === "repairing") return;
    const oldSpaces = spaces().filter((s) => s.machine === saved.name).map((s) => s.id);
    this.state[row.id] = "repairing";
    cx.notify();
    cx.spawn(async (cx) => {
      // Added under a spare name first, so a failed add leaves the old machine as it was.
      const spare = freeName(`${saved.name.slice(0, 25)}-repair`, this.taken);
      const added = await safe(hc(["machine", "add", "--name", spare, "--via", row.via, "--install", "--json"]));
      let r = added;
      if (r.code === 0) r = await safe(hc(["machine", "rm", saved.name]));
      if (r.code === 0) r = await safe(hc(["machine", "rename", spare, saved.name]));
      if (r.code !== 0) this.state[row.id] = errorText(r);
      else {
        this.state[row.id] = "connecting";
        let core = null;
        try { core = JSON.parse(added.stdout).id; } catch {}
        this.pending[row.id] = { machine: saved.name, core, oldSpaces, at: Date.now(), row };
        this.refresh(cx);
      }
      cx.notify();
    });
  }

  removeMachine(machine, state, key, busy, cx) {
    if (state[key] === busy || removingNames().has(machine.name)) return;
    setShared("containers.removing", [...removingNames(), machine.name]);
    const closing = spaces().filter((s) => s.machine === machine.name).map((s) => s.id);
    for (const id of closing) run("space.close", { id, confirmed: true });
    this.removing[key] = { machine: machine.name, target: machine.target, state, closing, at: Date.now() };
    for (const [id, pending] of Object.entries(this.pending)) {
      if (id !== key && pending.machine !== machine.name) continue;
      delete this.pending[id];
      if (this.state[id] === "connecting") delete this.state[id];
    }
    state[key] = busy;
    cx.notify();
    this.checkPending(cx);
  }

  machineRm(key, removal, cx) {
    removal.started = true;
    cx.spawn(async (cx) => {
      const r = await safe(hc(["machine", "rm", removal.machine]));
      if (r.code === 0) {
        removal.removedAt = Date.now();
        this.refresh(cx);
      } else {
        delete this.removing[key];
        setShared("containers.removing", [...removingNames()].filter((name) => name !== removal.machine));
        removal.state[key] = errorText(r);
      }
      cx.notify();
    });
  }

  forget(machine, cx) {
    this.removeMachine(machine, this.goneState, machine.name, "forgetting", cx);
  }

  disconnect(row, machine, cx) {
    this.removeMachine(machine, this.state, row.id, "disconnecting", cx);
  }

  checkPending(cx) {
    const watching = !isCollapsed() && window()?.active !== false;
    if (watching && !this.watching) this.refresh(cx);
    this.watching = watching;
    let changed = false;
    // Shipped Spaces caches machine ls for 15 s; spaces-containers refreshes when removal ends.
    const floor = find(layout(), "spaces")?.content?.mod === "spaces" ? 16000 : 0;
    for (const [key, removal] of Object.entries(this.removing)) {
      const elapsed = Date.now() - removal.at;
      if (!removal.started) {
        // space.close only queues: its doc write and terminal kills come after, and nothing
        // reports them done. Wait for the spaces to go and a moment more, 5 s at most.
        const open = spaces().some((s) => removal.closing.includes(s.id));
        if (elapsed < 5000 && (open || (removal.closing.length && elapsed < 2000))) continue;
        this.machineRm(key, removal, cx);
        continue;
      }
      if (elapsed < 30000 && (!removal.removedAt || Date.now() - removal.removedAt < floor
        || !(this.machineListAt > removal.removedAt)
        || this.added.some((m) => m.name === removal.machine)
        || machines().some((m) => m.name === removal.machine))) continue;
      setShared("containers.removing", [...removingNames()].filter((name) => name !== removal.machine));
      delete removal.state[key];
      delete this.removing[key];
      changed = true;
    }
    for (const [id, pending] of Object.entries(this.pending)) {
      const { machine, row, at, core, oldSpaces } = pending;
      const current = this.added.find((m) => m.name === machine);
      // Only a list fetched after the add can tell it's gone (removed elsewhere).
      if (!current && this.machineListAt > at) {
        delete this.pending[id];
        if (this.state[id] === "connecting") delete this.state[id];
        changed = true;
        continue;
      }
      const fresh = current?.state === "connected" && (!core || (current.id === core
        && !spaces().some((s) => oldSpaces.includes(s.id))));
      if (fresh && (machines().some((m) => m.name === machine && m.layout === "ready") || Date.now() - at > 5000)) {
        const existing = spaces().find((s) => s.machine === machine);
        if (existing) run("space.select", { id: existing.id });
        else run("space.new", { name: row.suggested_name, ...(row.workspace ? { folder: row.workspace } : {}), machine });
        delete this.pending[id];
        delete this.state[id];
        changed = true;
      } else if (Date.now() - at > 20000) {
        delete this.pending[id];
        this.state[id] = current?.state === "connected"
          ? "Connected, but Herdr hasn't loaded it yet - click again"
          : `Timed out waiting for ${machine} to connect - click again`;
        changed = true;
      }
    }
    if (changed) cx.notify();
  }

  row(t, row, compact) {
    const saved = this.saved(row);
    const machine = saved && machines().find((m) => m.name === saved.name);
    const state = this.state[row.id];
    const spaceCount = saved ? spaces().filter((s) => s.machine === saved.name).length : 0;
    const detail = row.kind === "devcontainer" ? row.folder : row.image;
    const paused = row.state === "paused";
    const stopped = row.state !== "running" && !paused;
    const disconnected = saved && (shared("containers.down") ?? []).includes(saved.name);
    const active = saved && spaces().some((s) => s.machine === saved.name && s.active);
    // hc's wording for these (herdr-core machines.rs, remote.rs); machine ls has no error kind.
    const repair = row.state === "running" && saved?.state === "unreachable"
      && /hc is not installed|a different core answers there/i.test(saved.why || "");
    const ready = machine?.layout === "ready" && saved?.state === "connected";
    const loading = !ready && machine?.layout === "loading";
    const progress = { connecting: "Connecting…", starting: "Starting…", repairing: "Repairing…", disconnecting: "Disconnecting…" }[state];
    const problem = state && !progress && !["confirm", "disconnect-confirm"].includes(state) ? state : null;
    const button = (label, action) => div().id(`${label.toLowerCase()}-${row.id}`).h(22).px(7).flex_none()
      .rounded(5).bg(t.raised).text_size(10.5).text_color(t.foreground).cursor_pointer()
      .hover((el) => el.bg(t.hover)).flex().items_center()
      .on_mouse_down("left", (_e, cx) => cx.stop_propagation())
      .on_click((_e, cx) => { cx.stop_propagation(); action(cx); }).child(label);
    return v_flex().flex_none()
      .child(h_flex().id(`container-${row.id}`).h(30).px(compact ? 2 : 8)
        .gap(compact ? 1 : 9).items_center().rounded(8).cursor_pointer()
        .when(!active, (el) => el.hover((s) => s.bg(t.hover)))
        // The rail has no room for lines below a row: what they'd say goes in its tooltip.
        .when(compact, (el) => el.justify_center().when(Boolean(progress || problem), (el) => el.tooltip(progress || problem)))
        .on_click((_e, cx) => compact && stopped ? this.start(row, cx)
          : compact && repair ? this.repair(row, saved, cx) : this.open(row, cx))
        .child(div().id(`container-icon-${row.id}`).flex_none()
          .when(row.kind === "devcontainer" && !row.has_feature,
            (el) => el.tooltip("Rebuilds need Repair - add the herdr-core Feature"))
          .child(svg(icon(row.kind === "devcontainer" ? "folder" : "server")).size(compact ? 12 : 18).text_color(t.muted)))
        .when(!compact, (el) => el.child(h_flex().flex_1().min_w(0).gap(5).items_center()
          .child(div().flex_none().max_w(150).truncate().text_color(stopped || disconnected || state === "disconnecting" ? t.dim : t.foreground).child(row.name))
          .child(div().flex_1().min_w(0).truncate().text_size(11.5).text_color(t.dim).child(detail || row.kind))))
        .when(!compact && (paused || stopped), (el) => el.child(div().text_size(10.5).text_color(paused ? t.blocked : t.dim)
          .child(paused ? "Paused" : "Stopped")))
        .when(!compact && disconnected, (el) => el.child(div().text_size(10.5).text_color(t.dim).child("Disconnected")))
        .when(!compact && Boolean(saved) && !disconnected && !paused && !stopped, (el) => el.child(div().id(`status-${row.id}`)
          .size(7).flex_none().rounded(4).bg(ready ? t.ansi2 : loading ? t.working : t.blocked)
          .tooltip(ready ? "Connected" : loading ? "Loading…" : "Unreachable")))
        .when(!compact && stopped && state !== "starting", (el) => el.child(button("Start", (cx) => this.start(row, cx))))
        .when(!compact && repair && !["repairing", "connecting"].includes(state),
          (el) => el.child(button("Repair", (cx) => this.repair(row, saved, cx))))
        .when(!compact && Boolean(saved),
          (el) => el.child(div().id(`disconnect-action-${row.id}`)
          .p(4).flex_none().rounded(5).text_color(t.dim).cursor_pointer()
          .hover((s) => s.bg(t.hover).text_color(t.foreground))
          .tooltip("Disconnect machine").flex().items_center().justify_center()
          .on_mouse_down("left", (_e, cx) => cx.stop_propagation())
          .on_click((_e, cx) => {
            cx.stop_propagation();
            if (["starting", "repairing", "connecting", "disconnecting"].includes(this.state[row.id])) return;
            this.state[row.id] = state === "disconnect-confirm" ? null : "disconnect-confirm";
            cx.notify();
          }).child(svg(icon("x")).size(12).flex_none()))))
      .when(!compact && repair && !row.has_feature && !["repairing", "connecting"].includes(state),
        (el) => el.child(div().px(12).pb(5).text_size(11).text_color(t.dim)
          .child("Rebuilds need Repair - add the herdr-core Feature")))
      .when(state === "confirm", (el) => el.child(v_flex().px(12).pb(7).gap(5)
        .child(div().text_size(11).text_color(t.muted).child(`Connecting copies herdr-core into ${row.name}`))
        .child(h_flex().gap(6)
          .child(div().id(`connect-${row.id}`).px(8).h(24).rounded(6).bg(t.raised).border_1().border_color(t.input)
            .text_size(11).text_color(t.foreground).cursor_pointer().hover((s) => s.bg(t.hover))
            .flex().items_center().on_click((_e, cx) => this.open(row, cx, true)).child("Connect"))
          .child(div().id(`cancel-${row.id}`).px(8).h(24).rounded(6)
            .text_size(11).text_color(t.muted).cursor_pointer().hover((s) => s.bg(t.hover))
            .flex().items_center().on_click((_e, cx) => { delete this.state[row.id]; cx.notify(); }).child("Cancel")))))
      .when(!compact && state === "disconnect-confirm", (el) => el.child(v_flex().px(12).pb(7).gap(5)
        .child(div().text_size(11).text_color(t.muted)
          .child(`Close ${spaceCount} space${spaceCount === 1 ? "" : "s"} and disconnect?`))
        .child(h_flex().gap(6)
          .child(button("Disconnect", (cx) => this.disconnect(row, saved, cx)))
          .child(button("Cancel", (cx) => { delete this.state[row.id]; cx.notify(); })))))
      .when(!compact && Boolean(progress), (el) => el.child(div().px(12).pb(5).text_size(11).text_color(t.dim).child(progress)))
      .when(!compact && Boolean(problem), (el) => el.child(div().px(12).pb(5).text_size(11).text_color(t.blocked).child(problem)));
  }

  goneRow(t, machine, unknown) {
    const state = this.goneState[machine.name];
    return v_flex().flex_none().px(8).py(4).gap(3)
      .child(h_flex().gap(7).items_center()
        .child(div().flex_1().min_w(0).truncate().text_size(12).text_color(t.dim).child(machine.name))
        .when(!unknown && state !== "forgetting", (el) => el.child(div().id(`forget-${machine.name}`)
          .h(22).px(7).rounded(5).bg(t.raised).text_size(10.5).text_color(t.foreground)
          .cursor_pointer().hover((s) => s.bg(t.hover)).flex().items_center()
          .on_click((_e, cx) => this.forget(machine, cx)).child("Forget"))))
      .child(div().text_size(10.5).text_color(t.dim).child(unknown ? this.error === "Docker isn't running" ? "Disconnected" : "container status unknown" : "container removed"))
      .when(Boolean(state), (el) => el.child(div().text_size(10.5)
        .text_color(state === "forgetting" ? t.dim : t.blocked)
        .child(state === "forgetting" ? "Forgetting…" : state)));
  }

  render() {
    const t = theme();
    const compact = narrow();
    const collapsed = isCollapsed();
    const removing = removingNames();
    const removingTargets = new Set(Object.values(this.removing).map((r) => r.target).filter(Boolean));
    const rows = visibleRows(this.rows ?? []).filter((r) => this.state[r.id] === "disconnecting"
      || (!removingTargets.has(r.via)
        && !this.added.some((m) => m.via && m.target === r.via && removing.has(m.name))));
    const dev = rows.filter((r) => r.kind === "devcontainer");
    const other = rows.filter((r) => r.kind !== "devcontainer");
    const unknown = Boolean(this.error);
    const missing = (unknown ? this.missing(rows) : this.gone).filter((m) => !removing.has(m.name));
    const folded = new Set(setting("folded", []));
    const groupHeader = (id, label, count) => {
      const open = !folded.has(id);
      return h_flex().id(`containers-group-${id}`)
        .px(8).pt(8).h(30).flex_none().gap(6).items_center().text_size(11).font_medium()
        .cursor_pointer()
        .on_click((_e, cx) => {
          open ? folded.add(id) : folded.delete(id);
          setShared("containers.folded", [...folded]);
          cx.notify();
        })
        .child(div().truncate().text_color(t.dim).child(label))
        .child(div().text_color(t.dim).child(String(count)))
        .child(div().flex_1())
        .when(!open, (el) => el.child(svg(icon("chevron-right")).size(11).flex_none().text_color(t.dim)));
    };
    const section = (id, label, list, renderRow) => list.length ? [
      groupHeader(id, label, list.length),
      ...(folded.has(id) ? [] : list.map(renderRow)),
    ] : [];
    return v_flex().size_full().min_h(0).bg(t.chrome).border_t(1).border_color(t.border).text_size(13)
      .child(h_flex().id("containers-header").h(31).flex_none().pl(compact ? 2 : 16).pr(compact ? 2 : 10)
        .items_center().cursor_pointer().on_click(() => this.toggleCollapse())
        .when(!compact, (el) => el.child(div().text_size(11.5).font_medium().text_color(t.dim).child("Containers")))
        .child(div().flex_1())
        .child(svg(icon(collapsed ? "chevron-right" : "chevron-down")).size(11).flex_none().text_color(t.dim)))
      .when(!collapsed, (el) => el.child(v_flex().flex_1().min_h(0).px(compact ? 0 : 8).overflow_y_scrollbar().mode("always")
        .when(Boolean(this.error), (el) => el.child(div().px(8).py(5).text_size(11.5).text_color(t.blocked).child(this.error)))
        .when(!this.error && Boolean(this.machineError), (el) => el.child(div().px(8).py(5).text_size(11.5).text_color(t.blocked).child(this.machineError)))
        .when(!this.error && this.rows === null, (el) => el.child(div().px(8).py(5).text_size(11.5).text_color(t.dim).child("Finding containers…")))
        .when(!this.error && this.rows !== null && rows.length === 0 && missing.length === 0,
          (el) => el.child(div().px(8).py(5).text_size(11.5).text_color(t.dim).child("No containers")))
        .children(compact ? rows.map((r) => this.row(t, r, compact)) : [
          ...section("dev", "Dev containers", dev, (r) => this.row(t, r, compact)),
          ...section("other", "Other containers", other, (r) => this.row(t, r, compact)),
          ...section("gone", unknown ? "Saved machines" : "Gone", missing, (m) => this.goneRow(t, m, unknown)),
        ])));
  }
}
