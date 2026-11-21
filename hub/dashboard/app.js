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
  ["mining", "Mining"], ["watts", "Power use"], ["cpu", "CPU"], ["clock", "Clock speed"], ["load", "Load"], ["cores", "Each core"],
  ["top", "Busiest program"], ["memory", "Memory"], ["swap", "Swap"], ["storage", "Storage"],
  ["diskio", "Storage speed"], ["network", "Network"], ["wifi", "Wi-Fi"], ["sensor", "Power sensors"],
  ["history", "24-hour chart"], ["averages", "Averages"],
  ["uptime", "Running for"], ["system", "System"], ["kernel", "Kernel memory"], ["connect", "Connect (SSH)"],
];
const WINDOWS = [["5m", "5m", "5 minutes"], ["15m", "15m", "15 minutes"], ["30m", "30m", "30 minutes"], ["1h", "1h", "1 hour"], ["24h", "24h", "24 hours"]];
const SHARED_KEYS = ["order", "groups", "members", "hidden", "price", "region"];
let latestVersion = null;
let layoutSaveTimer = null;

const LOOKS = [["tall", "Tall"], ["wide", "Wide"], ["gauges", "Gauges"], ["side", "Side by side"], ["table", "One row each"]];
const POWER_SCALE_WATTS = 12; // About the most a busy Pi 5 board draws, so the dial has room.
const PER_WATT_SCALE = 120; // Hashes per watt. A well-tuned Pi 5 lands near 90, so the dial has room.
const expanded = new Set(); // Cards opened one by one, in this browser only.
const collapsed = new Set(); // Cards closed one by one while "Expand all" is on.
const HISTORY_REFRESH_MS = 60000;
const HISTORY_METRICS = [
  ["temp", "Temperature", "°C", (v) => v.toFixed(1)],
  ["hashrate", "Hashrate", "H/s", (v) => Math.round(v).toLocaleString()],
  ["watts", "Power use", "W", (v) => v.toFixed(1)],
  ["cpu", "CPU", "%", (v) => Math.round(v)],
  ["fan", "Fan", "RPM", (v) => Math.round(v).toLocaleString()],
  ["memory", "Memory", "%", (v) => Math.round(v)],
  ["clock", "Clock", "GHz", (v) => (v / 1000).toFixed(2)],
  ["sensor_watts", "Sensor", "W", (v) => v.toFixed(1)],
];
const historyCache = {}; // Per computer: { data, fetched, loading }.
const chartChoice = {}; // Per computer: which reading its chart shows.
const FULL_HEIGHT = 1250; // The slider's top end means "fit all", with no scrolling.

const layout = loadLayout();
// Region and units (region.js): readings arrive as °C, bytes and world time; only what this screen shows changes.
let fmt = window.MF_REGION.make(layout.region, navigator.language);
let regionNote = "";
let arranging = false;
let dragging = null;
let latestPis = [];
let plan = { name: "free", plus: false, pro: false }; // From the hub: which paid features it may use.

function loadLayout() {
  const empty = { palette: "lavender", mode: "auto", expandAll: false, look: "tall", controls: 100, settingsOpen: true, size: 100, width: 340, height: FULL_HEIGHT, order: [], groups: [], members: {}, hidden: [], price: null, region: null };
  try {
    return { ...empty, ...JSON.parse(localStorage.getItem(LAYOUT_KEY)) };
  } catch {
    return empty;
  }
}

function shown(key) {
  return !layout.hidden.includes(key);
}

