"use strict";

const REFRESH_MS = 5000;
const SVG_NS = "http://www.w3.org/2000/svg";
const LAYOUT_KEY = "mindful-fleet-layout";
const MOODS = { ok: "Doing fine", warn: "Needs a look", bad: "Needs help now", offline: "Offline" };
const TREND_MIN_RANGE = 10; // Degrees. Keeps a half-degree wiggle looking like the small thing it is.
const LOW_VOLTS = 4.63;
const HIGH_VOLTS = 5.25;
const NOMINAL_VOLTS = 5;

const ROWS = [
  ["trend", "Temperature trend"], ["fan", "Fan"], ["speed", "Speed"], ["power", "Power and voltage"],
  ["cpu", "CPU"], ["clock", "Clock speed"], ["load", "Load"], ["cores", "Each core"],
  ["memory", "Memory"], ["swap", "Swap"], ["storage", "Storage"], ["network", "Network"],
  ["uptime", "Running for"], ["system", "System"],
];

const layout = loadLayout();
let arranging = false;
let dragging = null;
let latestPis = [];

function loadLayout() {
  const empty = { size: 100, order: [], groups: [], members: {}, hidden: [] };
  try {
    return { ...empty, ...JSON.parse(localStorage.getItem(LAYOUT_KEY)) };
  } catch (error) {
    return empty;
  }
}

function shown(key) {
  return !layout.hidden.includes(key);
}

function saveLayout() {
  try {
    localStorage.setItem(LAYOUT_KEY, JSON.stringify(layout));
  } catch (error) {
    // Private windows can refuse storage. The layout still works until the page closes.
  }
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text; // Never innerHTML: hostnames come from the network.
  return node;
}

function svg(tag, attrs, parent) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  if (parent) parent.appendChild(node);
  return node;
}

function button(label, title, onClick) {
  const node = el("button", "small", label);
  node.type = "button";
  node.title = title;
  node.setAttribute("aria-label", title);
  node.addEventListener("click", onClick);
  return node;
}

function mascot(status) {
  const root = svg("svg", { viewBox: "0 0 64 64", class: "mascot", "aria-hidden": "true" });
  const ink = "#23264a";
  for (const y of [18, 28, 38, 48]) {
    svg("rect", { x: 2, y, width: 8, height: 4, rx: 2, fill: ink }, root);
    svg("rect", { x: 54, y, width: 8, height: 4, rx: 2, fill: ink }, root);
  }
  svg("rect", { x: 8, y: 8, width: 48, height: 50, rx: 13, fill: "#fff", stroke: ink, "stroke-width": 3 }, root);
  const line = { fill: "none", stroke: ink, "stroke-width": 3, "stroke-linecap": "round" };
  if (status === "offline") {
    svg("path", { ...line, d: "M18 30q5 4 10 0M36 30q5 4 10 0" }, root);
    svg("path", { ...line, d: "M28 44h8" }, root);
    svg("path", { ...line, "stroke-width": 2, d: "M46 6h8l-8 8h8" }, root);
  } else {
    svg("circle", { cx: 23, cy: 29, r: status === "bad" ? 5 : 4, fill: ink }, root);
    svg("circle", { cx: 41, cy: 29, r: status === "bad" ? 5 : 4, fill: ink }, root);
    if (status === "ok") svg("path", { ...line, d: "M22 41q10 9 20 0" }, root);
    if (status === "warn") svg("path", { ...line, d: "M23 45q9-5 18 0" }, root);
    if (status === "bad") svg("ellipse", { cx: 32, cy: 45, rx: 6, ry: 5, fill: ink }, root);
    if (status !== "ok") {
      svg("path", { d: "M50 13c3 4 5 6 5 9a5 5 0 0 1-10 0c0-3 2-5 5-9z", fill: "#4d9de0", stroke: ink, "stroke-width": 2 }, root);
    }
  }
  return root;
}

function sparkline(history) {
  if (history.length < 2) return el("div", "spark");
  const middle = (Math.min(...history) + Math.max(...history)) / 2;
  const range = Math.max(TREND_MIN_RANGE, Math.max(...history) - Math.min(...history) + 2);
  const low = middle - range / 2;
  const root = svg("svg", { viewBox: "0 0 100 40", preserveAspectRatio: "none", class: "spark", role: "img", "aria-label": `Temperature over the last ${history.length} readings` });
  const points = history.map((value, i) =>
    `${(i / (history.length - 1) * 100).toFixed(1)},${(38 - (value - low) / range * 36).toFixed(1)}`);
  svg("polyline", { points: points.join(" ") }, root);
  return root;
}

