/* Fleet Telemetry dashboard - plain JavaScript, no build step.
 * 1. Loads current state from the REST API (/latest, /alerts).
 * 2. Opens a WebSocket and updates map, table, chart and alerts live.
 * All text is inserted with textContent (never innerHTML) to avoid XSS.
 */
(function () {
  "use strict";

  const CFG = window.FLEET_CONFIG || {};
  const API = (CFG.apiUrl || "").replace(/\/$/, "");
  const CARGO_LIMIT = 8;
  const OFFLINE_AFTER_MS = 30000;
  const MAX_POINTS = 90;

  const vehicles = {};      // id -> { latest, status, marker, history: [] }
  let alerts = [];
  let selected = null;
  const recentMsgTimes = [];
  let lastLatency = null;

  // ------------------------------------------------------------ helpers
  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([k, v]) => {
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else node.setAttribute(k, v);
    });
    (children || []).forEach((c) => node.appendChild(c));
    return node;
  }
  const fmt = (v, d, unit) => (v === null || v === undefined ? "--" : Number(v).toFixed(d) + (unit || ""));
  const timeStr = (ts) => new Date(Number(ts)).toLocaleTimeString();

  function statusOf(anomalies) {
    if (!anomalies || anomalies.length === 0) return "ok";
    const list = anomalies.map((a) => (typeof a === "string" ? { code: a } : a));
    const critCodes = ["CARGO_TEMP_HIGH", "ENGINE_OVERHEAT", "DOOR_OPEN_MOVING"];
    return list.some((a) => a.severity === "critical" || critCodes.includes(a.code)) ? "critical" : "warning";
  }
  const COLORS = { ok: "#3fb950", warning: "#d29922", critical: "#f85149", offline: "#8b98a5" };

  // ------------------------------------------------------------ map
  const map = L.map("map", { zoomControl: true }).setView([21.6, 72.6], 7);
  // Standard OSM tiles need no API key (CARTO now requires one); styles.css darkens them.
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors",
    maxZoom: 18,
    className: "dark-tiles",
  }).addTo(map);

  function upsertMarker(v) {
    const d = v.latest;
    const color = COLORS[v.status];
    const popup = `${d.vehicleId} | ${fmt(d.cargoTempC, 1, "°C")} | ${fmt(d.speedKmh, 0, " km/h")}`;
    if (!v.marker) {
      v.marker = L.circleMarker([d.lat, d.lon], {
        radius: 9, color: "#0f1419", weight: 2, fillColor: color, fillOpacity: 0.95,
      }).addTo(map);
      v.marker.bindTooltip(d.vehicleId, { permanent: true, direction: "right", className: "vlabel" });
      v.marker.on("click", () => selectVehicle(d.vehicleId));
    }
    v.marker.setLatLng([d.lat, d.lon]);
    v.marker.setStyle({ fillColor: color, radius: v.status === "critical" ? 12 : 9 });
    v.marker.bindPopup(popup);
  }

  // ------------------------------------------------------------ chart
  const chart = new Chart(document.getElementById("tempChart"), {
    type: "line",
    data: {
      labels: [],
      datasets: [
        { label: "Cargo °C", data: [], borderColor: "#58a6ff", backgroundColor: "rgba(88,166,255,.12)", fill: true, tension: 0.3, pointRadius: 0 },
        { label: "Limit", data: [], borderColor: "#f85149", borderDash: [6, 4], pointRadius: 0, borderWidth: 1.5 },
        { label: "Forecast", data: [], borderColor: "#d29922", pointRadius: 6, pointStyle: "circle", backgroundColor: "transparent", showLine: false },
      ],
    },
    options: {
      animation: false, responsive: true, maintainAspectRatio: false,
      scales: {
        x: { ticks: { color: "#8b98a5", maxTicksLimit: 8 }, grid: { color: "#26313d" } },
        y: { ticks: { color: "#8b98a5" }, grid: { color: "#26313d" }, suggestedMin: 0, suggestedMax: 12 },
      },
      plugins: { legend: { labels: { color: "#e6edf3" } } },
    },
  });

  function renderChart() {
    if (!selected || !vehicles[selected]) return;
    const v = vehicles[selected];
    const h = v.history.slice(-MAX_POINTS);
    const labels = h.map((r) => timeStr(r.ts));
    const temps = h.map((r) => r.cargoTempC);
    const forecast = h.map(() => null);
    const fc = v.latest.forecastCargoTempC;
    if (fc !== null && fc !== undefined) { labels.push("forecast"); temps.push(null); forecast.push(fc); }
    chart.data.labels = labels;
    chart.data.datasets[0].data = temps;
    chart.data.datasets[1].data = labels.map(() => CARGO_LIMIT);
    chart.data.datasets[2].data = forecast;
    chart.update();
  }

  async function selectVehicle(id) {
    selected = id;
    document.getElementById("chartVehicle").textContent = id;
    renderTable();
    try {
      const res = await fetch(`${API}/history?vehicleId=${encodeURIComponent(id)}&limit=${MAX_POINTS}`);
      if (res.ok) {
        const body = await res.json();
        if (body.readings && body.readings.length) {
          vehicles[id].history = body.readings.map((r) => ({ ts: r.ts || r.Timestamp, cargoTempC: r.cargoTempC }));
        }
      }
    } catch (e) { console.warn("history load failed", e); }
    renderChart();
  }

  // ------------------------------------------------------------ table
  function renderTable() {
    const tbody = document.getElementById("vehicleRows");
    const ids = Object.keys(vehicles).sort();
    tbody.replaceChildren();
    if (!ids.length) {
      tbody.appendChild(el("tr", {}, [el("td", { colspan: "7", class: "empty", text: "Waiting for telemetry..." })]));
      return;
    }
    ids.forEach((id) => {
      const v = vehicles[id];
      const d = v.latest;
      const tr = el("tr", { class: id === selected ? "selected" : "" }, [
        el("td", { text: id }),
        el("td", {}, [el("span", { class: "badge " + (v.status === "offline" ? "warning" : v.status), text: v.status.toUpperCase() })]),
        el("td", { class: d.cargoTempC > CARGO_LIMIT ? "hot" : "", text: fmt(d.cargoTempC, 1, "°C") }),
        el("td", { class: d.forecastCargoTempC > CARGO_LIMIT ? "hot" : "", text: d.forecastCargoTempC == null ? "stable" : fmt(d.forecastCargoTempC, 1, "°C") }),
        el("td", { text: fmt(d.speedKmh, 0, " km/h") }),
        el("td", { text: fmt(d.engineTempC, 0, "°C") }),
        el("td", { text: fmt(d.fuelPct, 0, "%") }),
      ]);
      tr.addEventListener("click", () => selectVehicle(id));
      tbody.appendChild(tr);
    });
  }

  // ------------------------------------------------------------ alerts
  function renderAlerts(freshCount) {
    const ul = document.getElementById("alertList");
    ul.replaceChildren();
    if (!alerts.length) { ul.appendChild(el("li", { class: "empty", text: "No alerts yet" })); return; }
    alerts.slice(0, 60).forEach((a, i) => {
      ul.appendChild(el("li", { class: a.severity + (i < freshCount ? " fresh" : "") }, [
        el("div", { text: `${a.vehicleId} - ${a.message}` }),
        el("div", { class: "meta", text: `${a.severity.toUpperCase()} · ${a.code} · ${timeStr(a.ts)}` }),
      ]));
    });
  }

  // ------------------------------------------------------------ KPIs
  function renderKpis() {
    const now = Date.now();
    while (recentMsgTimes.length && now - recentMsgTimes[0] > 60000) recentMsgTimes.shift();
    const list = Object.values(vehicles);
    const online = list.filter((v) => v.status !== "offline");
    document.getElementById("kpiVehicles").textContent = `${online.length}/${list.length}`;
    document.getElementById("kpiRate").textContent = recentMsgTimes.length;
    const temps = online.map((v) => Number(v.latest.cargoTempC));
    document.getElementById("kpiTemp").textContent = temps.length ? (temps.reduce((a, b) => a + b, 0) / temps.length).toFixed(1) + "°C" : "--";
    document.getElementById("kpiAlerts").textContent = alerts.filter((a) => now - a.ts < 15 * 60000).length;
    document.getElementById("kpiLatency").textContent = lastLatency === null ? "--" : lastLatency < 1000 ? Math.max(0, lastLatency) + " ms" : (lastLatency / 1000).toFixed(2) + " s";
  }

  // ------------------------------------------------------------ data in
  function applyReading(d, anomalies, forecast) {
    if (!d || !d.vehicleId) return;
    const id = d.vehicleId;
    const v = vehicles[id] || (vehicles[id] = { history: [] });
    v.latest = Object.assign({}, d, { forecastCargoTempC: forecast });
    v.status = statusOf(anomalies);
    v.lastSeen = Date.now();
    v.history.push({ ts: d.ts, cargoTempC: d.cargoTempC });
    if (v.history.length > 300) v.history.shift();
    upsertMarker(v);
  }

  function handleMessage(msg) {
    const items = msg.type === "batch" ? msg.items : msg.type === "telemetry" ? [msg] : [];
    let fresh = 0;
    items.forEach((m) => {
      applyReading(m.data, m.anomalies, m.forecastCargoTempC);
      recentMsgTimes.push(Date.now());
      lastLatency = Date.now() - Number(m.data.ts);
      // only alerts that just started (the cloud de-duplicates repeats)
      (m.newAlerts || m.anomalies || []).forEach((a) => {
        alerts.unshift(Object.assign({}, a, { vehicleId: m.data.vehicleId, ts: m.data.ts }));
        fresh++;
      });
    });
    if (alerts.length > 200) alerts = alerts.slice(0, 200);
    if (items.length) {
      renderTable();
      if (fresh) renderAlerts(fresh);
      if (selected && items.some((m) => m.data.vehicleId === selected)) renderChart();
      if (!selected) selectVehicle(Object.keys(vehicles).sort()[0]);
      renderKpis();
    }
  }

  async function initialLoad() {
    try {
      const [latest, al] = await Promise.all([
        fetch(`${API}/latest`).then((r) => r.json()),
        fetch(`${API}/alerts?limit=60`).then((r) => r.json()),
      ]);
      (latest.vehicles || []).forEach((d) => {
        applyReading(d, d.anomalies, d.forecastCargoTempC);
        const v = vehicles[d.vehicleId];
        v.lastSeen = Number(d.ts) || 0;   // so old trucks show as offline
      });
      alerts = al.alerts || [];
      renderTable(); renderAlerts(0); renderKpis();
    } catch (e) {
      console.warn("Initial REST load failed (is the API URL right?)", e);
    }
  }

  // ------------------------------------------------------------ websocket
  let ws, retry = 0, pingTimer;
  function setConn(state, text) {
    const c = document.getElementById("conn");
    c.className = "conn " + state;
    document.getElementById("connText").textContent = text;
  }
  function connect() {
    if (!CFG.wsUrl) { setConn("offline", "No wsUrl in config.js"); return; }
    ws = new WebSocket(CFG.wsUrl);
    ws.onopen = () => {
      retry = 0; setConn("online", "Live");
      clearInterval(pingTimer);
      // API Gateway closes idle sockets after 10 min - ping every 5 min
      pingTimer = setInterval(() => ws.readyState === 1 && ws.send(JSON.stringify({ action: "ping" })), 5 * 60000);
    };
    ws.onmessage = (e) => { try { handleMessage(JSON.parse(e.data)); } catch (err) { console.warn(err); } };
    ws.onclose = () => {
      clearInterval(pingTimer);
      const wait = Math.min(30000, 1000 * 2 ** retry++);
      setConn("offline", `Disconnected - retrying in ${Math.round(wait / 1000)}s`);
      setTimeout(connect, wait);
    };
    ws.onerror = () => ws.close();
  }

  // mark trucks offline if they stop reporting
  setInterval(() => {
    const now = Date.now();
    let changed = false;
    Object.values(vehicles).forEach((v) => {
      if (now - v.lastSeen > OFFLINE_AFTER_MS && v.status !== "offline") {
        v.status = "offline"; changed = true;
        if (v.marker) v.marker.setStyle({ fillColor: COLORS.offline });
      }
    });
    if (changed) renderTable();
    renderKpis();
  }, 5000);

  document.getElementById("modeText").textContent = CFG.mode || "unknown";
  initialLoad().then(connect);
})();