function saveLayout() {
  try {
    localStorage.setItem(LAYOUT_KEY, JSON.stringify(layout));
  } catch {
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
  return fmt.size(value);
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
  return fmt.speed(bytesPerSecond);
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

function versionBelow(version, target) {
  if (!version || !target) return false;
  const a = version.split(".").map(Number);
  const b = target.split(".").map(Number);
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    if ((a[i] || 0) !== (b[i] || 0)) return (a[i] || 0) < (b[i] || 0);
  }
  return false;
}

function hashes(value) {
  if (value == null) return "—";
  if (value >= 1e6) return `${(value / 1e6).toFixed(2)} MH/s`;
  if (value >= 1e4) return `${(value / 1e3).toFixed(1)} kH/s`;
  return `${Math.round(value).toLocaleString()} H/s`;
}

function energy(watts) {
  const kwhPerDay = watts * 24 / 1000;
  const perDay = kwhPerDay * (layout.price || 0);
  const cost = layout.price ? `, ${fmt.money(perDay, perDay < 1 ? 3 : 2)} a day` : "";
  return `about ${kwhPerDay.toFixed(2)} kWh a day${cost}`;
}

function wifiWords(dbm) {
  if (dbm >= -55) return "Excellent";
  if (dbm >= -67) return "Good";
  if (dbm >= -75) return "Fair";
  return "Weak";
}

const AVERAGE_ROWS = [
  ["cpu", "CPU %", (v) => Math.round(v)],
  ["clock", "Clock GHz", (v) => (v / 1000).toFixed(2)],
  ["load", "Load", (v) => v.toFixed(2)],
  ["temp", "Temp °C", (v) => Math.round(v)],
  ["slowed", "Slowed %", (v) => Math.round(v)],
  ["watts", "Power W", (v) => v.toFixed(1)],
  ["hashrate", "Hashrate H/s", (v) => (v >= 10000 ? `${(v / 1000).toFixed(1)}k` : Math.round(v))],
  ["sensor_watts", "Sensor W", (v) => v.toFixed(1)],
  ["fan", "Fan RPM", (v) => (v >= 1000 ? `${(v / 1000).toFixed(1)}k` : Math.round(v))],
  ["memory", "Memory %", (v) => Math.round(v)],
  ["volts", "Input V", (v) => v.toFixed(2)],
  ["net_in", "Net in KB/s", (v) => Math.round(v / 1024)],
  ["net_out", "Net out KB/s", (v) => Math.round(v / 1024)],
  ["disk_read", "Read KB/s", (v) => Math.round(v / 1024)],
  ["disk_write", "Write KB/s", (v) => Math.round(v / 1024)],
  ["wifi", "Wi-Fi dBm", (v) => Math.round(v)],
];

function averagesTable(pi) {
  const data = pi.averages && pi.averages.windows;
  if (!data || !data["5m"]) return null;
  const cores = pi.cpu_per_core.length;
  const now = {
    watts: pi.board_watts, hashrate: pi.hashrate,
    sensor_watts: pi.sensors && pi.sensors.length ? pi.sensors.reduce((sum, x) => sum + (x.watts || 0), 0) : null, slowed: pi.throttled == null ? null : (pi.throttled & 0b1110 ? 100 : 0),
    cpu: pi.health.cpu.average, clock: average(pi.cpu_mhz), load: pi.load_avg[0], temp: pi.temp_c,
    fan: pi.fan_rpm, memory: pi.health.memory.percent, volts: pi.input_volts, net_in: pi.net_rx_bps,
    net_out: pi.net_tx_bps, disk_read: pi.disk_read_bps, disk_write: pi.disk_write_bps, wifi: pi.wifi_dbm,
  };
  const rows = [];
  for (const [key, label, format] of AVERAGE_ROWS) {
    rows.push([key, label, format]);
    if (key === "cpu") {
      for (let i = 0; i < cores; i++) {
        now[`core${i}`] = pi.cpu_per_core[i];
        rows.push([`core${i}`, `Core ${i + 1} %`, (v) => Math.round(v)]);
      }
    }
  }

  const wrap = el("div", "averages");
  const table = el("table");
  const caption = el("caption", "", "Averages");
  table.appendChild(caption);
  const head = el("tr");
  head.appendChild(el("th", "", ""));
  head.appendChild(el("th", "", "Now"));
  for (const [, label, long] of WINDOWS) {
    const th = el("th", "", label);
    th.title = `Average over the last ${long}`;
    head.appendChild(th);
  }
  const thead = el("thead");
  thead.appendChild(head);
  const tbody = el("tbody");
  for (const [key, label, format] of rows) {
    const values = [now[key], ...WINDOWS.map(([name]) => data[name] && data[name][key])];
    if (values.every((value) => value == null)) continue;
    const tr = el("tr");
    tr.appendChild(el("th", "", label));
    for (const value of values) tr.appendChild(el("td", "", value == null ? "—" : String(format(value))));
    tbody.appendChild(tr);
  }
  table.append(thead, tbody);
  wrap.appendChild(table);
  const minutes = pi.averages.minutes;
  if (minutes < 1440) {
    wrap.appendChild(el("p", "averages-note", `The hub has ${duration(minutes * 60)} of history so far, so longer averages cover only that.`));
  }
  return wrap;
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
    const avg = pi.averages && pi.averages.windows["30m"] ? pi.averages.windows["30m"][`core${i}`] : null;
    const mhz = pi.cpu_mhz[i];
    const cell = el("div", "core");
    cell.title = `Core ${i + 1}: ${Math.round(value)}% now, ${avg == null ? "no" : `${Math.round(avg)}%`} average over 30 minutes`;
    const column = el("div", "core-bar");
    const level = el("i");
    level.style.height = `${value}%`;
    column.appendChild(level);
    cell.append(el("small", "", `Core ${i + 1}`), column, el("strong", "", `${Math.round(value)}%`));
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

function cardShell(pi, extraClass = "") {
  const article = el("article", `pi ${pi.health.status} ${extraClass}`.trim());
  article.dataset.id = pi.id;
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
  return article;
}

function cardHead(pi) {
  const h = pi.health;
  const head = el("div", "pi-head");
  const title = el("div", "pi-title");
  title.append(el("h2", "", pi.hostname), el("p", "pi-mood", MOODS[h.status]));
  const board = [shortModel(pi.model), boardMemory(pi)].filter(Boolean).join(", ");
  if (board) title.appendChild(el("p", "pi-board", board));
  if (pi.ip) title.appendChild(el("p", "pi-where", pi.ip));
  head.append(mascot(h.status), title);
  return head;
}

function issuesList(pi) {
  if (!pi.health.issues.length) return null;
  const list = el("ul", "issues");
  for (const issue of pi.health.issues) list.appendChild(el("li", issue.level, fmt.words(issue.text)));
  return list;
}

/* Every reading as label and value rows, then the cores and the averages table. */
function readings(pi) {
  const h = pi.health;
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
    const advice = h.power.level === "ok" ? "" : powerAdvice(pi.model);
    row("power", "Input voltage", meter(position, "", `${h.power.volts.toFixed(2)} V`,
      `${Math.round(h.power.volts / NOMINAL_VOLTS * 100)}% of the 5 V a Pi expects${advice ? `. ${advice}` : ""}`));
  }
  if (pi.core_volts != null) row("power", "Core voltage", el("dd", "", `${pi.core_volts.toFixed(3)} V`));
  if (pi.hashrate != null) {
    const best = Math.max(pi.hashrate_max || 0, pi.hashrate);
    const parts = [];
    if (pi.hashrate_15m != null) parts.push(`15-min ${hashes(pi.hashrate_15m)}`);
    if (pi.mining_threads != null) parts.push(`${pi.mining_threads} of ${cores || "?"} cores mining`);
    const mining = meter(best ? pi.hashrate / best * 100 : null, "", hashes(pi.hashrate), `of ${hashes(best)} best 15-min`, "Best 15-minute average seen since the hub started tracking");
    for (const part of parts) mining.appendChild(el("small", "detail", part));
    if (pi.board_watts) mining.appendChild(el("small", "detail", `${(pi.hashrate / pi.board_watts).toFixed(1)} H/s per watt`));
    if (pi.shares_total) {
      const level = pi.shares_good / pi.shares_total < 0.98 ? "detail warn-text" : "detail";
      mining.appendChild(el("small", level, `${pi.shares_good.toLocaleString()} of ${pi.shares_total.toLocaleString()} shares accepted`));
    }
    row("mining", "Mining", mining);
    if (pi.pool) {
      const pool = text({ level: "", text: pi.pool }, [pi.algo, pi.miner_version && `XMRig ${pi.miner_version}`].filter(Boolean).join(", "));
      if (pi.pool_tls) {
        const secure = pi.pool_tls !== "none" || pi.pool.endsWith(".onion") || /\.onion:/.test(pi.pool);
        pool.appendChild(el("small", secure ? "detail" : "detail warn-text",
          pi.pool_tls !== "none" ? `Encrypted (${pi.pool_tls})` : secure ? "Through Tor" : "Not encrypted"));
      }
      if (pi.rig_id) {
        pool.appendChild(el("small", "detail", `Rig ID ${pi.rig_id}`));
        const twins = latestPis.filter((other) => other.id !== pi.id && other.rig_id === pi.rig_id && other.health.online);
        if (twins.length) {
          pool.appendChild(el("small", "detail warn-text", `Same rig ID as ${twins.map((t) => t.hostname).join(", ")}, so the pool can't tell them apart`));
        }
      }
      row("mining", "Pool", pool);
    }
  }
  if (pi.board_watts != null) {
    row("watts", "Power use", text({ level: "", text: `${pi.board_watts.toFixed(1)} W` }, energy(pi.board_watts)));
  }
  if (cores) {
    row("cpu", "CPU", meter(h.cpu.average, "", `${Math.round(h.cpu.average)}%`, `${h.cpu.busy} of ${cores} cores in use`));
  }
  const mhz = average(pi.cpu_mhz);
  if (mhz != null && pi.cpu_max_mhz) {
    row("clock", "Clock speed", meter(mhz / pi.cpu_max_mhz * 100, "", ghz(mhz), `of ${ghz(pi.cpu_max_mhz)} max`, "Average across cores"));
  }
  if (pi.top_process) {
    const used = pi.top_process_cpu / 100;
    row("top", "Busiest program", text({ level: "", text: pi.top_process }, cores ? `using ${used.toFixed(2)} of ${cores} cores` : `${pi.top_process_cpu}% of one core`));
  }
  if (pi.load_avg.length && cores) {
    const one = pi.load_avg[0];
    row("load", "Load", meter(one / cores * 100, "", one.toFixed(2), `of ${cores} cores`, "How many tasks are running or waiting. Equal to the core count means fully busy"));
  }
  row("memory", "Memory", meter(h.memory.percent, h.memory.level, `${Math.round(h.memory.percent)}%`, `${bytes(pi.mem_used)} of ${bytes(pi.mem_total)} usable`));
  if (pi.swap_total) {
    row("swap", "Swap", meter(pi.swap_used / pi.swap_total * 100, "", `${Math.round(pi.swap_used / pi.swap_total * 100)}%`, `${bytes(pi.swap_used)} of ${bytes(pi.swap_total)}`, "Disk space used as overflow memory"));
  }
  row("storage", "Storage", meter(h.disk.percent, h.disk.level, `${Math.round(h.disk.percent)}%`, `${bytes(pi.disk_used)} of ${bytes(pi.disk_total)}`));
  if (pi.disk_read_bps != null) {
    row("diskio", "Storage speed", text({ level: "", text: `↓ ${rate(pi.disk_read_bps)}  ↑ ${rate(pi.disk_write_bps)}` }, "read and write"));
  }
  if (pi.wifi_dbm != null) {
    const strength = Math.max(0, Math.min(100, (pi.wifi_dbm + 90) / 60 * 100));
    row("wifi", "Wi-Fi", meter(strength, pi.wifi_dbm < -75 ? "warn" : "", `${pi.wifi_dbm} dBm`, `${wifiWords(pi.wifi_dbm)} signal`, "Closer to 0 dBm is stronger. -90 dBm is barely usable"));
  }
  for (const sensor of pi.sensors || []) {
    const parts = [sensor.volts != null && `${sensor.volts.toFixed(2)} V`, sensor.amps != null && `${sensor.amps.toFixed(2)} A`].filter(Boolean);
    row("sensor", sensor.label, text({ level: "", text: sensor.watts != null ? `${sensor.watts.toFixed(1)} W` : parts.join(", ") || "No reading" },
      [sensor.watts != null && parts.join(", "), sensor.chip && `${sensor.chip.toUpperCase()} sensor`].filter(Boolean).join(", ")));
  }
  if (pi.net_rx_bps != null) {
    row("network", "Network", text({ level: "", text: `↓ ${rate(pi.net_rx_bps)}  ↑ ${rate(pi.net_tx_bps)}` }, "in and out"));
  }
  row("uptime", "Running for", el("dd", "", duration(pi.uptime_s)));
  if (pi.os_name || pi.kernel) {
    const system = text({ level: "", text: pi.os_name || "Unknown" }, pi.kernel ? `kernel ${pi.kernel}` : "");
    if (pi.reporter_version) system.appendChild(el("small", "detail", `MindfulFleet ${pi.reporter_version}`));
    if (versionBelow(pi.reporter_version, latestVersion)) {
      system.appendChild(el("small", "detail update", `Update available: ${latestVersion}`));
    }
    row("system", "System", system);
  }
  if (pi.page_kb != null || pi.huge_pages != null) {
    // Kernel check (#44): what the kernel offers XMRig, in plain words.
    const pages = pi.page_kb != null ? `${pi.page_kb} KB memory pages` : "Memory page size unknown";
    const huge = pi.huge_pages ? `${pi.huge_pages} huge pages reserved` : "no huge pages reserved";
    const detail = pi.huge_pages ? "XMRig can use them for a faster hashrate." : "XMRig runs without huge pages, which is normal on a stock Raspberry Pi kernel.";
    row("kernel", "Kernel memory", text({ level: "", text: `${pages}, ${huge}` },
      `${detail}${pi.thp ? ` Automatic huge pages: ${pi.thp}.` : ""}`));
  }
  if (pi.ip) row("connect", "Connect", sshBox(pi.ip));
  const parts = [facts];
  if (pi.cpu_per_core.length && shown("cores")) parts.push(coreGrid(pi));
  if (shown("history")) parts.push(historyChart(pi));
  if (shown("averages")) {
    const table = averagesTable(pi);
    if (table) parts.push(table);
  }
  return parts;

}

function isOpen(id) {
  return layout.expandAll ? !collapsed.has(id) : expanded.has(id);
}

function loadHistory(pi) {
  const cached = historyCache[pi.id];
  if (cached && (cached.loading || Date.now() - cached.fetched < HISTORY_REFRESH_MS)) return;
  historyCache[pi.id] = { ...(cached || {}), loading: true };
  fetch(`/api/history?id=${encodeURIComponent(pi.id)}`, { cache: "no-store" })
    .then((response) => (response.ok ? response.json() : null))
    .then((data) => {
      historyCache[pi.id] = { data, fetched: Date.now(), loading: false };
      if (!busyEditing()) render();
    })
    .catch(() => { historyCache[pi.id] = { ...(historyCache[pi.id] || {}), fetched: Date.now(), loading: false }; });
}

function clockTime(seconds) {
  return fmt.time(seconds);
}

/* One reading over the last 24 hours, as 5-minute averages from the hub. */
function historyChart(pi) {
  loadHistory(pi);
  const wrap = el("div", "history");
  const data = historyCache[pi.id] && historyCache[pi.id].data;
  const available = HISTORY_METRICS.filter(([key]) => data && data.values[key] && data.values[key].some((v) => v != null));
  let choice = chartChoice[pi.id] || "temp";
  if (available.length && !available.some(([key]) => key === choice)) choice = available[0][0];

  const head = el("div", "history-head");
  head.appendChild(el("strong", "", "Last 24 hours"));
  const select = el("select");
  select.setAttribute("aria-label", `Reading to chart for ${pi.hostname}`);
  for (const [key, label] of available) {
    const option = el("option", "", label);
    option.value = key;
    select.appendChild(option);
  }
  select.value = choice;
  select.addEventListener("change", () => {
    chartChoice[pi.id] = select.value;
    select.blur();
    render();
  });
  if (available.length) head.appendChild(select);
  wrap.appendChild(head);

  const [, label, unit, format] = HISTORY_METRICS.find(([key]) => key === choice);
  const values = data ? data.values[choice] || [] : [];
  const points = data ? data.times.map((t, i) => [t, values[i]]).filter(([, v]) => v != null) : [];
  if (points.length < 2) {
    wrap.appendChild(el("p", "history-empty", data ? "Not enough history yet. Check back in 10 minutes." : "Loading history…"));
    return wrap;
  }

  const W = 300;
  const H = 110;
  const pad = { left: 4, right: 4, top: 8, bottom: 8 };
  const times = data.times;
  const t0 = times[0];
  const t1 = Math.max(times[times.length - 1], t0 + 1);
  let low = Math.min(...points.map(([, v]) => v));
  let high = Math.max(...points.map(([, v]) => v));
  if (high - low < 1e-9) { high += 1; low -= 1; }
  const x = (t) => pad.left + (t - t0) / (t1 - t0) * (W - pad.left - pad.right);
  const y = (v) => H - pad.bottom - (v - low) / (high - low) * (H - pad.top - pad.bottom);

  const plot = el("div", "history-plot");
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none", role: "img",
    "aria-label": `${label} over the last 24 hours, from ${format(low)} to ${format(high)} ${unit}` });
  for (const v of [high, (high + low) / 2, low]) svg("line", { x1: 0, x2: W, y1: y(v), y2: y(v), class: "history-grid" }, root);
  // Gaps in reporting break the line rather than drawing across them.
  let run = [];
  const runs = [run];
  times.forEach((t, i) => {
    if (values[i] == null) { run = []; runs.push(run); } else run.push(`${x(t).toFixed(1)},${y(values[i]).toFixed(1)}`);
  });
  for (const r of runs) if (r.length > 1) svg("polyline", { points: r.join(" "), class: "history-line" }, root);
  const dot = svg("circle", { r: 3.5, class: "history-dot", visibility: "hidden" }, root);
  const cross = svg("line", { y1: 0, y2: H, class: "history-cross", visibility: "hidden" }, root);
  plot.appendChild(root);

  const axis = el("div", "history-y");
  axis.append(el("span", "", `${format(high)} ${unit}`), el("span", "", `${format(low)} ${unit}`));
  const tip = el("div", "history-tip");
  tip.hidden = true;
  plot.append(axis, tip);

  // Hovering shows the nearest 5-minute average and its time.
  root.addEventListener("pointermove", (event) => {
    const box = root.getBoundingClientRect();
    const t = t0 + (event.clientX - box.left) / box.width * (t1 - t0);
    let best = points[0];
    for (const point of points) if (Math.abs(point[0] - t) < Math.abs(best[0] - t)) best = point;
    const px = x(best[0]);
    dot.setAttribute("cx", px); dot.setAttribute("cy", y(best[1])); dot.setAttribute("visibility", "visible");
    cross.setAttribute("x1", px); cross.setAttribute("x2", px); cross.setAttribute("visibility", "visible");
    tip.textContent = `${format(best[1])} ${unit} at ${clockTime(best[0])}`;
    tip.style.left = `${Math.min(Math.max(px / W * 100, 15), 85)}%`;
    tip.hidden = false;
  });
  root.addEventListener("pointerleave", () => {
    dot.setAttribute("visibility", "hidden");
    cross.setAttribute("visibility", "hidden");
    tip.hidden = true;
  });
  wrap.appendChild(plot);
  const xAxis = el("div", "history-x");
  xAxis.append(el("span", "", clockTime(t0)), el("span", "", "now"));
  wrap.appendChild(xAxis);
  return wrap;
}