function bytes(value) {
  if (value == null) return "";
  const gb = value / 1024 ** 3;
  if (gb < 1) return `${Math.round(value / 1024 ** 2)} MB`;
  return gb >= 100 ? `${gb.toFixed(0)} GB` : `${gb.toFixed(1)} GB`;
}

function duration(seconds) {
  if (seconds == null) return "Unknown";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days) return `${days} day${days === 1 ? "" : "s"}, ${hours} hr`;
  if (hours) return `${hours} hr, ${minutes} min`;
  return `${minutes} min`;
}

function rate(bytesPerSecond) {
  if (bytesPerSecond == null) return "—";
  if (bytesPerSecond >= 1024 ** 2) return `${(bytesPerSecond / 1024 ** 2).toFixed(1)} MB/s`;
  if (bytesPerSecond >= 1024) return `${Math.round(bytesPerSecond / 1024)} KB/s`;
  return `${bytesPerSecond} B/s`;
}

function ghz(mhz) {
  return `${(mhz / 1000).toFixed(2)} GHz`;
}

function average(values) {
  return values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
}

function shortModel(model) {
  return model ? model.replace(/ Model [A-Z]\b/, "").replace(/ Rev [\d.]+$/, "").trim() : null;
}

function boardMemory(pi) {
  let mb = pi.board_ram_mb;
  if (!mb && pi.mem_total) {
    // Some memory is reserved for the system, so round up to the nearest board size.
    const usable = pi.mem_total / 1024 ** 2;
    mb = [512, 1024, 2048, 4096, 8192, 16384, 32768].find((size) => size >= usable * 0.98) || null;
  }
  if (!mb) return null;
  return mb >= 1024 ? `${mb / 1024} GB` : `${mb} MB`;
}

function meter(percent, level, label, detail, title) {
  const wrap = el("dd", "meter");
  if (percent == null) {
    wrap.textContent = "No reading";
    return wrap;
  }
  const line = el("div", "meter-line");
  const bar = el("div", "bar");
  const fill = el("div", `fill ${level || ""}`);
  fill.style.width = `${Math.max(0, Math.min(100, percent))}%`;
  bar.appendChild(fill);
  line.append(bar, el("span", level || "", label));
  wrap.appendChild(line);
  if (detail) wrap.appendChild(el("small", "detail", detail));
  if (title) wrap.title = title;
  return wrap;
}

function coreGrid(pi) {
  const grid = el("div", "cores");
  pi.cpu_per_core.forEach((value, i) => {
    const avg = pi.core_average[i];
    const mhz = pi.cpu_mhz[i];
    const cell = el("div", "core");
    cell.title = `Core ${i + 1}: ${Math.round(value)}% now, ${avg == null ? "no" : `${Math.round(avg)}%`} average over 30 minutes`;
    const column = el("div", "core-bar");
    const level = el("i");
    level.style.height = `${value}%`;
    column.appendChild(level);
    cell.append(el("small", "", `Core ${i + 1}`), column, el("strong", "", `${Math.round(value)}%`));
    if (avg != null) cell.appendChild(el("small", "", `avg ${Math.round(avg)}%`));
    if (mhz != null) cell.appendChild(el("small", "", ghz(mhz)));
    grid.appendChild(cell);
  });
  return grid;
}

function arrangeBar(pi, section) {
  const bar = el("div", "arrange-bar");
  const index = section.findIndex((other) => other.id === pi.id);
  const earlier = button("←", `Move ${pi.hostname} earlier`, () => moveWithin(section, index, index - 1));
  const later = button("→", `Move ${pi.hostname} later`, () => moveWithin(section, index, index + 1));
  earlier.disabled = index === 0;
  later.disabled = index === section.length - 1;

  const select = el("select");
  select.setAttribute("aria-label", `Group for ${pi.hostname}`);
  const none = el("option", "", "No group");
  none.value = "";
  select.appendChild(none);
  for (const group of layout.groups) {
    const option = el("option", "", group.name);
    option.value = group.id;
    select.appendChild(option);
  }
  select.value = layout.members[pi.id] || "";
  select.addEventListener("change", () => {
    setGroup(pi.id, select.value);
    select.blur();
    commit();
  });
  bar.append(earlier, later, select);
  return bar;
}

