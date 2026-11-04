"use strict";

const REFRESH_MS = 5000;
const SVG_NS = "http://www.w3.org/2000/svg";
const MOODS = { ok: "Doing fine", warn: "Needs a look", bad: "Needs help now", offline: "Offline" };

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
  const low = Math.min(...history) - 2;
  const high = Math.max(...history) + 2;
  const root = svg("svg", { viewBox: "0 0 100 40", preserveAspectRatio: "none", class: "spark", role: "img", "aria-label": `Temperature over the last ${history.length} readings` });
  const points = history.map((value, i) =>
    `${(i / (history.length - 1) * 100).toFixed(1)},${(38 - (value - low) / (high - low) * 36).toFixed(1)}`);
  svg("polyline", { points: points.join(" ") }, root);
  return root;
}

function bytes(value) {
  if (value == null) return "";
  const gb = value / 1024 ** 3;
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

function meter(item, used, total) {
  const wrap = el("dd", "meter");
  if (item.percent == null) {
    wrap.textContent = "No reading";
    return wrap;
  }
  const bar = el("div", "bar");
  const fill = el("div", `fill ${item.level}`);
  fill.style.width = `${Math.min(100, item.percent)}%`;
  bar.appendChild(fill);
  wrap.title = `${bytes(used)} of ${bytes(total)}`;
  wrap.append(bar, el("span", item.level, `${Math.round(item.percent)}%`));
  return wrap;
}

function cores(values) {
  const dd = el("dd", "cores");
  const average = values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
  dd.setAttribute("role", "img");
  dd.setAttribute("aria-label", average == null ? "No reading" : `Average ${Math.round(average)} percent across ${values.length} cores`);
  dd.title = values.map((value, i) => `Core ${i + 1}: ${Math.round(value)}%`).join("\n");
  for (const value of values) {
    const core = el("div", "core");
    const level = el("i");
    level.style.height = `${value}%`;
    core.appendChild(level);
    dd.appendChild(core);
  }
  return dd;
}

function card(pi) {
  const h = pi.health;
  const article = el("article", `pi ${h.status}`);

  const head = el("div", "pi-head");
  const title = el("div", "pi-title");
  title.append(el("h2", "", pi.hostname), el("p", "pi-mood", MOODS[h.status]));
  if (pi.ip) title.appendChild(el("p", "pi-where", pi.ip));
  head.append(mascot(h.status), title);

  const body = el("div", h.online ? "pi-body" : "pi-body stale");
  const heat = el("div", "heat");
  const temp = h.temp.value == null ? "—" : `${Math.round(h.temp.value)}°C`;
  heat.append(el("div", `degrees ${h.temp.level}`, temp), sparkline(pi.history));
  body.appendChild(heat);
  if (!h.online) body.appendChild(el("p", "heat-note", "Last known numbers"));

  if (h.issues.length) {
    const list = el("ul", "issues");
    for (const issue of h.issues) list.appendChild(el("li", issue.level, issue.text));
    body.appendChild(list);
  }

  const facts = el("dl", "facts");
  const row = (label, value) => {
    facts.appendChild(el("dt", "", label));
    facts.appendChild(value);
  };
  const text = (item) => el("dd", item.level, item.text);
  row("Fan", text(h.fan));
  row("Speed", text(h.speed));
  row("Power", text(h.power));
  row("CPU", cores(pi.cpu_per_core));
  row("Memory", meter(h.memory, pi.mem_used, pi.mem_total));
  row("Storage", meter(h.disk, pi.disk_used, pi.disk_total));
  row("Running for", el("dd", "", duration(pi.uptime_s)));
  body.appendChild(facts);

  article.append(head, body);
  return article;
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

async function refresh() {
  const connection = document.getElementById("connection");
  try {
    const response = await fetch("/api/fleet", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const { pis } = await response.json();
    connection.hidden = true;
    document.getElementById("empty").hidden = pis.length > 0;
    document.getElementById("summary").textContent = pis.length ? headline(pis) : "Your fleet is empty.";
    document.title = pis.some((pi) => pi.health.status !== "ok") ? "⚠ Mindful Fleet" : "Mindful Fleet";
    document.getElementById("fleet").replaceChildren(...pis.map(card));
  } catch (error) {
    connection.textContent = "Can't reach the hub. These numbers may be out of date. Retrying every 5 seconds.";
    connection.hidden = false;
  }
}

refresh();
setInterval(refresh, REFRESH_MS);