function detailsToggle(pi, container) {
  const open = isOpen(pi.id);
  const button = el("button", "details-toggle", open ? "Hide readings" : "All readings");
  button.type = "button";
  button.setAttribute("aria-expanded", String(open));
  button.addEventListener("click", () => {
    const set = layout.expandAll ? collapsed : expanded;
    if (set.has(pi.id)) set.delete(pi.id);
    else set.add(pi.id);
    render();
  });
  container.appendChild(button);
  if (open) {
    const more = el("div", "more");
    more.append(...readings(pi));
    container.appendChild(more);
  }
}

function tallCard(pi, section) {
  const h = pi.health;
  const article = cardShell(pi);
  if (layout.height < FULL_HEIGHT) article.style.maxHeight = `${layout.height}px`;
  const body = el("div", h.online ? "pi-body" : "pi-body stale");
  if (arranging) body.appendChild(arrangeBar(pi, section));
  const heat = el("div", "heat");
  const temp = h.temp.value == null ? "—" : `${Math.round(fmt.temp(h.temp.value))}${fmt.tempUnit}`;
  heat.append(el("div", `degrees ${h.temp.level}`, temp));
  if (shown("trend")) heat.appendChild(sparkline(pi.history));
  body.appendChild(heat);
  if (!h.online) body.appendChild(el("p", "heat-note", "Last known numbers"));
  const issues = issuesList(pi);
  if (issues) body.appendChild(issues);
  body.append(...readings(pi));
  article.append(...[historySwitch(pi), cardHead(pi), body].filter(Boolean));
  return article;
}

/* A round dial: the arc fills from left to right like a car gauge, with the number in the middle. */
function gauge({ label, value, unit, percent, level = "", limit }) {
  const wrap = el("figure", `gauge ${level}`.trim());
  const root = svg("svg", { viewBox: "0 0 100 86", role: "img", "aria-label": `${label}: ${value}${unit ? ` ${unit}` : ""}${limit ? `, ${limit}` : ""}` });
  const arc = "M 18 78 A 40 40 0 1 1 82 78";
  // A reading with no upper limit shows just the number.
  if (percent != null) svg("path", { d: arc, class: "gauge-track", pathLength: 100 }, root);
  const filled = Math.max(0, Math.min(100, percent ?? 0));
  if (percent != null && filled > 0) svg("path", { d: arc, class: "gauge-fill", pathLength: 100, "stroke-dasharray": `${filled} 100` }, root);
  const number = svg("text", { x: 50, y: 52, class: "gauge-value", "text-anchor": "middle" }, root);
  number.textContent = value;
  if (unit) {
    const units = svg("text", { x: 50, y: 66, class: "gauge-unit", "text-anchor": "middle" }, root);
    units.textContent = unit;
  }
  wrap.appendChild(root);
  wrap.appendChild(el("figcaption", "", label));
  if (limit) wrap.appendChild(el("small", "gauge-limit", limit));
  return wrap;
}

function compactHashes(value) {
  if (value >= 1e6) return [(value / 1e6).toFixed(2), "MH/s"];
  if (value >= 1e4) return [(value / 1e3).toFixed(1), "kH/s"];
  return [Math.round(value).toLocaleString(), "H/s"];
}

/* The headline readings, shared by the Gauges and Wide looks. Each has a value, a fill and its limit. */
function keyReadings(pi) {
  const h = pi.health;
  const list = [];
  const add = (key, item) => { if (shown(key)) list.push(item); };
  if (h.temp.value != null) {
    add("trend", { key: "temp", label: "Temperature", value: Math.round(fmt.temp(h.temp.value)), unit: fmt.tempUnit, percent: h.temp.value / 90 * 100, level: h.temp.level,
      limit: `warm at ${Math.round(fmt.temp(70))}, hot at ${Math.round(fmt.temp(80))}` });
  }
  if (pi.hashrate != null) {
    const best = Math.max(pi.hashrate_max || 0, pi.hashrate);
    const [value, unit] = compactHashes(pi.hashrate);
    add("mining", { key: "hashrate", label: "Hashrate", value, unit, percent: best ? pi.hashrate / best * 100 : 0, limit: `best ${hashes(best)}` });
    if (pi.board_watts) {
      const perWatt = pi.hashrate / pi.board_watts;
      add("mining", { key: "hpw", label: "Per watt", value: perWatt.toFixed(1), unit: "H/s/W", percent: perWatt / PER_WATT_SCALE * 100, limit: `of ${PER_WATT_SCALE} H/s/W scale` });
    }
  }
  if (pi.board_watts != null) {
    add("watts", { key: "watts", label: "Power use", value: pi.board_watts.toFixed(1), unit: "W", percent: pi.board_watts / POWER_SCALE_WATTS * 100, limit: `of ${POWER_SCALE_WATTS} W scale` });
  }
  if (h.cpu.total) {
    add("cpu", { key: "cpu", label: "CPU", value: Math.round(h.cpu.average), unit: "%", percent: h.cpu.average, limit: `${h.cpu.busy} of ${h.cpu.total} cores in use` });
  }
  const mhz = average(pi.cpu_mhz);
  if (mhz != null && pi.cpu_max_mhz) {
    add("clock", { key: "clock", label: "Clock", value: (mhz / 1000).toFixed(2), unit: "GHz", percent: mhz / pi.cpu_max_mhz * 100, limit: `of ${ghz(pi.cpu_max_mhz)}` });
  }
  if (pi.fan_rpm != null && (pi.fan_rpm > 0 || pi.fan_max_rpm)) {
    const percent = h.fan.percent ?? (pi.fan_max_rpm ? pi.fan_rpm / pi.fan_max_rpm * 100 : 0);
    add("fan", { key: "fan", label: "Fan", value: pi.fan_rpm >= 1000 ? (pi.fan_rpm / 1000).toFixed(1) : pi.fan_rpm, unit: pi.fan_rpm >= 1000 ? "k RPM" : "RPM", percent, level: h.fan.level, limit: h.fan.percent != null ? `${h.fan.percent}% effort` : h.fan.text });
  }
  if (h.memory.percent != null) {
    add("memory", { key: "memory", label: "Memory", value: Math.round(h.memory.percent), unit: "%", percent: h.memory.percent, level: h.memory.level, limit: `of ${bytes(pi.mem_total)}` });
  }
  if (h.disk.percent != null) {
    add("storage", { key: "storage", label: "Storage", value: Math.round(h.disk.percent), unit: "%", percent: h.disk.percent, level: h.disk.level, limit: `of ${bytes(pi.disk_total)}` });
  }
  if (h.power.volts != null) {
    add("power", { key: "volts", label: "Input", value: h.power.volts.toFixed(2), unit: "V", percent: (h.power.volts - LOW_VOLTS) / (HIGH_VOLTS - LOW_VOLTS) * 100, level: h.power.level, limit: h.power.text });
  }
  return list;
}

function gaugeCard(pi, section) {
  const h = pi.health;
  const article = cardShell(pi, "gauges-card");
  if (layout.height < FULL_HEIGHT) article.style.maxHeight = `${layout.height}px`;
  const body = el("div", h.online ? "pi-body" : "pi-body stale");
  if (arranging) body.appendChild(arrangeBar(pi, section));
  if (!h.online) body.appendChild(el("p", "heat-note", "Last known numbers"));
  const issues = issuesList(pi);
  if (issues) body.appendChild(issues);
  const dials = el("div", "gauges");
  for (const item of keyReadings(pi)) dials.appendChild(gauge(item));
  body.appendChild(dials);
  detailsToggle(pi, body);
  article.append(...[historySwitch(pi), cardHead(pi), body].filter(Boolean));
  return article;
}

function wideRow(pi, section) {
  const h = pi.health;
  const article = cardShell(pi, "wide");
  const who = el("div", "wide-who");
  who.appendChild(mascot(h.status));
  const title = el("div", "pi-title");
  title.append(el("h2", "", pi.hostname), el("p", "pi-mood", MOODS[h.status]));
  const board = [shortModel(pi.model), boardMemory(pi)].filter(Boolean).join(", ");
  if (board) title.appendChild(el("p", "pi-board", board));
  if (pi.ip) title.appendChild(el("p", "pi-where", pi.ip));
  who.appendChild(title);

  const cells = el("div", "wide-cells");
  for (const item of keyReadings(pi)) {
    const cell = el("div", `cell ${item.level || ""}`.trim());
    cell.appendChild(el("span", "cell-label", item.label));
    const value = el("strong", "", `${item.value}`);
    if (item.unit) value.appendChild(el("small", "", item.unit === "%" || item.unit === "°C" || item.unit === "°F" ? item.unit : ` ${item.unit}`));
    cell.appendChild(value);
    if (item.percent != null) {
      const bar = el("div", "bar");
      const fill = el("div", `fill ${item.level || ""}`);
      fill.style.width = `${Math.max(0, Math.min(100, item.percent))}%`;
      bar.appendChild(fill);
      cell.appendChild(bar);
    }
    if (item.limit) cell.appendChild(el("small", "cell-limit", item.limit));
    cells.appendChild(cell);
  }
  if (shown("uptime")) {
    const up = el("div", "cell cell-text");
    up.append(el("span", "cell-label", "Running for"), el("strong", "", duration(pi.uptime_s)));
    cells.appendChild(up);
  }

  const body = el("div", h.online ? "wide-body" : "wide-body stale");
  if (arranging) body.appendChild(arrangeBar(pi, section));
  const top = el("div", "wide-top");
  top.append(who, cells);
  const switcher = historySwitch(pi);
  if (switcher) body.appendChild(switcher);
  body.appendChild(top);
  const issues = issuesList(pi);
  if (issues) body.appendChild(issues);
  if (!h.online) body.appendChild(el("p", "heat-note", "Last known numbers"));
  detailsToggle(pi, body);
  article.appendChild(body);
  return article;
}

function card(pi, section) {
  if (layout.look === "wide") return wideRow(pi, section);
  if (layout.look === "gauges") return gaugeCard(pi, section);
  return tallCard(pi, section);
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
  shareLayout();
  render();
}

function shareLayout() {
  clearTimeout(layoutSaveTimer);
  layoutSaveTimer = setTimeout(() => {
    const shared = Object.fromEntries(SHARED_KEYS.map((key) => [key, layout[key]]));
    fetch("/api/layout", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(shared) })
      .then((response) => {
        if (response.status === 401) signedOut(); // This browser keeps its own copy until you sign in again.
      })
      .catch(() => {}); // An older hub can't store layouts. This browser still keeps its own copy.
  }, 600);
}