function card(pi, section) {
  const h = pi.health;
  const article = el("article", `pi ${h.status}`);

  if (arranging) {
    article.draggable = true;
    article.classList.add("movable");
    article.addEventListener("dragstart", (event) => {
      dragging = pi.id;
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", pi.id); // Firefox won't start a drag without this.
      setTimeout(() => article.classList.add("dragging")); // Changing the card during dragstart can cancel the drag.
    });
    article.addEventListener("dragend", () => {
      dragging = null;
      render();
    });
    article.addEventListener("dragover", (event) => event.preventDefault());
    article.addEventListener("drop", (event) => {
      event.preventDefault();
      event.stopPropagation();
      const moving = dragging;
      dragging = null;
      if (moving && moving !== pi.id) placeBefore(moving, pi.id);
    });
  }

  const head = el("div", "pi-head");
  const title = el("div", "pi-title");
  title.append(el("h2", "", pi.hostname), el("p", "pi-mood", MOODS[h.status]));
  const board = [shortModel(pi.model), boardMemory(pi)].filter(Boolean).join(", ");
  if (board) title.appendChild(el("p", "pi-board", board));
  if (pi.ip) title.appendChild(el("p", "pi-where", pi.ip));
  head.append(mascot(h.status), title);

  const body = el("div", h.online ? "pi-body" : "pi-body stale");
  if (arranging) body.appendChild(arrangeBar(pi, section));
  const heat = el("div", "heat");
  const temp = h.temp.value == null ? "—" : `${Math.round(h.temp.value)}°C`;
  heat.append(el("div", `degrees ${h.temp.level}`, temp));
  if (shown("trend")) heat.appendChild(sparkline(pi.history));
  body.appendChild(heat);
  if (!h.online) body.appendChild(el("p", "heat-note", "Last known numbers"));

  if (h.issues.length) {
    const list = el("ul", "issues");
    for (const issue of h.issues) list.appendChild(el("li", issue.level, issue.text));
    body.appendChild(list);
  }

  const facts = el("dl", "facts");
  const row = (key, label, value) => {
    if (shown(key)) facts.append(el("dt", "", label), value);
  };
  const text = (item, detail) => {
    const dd = el("dd", item.level, item.text);
    if (detail) dd.appendChild(el("small", "detail", detail));
    return dd;
  };
  const cores = h.cpu.total;

  row("fan", "Fan", text(h.fan, pi.fan_max_rpm ? `fastest seen ${pi.fan_max_rpm.toLocaleString()} RPM` : ""));
  if (h.fan.percent != null) row("fan", "Fan effort", meter(h.fan.percent, h.fan.percent >= 100 ? "warn" : "", `${h.fan.percent}%`, "of full power", "How hard the board is driving the fan"));
  row("speed", "Speed", text(h.speed));
  row("power", "Power", text(h.power));
  if (h.power.volts != null) {
    const position = (h.power.volts - LOW_VOLTS) / (HIGH_VOLTS - LOW_VOLTS) * 100;
    row("power", "Input voltage", meter(position, "", `${h.power.volts.toFixed(2)} V`, `${Math.round(h.power.volts / NOMINAL_VOLTS * 100)}% of the 5 V a Pi expects`));
  }
  if (pi.core_volts != null) row("power", "Core voltage", el("dd", "", `${pi.core_volts.toFixed(3)} V`));
  if (cores) {
    row("cpu", "CPU", meter(h.cpu.average, "", `${Math.round(h.cpu.average)}%`, `${h.cpu.busy} of ${cores} cores in use`));
  }
  const mhz = average(pi.cpu_mhz);
  if (mhz != null && pi.cpu_max_mhz) {
    row("clock", "Clock speed", meter(mhz / pi.cpu_max_mhz * 100, "", ghz(mhz), `of ${ghz(pi.cpu_max_mhz)} max`, "Average across cores"));
  }
  if (pi.load_avg.length && cores) {
    const [one, five, fifteen] = pi.load_avg;
    row("load", "Load", meter(one / cores * 100, "", one.toFixed(2), `of ${cores} cores, 5 and 15 min: ${five.toFixed(2)}, ${fifteen.toFixed(2)}`, "How many tasks are running or waiting. Equal to the core count means fully busy"));
  }
  row("memory", "Memory", meter(h.memory.percent, h.memory.level, `${Math.round(h.memory.percent)}%`, `${bytes(pi.mem_used)} of ${bytes(pi.mem_total)} usable`));
  if (pi.swap_total) {
    row("swap", "Swap", meter(pi.swap_used / pi.swap_total * 100, "", `${Math.round(pi.swap_used / pi.swap_total * 100)}%`, `${bytes(pi.swap_used)} of ${bytes(pi.swap_total)}`, "Disk space used as overflow memory"));
  }
  row("storage", "Storage", meter(h.disk.percent, h.disk.level, `${Math.round(h.disk.percent)}%`, `${bytes(pi.disk_used)} of ${bytes(pi.disk_total)}`));
  if (pi.net_rx_bps != null) {
    row("network", "Network", text({ level: "", text: `↓ ${rate(pi.net_rx_bps)}  ↑ ${rate(pi.net_tx_bps)}` }, "in and out"));
  }
  row("uptime", "Running for", el("dd", "", duration(pi.uptime_s)));
  if (pi.os_name || pi.kernel) {
    const system = text({ level: "", text: pi.os_name || "Unknown" }, pi.kernel ? `kernel ${pi.kernel}` : "");
    if (pi.reporter_version) system.appendChild(el("small", "detail", `Mindful Fleet ${pi.reporter_version}`));
    row("system", "System", system);
  }
  body.appendChild(facts);
  if (pi.cpu_per_core.length && shown("cores")) body.appendChild(coreGrid(pi));

  article.append(head, body);
  return article;
}

