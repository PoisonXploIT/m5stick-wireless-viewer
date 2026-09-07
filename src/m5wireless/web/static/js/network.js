// Vista de detalle de red: /network?bssid=...
//
// Consume GET /api/networks/{bssid} (ya existe desde la Fase 3) con polling
// cada 5 s: no usa SSE porque el detalle es consulta bajo demanda y el
// historico solo crece cuando la red esta activa.
//
// Graficas en SVG propio, sin dependencias (mismo criterio que el dashboard:
// sin CDN ni build step; en campo sin internet tiene que funcionar):
// - Evolucion RSSI: linea + area degradada + puntos + crosshair con tooltip.
// - Actividad temporal: histograma de observaciones por bucket de tiempo.

// <reference lib="dom" />

(function () {
  "use strict";

  const POLL_MS = 5000;
  const HISTORY_MAX = 200;
  const ACTIVITY_BUCKETS = 30;

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
    const ssid = d.ssid || "(SSID oculto)";
    el("detail-ssid").textContent = ssid;
    el("crumb-ssid").textContent = ssid;
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

  // Color semantico por tipo de evento del historico (B2).
  const EVENT_CLASS = {
    network_seen: "ev-net",
    client_associated: "ev-client",
  };

  function renderHistory(history) {
    const tbody = el("history-body");
    tbody.replaceChildren();
    el("history-empty").hidden = history.length > 0;
    // Mas reciente primero; tope para no degradar con capturas largas.
    const rows = history.slice(-HISTORY_MAX).reverse();
    el("history-count").textContent =
      history.length > HISTORY_MAX
        ? `${HISTORY_MAX} de ${history.length}`
        : String(history.length);
    const frag = document.createDocumentFragment();
    for (const h of rows) {
      const tr = document.createElement("tr");
      const time = document.createElement("td");
      time.className = "time";
      time.textContent = fmtTime(h.timestamp);
      const ev = document.createElement("td");
      const badge = document.createElement("span");
      badge.className = `badge event-badge ${EVENT_CLASS[h.event_type] || "ev-other"}`;
      badge.textContent = h.event_type;
      ev.appendChild(badge);
      const fw = document.createElement("td");
      fw.textContent = h.firmware;
      const src = document.createElement("td");
      src.textContent = h.source;
      const rssi = document.createElement("td");
      rssi.className = "mono num";
      rssi.textContent = h.rssi === null ? "—" : `${h.rssi} dBm`;
      const client = document.createElement("td");
      client.className = "mono";
      client.textContent = h.client_mac || "—";
      tr.append(time, ev, fw, src, rssi, client);
      frag.appendChild(tr);
    }
    tbody.appendChild(frag);
    renderRssiChart(history);
    renderActivityChart(history);
  }

  const SVG_NS = "http://www.w3.org/2000/svg";

  function svgEl(name, attrs) {
    const node = document.createElementNS(SVG_NS, name);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
    return node;
  }

  // Dimensiones comunes de las graficas.
  const CH = { W: 640, H: 180, PL: 44, PR: 12, PT: 12, PB: 26 };

  // SVG en linea: (timestamp, rssi) del historico, orden cronologico.
  // Area degradada + puntos + crosshair con tooltip (B2).
  function renderRssiChart(history) {
    const wrap = el("rssi-chart");
    wrap.replaceChildren();
    const points = history
      .filter((h) => h.rssi !== null)
      .map((h) => ({ t: new Date(h.timestamp).getTime(), rssi: h.rssi }))
      .sort((a, b) => a.t - b.t);
    el("rssi-empty").hidden = points.length > 0;
    if (points.length < 2) return;

    const { W, H, PL, PR, PT, PB } = CH;
    const rssiMin = Math.min(...points.map((p) => p.rssi));
    const rssiMax = Math.max(...points.map((p) => p.rssi));
    const tMin = points[0].t;
    const tMax = points[points.length - 1].t;
    const span = Math.max(1, tMax - tMin);
    // Rango Y con margen; si todo el RSSI es constante, abrir +-2 dBm.
    const yLo = rssiMin === rssiMax ? rssiMin - 2 : rssiMin;
    const yHi = rssiMin === rssiMax ? rssiMax + 2 : rssiMax;
    const x = (t) => PL + ((t - tMin) / span) * (W - PL - PR);
    const y = (r) => PT + (1 - (r - yLo) / (yHi - yLo)) * (H - PT - PB);
    const coords = points.map((p) => ({ px: x(p.t), py: y(p.rssi), ...p }));

    const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart" });

    const defs = svgEl("defs", {});
    const grad = svgEl("linearGradient", {
      id: "rssi-area-grad",
      x1: "0",
      y1: "0",
      x2: "0",
      y2: "1",
    });
    grad.append(
      svgEl("stop", { offset: "0%", "stop-color": "#4c8dff", "stop-opacity": "0.28" }),
      svgEl("stop", { offset: "100%", "stop-color": "#4c8dff", "stop-opacity": "0.02" })
    );
    defs.appendChild(grad);
    svg.appendChild(defs);

    for (const rssi of [yLo, yHi]) {
      svg.appendChild(
        svgEl("line", {
          x1: PL,
          x2: W - PR,
          y1: y(rssi),
          y2: y(rssi),
          class: "chart-grid",
        })
      );
      const label = svgEl("text", {
        x: PL - 6,
        y: y(rssi) + 4,
        "text-anchor": "end",
        class: "chart-label",
      });
      label.textContent = `${Math.round(rssi)}`;
      svg.appendChild(label);
    }

    const area = svgEl("polygon", {
      points: `${PL},${H - PB} ${coords.map((c) => `${c.px.toFixed(1)},${c.py.toFixed(1)}`).join(" ")} ${(W - PR).toFixed(1)},${H - PB}`,
      fill: "url(#rssi-area-grad)",
    });
    svg.appendChild(area);

    svg.appendChild(
      svgEl("polyline", {
        points: coords.map((c) => `${c.px.toFixed(1)},${c.py.toFixed(1)}`).join(" "),
        class: "chart-line",
      })
    );

    for (const c of coords) {
      svg.appendChild(svgEl("circle", { cx: c.px, cy: c.py, r: 2.2, class: "chart-dot" }));
    }

    for (const [t, text, anchor] of [
      [tMin, fmtTime(new Date(tMin).toISOString()), "start"],
      [tMax, fmtTime(new Date(tMax).toISOString()), "end"],
    ]) {
      const label = svgEl("text", {
        x: x(t),
        y: H - 6,
        "text-anchor": anchor,
        class: "chart-label",
      });
      label.textContent = text;
      svg.appendChild(label);
    }

    // Crosshair + tooltip: sigue el punto mas cercano en X.
    const crosshair = svgEl("line", {
      y1: PT,
      y2: H - PB,
      class: "chart-crosshair",
      visibility: "hidden",
    });
    svg.appendChild(crosshair);
    wrap.appendChild(svg);

    const tooltip = document.createElement("div");
    tooltip.className = "chart-tooltip";
    tooltip.hidden = true;
    wrap.appendChild(tooltip);

    svg.addEventListener("mousemove", (ev) => {
      const rect = svg.getBoundingClientRect();
      // Coordenada en el viewBox (la pantalla puede escalar el SVG).
      const sx = ((ev.clientX - rect.left) / rect.width) * W;
      let best = null;
      for (const c of coords) {
        if (best === null || Math.abs(c.px - sx) < Math.abs(best.px - sx)) best = c;
      }
      if (!best) return;
      crosshair.setAttribute("x1", best.px);
      crosshair.setAttribute("x2", best.px);
      crosshair.setAttribute("visibility", "visible");
      tooltip.hidden = false;
      tooltip.innerHTML = "";
      const t1 = document.createElement("div");
      t1.className = "tt-strong";
      t1.textContent = `${best.rssi} dBm`;
      const t2 = document.createElement("div");
      t2.textContent = new Date(best.t).toLocaleString("es-ES", { hour12: false });
      tooltip.append(t1, t2);
      // Posicion: cerca del punto, sin salirse de la grafica.
      const leftPct = (best.px / W) * 100;
      tooltip.style.left = `${Math.min(78, Math.max(2, leftPct))}%`;
    });
    svg.addEventListener("mouseleave", () => {
      crosshair.setAttribute("visibility", "hidden");
      tooltip.hidden = true;
    });
  }

  // Histograma de observaciones (todas, con o sin RSSI) por bucket de tiempo.
  function renderActivityChart(history) {
    const wrap = el("activity-chart");
    wrap.replaceChildren();
    el("activity-empty").hidden = history.length > 0;
    if (history.length === 0) return;

    const { W, H, PL, PR, PT, PB } = CH;
    const ts = history.map((h) => new Date(h.timestamp).getTime());
    const tMin = Math.min(...ts);
    const tMax = Math.max(...ts);
    const span = Math.max(1, tMax - tMin);
    const buckets = new Array(ACTIVITY_BUCKETS).fill(0);
    for (const t of ts) {
      const i = Math.min(
        ACTIVITY_BUCKETS - 1,
        Math.floor(((t - tMin) / span) * ACTIVITY_BUCKETS)
      );
      buckets[i] += 1;
    }
    const max = Math.max(...buckets, 1);
    const innerW = W - PL - PR;
    const innerH = H - PT - PB;
    const barW = innerW / ACTIVITY_BUCKETS;

    const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart" });
    svg.appendChild(
      svgEl("line", { x1: PL, x2: W - PR, y1: H - PB, y2: H - PB, class: "chart-grid" })
    );
    for (let i = 0; i < ACTIVITY_BUCKETS; i++) {
      if (buckets[i] === 0) continue;
      const h = (buckets[i] / max) * (innerH - 4);
      svg.appendChild(
        svgEl("rect", {
          x: PL + i * barW + 1,
          y: H - PB - h,
          width: Math.max(1, barW - 2),
          height: h,
          rx: 1.5,
          class: "chart-bar",
        })
      );
    }
    for (const [t, text, anchor] of [
      [tMin, fmtTime(new Date(tMin).toISOString()), "start"],
      [tMax, fmtTime(new Date(tMax).toISOString()), "end"],
    ]) {
      const label = svgEl("text", {
        x: PL + (anchor === "start" ? 0 : innerW),
        y: H - 6,
        "text-anchor": anchor,
        class: "chart-label",
      });
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
  el("copy-bssid").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(bssid);
    } catch (_err) {
      // portapapeles no disponible (permiso denegado o contexto inseguro).
    }
  });
  load();
  window.setInterval(load, POLL_MS);
})();