async function loadSharedLayout() {
  try {
    const response = await fetch("/api/layout", { cache: "no-store" });
    if (!response.ok) return;
    const shared = await response.json();
    // A hub with no saved layout yet answers {}: that's a brand-new hub, so Region and units can still be matched below.
    if (!shared || typeof shared !== "object" || Array.isArray(shared)) return;
    for (const key of SHARED_KEYS) if (shared[key]) layout[key] = shared[key];
    if (!shared.region && !layout.region) {
      // A brand-new hub: start from this browser's country (D-090). An upgraded hub already keeps °C (settle_region).
      layout.region = window.MF_REGION.guess(navigator.language);
      const made = window.MF_REGION.make(layout.region, navigator.language);
      regionNote = `Units matched to this browser's region: ${made.tempUnit}, ${layout.region.currency}. Change them in Settings → Region and units.`;
      shareLayout();
    }
    saveLayout();
  } catch {
    // Keep this browser's own layout.
  }
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
  grid.style.fontSize = `${layout.size}%`;
  if (layout.look === "wide") grid.classList.add("rows");
  grid.style.setProperty("--card-width", `${layout.width}px`);
  if ((layout.look === "side" || layout.look === "table") && !arranging) grid.replaceChildren(compareTable(pis));
  else grid.replaceChildren(...pis.map((pi) => card(pi, pis)));
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

function showFleetPower() {
  const online = latestPis.filter((pi) => pi.health.online);
  const powered = online.filter((pi) => pi.board_watts != null);
  const miners = online.filter((pi) => pi.hashrate != null);
  const line = document.getElementById("fleet-power");
  const parts = [];
  if (miners.length) {
    const rate = miners.reduce((sum, pi) => sum + pi.hashrate, 0);
    const measured = miners.filter((pi) => pi.board_watts);
    const ratio = measured.length
      ? `, ${(measured.reduce((s, pi) => s + pi.hashrate, 0) / measured.reduce((s, pi) => s + pi.board_watts, 0)).toFixed(1)} H/s per watt`
      : "";
    parts.push(`Fleet hashrate: ${hashes(rate)} from ${miners.length} miner${miners.length === 1 ? "" : "s"}${ratio}.`);
  }
  if (powered.length) {
    const total = powered.reduce((sum, pi) => sum + pi.board_watts, 0);
    parts.push(`Fleet power: ${total.toFixed(1)} W across ${powered.length} computer${powered.length === 1 ? "" : "s"}, ${energy(total)}. Measured by each board, not counting fans or USB devices.`);
  }
  const sensed = online.flatMap((pi) => pi.sensors || []).filter((s) => s.watts != null);
  if (sensed.length) {
    const extra = sensed.reduce((sum, s) => sum + s.watts, 0);
    parts.push(`Power sensors: ${extra.toFixed(1)} W more from ${sensed.length} sensor${sensed.length === 1 ? "" : "s"}.`);
  }
  const cooling = coolingAdvice(online);
  if (cooling) parts.push(cooling);
  line.hidden = !parts.length;
  line.textContent = parts.join(" ");
}

/* Power-supply advice for low voltage (#46): the official supply for the board, and a short cable. */
function powerAdvice(model) {
  if (/Pi 5/.test(model || "")) return "A Pi 5 wants the official 27 W supply (5.1 V, 5 A) and a short, thick cable";
  if (/Pi 4/.test(model || "")) return "A Pi 4 wants the official 15 W supply (5.1 V, 3 A) and a short, thick cable";
  return "Try the board's official power supply and a short, thick cable";
}

/* Cooling advisor (#45): compares the warmest and coolest computers and says what usually helps. */
function coolingAdvice(online) {
  const warm = online.filter((pi) => pi.temp_c != null);
  if (warm.length < 2) return "";
  const hot = warm.reduce((a, b) => (b.temp_c > a.temp_c ? b : a));
  const cool = warm.reduce((a, b) => (b.temp_c < a.temp_c ? b : a));
  const gap = hot.temp_c - cool.temp_c;
  if (gap < 10) return "";
  const words = `Cooling: ${hot.hostname} runs ${Math.round(fmt.tempChange(gap))}${fmt.tempUnit} warmer than ${cool.hostname}`;
  if (!hot.fan_rpm && cool.fan_rpm) return `${words}. ${cool.hostname} has a working fan and ${hot.hostname} doesn't: a fan or a bigger cooler usually closes most of that gap.`;
  if (hot.health.fan.percent != null && hot.health.fan.percent >= 90) return `${words}, with its fan already at full effort. Check the airflow around it, its spot in the rack, or a bigger cooler.`;
  return `${words}. Check its cooler, its fan and the airflow around it.`;
}

/* "Connect" row (#65 part 1): the SSH command for this computer, ready to copy. */
function sshBox(ip) {
  const command = `ssh ${ip}`;
  const box = el("dd", "connect");
  const code = el("code", "", command);
  const copy = button("Copy", `Copy ${command}`, () => {
    const done = () => { copy.textContent = "Copied"; setTimeout(() => { copy.textContent = "Copy"; }, 2000); };
    const select = () => {
      // Plain http pages can't use the clipboard, so select the command for Ctrl+C instead.
      const range = document.createRange();
      range.selectNodeContents(code);
      window.getSelection().removeAllRanges();
      window.getSelection().addRange(range);
      copy.textContent = "Press Ctrl+C";
    };
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(command).then(done, select);
    else select();
  });
  box.append(code, copy, el("small", "detail", "Uses this computer's sign-in name. If the Pi's is different, type it and @ before the address."));
  return box;
}

/* Side by side (#61): one column per computer and one row per reading, or one row per computer ("One row each"). */
function compareTable(pis) {
  const lists = pis.map((pi) => [pi, new Map(keyReadings(pi).map((item) => [item.key, item]))]);
  const readingsSeen = [];
  for (const [, items] of lists) {
    for (const [key, item] of items) if (!readingsSeen.some(([k]) => k === key)) readingsSeen.push([key, item.label]);
  }
  const value = (item) => {
    if (!item) return el("td", "", "—");
    const unit = ["%", "°C", "°F"].includes(item.unit) ? item.unit : ` ${item.unit}`;
    return el("td", item.level || "", `${item.value}${unit}`);
  };
  const status = (pi) => el("td", pi.health.status, MOODS[pi.health.status]);
  const table = el("table", "side-table");
  const head = el("tr");
  if (layout.look === "side") {
    head.appendChild(el("th", "", ""));
    for (const [pi] of lists) head.appendChild(el("th", "", pi.hostname));
    table.appendChild(head);
    const statusRow = el("tr");
    statusRow.appendChild(el("th", "", "How it's doing"));
    for (const [pi] of lists) statusRow.appendChild(status(pi));
    table.appendChild(statusRow);
    for (const [key, label] of readingsSeen) {
      const row = el("tr");
      row.appendChild(el("th", "", label));
      for (const [, items] of lists) row.appendChild(value(items.get(key)));
      table.appendChild(row);
    }
  } else {
    for (const words of ["Computer", "How it's doing", ...readingsSeen.map(([, label]) => label)]) head.appendChild(el("th", "", words));
    table.appendChild(head);
    for (const [pi, items] of lists) {
      const row = el("tr");
      row.append(el("th", "", pi.hostname), status(pi));
      for (const [key] of readingsSeen) row.appendChild(value(items.get(key)));
      table.appendChild(row);
    }
  }
  const wrap = el("div", "side-wrap");
  wrap.appendChild(table);
  return wrap;
}

function render() {
  showFleetPower();
  const pis = sortedPis();
  const known = new Set(layout.groups.map((group) => group.id));
  const sections = [];
  layout.groups.forEach((group, index) => {
    const members = pis.filter((pi) => layout.members[pi.id] === group.id);
    if (members.length || arranging) sections.push(section(group, members, index));
  });
  const loose = pis.filter((pi) => !known.has(layout.members[pi.id]));
  if (loose.length || (arranging && layout.groups.length)) sections.push(section(null, loose, -1));
  // Rebuilding the cards every few seconds would jump each one back to the top, so keep where people scrolled.
  const scrolled = {};
  for (const body of document.querySelectorAll(".pi[data-id] .pi-body")) {
    if (body.scrollTop) scrolled[body.parentElement.dataset.id] = body.scrollTop;
  }
  document.getElementById("fleet").replaceChildren(...sections);
  for (const [id, top] of Object.entries(scrolled)) {
    const body = document.querySelector(`.pi[data-id="${id}"] .pi-body`);
    if (body) body.scrollTop = top;
  }
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
    if (response.status === 401) {
      signedOut();
      return;
    }
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const { pis, version: hubVersion, latest_version: latest, password_set: passwordSet, plan: planNow, history: info } = await response.json();
    showVersion(hubVersion, latest);
    setUpAccount(!!passwordSet);
    if (planNow && planNow.plus !== plan.plus) {
      plan = planNow;
      for (const option of document.querySelectorAll("#palette option")) {
        if (option.dataset.name) option.textContent = option.dataset.plus && !plan.plus ? `${option.dataset.name} (Plus)` : option.dataset.name;
      }
      applyColors();
      if (view.open) void loadHistoryView();
    } else if (planNow) {
      plan = planNow;
    }
    historyInfo = info || null;
    latestPis = pis;
    latestVersion = latest;
    connection.hidden = true;
    document.getElementById("empty").hidden = pis.length > 0;
    document.getElementById("summary").textContent = pis.length ? headline(pis) : "Your fleet is empty.";

    document.title = pis.some((pi) => pi.health.status !== "ok") ? "⚠ MindfulFleet" : "MindfulFleet";
    if (!busyEditing()) render();
  } catch {
    connection.textContent = "Can't reach the hub. These numbers may be out of date. Retrying every 5 seconds.";
    connection.hidden = false;
  }
}

function stepButton(input, direction, title) {
  const node = el("button", "step", direction < 0 ? "−" : "+");
  node.type = "button";
  node.title = title;
  node.setAttribute("aria-label", title);
  let repeat = null;
  let held = 0;
  const step = (size = 1) => {
    const before = input.value;
    if (direction < 0) input.stepDown(size);
    else input.stepUp(size);
    if (input.value !== before) input.dispatchEvent(new Event("input"));
  };
  // The longer it's held, the faster it goes: single steps first, then 5 and 20 at a time.
  const keepGoing = () => {
    held += 1;
    step(held > 40 ? 20 : held > 15 ? 5 : 1);
  };
  const stop = () => {
    clearTimeout(repeat);
    clearInterval(repeat);
    repeat = null;
  };
  // Holding the button keeps it moving, after a short pause so a single click stays a single step.
  node.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    held = 0;
    step();
    repeat = setTimeout(() => { repeat = setInterval(keepGoing, 70); }, 400);
  });
  for (const type of ["pointerup", "pointerleave", "pointercancel"]) node.addEventListener(type, stop);
  node.addEventListener("click", (event) => {
    if (event.detail === 0) step(); // Keyboard Enter or Space.
  });
  return node;
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

// Color palettes (palettes.js). Three are free; the rest are MindfulFleet Plus. Until Plus can be bought, a Plus
// palette is a preview for this visit only and isn't saved. Each browser keeps its own choice, like text size.
const PALETTE_DATA = window.MF_PALETTES || { status: { light: {}, dark: {} }, palettes: {} };
const PALETTES = PALETTE_DATA.palettes;
const MODES = [["auto", "Match my device"], ["light", "Light"], ["dark", "Dark"]];
let previewPalette = null;
const darkQuery = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

// The palette to show: a preview, else the saved choice if it can be used, else Lavender. The saved choice
// itself is never changed here, so a Plus palette comes back once Plus is active again.
function shownPalette() {
  if (previewPalette) return previewPalette;
  return usable(layout.palette) ? layout.palette : "lavender";
}

function usable(key) {
  return Boolean(PALETTES[key]) && (!PALETTES[key].plus || plan.plus);
}

function wantsDark() {
  if (layout.mode === "dark") return true;
  if (layout.mode === "light") return false;
  return Boolean(darkQuery && darkQuery.matches);
}

function applyColors() {
  const palette = PALETTES[shownPalette()];
  if (!palette) return;
  const dark = wantsDark();
  const status = PALETTE_DATA.status;
  const light = { ...status.light, ...palette.light };
  const colors = dark ? { ...light, ...status.dark, ...palette.dark } : light;
  const root = document.documentElement.style;
  for (const [name, value] of Object.entries(colors)) root.setProperty(`--${name}`, value);
  root.setProperty("color-scheme", dark ? "dark" : "light");
}