/* Layout changes */

function orderIndex(pi, fallback) {
  const index = layout.order.indexOf(pi.id);
  return index === -1 ? layout.order.length + fallback : index;
}

function sortedPis() {
  return latestPis
    .map((pi, i) => ({ pi, rank: orderIndex(pi, i) }))
    .sort((a, b) => a.rank - b.rank)
    .map((item) => item.pi);
}

function commit() {
  saveLayout();
  render();
}

function setGroup(piId, groupId) {
  if (groupId) layout.members[piId] = groupId;
  else delete layout.members[piId];
}

function moveWithin(section, from, to) {
  if (to < 0 || to >= section.length) return;
  const ids = sortedPis().map((pi) => pi.id);
  const moving = section[from].id;
  const target = section[to].id;
  ids.splice(ids.indexOf(moving), 1);
  ids.splice(ids.indexOf(target) + (to > from ? 1 : 0), 0, moving);
  layout.order = ids;
  commit();
}

function placeBefore(movingId, targetId) {
  const ids = sortedPis().map((pi) => pi.id);
  ids.splice(ids.indexOf(movingId), 1);
  ids.splice(ids.indexOf(targetId), 0, movingId);
  layout.order = ids;
  setGroup(movingId, layout.members[targetId] || "");
  commit();
}

function addGroup(name) {
  layout.groups.push({ id: `g${Date.now().toString(36)}`, name });
  commit();
}

function moveGroup(index, change) {
  const [group] = layout.groups.splice(index, 1);
  layout.groups.splice(index + change, 0, group);
  commit();
}

function removeGroup(groupId) {
  layout.groups = layout.groups.filter((group) => group.id !== groupId);
  for (const [piId, member] of Object.entries(layout.members)) {
    if (member === groupId) delete layout.members[piId];
  }
  commit();
}

/* Drawing */

function groupHeader(group, pis, index) {
  const header = el("div", "group-head");
  if (arranging && group) {
    const name = el("input", "group-rename");
    name.value = group.name;
    name.maxLength = 40;
    name.setAttribute("aria-label", "Group name");
    name.addEventListener("change", () => {
      group.name = name.value.trim() || group.name;
      name.blur();
      commit();
    });
    const up = button("↑", `Move ${group.name} up`, () => moveGroup(index, -1));
    const down = button("↓", `Move ${group.name} down`, () => moveGroup(index, 1));
    up.disabled = index === 0;
    down.disabled = index === layout.groups.length - 1;
    header.append(name, up, down, button("Remove group", `Remove ${group.name}. Its cards stay on the page`, () => removeGroup(group.id)));
  } else {
    header.appendChild(el("h2", "", group ? group.name : "Not in a group"));
  }
  if (pis.length) {
    const fine = pis.filter((pi) => pi.health.status === "ok").length;
    header.appendChild(el("span", "group-count", `${fine} of ${pis.length} doing fine`));
  }
  return header;
}

