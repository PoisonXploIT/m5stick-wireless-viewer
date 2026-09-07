"""Vista de detalle de red: /network?bssid=...

Consume GET /api/networks/{bssid} (ya existe desde la Fase 3) con polling
cada 5 s: no usa SSE porque el detalle es consulta bajo demanda y el
histórico solo crece cuando la red esta activa.

La grafica de evolucion RSSI es SVG generado a mano, sin dependencias
(mismo criterio que las barras de canal del dashboard: el proyecto no
arrastra CDN ni build step; usarlo en campo sin internet tiene que funcionar).
"""

// <reference lib="dom" />

(function () {
  "use strict";

  const POLL_MS = 5000;
  const HISTORY_MAX = 200;

  const params = new URLSearchParams(window.location.search);
  const bssid = (params.get("bssid") || "").trim();

  const stateEl = document.getElementById("detail-state");

  function el(id) {
    return document.getElementById(id);
  }

  function fmtTime(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleTimeString("es-ES", { hour12: false });
  }

  // "hace 2 min" con la hora exacta en el title (item de mejora UI nº 3).
  function timeAgo(iso) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return { text: "—", title: "" };
    const secs = Math.max(0, Math.round((Date.now() - d.getTime()) / 1000));
    let text;
    if (secs < 60) text = `hace ${secs} s`;
    else if (secs < 3600) text = `hace ${Math.floor(secs / 60)} min`;
    else if (secs < 86400) text = `hace ${Math.floor(secs / 3600)} h`;
    else text = `hace ${Math.floor(secs / 86400)} d`;
    return { text, title: d.toLocaleString("es-ES", { hour12: false }) };
  }

  function setState(ok, text) {
    stateEl.textContent = text;
    stateEl.className = `status ${ok ? "status-online" : "status-warn"}`;
  }

  function renderInfo(d) {
    el("detail-ssid").textContent = d.ssid || "(SSID oculto)";
    el("info-bssid").textContent = d.bssid;
    el("info-channel").textContent = d.channel === null ? "—" : String(d.channel);
    el("info-rssi").textContent = d.rssi === null ? "—" : `${d.rssi} dBm`;
    el("info-clients").textContent = String(d.clients.length);

    const first = timeAgo(d.first_seen);
    el("info-first").textContent = first.text;
    el("info-first").title = first.title;
    const last = timeAgo(d.last_seen);
    el("info-last").textContent = last.text;
    el("info-last").title = last.title;
  }

  function renderClients(clients) {
    const tbody = el("clients-body");
    tbody.replaceChildren();
    el("clients-empty").hidden = clients.length > 0;
    const frag = document.createDocumentFragment();
    for (const c of clients) {
      const tr = document.createElement("tr");
      const mac = document.createElement("td");
      mac.className = "mono";
      mac.textContent = c.mac;
      const first = document.createElement("td");
      first.className = "time";
      first.textContent = fmtTime(c.first_seen);
      const last = document.createElement("td");
      last.className = "time";
      last.textContent = fmtTime(c.last_seen);
      tr.append(mac, first, last);
      frag.appendChild(tr);
    }
    tbody.appendChild(frag);
  }

  function renderHistory(history) {
    const tbody = el("history-body");
    tbody.replaceChildren();
    el("history-empty").hidden = history.length > 0;
    // Mas reciente primero; tope para no degradar con capturas largas.
    const rows = history.slice(-HISTORY_MAX).reverse();
    el("history-count").textContent =
      history.length > HISTORY_MAX ? `${HISTORY_MAX} de ${history.length}` : String(history.length);
    const frag = document.createDocumentFragment();
    for (const h of rows) {
      const tr = document.createElement("tr");
      const cells = [
        [fmtTime(h.timestamp), "time"],
        [h.event_type, "mono"],
        [h.firmware, ""],
        [h.source, ""],
        [h.rssi === null ? "—" : `${h.rssi} dBm`, "mono num"],
        [h.client_mac || "—", "mono"],
      ];
      for (const [text, cls] of cells) {
        const td = document.createElement("td");
        td.textContent = text;
        if (cls) td.className = cls;
        tr.appendChild(td);
      }
      frag.appendChild(tr);
    }
    tbody.appendChild(frag);
    renderRssiChart(history);
  }

  // SVG en linea: (timestamp, rssi) del historico, orden cronologico.
  function renderRssiChart(history) {
    const wrap = el("rssi-chart");
    wrap.replaceChildren();
    const points = history
      .filter((h) => h.rssi !== null)
      .map((h) => ({ t: new Date(h.timestamp).getTime(), rssi: h.rssi }));
    el("rssi-empty").hidden = points.length > 0;
    if (points.length < 2) return;

    points.sort((a, b) => a.t - b.t);
    const W = 640;
    const H = 180;
    const PAD_L = 44;
    const PAD_R = 12;
    const PAD_T = 12;
    const PAD_B = 26;
    const rssiMin = Math.min(...points.map((p) => p.rssi));
    const rssiMax = Math.max(...points.map((p) => p.rssi));
    const tMin = points[0].t;
    const tMax = points[points.length - 1].t;
    const span = Math.max(1, tMax - tMin);
    // Rango Y con margen; si todo el RSSI es constante, abrir +-2 dBm.
    const yLo = rssiMin === rssiMax ? rssiMin - 2 : rssiMin;
    const yHi = rssiMin === rssiMax ? rssiMax + 2 : rssiMax;
    const x = (t) => PAD_L + ((t - tMin) / span) * (W - PAD_L - PAD_R);
    const y = (r) => PAD_T + (1 - (r - yLo) / (yHi - yLo)) * (H - PAD_T - PAD_B);

    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("class", "chart");

    const grid = document.createElementNS(svg.namespaceURI, "g");
    for (const rssi of [yLo, yHi]) {
      const line = document.createElementNS(svg.namespaceURI, "line");
      line.setAttribute("x1", String(PAD_L));
      line.setAttribute("x2", String(W - PAD_R));
      line.setAttribute("y1", String(y(rssi)));
      line.setAttribute("y2", String(y(rssi)));
      line.setAttribute("class", "chart-grid");
      grid.appendChild(line);
      const label = document.createElementNS(svg.namespaceURI, "text");
      label.setAttribute("x", String(PAD_L - 6));
      label.setAttribute("y", String(y(rssi) + 4));
      label.setAttribute("text-anchor", "end");
      label.setAttribute("class", "chart-label");
      label.textContent = `${Math.round(rssi)}`;
      grid.appendChild(label);
    }
    svg.appendChild(grid);

    const polyline = document.createElementNS(svg.namespaceURI, "polyline");
    polyline.setAttribute(
      "points",
      points.map((p) => `${x(p.t).toFixed(1)},${y(p.rssi).toFixed(1)}`).join(" ")
    );
    polyline.setAttribute("class", "chart-line");
    svg.appendChild(polyline);

    for (const [t, text, anchor] of [
      [tMin, fmtTime(new Date(tMin).toISOString()), "start"],
      [tMax, fmtTime(new Date(tMax).toISOString()), "end"],
    ]) {
      const label = document.createElementNS(svg.namespaceURI, "text");
      label.setAttribute("x", String(x(t)));
      label.setAttribute("y", String(H - 6));
      label.setAttribute("text-anchor", anchor);
      label.setAttribute("class", "chart-label");
      label.textContent = text;
      svg.appendChild(label);
    }
    wrap.appendChild(svg);
  }

  async function load() {
    let res;
    try {
      res = await fetch(`/api/networks/${encodeURIComponent(bssid)}`);
    } catch (_err) {
      setState(false, "sin conexión con la API");
      return;
    }
    if (res.status === 404) {
      setState(false, `red desconocida: ${bssid}`);
      document.title = "m5wireless — red desconocida";
      return;
    }
    if (!res.ok) {
      setState(false, `error de API (${res.status})`);
      return;
    }
    const d = await res.json();
    renderInfo(d);
    renderClients(d.clients);
    renderHistory(d.history);
    setState(true, `${d.history.length} observaciones`);
  }

  if (!bssid) {
    setState(false, "falta el parámetro ?bssid=");
    return;
  }
  load();
  window.setInterval(load, POLL_MS);
})();