function setUpColors() {
  const palette = document.getElementById("palette");
  const mode = document.getElementById("mode");
  const note = document.getElementById("palette-note");
  const free = el("optgroup");
  free.label = "Free";
  const plus = el("optgroup");
  plus.label = "MindfulFleet Plus";
  for (const [key, info] of Object.entries(PALETTES)) {
    const option = el("option", "", info.plus && !plan.plus ? `${info.name} (Plus)` : info.name);
    option.value = key;
    option.dataset.name = info.name;
    option.dataset.plus = info.plus ? "1" : "";
    (info.plus ? plus : free).appendChild(option);
  }
  palette.append(free, plus);
  palette.value = shownPalette();
  for (const [value, label] of MODES) {
    const option = el("option", "", label);
    option.value = value;
    mode.appendChild(option);
  }
  mode.value = MODES.some(([value]) => value === layout.mode) ? layout.mode : "auto";
  palette.addEventListener("change", () => {
    const info = PALETTES[palette.value];
    if (usable(palette.value)) {
      previewPalette = null;
      layout.palette = palette.value;
      saveLayout();
      note.hidden = true;
    } else {
      previewPalette = palette.value;
      note.textContent = `Previewing ${info.name}, a MindfulFleet Plus palette. Next time you open the dashboard it goes back to ` +
        `${PALETTES[usable(layout.palette) ? layout.palette : "lavender"].name}. MindfulFleet Plus is coming soon.`;
      note.hidden = false;
    }
    applyColors();
  });
  mode.addEventListener("change", () => {
    layout.mode = mode.value;
    saveLayout();
    applyColors();
  });
  applyColors();
}

if (darkQuery && darkQuery.addEventListener) darkQuery.addEventListener("change", applyColors);
else if (darkQuery && darkQuery.addListener) darkQuery.addListener(applyColors);  // Safari before 14.
applyColors();  // Right away, so the sign-in page and the first frame already use the chosen colors.

/* Region and units: rebuild the formatter, then the temperature rows that tables and charts read. */
function useRegion() {
  fmt = window.MF_REGION.make(layout.region, navigator.language);
  const temp = (v) => fmt.number(fmt.temp(v), 1);
  for (const list of [HISTORY_METRICS, VIEW_METRICS]) {
    const row = list.find(([key]) => key === "temp");
    row[2] = fmt.tempUnit;
    row[3] = temp;
  }
  const average = AVERAGE_ROWS.find(([key]) => key === "temp");
  average[1] = `Temp ${fmt.tempUnit}`;
  average[2] = (v) => Math.round(fmt.temp(v));
  const bits = fmt.region.speed === "bits";
  for (const [key, words] of [["net_in", "Net in"], ["net_out", "Net out"]]) {
    const row = AVERAGE_ROWS.find(([k]) => k === key);
    row[1] = `${words} ${bits ? "kbps" : "KB/s"}`;
    row[2] = bits ? (v) => Math.round(v * 8 / 1000) : (v) => Math.round(v / 1024);
  }
  document.getElementById("price-label").textContent = `Electricity price per kWh (${fmt.region.currency})`;
}

function countryName(code) {
  try {
    return new Intl.DisplayNames(["en"], { type: "region" }).of(code);
  } catch {
    return code;
  }
}

function setUpRegion() {
  const R = window.MF_REGION;
  const picks = {
    country: [["", `Match this browser (${countryName(R.countryOf(navigator.language)) || "unknown"})`],
      ...[...new Set([...Object.keys(R.CURRENCY_OF), ...R.EURO, ...R.FAHRENHEIT])]
        .map((code) => [code, countryName(code)]).sort((a, b) => a[1].localeCompare(b[1]))],
    units: [["metric", "Metric (°C)"], ["imperial", "Imperial (°F)"]],
    currency: R.CURRENCIES.map((code) => [code, code]),
    dates: [["auto", "Like my region"], ["mdy", "Nov 21, 2026"], ["dmy", "21 Nov 2026"], ["ymd", "2026-11-21"]],
    clock: [["auto", "Like my region"], ["12", "12-hour (2:30 PM)"], ["24", "24-hour (14:30)"]],
    timezone: [["auto", `This browser's (${Intl.DateTimeFormat().resolvedOptions().timeZone})`],
      ...(Intl.supportedValuesOf ? Intl.supportedValuesOf("timeZone") : []).map((zone) => [zone, zone])],
    sizes: [["binary", "Like Linux tools (1 GB = 1,024 MB)"], ["decimal", "Like the box (1 GB = 1,000 MB)"]],
    speed: [["bytes", "MB/s (bytes)"], ["bits", "Mbps (bits)"]],
  };
  const show = () => {
    const region = R.clean(layout.region);
    for (const key of Object.keys(picks)) document.getElementById(`region-${key}`).value = region[key];
  };
  for (const [key, options] of Object.entries(picks)) {
    const select = document.getElementById(`region-${key}`);
    for (const [value, label] of options) {
      const option = el("option", "", label);
      option.value = value;
      select.appendChild(option);
    }
    select.addEventListener("change", () => {
      const region = R.clean(layout.region);
      // A new country brings its own units and money; the clock, dates, time zone and sizes stay as chosen.
      layout.region = key === "country"
        ? R.clean({ ...region, ...(select.value ? R.forCountry(select.value) : R.guess(navigator.language)), country: select.value,
          dates: region.dates, clock: region.clock, timezone: region.timezone, sizes: region.sizes, speed: region.speed })
        : R.clean({ ...region, [key]: select.value });
      show();
      useRegion();
      commit();
    });
  }
  show();
}

function showVersion(version, latest) {
  const bar = document.getElementById("version-bar");
  if (!version) return;
  bar.textContent = !latest ? `MindfulFleet ${version}`
    : versionBelow(version, latest) ? `MindfulFleet ${version} · ${latest} is available. Each computer installs it at its next update check, or run sudo mindful-fleet update on it.`
      : `MindfulFleet ${version} · Up to date`;
  bar.hidden = false;
}

/* #70: an eye button next to every password box, to see what you typed. */
function eyeIcon(open) {
  const icon = svg("svg", { viewBox: "0 0 24 24", width: 18, height: 18, "aria-hidden": "true", class: "eye-icon" });
  svg("path", { d: "M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z", fill: "none", stroke: "currentColor", "stroke-width": 2 }, icon);
  svg("circle", { cx: 12, cy: 12, r: 3, fill: "none", stroke: "currentColor", "stroke-width": 2 }, icon);
  if (!open) svg("line", { x1: 3, y1: 21, x2: 21, y2: 3, stroke: "currentColor", "stroke-width": 2 }, icon);
  return icon;
}

function setUpPasswordEyes() {
  for (const input of document.querySelectorAll('input[type="password"]')) {
    const wrap = el("span", "password-wrap");
    const eye = el("button", "eye");
    eye.type = "button";
    const show = (visible) => {
      input.type = visible ? "text" : "password";
      eye.replaceChildren(eyeIcon(!visible));
      const words = visible ? "Hide password" : "Show password";
      eye.title = words;
      eye.setAttribute("aria-label", words);
      eye.setAttribute("aria-pressed", String(visible));
    };
    eye.addEventListener("click", () => show(input.type === "password"));
    // Hidden again whenever its form is sent, so the next person at the screen doesn't see it.
    if (input.form) input.form.addEventListener("submit", () => show(false));
    input.replaceWith(wrap);
    wrap.append(input, eye);
    show(false);
  }
}

/* "How do I set it?" on the no-password banner: the steps, with this hub's address filled in (D-082 part 1). */
function setUpPasswordHelp() {
  const help = document.getElementById("password-help");
  const host = window.location.hostname || "the hub's address";
  const step = (words, code) => {
    const item = el("li", "", words);
    if (code) item.appendChild(el("code", "", code));
    return item;
  };
  const windows = el("ol");
  windows.append(
    step("Press the Start button, type PowerShell and open it."),
    step("Type this, with YOUR-NAME changed to the name you sign in to the hub with, then press Enter: ", `ssh YOUR-NAME@${host}`),
    step("Type the hub's sign-in password. Nothing shows while you type; that's normal. Press Enter."),
    step("Type this and press Enter: ", "sudo mindful-fleet password"),
    step("Choose a dashboard password of at least 10 characters, and type it twice."),
    step("Come back to this page and reload it. It asks for the new password."));
  help.append(el("p", "", "On a Windows computer:"), windows,
    el("p", "", "On a Mac: open Terminal (press Command and Space, type Terminal), then do steps 2 to 6."),
    el("p", "", "At the hub itself, with a keyboard and screen: open Terminal, then do steps 4 to 6."));
  const toggle = document.getElementById("password-help-button");
  toggle.addEventListener("click", () => {
    help.hidden = !help.hidden;
    toggle.setAttribute("aria-expanded", String(!help.hidden));
  });
}

function setUpTools() {
  useRegion();
  setUpRegion();
  setUpRowPicker();
  setUpColors();
  setUpHistory();
  const look = document.getElementById("look");
  for (const [value, label] of LOOKS) {
    const option = el("option", "", label);
    option.value = value;
    look.appendChild(option);
  }
  look.value = LOOKS.some(([value]) => value === layout.look) ? layout.look : "tall";
  look.addEventListener("change", () => {
    layout.look = look.value;
    saveLayout();
    showExpandAll();
    render();
  });
  const expandAll = document.getElementById("expand-all");
  const showExpandAll = () => {
    expandAll.hidden = layout.look === "tall";
    expandAll.textContent = layout.expandAll ? "Collapse all" : "Expand all";
    expandAll.setAttribute("aria-pressed", String(!!layout.expandAll));
  };
  showExpandAll();
  expandAll.addEventListener("click", () => {
    layout.expandAll = !layout.expandAll;
    expanded.clear();
    collapsed.clear();
    saveLayout();
    showExpandAll();
    render();
  });
  const price = document.getElementById("price");
  price.value = layout.price ?? "";
  price.addEventListener("change", () => {
    const value = Number(price.value);
    layout.price = price.value === "" || !(value >= 0 && value <= 10) ? null : value;
    price.value = layout.price ?? "";
    commit();
  });
  const sliders = [
    ["width", (v) => `${v}px`],
    ["size", (v) => `${v}%`],
    ["height", (v) => (v >= FULL_HEIGHT ? "Fit all" : `${v}px`)],
    ["controls", (v) => `${v}%`],
  ];
  const panel = document.getElementById("settings");
  const scaleControls = () => { panel.style.fontSize = `${layout.controls}%`; };
  scaleControls();
  panel.open = layout.settingsOpen !== false;
  panel.addEventListener("toggle", () => {
    layout.settingsOpen = panel.open;
    saveLayout();
  });
  for (const [key, describe] of sliders) {
    const input = document.getElementById(key);
    const label = document.getElementById(`${key}-value`);
    input.value = layout[key];
    label.textContent = describe(layout[key]);
    input.addEventListener("input", () => {
      layout[key] = Number(input.value);
      label.textContent = describe(layout[key]);
      saveLayout();
      if (key === "controls") scaleControls();
      else render();
    });
    const name = input.parentElement.firstElementChild.textContent.trim().toLowerCase();
    input.before(stepButton(input, -1, `Make ${name} smaller`));
    input.after(stepButton(input, 1, `Make ${name} bigger`));
  }

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

// Signing in. The password goes only to the hub; the browser keeps a sign-in cookie it can't read from scripts.
let started = false;
let refreshTimer = null;

async function sendJson(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  let data = {};
  try {
    data = await response.json();
  } catch {
    // An empty or odd reply still has a status.
  }
  return { ok: response.ok, status: response.status, data };
}

function waitWords(data) {
  if (!data.wait) return "";
  const minutes = Math.ceil(data.wait / 60);
  return data.wait < 60 ? ` Try again in ${data.wait} seconds.` : ` Try again in ${minutes} minute${minutes === 1 ? "" : "s"}.`;
}

// Signed out while the page was open (hub restart, or a new password): stop refreshing and ask once.
function signedOut() {
  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = null;
  if (document.getElementById("sign-in").hidden) showSignIn("Please sign in again.");
}

function showSignIn(message) {
  document.getElementById("sign-in").hidden = false;
  for (const part of document.querySelectorAll(".top, main, .foot")) part.hidden = true;
  document.getElementById("sign-in-message").textContent = message;
  document.getElementById("sign-in-password").focus();
}

function hideSignIn() {
  document.getElementById("sign-in").hidden = true;
  for (const part of document.querySelectorAll(".top, main, .foot")) part.hidden = false;
}

function setUpAccount(passwordSet) {
  document.getElementById("no-password").hidden = passwordSet;
  document.getElementById("account").hidden = !passwordSet;
}

function setUpSignIn() {
  setUpFindPis();
  const form = document.getElementById("sign-in-form");
  const input = document.getElementById("sign-in-password");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const message = document.getElementById("sign-in-message");
    message.textContent = "Checking…";
    try {
      const { ok, data } = await sendJson("/api/sign-in", { password: input.value });
      if (!ok) {
        message.textContent = (data.error || "That didn't work.") + waitWords(data);
        input.select();
        return;
      }
      input.value = "";
      hideSignIn();
      start();
    } catch {
      message.textContent = "Can't reach the hub. Check it's switched on, then try again.";
    }
  });

  document.getElementById("sign-out").addEventListener("click", async () => {
    try {
      await sendJson("/api/sign-out", {});
    } finally {
      window.location.reload();
    }
  });

  document.getElementById("change-password").addEventListener("submit", async (event) => {
    event.preventDefault();
    const message = document.getElementById("change-message");
    const current = document.getElementById("current-password");
    const fresh = document.getElementById("new-password");
    const repeat = document.getElementById("repeat-password");
    if (fresh.value !== repeat.value) {
      message.textContent = "The two new passwords don't match.";
      return;
    }
    message.textContent = "Saving…";
    try {
      const { ok, data } = await sendJson("/api/password", { current: current.value, new: fresh.value });
      if (!ok) {
        message.textContent = (data.error || "That didn't work.") + waitWords(data);
        return;
      }
      for (const field of [current, fresh, repeat]) field.value = "";
      message.textContent = "Password changed. Every other browser has been signed out.";
    } catch {
      message.textContent = "Can't reach the hub. Your password wasn't changed.";
    }
  });
}