function section(group, pis, index) {
  const wrap = el("section", "group");
  if (layout.groups.length) wrap.appendChild(groupHeader(group, pis, index));
  const grid = el("div", "fleet");
  grid.style.zoom = layout.size / 100;
  grid.replaceChildren(...pis.map((pi) => card(pi, pis)));
  if (arranging) {
    grid.classList.add("drop-zone");
    if (!pis.length) grid.appendChild(el("p", "drop-note", "Drop a card here"));
    wrap.addEventListener("dragover", (event) => event.preventDefault());
    wrap.addEventListener("drop", (event) => {
      event.preventDefault();
      if (!dragging) return;
      setGroup(dragging, group ? group.id : "");
      dragging = null;
      commit();
    });
  }
  wrap.appendChild(grid);
  return wrap;
}

function render() {
  const pis = sortedPis();
  const known = new Set(layout.groups.map((group) => group.id));
  const sections = [];
  layout.groups.forEach((group, index) => {
    const members = pis.filter((pi) => layout.members[pi.id] === group.id);
    if (members.length || arranging) sections.push(section(group, members, index));
  });
  const loose = pis.filter((pi) => !known.has(layout.members[pi.id]));
  if (loose.length || (arranging && layout.groups.length)) sections.push(section(null, loose, -1));
  document.getElementById("fleet").replaceChildren(...sections);
}

function headline(pis) {
  const fine = pis.filter((pi) => pi.health.status === "ok").length;
  const trouble = pis.filter((pi) => pi.health.status !== "ok");
  if (!trouble.length) {
    return pis.length === 1 ? `${pis[0].hostname} is doing fine.` : `All ${pis.length} are doing fine.`;
  }
  const rank = { offline: 0, bad: 1, warn: 2 };
  const names = [...trouble].sort((a, b) => rank[a.health.status] - rank[b.health.status]).map((pi) => pi.hostname);
  const named = names.length <= 2 ? names.join(" and ") : `${names[0]} and ${names.length - 1} others`;
  const verb = names.length === 1 ? "needs" : "need";
  return `${fine} of ${pis.length} doing fine. ${named} ${verb} a look.`;
}

function busyEditing() {
  const active = document.activeElement;
  return dragging || (active && active.closest("#fleet") && ["INPUT", "SELECT"].includes(active.tagName));
}

async function refresh() {
  const connection = document.getElementById("connection");
  try {
    const response = await fetch("/api/fleet", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const { pis } = await response.json();
    latestPis = pis;
    connection.hidden = true;
    document.getElementById("empty").hidden = pis.length > 0;
    document.getElementById("summary").textContent = pis.length ? headline(pis) : "Your fleet is empty.";
    document.title = pis.some((pi) => pi.health.status !== "ok") ? "⚠ Mindful Fleet" : "Mindful Fleet";
    if (!busyEditing()) render();
  } catch (error) {
    connection.textContent = "Can't reach the hub. These numbers may be out of date. Retrying every 5 seconds.";
    connection.hidden = false;
  }
}

function setUpRowPicker() {
  const list = document.getElementById("row-list");
  for (const [key, label] of ROWS) {
    const item = el("label", "row-choice");
    const box = el("input");
    box.type = "checkbox";
    box.checked = shown(key);
    box.addEventListener("change", () => {
      layout.hidden = layout.hidden.filter((hidden) => hidden !== key);
      if (!box.checked) layout.hidden.push(key);
      commit();
    });
    item.append(box, document.createTextNode(` ${label}`));
    list.appendChild(item);
  }
}

function setUpTools() {
  setUpRowPicker();
  const size = document.getElementById("size");
  size.value = layout.size;
  size.addEventListener("input", () => {
    layout.size = Number(size.value);
    saveLayout();
    render();
  });

  const toggle = document.getElementById("arrange");
  toggle.addEventListener("click", () => {
    arranging = !arranging;
    toggle.setAttribute("aria-pressed", String(arranging));
    toggle.textContent = arranging ? "Done arranging" : "Arrange cards";
    document.getElementById("new-group").hidden = !arranging;
    document.getElementById("arrange-help").hidden = !arranging;
    render();
  });

  const name = document.getElementById("group-name");
  document.getElementById("new-group").addEventListener("submit", (event) => {
    event.preventDefault();
    const value = name.value.trim();
    if (!value) return;
    name.value = "";
    addGroup(value);
  });

  document.getElementById("reset").addEventListener("click", () => {
    layout.order = [];
    layout.groups = [];
    layout.members = {};
    commit();
  });
}

setUpTools();
refresh();
setInterval(refresh, REFRESH_MS);