// Find my Pis: the hub looks for Raspberry Pis on its own network. Built with textContent only.
function findPiLine(pi) {
  const item = document.createElement("li");
  const head = document.createElement("strong");
  head.textContent = pi.reporting ? `${pi.name} (${pi.ip})` : pi.ip;
  const note = document.createElement("div");
  note.className = "soft";
  const parts = [pi.reporting ? "On your dashboard" : "Not on your dashboard yet", `hardware address ${pi.mac}`];
  if (pi.ssh) parts.push("SSH is on");
  note.textContent = parts.join(" · ");
  item.append(head, note);
  return item;
}

function setUpFindPis() {
  const button = document.getElementById("find-pis-go");
  const message = document.getElementById("find-pis-message");
  const list = document.getElementById("find-pis-list");
  button.addEventListener("click", async () => {
    button.disabled = true;
    message.textContent = "Looking…";
    list.replaceChildren();
    try {
      const { ok, status, data } = await sendJson("/api/discover", {});
      if (status === 401) {
        signedOut();
        return;
      }
      if (!ok) {
        message.textContent = data.error || "That didn't work. Try again in a moment.";
        return;
      }
      const found = data.found || [];
      const fresh = found.filter((pi) => !pi.reporting).length;
      message.textContent = found.length === 0
        ? `No Raspberry Pis answered on ${data.network}. Pis on another network, or set to use a made-up hardware address, don't show here.`
        : `Found ${found.length} Raspberry Pi${found.length === 1 ? "" : "s"} on ${data.network}` +
          (fresh ? `, ${fresh} not on your dashboard yet. To add one, run the join line the hub showed when it was installed.` : ", all on your dashboard.");
      list.replaceChildren(...found.map(findPiLine));
    } catch {
      message.textContent = "Can't reach the hub. Check it's switched on, then try again.";
    } finally {
      button.disabled = false;
    }
  });
}

function start() {
  const keepRefreshing = () => {
    void refresh();
    if (!refreshTimer) refreshTimer = setInterval(refresh, REFRESH_MS);
  };
  if (started) {
    keepRefreshing();
    return;
  }
  started = true;
  void loadSharedLayout().finally(() => {
    setUpTools();
    keepRefreshing();
    if (regionNote) toast(regionNote);
  });
}

async function begin() {
  setUpSignIn();
  setUpPasswordEyes();
  setUpPasswordHelp();
  let status = { password_set: false, signed_in: true };
  try {
    const response = await fetch("/api/auth", { cache: "no-store" });
    if (response.ok) status = await response.json();
  } catch {
    // The hub may be restarting. Carry on; refresh() reports it.
  }
  setUpAccount(status.password_set);
  if (status.password_set && !status.signed_in) showSignIn("");
  else start();
}

void begin();

/* History (MindfulFleet Plus): each Pi's readings kept on the hub for up to 3 years. Export is Pro. */

let historyInfo = null; // From the hub: settings per Pi, space used, limits.
const customEvery = new Set(); // Cards (or "all") showing the Custom interval boxes.
const HISTORY_COLORS = 6;
const VIEW_METRICS = [
  ["temp", "Temperature", "°C", (v) => v.toFixed(1)],
  ["hashrate", "Hashrate", "H/s", (v) => Math.round(v).toLocaleString()],
  ["per_watt", "Hashes per watt", "H/s/W", (v) => v.toFixed(1)],
  ["watts", "Power use", "W", (v) => v.toFixed(2)],
  ["fan", "Fan", "RPM", (v) => Math.round(v).toLocaleString()],
  ["slowed", "Time slowed down", "%", (v) => Math.round(v)],
  ["clock", "Clock", "GHz", (v) => (v / 1000).toFixed(2)],
  ["cpu", "CPU", "%", (v) => Math.round(v)],
  ["memory", "Memory", "%", (v) => Math.round(v)],
  ["load", "Load", "", (v) => v.toFixed(2)],
  ["volts", "Input voltage", "V", (v) => v.toFixed(2)],
  ["wifi", "Wi-Fi signal", "dBm", (v) => Math.round(v)],
  ["sensor_watts", "Sensor power", "W", (v) => v.toFixed(2)],
  ["net_in", "Network in", "", (v) => rate(v)],
  ["net_out", "Network out", "", (v) => rate(v)],
  ["disk_read", "Disk reads", "", (v) => rate(v)],
  ["disk_write", "Disk writes", "", (v) => rate(v)],
];
const COMPARE_ROWS = ["temp", "hashrate", "per_watt", "watts", "fan", "slowed", "clock", "cpu"];
const RANGES = [["1", "Last 24 hours"], ["7", "Last 7 days"], ["30", "Last 30 days"], ["90", "Last 90 days"],
  ["365", "Last year"], ["1095", "Last 3 years"], ["custom", "Pick dates"]];
const view = { open: false, ids: null, reading: "temp", range: "7", from: "", to: "", data: null, markers: [], compare: null, message: "" };

function everyWords(seconds) {
  if (seconds % 86400 === 0) return seconds === 86400 ? "1 day" : `${seconds / 86400} days`;
  if (seconds % 3600 === 0) return seconds === 3600 ? "1 hour" : `${seconds / 3600} hours`;
  if (seconds % 60 === 0) return `${seconds / 60} min`;
  return `${seconds} s`;
}

function toast(text) {
  const note = el("p", "toast", text);
  note.setAttribute("role", "status");
  document.body.appendChild(note);
  setTimeout(() => note.remove(), 7000);
}

async function sendHistory(method, path, body) {
  const response = await fetch(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  let data = {};
  try {
    data = await response.json();
  } catch {
    // An empty or odd reply still has a status.
  }
  if (response.status === 401) signedOut();
  return { ok: response.ok, data };
}

async function saveHistorySetting(ids, change) {
  const { ok, data } = await sendHistory("PUT", "/api/history/settings", { ids, ...change });
  if (!ok) {
    toast(data.error || "The hub couldn't save that. Try again in a moment.");
    return;
  }
  if (historyInfo) historyInfo.settings = data.settings;
  render();
  if (view.open) renderHistoryView();
}

/* The interval menu: Ed's list plus 1 hour and 6 hours, and Custom for anything else. */
function intervalPicker(key, every, label, onPick) {
  const wrap = el("span", "every");
  const choices = (historyInfo && historyInfo.intervals) || [15, 30, 60, 180, 300, 900, 1800, 3600, 21600, 86400];
  if (customEvery.has(key)) {
    const amount = el("input");
    amount.type = "number";
    amount.min = "1";
    amount.step = "1";
    amount.value = String(every % 3600 === 0 ? every / 3600 : every % 60 === 0 ? every / 60 : every);
    amount.setAttribute("aria-label", `${label}: how many`);
    const unit = el("select");
    unit.setAttribute("aria-label", `${label}: seconds, minutes, hours or days`);
    for (const [value, words] of [["1", "seconds"], ["60", "minutes"], ["3600", "hours"], ["86400", "days"]]) {
      const option = el("option", "", words);
      option.value = value;
      unit.appendChild(option);
    }
    unit.value = every % 3600 === 0 ? "3600" : every % 60 === 0 ? "60" : "1";
    const save = button("Save", `Save ${label}`, () => {
      const seconds = Math.round(Number(amount.value) * Number(unit.value));
      const min = (historyInfo && historyInfo.min_interval) || 15;
      const max = (historyInfo && historyInfo.max_interval) || 604800;
      if (!Number.isFinite(seconds) || seconds < min || seconds > max) {
        toast(`Pick between ${min} seconds and 7 days. ${min} seconds is how often a Pi reports.`);
        return;
      }
      customEvery.delete(key);
      onPick(seconds);
    });
    const cancel = button("Cancel", "Keep the interval as it was", () => {
      customEvery.delete(key);
      render();
      if (view.open) renderHistoryView();
    });
    wrap.append(el("span", "every-label", "Every"), amount, unit, save, cancel);
    return wrap;
  }
  const select = el("select");
  select.setAttribute("aria-label", label);
  const options = choices.includes(every) ? choices : [...choices, every].sort((a, b) => a - b);
  for (const seconds of options) {
    const option = el("option", "", `Every ${everyWords(seconds)}`);
    option.value = String(seconds);
    select.appendChild(option);
  }
  const custom = el("option", "", "Custom…");
  custom.value = "custom";
  select.appendChild(custom);
  select.value = String(every);
  select.addEventListener("change", () => {
    if (select.value === "custom") {
      customEvery.add(key);
      select.blur();
      render();
      if (view.open) renderHistoryView();
      return;
    }
    select.blur();
    onPick(Number(select.value));
  });
  wrap.appendChild(select);
  return wrap;
}

/* The switch at the top of each card. */
function historySwitch(pi) {
  if (!historyInfo) return null;
  const wrap = el("div", "history-switch");
  if (!plan.plus) {
    const locked = button("History · Plus", "History keeps this Pi's readings for up to 3 years. It's part of MindfulFleet Plus.", () => {
      toast("History keeps each Pi's readings on your hub for up to 3 years, with charts and markers. It's part of MindfulFleet Plus, coming soon.");
    });
    locked.classList.add("locked");
    wrap.appendChild(locked);
    return wrap;
  }
  const setting = historyInfo.settings[pi.id] || { enabled: false, every: 60 };
  const toggle = button(setting.enabled ? "History on" : "History off",
    `${setting.enabled ? "Stop" : "Start"} saving ${pi.hostname}'s readings`, () => saveHistorySetting([pi.id], { enabled: !setting.enabled }));
  toggle.setAttribute("aria-pressed", String(setting.enabled));
  wrap.appendChild(toggle);
  if (setting.enabled) wrap.appendChild(intervalPicker(pi.id, setting.every, `How often to save ${pi.hostname}'s snapshot`, (every) => saveHistorySetting([pi.id], { every })));
  return wrap;
}

/* The History page */

function rangeTimes() {
  const now = Math.floor(Date.now() / 1000);
  if (view.range === "custom") {
    const from = view.from ? new Date(`${view.from}T00:00`).getTime() / 1000 : now - 7 * 86400;
    const to = view.to ? new Date(`${view.to}T00:00`).getTime() / 1000 + 86400 : now;
    return [Math.floor(from), Math.min(Math.floor(to), now + 86400)];
  }
  return [now - Number(view.range) * 86400, now];
}

function chosenIds() {
  if (view.ids) return view.ids.filter((id) => latestPis.some((pi) => pi.id === id));
  const on = latestPis.filter((pi) => historyInfo && historyInfo.settings[pi.id] && historyInfo.settings[pi.id].enabled).map((pi) => pi.id);
  return on.length ? on : latestPis.slice(0, 1).map((pi) => pi.id);
}

function nameOf(id) {
  const pi = latestPis.find((p) => p.id === id);
  return pi ? pi.hostname : "a Pi no longer on the dashboard";
}

function dateWords(seconds, withTime) {
  return fmt.date(seconds, withTime);
}

async function loadHistoryView() {
  const ids = chosenIds().slice(0, HISTORY_COLORS);
  const [from, to] = rangeTimes();
  view.message = "";
  if (!ids.length || !plan.plus) {
    view.data = null;
    renderHistoryView();
    return;
  }
  try {
    const query = `from=${from}&to=${to}`;
    const [series, markers] = await Promise.all([
      fetch(`/api/history/range?ids=${ids.join(",")}&${query}&readings=${encodeURIComponent(view.reading)}&points=600`, { cache: "no-store" }),
      fetch(`/api/history/markers?${query}`, { cache: "no-store" }),
    ]);
    if (series.status === 401 || markers.status === 401) {
      signedOut();
      return;
    }
    const body = await series.json();
    view.data = series.ok ? { ...body, from, to, ids } : null;
    view.markers = markers.ok ? (await markers.json()).markers : [];
    if (!series.ok) view.message = body.error || "The hub couldn't load History just now.";
  } catch {
    view.message = "Can't reach the hub. Try again in a moment.";
  }
  renderHistoryView();
}

function bigChart() {
  const wrap = el("div", "history big-history");
  const metric = VIEW_METRICS.find(([key]) => key === view.reading) || VIEW_METRICS[0];
  const [key, label, unit, format] = metric;
  const data = view.data;
  const lines = data ? data.ids.map((id, i) => ({ id, i, s: data.series[id] })).filter((line) => line.s && line.s.times.length) : [];
  const values = lines.flatMap((line) => line.s.values[key].filter((v) => v != null));
  if (!data || values.length < 2) {
    wrap.appendChild(el("p", "history-empty", data ? "No history for these Pis in this time yet. Turn History on with the switch at the top of a card; " +
      "the first snapshots appear within a few minutes." : "Loading…"));
    return wrap;
  }
  const single = lines.length === 1;
  const lows = single ? lines[0].s.low[key].filter((v) => v != null) : [];
  const highs = single ? lines[0].s.high[key].filter((v) => v != null) : [];
  let low = Math.min(...values, ...lows);
  let high = Math.max(...values, ...highs);
  if (high - low < 1e-9) { high += 1; low -= 1; }
  const W = 1000;
  const H = 320;
  const pad = 10;
  const t0 = data.from;
  const t1 = Math.max(data.to, t0 + 1);
  const x = (t) => (t - t0) / (t1 - t0) * W;
  const y = (v) => H - pad - (v - low) / (high - low) * (H - 2 * pad);
  const plot = el("div", "history-plot");
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none", role: "img",
    "aria-label": `${label} from ${dateWords(t0)} to ${dateWords(t1)}, between ${format(low)} and ${format(high)} ${unit}` });
  for (const v of [high, (high + low) / 2, low]) svg("line", { x1: 0, x2: W, y1: y(v), y2: y(v), class: "history-grid" }, root);

  // Gaps (the Pi or History was off) break the line instead of drawing across them.
  const runsOf = (s, list) => {
    const runs = [];
    let run = [];
    s.times.forEach((t, i) => {
      const v = list[i];
      if (v == null || (i && t - s.times[i - 1] > 2.5 * s.step)) { if (run.length) runs.push(run); run = []; }
      if (v != null) run.push([t, v]);
    });
    if (run.length) runs.push(run);
    return runs;
  };
  if (single) {
    const s = lines[0].s;
    const lowRuns = runsOf(s, s.low[key]);
    const highRuns = runsOf(s, s.high[key]);
    lowRuns.forEach((lowRun, r) => {
      const highRun = highRuns[r];
      if (!highRun || highRun.length !== lowRun.length || lowRun.length < 2) return;
      const top = highRun.map(([t, v]) => `${x(t).toFixed(1)},${y(v).toFixed(1)}`);
      const bottom = lowRun.slice().reverse().map(([t, v]) => `${x(t).toFixed(1)},${y(v).toFixed(1)}`);
      svg("polygon", { points: [...top, ...bottom].join(" "), class: "history-band series-0" }, root);
    });
  }
  for (const line of lines) {
    for (const run of runsOf(line.s, line.s.values[key])) {
      if (run.length > 1) svg("polyline", { points: run.map(([t, v]) => `${x(t).toFixed(1)},${y(v).toFixed(1)}`).join(" "), class: `history-line series-${line.i}` }, root);
      else svg("circle", { cx: x(run[0][0]), cy: y(run[0][1]), r: 3, class: `history-point series-${line.i}` }, root);
    }
  }
  for (const marker of view.markers) {
    if (marker.t < t0 || marker.t > t1) continue;
    if (marker.pi && !data.ids.includes(marker.pi)) continue;
    const mark = svg("line", { x1: x(marker.t), x2: x(marker.t), y1: 0, y2: H, class: "history-marker" }, root);
    const title = svg("title", {}, mark);
    title.textContent = `${marker.text} (${marker.pi ? nameOf(marker.pi) : "all Pis"}, ${dateWords(marker.t, true)})`;
  }
  const cross = svg("line", { y1: 0, y2: H, class: "history-cross", visibility: "hidden" }, root);
  plot.appendChild(root);
  const axis = el("div", "history-y");
  axis.append(el("span", "", `${format(high)} ${unit}`.trim()), el("span", "", `${format(low)} ${unit}`.trim()));
  const tip = el("div", "history-tip");
  tip.hidden = true;
  plot.append(axis, tip);

  root.addEventListener("pointermove", (event) => {
    const box = root.getBoundingClientRect();
    const t = t0 + (event.clientX - box.left) / box.width * (t1 - t0);
    const parts = [];
    let shownAt = null;
    for (const line of lines) {
      let best = -1;
      line.s.times.forEach((time, i) => {
        if (line.s.values[key][i] != null && (best < 0 || Math.abs(time - t) < Math.abs(line.s.times[best] - t))) best = i;
      });
      if (best < 0 || Math.abs(line.s.times[best] - t) > 3 * line.s.step) continue;
      shownAt = shownAt || line.s.times[best];
      parts.push(`${nameOf(line.id)} ${format(line.s.values[key][best])} ${unit}`.trim());
    }
    const near = view.markers.find((m) => Math.abs(m.t - t) < (t1 - t0) / 150);
    if (near) parts.push(`◆ ${near.text}`);
    if (!parts.length) { tip.hidden = true; cross.setAttribute("visibility", "hidden"); return; }
    const px = x(shownAt || t);
    cross.setAttribute("x1", px); cross.setAttribute("x2", px); cross.setAttribute("visibility", "visible");
    tip.textContent = `${dateWords(shownAt || t, true)}: ${parts.join(" · ")}`;
    tip.style.left = `${Math.min(Math.max(px / W * 100, 20), 80)}%`;
    tip.hidden = false;
  });
  root.addEventListener("pointerleave", () => { tip.hidden = true; cross.setAttribute("visibility", "hidden"); });
  wrap.appendChild(plot);
  const xAxis = el("div", "history-x");
  const withTime = t1 - t0 <= 2 * 86400;
  xAxis.append(el("span", "", dateWords(t0, withTime)), el("span", "", dateWords((t0 + t1) / 2, withTime)), el("span", "", dateWords(t1, withTime)));
  wrap.appendChild(xAxis);
  const legend = el("div", "legend");
  for (const line of lines) {
    const item = el("span", "legend-item");
    item.append(el("span", `swatch series-${line.i}`), document.createTextNode(nameOf(line.id)));
    legend.appendChild(item);
  }
  if (single) legend.appendChild(el("span", "legend-note", "Shaded: the lowest and highest readings"));
  if (view.markers.length) legend.appendChild(el("span", "legend-note", "Dashed lines: markers"));
  wrap.appendChild(legend);
  return wrap;
}

function compareBox() {
  const found = view.compare;
  const box = el("div", "compare");
  const marker = view.markers.find((m) => m.id === found.marker) || { text: "Marker", t: found.t };
  box.appendChild(el("h3", "", `Before and after: ${marker.text}`));
  box.appendChild(el("p", "help", `${nameOf(found.pi)}, ${found.days} day${found.days === 1 ? "" : "s"} on each side of ${dateWords(found.t, true)}. ` +
    `${found.before_hours} hours of history before, ${found.after_hours} after.`));
  if (found.before_hours < found.days * 12 || found.after_hours < found.days * 12) {
    box.appendChild(el("p", "help", "Less than half of that time has history yet, so treat these numbers as early signs."));
  }
  const table = el("table", "compare-table");
  const head = el("tr");
  for (const words of ["", "Before", "After", "Change"]) head.appendChild(el("th", "", words));
  table.appendChild(head);
  for (const key of COMPARE_ROWS) {
    const metric = VIEW_METRICS.find(([k]) => k === key);
    const before = found.before[key];
    const after = found.after[key];
    if (before == null && after == null) continue;
    const [, label, unit, format] = metric;
    const row = el("tr");
    row.appendChild(el("th", "", label));
    row.appendChild(el("td", "", before == null ? "—" : `${format(before)} ${unit}`.trim()));
    row.appendChild(el("td", "", after == null ? "—" : `${format(after)} ${unit}`.trim()));
    let change = "—";
    if (before != null && after != null) {
      const diff = after - before;
      const sign = diff > 0 ? "+" : diff < 0 ? "−" : "";
      // A temperature difference converts without the +32: 20 °C cooler is 36 °F cooler.
      change = key === "temp" ? `${sign}${fmt.number(fmt.tempChange(Math.abs(diff)), 1)} ${unit}` : `${sign}${format(Math.abs(diff))} ${unit}`.trim();
      if (Math.abs(before) > 1e-9 && ["hashrate", "per_watt", "watts", "fan"].includes(key)) change += ` (${sign}${Math.abs(diff / before * 100).toFixed(1)}%)`;
    }
    row.appendChild(el("td", "", change));
    table.appendChild(row);
  }
  box.appendChild(table);
  box.appendChild(button("Close", "Close Before and after", () => { view.compare = null; renderHistoryView(); }));
  return box;
}

async function showCompare(marker, days) {
  const pi = marker.pi || chosenIds()[0];
  if (!pi) return;
  const response = await fetch(`/api/history/compare?id=${pi}&t=${marker.t}&days=${days}`, { cache: "no-store" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    toast(data.error || "The hub couldn't work that out just now.");
    return;
  }
  view.compare = { ...data, marker: marker.id, pi, t: marker.t };
  renderHistoryView();
}

function markersBox() {
  const box = el("div", "markers");
  box.appendChild(el("h3", "", "Markers"));
  box.appendChild(el("p", "help", "Note a change, like \"Ice cooler installed\". It shows as a dashed line, and Before and after compares the days on each side."));
  const list = el("ul", "marker-list");
  for (const marker of view.markers) {
    const item = el("li");
    item.appendChild(el("span", "", `${dateWords(marker.t, true)} · ${marker.pi ? nameOf(marker.pi) : "All Pis"} · ${marker.text}`));
    const days = el("select");
    days.setAttribute("aria-label", `Days to compare on each side of ${marker.text}`);
    for (const n of [1, 3, 7, 14, 30]) {
      const option = el("option", "", `${n} day${n === 1 ? "" : "s"} each side`);
      option.value = String(n);
      days.appendChild(option);
    }
    days.value = "7";
    item.append(days, button("Before and after", `Compare before and after ${marker.text}`, () => void showCompare(marker, Number(days.value))),
      button("Delete", `Delete the marker ${marker.text}`, async () => {
        const { ok, data } = await sendHistory("POST", "/api/history/markers", { delete: marker.id });
        if (!ok) toast(data.error || "Couldn't delete it just now.");
        await loadHistoryView();
      }));
    list.appendChild(item);
  }
  if (!view.markers.length) list.appendChild(el("li", "help", "No markers in this time."));
  box.appendChild(list);

  const form = el("form", "marker-form");
  const who = el("select");
  who.setAttribute("aria-label", "Which Pi the marker is for");
  const all = el("option", "", "All Pis");
  all.value = "";
  who.appendChild(all);
  for (const pi of latestPis) {
    const option = el("option", "", pi.hostname);
    option.value = pi.id;
    who.appendChild(option);
  }
  who.value = chosenIds().length === 1 ? chosenIds()[0] : "";
  const when = el("input");
  when.type = "datetime-local";
  const now = new Date(Date.now() - new Date().getTimezoneOffset() * 60000);
  when.value = now.toISOString().slice(0, 16);
  when.setAttribute("aria-label", "When it happened");
  const text = el("input");
  text.type = "text";
  text.maxLength = 80;
  text.placeholder = "What changed, e.g. Ice cooler installed";
  text.setAttribute("aria-label", "What changed");
  const add = el("button", "", "Add marker");
  add.type = "submit";
  form.append(who, when, text, add);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const t = Math.floor(new Date(when.value).getTime() / 1000);
    const { ok, data } = await sendHistory("POST", "/api/history/markers", { pi: who.value || null, t, text: text.value });
    if (!ok) {
      toast(data.error || "Couldn't add the marker just now.");
      return;
    }
    await loadHistoryView();
  });
  box.appendChild(form);
  return box;
}

function exportBox() {
  const box = el("div", "export");
  box.appendChild(el("h3", "", "Export to Excel or Google Sheets"));
  if (!plan.pro) {
    box.appendChild(el("p", "help", "Download History as an Excel or CSV file: one tab per Pi, ready for your own charts. " +
      "Exporting is part of MindfulFleet Pro."));
    return box;
  }
  const kind = el("select");
  kind.setAttribute("aria-label", "File type");
  for (const [value, words] of [["xlsx", "Excel (.xlsx)"], ["csv", "CSV"]]) {
    const option = el("option", "", words);
    option.value = value;
    kind.appendChild(option);
  }
  const every = el("select");
  every.setAttribute("aria-label", "How much detail");
  for (const [value, words] of [["3600", "Hourly averages"], ["86400", "Daily averages"], ["0", "Every snapshot"]]) {
    const option = el("option", "", words);
    option.value = value;
    every.appendChild(option);
  }
  const lows = el("input");
  lows.type = "checkbox";
  const lowsLabel = el("label", "check");
  lowsLabel.append(lows, document.createTextNode(" Include lows and highs"));
  const link = el("a", "button-link", "Download");
  const update = () => {
    const [from, to] = rangeTimes();
    link.href = `/api/history/export?ids=${chosenIds().join(",")}&from=${from}&to=${to}&format=${kind.value}&every=${every.value}&lows=${lows.checked ? 1 : 0}`;
  };
  for (const input of [kind, every, lows]) input.addEventListener("change", update);
  update();
  link.addEventListener("click", () => toast("Getting the file ready. A big export can take a minute."));
  box.append(kind, every, lowsLabel, link);
  box.appendChild(el("p", "help", "Uses the Pis and dates chosen above. Google Sheets: File → Import → Upload, then pick the file. " +
    "One Excel tab holds about a million rows, so for long stretches pick hourly or daily averages."));
  return box;
}

function importBox() {
  const box = el("details", "rows import");
  box.appendChild(el("summary", "", "Load a saved 24 hours"));
  box.appendChild(el("p", "help", "If you saved the last 24 hours to a file before History was on, load it here. It fills in only where History has nothing."));
  const file = el("input");
  file.type = "file";
  file.accept = ".json,application/json";
  file.setAttribute("aria-label", "Saved file");
  file.addEventListener("change", async () => {
    const chosen = file.files && file.files[0];
    if (!chosen) return;
    if (chosen.size > 2 * 1024 * 1024) {
      toast("That file is bigger than a saved 24 hours should be.");
      return;
    }
    let saved;
    try {
      saved = JSON.parse(await chosen.text());
    } catch {
      toast("That isn't a saved MindfulFleet file.");
      return;
    }
    const { ok, data } = await sendHistory("POST", "/api/history/import", saved);
    if (!ok) {
      toast(data.error || "Couldn't load it just now.");
      return;
    }
    const added = Object.entries(data.added || {}).map(([name, count]) => `${name}: ${count}`).join(", ");
    toast(added ? `Loaded snapshots. ${added}.` : "Nothing new to load: none of those Pis matched this dashboard.");
    await loadHistoryView();
  });
  box.appendChild(file);
  return box;
}

function renderHistoryView() {
  const page = document.getElementById("history-view");
  if (!page) return;
  page.hidden = !view.open;
  document.getElementById("open-history").setAttribute("aria-pressed", String(view.open));
  if (!view.open) return;
  const focused = page.contains(document.activeElement) ? document.activeElement.getAttribute("aria-label") : null;
  page.replaceChildren();
  const head = el("div", "history-view-head");
  head.appendChild(el("h2", "", "History"));
  head.appendChild(button("Close", "Close History", () => { view.open = false; renderHistoryView(); }));
  page.appendChild(head);
  if (!historyInfo) {
    page.appendChild(el("p", "help", "This hub doesn't keep History."));
    return;
  }
  if (!plan.plus) {
    page.appendChild(el("p", "help", "History keeps each Pi's readings on your hub for up to 3 years: charts by day, week, month or year, " +
      "several Pis on one chart, markers for changes like a new cooler, and Before and after. It's part of MindfulFleet Plus, coming soon."));
    return;
  }
  const used = historyInfo.used_bytes ? `History uses ${bytes(historyInfo.used_bytes)} on the hub. ` : "";
  page.appendChild(el("p", "help", `${used}Every snapshot is kept for ${historyInfo.full_days} days, then one per 3 hours for 3 years. Nothing leaves your hub.`));
  if (historyInfo.paused) page.appendChild(el("p", "connection", historyInfo.paused));
  if (view.message) page.appendChild(el("p", "connection", view.message));

  const controls = el("div", "history-controls");
  const pick = el("fieldset", "pi-pick");
  pick.appendChild(el("legend", "", "Pis (up to 6 on one chart)"));
  const ids = chosenIds();
  latestPis.forEach((pi) => {
    const label = el("label", "check");
    const box = el("input");
    box.type = "checkbox";
    box.checked = ids.includes(pi.id);
    box.addEventListener("change", () => {
      const next = new Set(chosenIds());
      if (box.checked) next.add(pi.id); else next.delete(pi.id);
      view.ids = latestPis.map((p) => p.id).filter((id) => next.has(id));
      view.compare = null;
      void loadHistoryView();
    });
    const index = ids.slice(0, HISTORY_COLORS).indexOf(pi.id);
    label.append(box, index >= 0 ? el("span", `swatch series-${index}`) : el("span", "swatch"), document.createTextNode(` ${pi.hostname}`));
    const setting = historyInfo.settings[pi.id];
    label.appendChild(el("small", "", setting && setting.enabled ? ` every ${everyWords(setting.every)}` : " (History off)"));
    pick.appendChild(label);
  });
  if (ids.length > HISTORY_COLORS) pick.appendChild(el("p", "help", "Showing the first 6."));
  controls.appendChild(pick);

  const choose = el("div", "history-choose");
  const reading = el("select");
  reading.setAttribute("aria-label", "Reading to chart");
  for (const [key, label] of VIEW_METRICS) {
    const option = el("option", "", label);
    option.value = key;
    reading.appendChild(option);
  }
  reading.value = view.reading;
  reading.addEventListener("change", () => { view.reading = reading.value; void loadHistoryView(); });
  const range = el("select");
  range.setAttribute("aria-label", "Time to show");
  for (const [value, label] of RANGES) {
    const option = el("option", "", label);
    option.value = value;
    range.appendChild(option);
  }
  range.value = view.range;
  range.addEventListener("change", () => { view.range = range.value; void loadHistoryView(); });
  choose.append(reading, range);
  if (view.range === "custom") {
    for (const [key, words] of [["from", "From"], ["to", "To"]]) {
      const date = el("input");
      date.type = "date";
      date.value = view[key];
      date.setAttribute("aria-label", words);
      date.addEventListener("change", () => { view[key] = date.value; void loadHistoryView(); });
      const label = el("label", "pick");
      label.append(document.createTextNode(`${words} `), date);
      choose.appendChild(label);
    }
  }
  controls.appendChild(choose);

  const everyone = el("div", "history-all");
  everyone.appendChild(el("span", "", "All Pis:"));
  const allSettings = Object.values(historyInfo.settings);
  const allEvery = allSettings.length ? allSettings[0].every : 60;
  everyone.appendChild(intervalPicker("all", allEvery, "How often to save every Pi's snapshot",
    (every) => saveHistorySetting(latestPis.map((pi) => pi.id), { every })));
  everyone.append(
    button("Turn on for all", "Turn History on for every Pi", () => saveHistorySetting(latestPis.map((pi) => pi.id), { enabled: true })),
    button("Turn off for all", "Turn History off for every Pi; what's saved is kept", () => saveHistorySetting(latestPis.map((pi) => pi.id), { enabled: false })));
  controls.appendChild(everyone);
  page.appendChild(controls);

  page.appendChild(bigChart());
  if (view.compare) page.appendChild(compareBox());
  const lower = el("div", "history-lower");
  lower.append(markersBox(), exportBox());
  page.appendChild(lower);
  page.appendChild(importBox());
  if (focused) {
    const again = [...page.querySelectorAll("[aria-label]")].find((node) => node.getAttribute("aria-label") === focused);
    if (again) again.focus();
  }
}

function setUpHistory() {
  const open = document.getElementById("open-history");
  if (!open) return;
  open.addEventListener("click", () => {
    view.open = !view.open;
    renderHistoryView();
    if (view.open) {
      void loadHistoryView();
      document.getElementById("history-view").scrollIntoView({ behavior: "smooth", block: "start" });
    }
  });
}
