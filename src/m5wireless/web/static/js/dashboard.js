// m5wireless dashboard (Fase 4 + Plan UI/UX v2, Hitos A-C): vanilla JS, sin frameworks.
// - SSE en /api/events con reconexion controlada (sin polling).
// - Actualizacion incremental: cada evento parchea solo la fila afectada;
//   render completo solo al cambiar filtro/orden.
// - Filtros en cliente con persistencia (localStorage) y chips de activos.
// - Sparklines de actividad (10 min) y heatmap de canales por banda, SVG/CSS propios.
// - Hito C: skeletons de carga, empty states con CTA, toasts de errores,
//   stats del pipeline (/api/health) y anunciador accesible de la consola.

(() => {
  "use strict";

  const MAX_CONSOLE_LINES = 500;
  const RECONNECT_MS = 3000;
  const CONSOLE_LIMIT = 200;
  const STATUS_POLL_MS = 5000;
  const SPARK_WINDOW_MS = 10 * 60 * 1000; // 10 min de actividad
  const SPARK_BUCKETS = 30;
  const LIVE_THRESHOLD_MS = 30 * 1000; // "activa ahora" si se vio hace <30 s
  const FILTERS_STORAGE_KEY = "m5wireless.filters";
  const SKELETON_ROWS = 6;
  const TOAST_MS = 5000;
  const TOAST_DEDUPE_MS = 8000; // anti-spam: mismo mensaje dentro de esta ventana se descarta

  // ---- estado ----
  const networks = new Map(); // bssid -> {bssid, ssid, channel, rssi, last_seen}
  const clients = new Map(); // mac -> {mac, bssid}
  // Ventana de actividad para las sparklines: timestamps de eventos recientes.
  const activity = { networks: [], clients: [] };
  let sortKey = "last_seen";
  let sortDir = "desc";
  let es = null; // EventSource actual
  let reconnectTimer = null;
  let hadDisconnect = false; // para anunciar la reconexion una sola vez

  // ---- DOM ----
  const $ = (id) => document.getElementById(id);
  const tbody = $("networks-body");
  const consoleEl = $("console");
  const channels24El = $("channels-24");
  const channels5El = $("channels-5");
  const statusEl = $("sse-status");
  const connStatusEl = $("conn-status");
  const filterText = $("filter-text");
  const filterChannel = $("filter-channel");
  const filterRssi = $("filter-rssi");
  const filterClients = $("filter-clients");
  const chipsEl = $("filter-chips");

  // ---- utilidades ----

  function rssiClass(rssi) {
    if (rssi === null || rssi === undefined) return "";
    if (rssi >= -60) return "rssi-good";
    if (rssi >= -80) return "rssi-mid";
    return "rssi-bad";
  }

  function fmtTime(iso) {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString("es-ES", { hour12: false });
  }

  function countClientsFor(bssid) {
    let n = 0;
    for (const c of clients.values()) if (c.bssid === bssid) n += 1;
    return n;
  }

  async function fetchJSON(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`${url} -> ${res.status}`);
    return res.json();
  }

  // ---- toasts (C2): errores y avisos que antes eran silencio total ----

  let lastToast = { msg: "", t: 0 };

  function showToast(message, type = "info") {
    const now = Date.now();
    if (message === lastToast.msg && now - lastToast.t < TOAST_DEDUPE_MS) return;
    lastToast = { msg: message, t: now };
    const box = $("toasts");
    if (!box) return;
    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    box.appendChild(toast);
    while (box.children.length > 4) box.firstElementChild.remove();
    setTimeout(() => {
      toast.classList.add("toast-out");
      toast.addEventListener("animationend", () => toast.remove(), { once: true });
    }, TOAST_MS);
  }

  // Anunciador accesible (C3): la consola en si es aria-live=off (alta
  // frecuencia); los cambios de estado se anuncian por aqui.
  function announce(msg) {
    const el = $("console-announcer");
    if (el) el.textContent = msg;
  }

  // ---- estado: upserts ----

  function upsertNetwork(data) {
    const prev = networks.get(data.bssid);
    networks.set(data.bssid, {
      bssid: data.bssid,
      ssid: data.ssid !== null && data.ssid !== undefined ? data.ssid : (prev ? prev.ssid : null),
      channel: data.channel !== null && data.channel !== undefined ? data.channel : (prev ? prev.channel : null),
      rssi: data.rssi !== null && data.rssi !== undefined ? data.rssi : (prev ? prev.rssi : null),
      // SSE trae `timestamp`; /api/networks trae `last_seen`.
      last_seen: data.last_seen || data.timestamp,
    });
  }

  function upsertClient(data) {
    const prev = clients.get(data.mac);
    clients.set(data.mac, {
      mac: data.mac,
      bssid: data.bssid !== null && data.bssid !== undefined ? data.bssid : (prev ? prev.bssid : null),
    });
  }

  function trackActivity(bucket, ts) {
    const t = new Date(ts).getTime();
    if (Number.isNaN(t)) return;
    bucket.push(t);
    const cutoff = Date.now() - SPARK_WINDOW_MS;
    while (bucket.length > 0 && bucket[0] < cutoff) bucket.shift();
  }

  // ---- consola ----
  // Entradas tipadas: {ts, type, text}. En pausa, las nuevas lineas se
  // acumulan en `pendingLines` y el badge las cuenta (B1).
  const consoleLines = [];
  const pendingLines = [];
  let consolePaused = false;
  const consoleTypeClass = {
    network_seen: "txt-net",
    client_associated: "txt-client",
    status: "txt-status",
  };

  function appendConsoleLine(entry) {
    if (!entry || !entry.text) return;
    if (consolePaused) {
      pendingLines.push(entry);
      const badge = $("console-pending");
      badge.textContent = String(pendingLines.length);
      badge.hidden = false;
      return;
    }
    renderConsoleLine(entry);
  }

  function renderConsoleLine(entry) {
    const nearBottom =
      consoleEl.scrollHeight - consoleEl.scrollTop - consoleEl.clientHeight < 40;
    consoleLines.push(entry);
    if (consoleLines.length > MAX_CONSOLE_LINES) {
      consoleLines.shift();
      if (consoleEl.firstElementChild) consoleEl.firstElementChild.remove();
    }
    consoleEl.appendChild(consoleLineEl(entry));
    if (nearBottom) consoleEl.scrollTop = consoleEl.scrollHeight;
  }

  function consoleLineEl(entry) {
    const div = document.createElement("div");
    div.className = "console-line";
    const ts = document.createElement("span");
    ts.className = "ts";
    ts.textContent = entry.ts ? fmtTime(entry.ts) : "--:--:--";
    const txt = document.createElement("span");
    txt.className = consoleTypeClass[entry.type] || "txt-net";
    txt.textContent = entry.text;
    div.append(ts, txt);
    return div;
  }

  function resumeConsole() {
    while (pendingLines.length > 0) renderConsoleLine(pendingLines.shift());
    const badge = $("console-pending");
    badge.hidden = true;
    badge.textContent = "";
  }

  function bindConsoleControls() {
    const pauseBtn = $("console-pause");
    const pauseLabel = $("console-pause-label");
    pauseBtn.addEventListener("click", () => {
      consolePaused = !consolePaused;
      pauseLabel.textContent = consolePaused ? "Reanudar" : "Pausar";
      announce(consolePaused ? "Consola en pausa" : "Consola reanudada");
      if (!consolePaused) resumeConsole();
    });
    $("console-clear").addEventListener("click", () => {
      consoleLines.length = 0;
      pendingLines.length = 0;
      consoleEl.textContent = "";
      const badge = $("console-pending");
      badge.hidden = true;
      badge.textContent = "";
      announce("Consola vaciada");
    });
    $("console-copy").addEventListener("click", async () => {
      const text = consoleLines.map((l) => l.text).join("\n");
      if (!text) return;
      try {
        await navigator.clipboard.writeText(text);
        showToast("Consola copiada al portapapeles", "info");
      } catch (_err) {
        // portapapeles no disponible (http no-localhost o permiso denegado).
        showToast("No se pudo copiar: permiso denegado", "warn");
      }
    });
  }

  // ---- tabla: render completo e incremental ----

  function rowValues(net) {
    return {
      ssid: net.ssid || "",
      bssid: net.bssid,
      channel: net.channel,
      rssi: net.rssi,
      n_clients: countClientsFor(net.bssid),
      last_seen: net.last_seen,
    };
  }

  function cmpValues(a, b) {
    // null siempre va al final, sea el orden que sea.
    if (a === null || a === undefined) return b === null || b === undefined ? 0 : 1;
    if (b === null || b === undefined) return -1;
    if (typeof a === "string" && typeof b === "string") return a.localeCompare(b);
    return a < b ? -1 : a > b ? 1 : 0;
  }

  function matchesFilters(v) {
    const text = filterText.value.trim().toLowerCase();
    if (text !== "") {
      const hay = `${v.ssid} ${v.bssid}`.toLowerCase();
      if (!hay.includes(text)) return false;
    }
    const channel = filterChannel.value === "" ? null : Number(filterChannel.value);
    if (channel !== null && v.channel !== channel) return false;
    const minRssiRaw = filterRssi.value.trim();
    const minRssi = minRssiRaw === "" ? null : Number(minRssiRaw);
    if (minRssi !== null && (v.rssi === null || v.rssi < minRssi)) return false;
    if (filterClients.checked && v.n_clients === 0) return false;
    return true;
  }

  function visibleRows() {
    const rows = [];
    for (const net of networks.values()) {
      const v = rowValues(net);
      if (matchesFilters(v)) rows.push(v);
    }

    const dir = sortDir === "asc" ? 1 : -1;
    rows.sort((x, y) => {
      const c = cmpValues(x[sortKey], y[sortKey]);
      return c !== 0 ? c * dir : x.bssid.localeCompare(y.bssid);
    });
    return rows;
  }

  // Pone el texto en un span.badge dentro de la celda (un <td> con
  // display:inline-block romperia el layout de la fila).
  function badgeCell(td, text, cls) {
    let span = td.firstElementChild;
    if (!span || span.tagName !== "SPAN") {
      td.textContent = "";
      span = document.createElement("span");
      td.appendChild(span);
    }
    span.className = `badge ${cls || ""}`;
    span.textContent = text;
  }

  // Celda RSSI: barra de senal mini + valor (A5).
  function signalCell(td, rssi) {
    td.textContent = "";
    td.className = "mono";
    if (rssi === null || rssi === undefined) {
      td.textContent = "—";
      return;
    }
    const wrap = document.createElement("span");
    wrap.className = "signal";
    const track = document.createElement("span");
    track.className = "signal-track";
    const fill = document.createElement("span");
    fill.className = `signal-fill ${rssiClass(rssi)}`.trim();
    // -100..0 dBm -> 5..100% de la barra.
    fill.style.width = `${Math.max(5, Math.min(100, Math.round(((rssi + 100) / 100) * 100)))}%`;
    track.appendChild(fill);
    const value = document.createElement("span");
    value.textContent = `${rssi} dBm`;
    wrap.append(track, value);
    td.appendChild(wrap);
  }

  // Celda "ultima vista": punto pulsante si la red esta activa (A5).
  function timeCell(td, iso) {
    td.textContent = "";
    td.className = "time";
    const live =
      iso && Date.now() - new Date(iso).getTime() < LIVE_THRESHOLD_MS;
    if (live) {
      const wrap = document.createElement("span");
      wrap.className = "cell-live";
      const dot = document.createElement("span");
      dot.className = "live-dot";
      dot.title = "Activa ahora";
      const t = document.createElement("span");
      t.textContent = fmtTime(iso);
      wrap.append(dot, t);
      td.appendChild(wrap);
    } else {
      td.textContent = fmtTime(iso);
    }
  }

  function renderRow(v) {
    const tr = document.createElement("tr");
    tr.dataset.bssid = v.bssid;
    const cells = [
      v.ssid || "—",
      v.bssid,
      v.channel === null ? "—" : String(v.channel),
      null, // RSSI: signalCell
      String(v.n_clients),
      null, // hora: timeCell
    ];
    cells.forEach((text, i) => {
      const td = document.createElement("td");
      if (i === 0) {
        td.textContent = text;
      }
      if (i === 1) {
        // BSSID: enlace a la vista de detalle de la red.
        td.className = "mono";
        const a = document.createElement("a");
        a.href = `/network?bssid=${encodeURIComponent(v.bssid)}`;
        a.textContent = text;
        a.className = "bssid-link";
        td.appendChild(a);
      }
      if (i === 2) badgeCell(td, text);
      if (i === 3) signalCell(td, v.rssi);
      if (i === 4) {
        td.textContent = text;
        td.className = "num";
      }
      if (i === 5) timeCell(td, v.last_seen);
      tr.appendChild(td);
    });
    return tr;
  }

  // Flash visual al insertar/actualizar una fila en vivo (clase CSS row-flash).
  function flashRow(tr) {
    if (!tr) return;
    tr.classList.remove("row-flash");
    void tr.offsetWidth; // reinicia la animacion
    tr.addEventListener("animationend", () => tr.classList.remove("row-flash"), {
      once: true,
    });
    tr.classList.add("row-flash");
  }

  // Icono SVG reutilizable para los empty states (antena sin senal).
  function emptyIcon() {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("width", "34");
    svg.setAttribute("height", "34");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "1.6");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("class", "empty-icon");
    svg.setAttribute("aria-hidden", "true");
    svg.innerHTML =
      '<path d="M5 12.5a10 10 0 0 1 14 0"/><path d="M8.5 16a5.5 5.5 0 0 1 7 0"/><circle cx="12" cy="19" r="1.3" fill="currentColor" stroke="none"/><path d="M3 3l18 18" stroke="#e5534b"/>';
    return svg;
  }

  // Fila de estado vacio (C1): dos variantes —
  //  - sin datos: CTA con el comando para arrancar una fuente;
  //  - con datos pero filtros que lo ocultan todo: boton limpiar filtros.
  function syncEmptyState() {
    const existing = tbody.querySelector("#empty-state-row");
    if (existing) existing.remove();
    if (tbody.querySelectorAll("tr[data-bssid]").length > 0) return;

    const row = document.createElement("tr");
    row.id = "empty-state-row";
    const td = document.createElement("td");
    td.colSpan = 6;
    td.className = "empty-state";

    const box = document.createElement("div");
    box.className = "empty-box";
    box.appendChild(emptyIcon());
    const title = document.createElement("p");
    title.className = "empty-title";
    const hint = document.createElement("p");
    hint.className = "empty-hint";

    if (networks.size === 0) {
      title.textContent = "Aún no hay redes visibles";
      hint.textContent = "Arranca una fuente de captura para empezar:";
      const code = document.createElement("code");
      code.className = "empty-cmd mono";
      code.textContent = "m5wireless run --demo";
      const hint2 = document.createElement("p");
      hint2.className = "empty-hint";
      hint2.textContent = "o con hardware: m5wireless run --source serial --port COM4";
      box.append(title, hint, code, hint2);
    } else {
      title.textContent = "Ninguna red coincide con los filtros";
      hint.textContent = `${networks.size} red(es) oculta(s) por los filtros activos.`;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn btn-sm";
      btn.textContent = "Limpiar filtros";
      btn.addEventListener("click", clearFilters);
      box.append(title, hint, btn);
    }
    td.appendChild(box);
    row.appendChild(td);
    tbody.appendChild(row);
  }

  function clearFilters() {
    filterText.value = "";
    filterChannel.value = "";
    filterRssi.value = "";
    filterClients.checked = false;
    onFilterChanged();
  }

  // Skeletons de carga inicial (C1): filas placeholder mientras llega la
  // primera respuesta del API. El contenido NUNCA depende de animaciones.
  function showSkeletons() {
    const frag = document.createDocumentFragment();
    for (let i = 0; i < SKELETON_ROWS; i++) {
      const tr = document.createElement("tr");
      tr.className = "skeleton-row";
      for (let c = 0; c < 6; c++) {
        const td = document.createElement("td");
        const bar = document.createElement("span");
        bar.className = "skeleton-bar";
        // Anchos variados para que no parezca una tabla de mamposteria.
        bar.style.width = `${55 + ((i * 7 + c * 13) % 40)}%`;
        td.appendChild(bar);
        tr.appendChild(td);
      }
      frag.appendChild(tr);
    }
    tbody.appendChild(frag);
  }

  function hideSkeletons() {
    for (const tr of tbody.querySelectorAll("tr.skeleton-row")) tr.remove();
  }

  function renderTable() {
    tbody.textContent = "";
    const frag = document.createDocumentFragment();
    for (const v of visibleRows()) frag.appendChild(renderRow(v));
    tbody.appendChild(frag);
    applySortHeaders();
    syncEmptyState();
  }

  // Parchea solo la fila de `bssid` si existe y es visible. Devuelve true si
  // la fila estaba oculta/ausente (solo se actualizo el estado).
  function patchRow(bssid) {
    const tr = tbody.querySelector(`tr[data-bssid="${CSS.escape(bssid)}"]`);
    if (!tr || tr.style.display === "none") return false;
    const v = rowValues(networks.get(bssid));
    const tds = tr.children;
    tds[0].textContent = v.ssid || "—";
    badgeCell(tds[2], v.channel === null ? "—" : String(v.channel));
    signalCell(tds[3], v.rssi);
    tds[4].textContent = String(v.n_clients);
    tds[4].className = "num";
    timeCell(tds[5], v.last_seen);
    flashRow(tr);
    return true;
  }

  function applySortHeaders() {
    for (const th of tbody.closest("table").querySelectorAll("th[data-key]")) {
      th.classList.remove("sorted-asc", "sorted-desc");
      if (th.dataset.key === sortKey) {
        th.classList.add(sortDir === "asc" ? "sorted-asc" : "sorted-desc");
      }
    }
  }

  // ---- contadores, sparklines y distribucion por canal ----

  // Sparkline SVG (linea + area) a partir de timestamps en la ventana de 10 min.
  function renderSpark(el, timestamps) {
    el.textContent = "";
    const now = Date.now();
    const cutoff = now - SPARK_WINDOW_MS;
    const buckets = new Array(SPARK_BUCKETS).fill(0);
    for (const t of timestamps) {
      if (t < cutoff) continue;
      const i = Math.min(
        SPARK_BUCKETS - 1,
        Math.floor(((t - cutoff) / SPARK_WINDOW_MS) * SPARK_BUCKETS)
      );
      buckets[i] += 1;
    }
    const W = 120;
    const H = 26;
    const max = Math.max(...buckets, 1);
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    const stepX = W / SPARK_BUCKETS;
    const pts = buckets.map((n, i) => {
      const px = (i + 0.5) * stepX;
      const py = n === 0 ? H - 1 : H - 2 - (n / max) * (H - 6);
      return `${px.toFixed(1)},${py.toFixed(1)}`;
    });
    const area = document.createElementNS(svg.namespaceURI, "polygon");
    area.setAttribute("points", `0,${H} ${pts.join(" ")} ${W},${H}`);
    area.setAttribute("class", "spark-area");
    const line = document.createElementNS(svg.namespaceURI, "polyline");
    line.setAttribute("points", pts.join(" "));
    line.setAttribute("class", "spark-line");
    svg.append(area, line);
    el.appendChild(svg);
  }

  function updateCounters() {
    $("count-networks").textContent = String(networks.size);
    $("count-clients").textContent = String(clients.size);
    renderSpark($("spark-networks"), activity.networks);
    renderSpark($("spark-clients"), activity.clients);
  }

  // Color por ocupacion absoluta: con pocas redes el ratio al maximo de la
  // banda pintaria todo "alta" (enganoso); los umbrales absolutos reflejan
  // congestion real (A7).
  function bandClass(n) {
    if (n >= 6) return "ch-high";
    if (n >= 3) return "ch-mid";
    return "ch-low";
  }

  function renderBand(el, entries, max) {
    el.textContent = "";
    if (entries.length === 0) {
      const li = document.createElement("li");
      const label = document.createElement("span");
      label.className = "ch-num";
      label.textContent = "—";
      const note = document.createElement("span");
      note.className = "ch-count";
      note.style.gridColumn = "2 / 4";
      note.style.textAlign = "left";
      note.textContent = "sin datos";
      li.append(label, note);
      el.appendChild(li);
      return;
    }
    for (const [ch, n] of entries) {
      const li = document.createElement("li");
      const label = document.createElement("span");
      label.className = "ch-num";
      label.textContent = String(ch);
      const track = document.createElement("div");
      track.className = "bar-track";
      const fill = document.createElement("div");
      fill.className = `bar-fill ${bandClass(n, max)}`;
      fill.style.width = `${Math.max(4, Math.round((n / max) * 100))}%`;
      track.appendChild(fill);
      const count = document.createElement("span");
      count.className = "ch-count";
      count.textContent = String(n);
      li.append(label, track, count);
      el.appendChild(li);
    }
  }

  function renderChannels() {
    const dist = {};
    for (const net of networks.values()) {
      if (net.channel !== null) dist[net.channel] = (dist[net.channel] || 0) + 1;
    }
    const entries = Object.entries(dist).map(([ch, n]) => [Number(ch), n]).sort((a, b) => a[0] - b[0]);
    const band24 = entries.filter(([ch]) => ch >= 1 && ch <= 14);
    const band5 = entries.filter(([ch]) => ch > 14);
    renderBand(channels24El, band24, band24.length ? Math.max(...band24.map(([, n]) => n)) : 1);
    renderBand(channels5El, band5, band5.length ? Math.max(...band5.map(([, n]) => n)) : 1);

    // Opciones del filtro de canal (union con los ya existentes).
    const existing = new Set(
      [...filterChannel.options].map((o) => o.value).filter((v) => v !== "")
    );
    for (const [ch] of entries) {
      if (!existing.has(String(ch))) {
        const opt = document.createElement("option");
        opt.value = String(ch);
        opt.textContent = `Canal ${ch}`;
        filterChannel.appendChild(opt);
      }
    }
  }

  // ---- eventos SSE ----

  function handleEvent(data) {
    if (data.event === "network_seen") {
      upsertNetwork(data);
      trackActivity(activity.networks, data.timestamp || data.last_seen);
      renderChannels();
      updateCounters();
      // Red nueva: render completo (mantiene el orden de la columna de
      // ordenacion). Fila ya visible: parcheo incremental. Deja de pasar el
      // filtro: se elimina.
      const v = rowValues(networks.get(data.bssid));
      const tr = tbody.querySelector(`tr[data-bssid="${CSS.escape(data.bssid)}"]`);
      if (!matchesFilters(v)) {
        if (tr) {
          tr.remove();
          syncEmptyState();
        }
      } else if (tr) {
        patchRow(data.bssid);
      } else {
        renderTable();
        flashRow(
          tbody.querySelector(`tr[data-bssid="${CSS.escape(data.bssid)}"]`)
        );
      }
      appendConsoleLine({
        ts: data.timestamp || data.last_seen,
        type: "network_seen",
        text: data.raw_line,
      });
    } else if (data.event === "client_associated") {
      upsertClient(data);
      trackActivity(activity.clients, data.timestamp || data.last_seen);
      updateCounters();
      if (data.bssid) patchRow(data.bssid); // refresca n_clients de la red
      appendConsoleLine({
        ts: data.timestamp || data.last_seen,
        type: "client_associated",
        text: data.raw_line,
      });
    } else {
      // status u otros eventos del ciclo de vida del firmware.
      appendConsoleLine({
        ts: data.timestamp,
        type: "status",
        text: data.raw_line,
      });
    }
  }

  // ---- estado de conexion (polling ligero) + stats del pipeline (C4) ----

  async function refreshConnStatus() {
    let data;
    try {
      data = await fetchJSON("/api/status");
    } catch (_err) {
      connStatusEl.textContent = "fuente: sin datos";
      connStatusEl.className = "status status-offline";
      showToast("No se pudo consultar el estado de la fuente", "warn");
      return;
    }
    if (!data.source || !data.state) {
      connStatusEl.textContent = "fuente: —";
      connStatusEl.className = "status status-offline";
      return;
    }
    const bits = [];
    if (data.port) bits.push(`${data.port} @ ${data.baudrate ?? 115200}`);
    if (data.path) bits.push(data.path);
    if (data.firmware) bits.push(data.firmware);
    bits.push(data.state);
    const ok = data.state === "conectado" || data.state === "reproduciendo";
    connStatusEl.textContent = `fuente: ${bits.join(" · ")}`;
    connStatusEl.title = connStatusEl.textContent;
    connStatusEl.className = `status ${ok ? "status-online" : "status-warn"}`;
  }

  // Stats del collector expuestos por /api/health (lineas/eventos/errores).
  async function refreshPipelineStats() {
    let h;
    try {
      h = await fetchJSON("/api/health");
    } catch (_err) {
      // Caida del backend: ya la anuncia refreshConnStatus o el toast SSE.
      return;
    }
    $("meta-source").textContent = h.source || h.store || "—";
    const c = h.collector;
    $("stat-lines").textContent = c ? String(c.lines) : "—";
    $("stat-events").textContent = c ? String(c.events) : "—";
    const errs = $("stat-errors");
    errs.textContent = c ? String(c.errors) : "—";
    errs.classList.toggle("stat-errors-warn", Boolean(c) && c.errors > 0);
  }

  function refreshStatus() {
    refreshConnStatus();
    refreshPipelineStats();
  }

  function setSseStatus(online) {
    statusEl.textContent = online ? "SSE: conectado" : "SSE: desconectado";
    statusEl.className = `status ${online ? "status-online" : "status-offline"}`;
  }

  function connectSSE() {
    if (es) es.close();
    es = new EventSource("/api/events");
    es.onopen = () => {
      if (hadDisconnect) showToast("Conexión en vivo restablecida", "info");
      hadDisconnect = false;
      setSseStatus(true);
    };
    es.onerror = () => {
      // Cierre explicito + reconexion controlada (no dependemos del auto-retry).
      hadDisconnect = true;
      setSseStatus(false);
      showToast("Conexión en vivo perdida — reconectando…", "warn");
      const current = es;
      es = null;
      current.close();
      if (reconnectTimer === null) {
        reconnectTimer = setTimeout(() => {
          reconnectTimer = null;
          connectSSE();
        }, RECONNECT_MS);
      }
    };
    es.onmessage = (e) => {
      try {
        handleEvent(JSON.parse(e.data));
      } catch (_err) {
        // frame no JSON: lo ignoramos, el stream sigue.
      }
    };
  }

  // ---- filtros: persistencia + chips (A6) ----

  function saveFilters() {
    try {
      localStorage.setItem(
        FILTERS_STORAGE_KEY,
        JSON.stringify({
          text: filterText.value,
          channel: filterChannel.value,
          rssi: filterRssi.value,
          clientsOnly: filterClients.checked,
          sortKey,
          sortDir,
        })
      );
    } catch (_err) {
      // localStorage no disponible: los filtros quedan solo en memoria.
    }
  }

  function loadFilters() {
    try {
      const raw = localStorage.getItem(FILTERS_STORAGE_KEY);
      if (!raw) return;
      const saved = JSON.parse(raw);
      if (typeof saved.text === "string") filterText.value = saved.text;
      if (typeof saved.channel === "string") filterChannel.value = saved.channel;
      if (typeof saved.rssi === "string") filterRssi.value = saved.rssi;
      if (typeof saved.clientsOnly === "boolean") filterClients.checked = saved.clientsOnly;
      // Ordenacion tambien se restaura (volver del detalle, recarga).
      if (typeof saved.sortKey === "string") sortKey = saved.sortKey;
      if (saved.sortDir === "asc" || saved.sortDir === "desc") sortDir = saved.sortDir;
    } catch (_err) {
      // valor corrupto: se ignoran los filtros guardados.
    }
  }

  // Chips de filtros activos, con boton de dismiss por filtro.
  function renderChips() {
    chipsEl.textContent = "";
    const active = [];
    const text = filterText.value.trim();
    if (text) active.push({ key: "text", label: `texto: <b>${text}</b>` });
    if (filterChannel.value) {
      active.push({ key: "channel", label: `canal: <b>${filterChannel.value}</b>` });
    }
    if (filterRssi.value.trim()) {
      active.push({ key: "rssi", label: `RSSI ≥ <b>${filterRssi.value.trim()} dBm</b>` });
    }
    if (filterClients.checked) active.push({ key: "clients", label: "solo con clientes" });
    for (const { key, label } of active) {
      const chip = document.createElement("span");
      chip.className = "chip";
      const txt = document.createElement("span");
      txt.innerHTML = label;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.title = "Quitar filtro";
      btn.setAttribute("aria-label", "Quitar filtro");
      btn.textContent = "×";
      btn.addEventListener("click", () => {
        if (key === "text") filterText.value = "";
        else if (key === "channel") filterChannel.value = "";
        else if (key === "rssi") filterRssi.value = "";
        else if (key === "clients") filterClients.checked = false;
        saveFilters();
        renderTable();
        renderChips();
      });
      chip.append(txt, btn);
      chipsEl.appendChild(chip);
    }
  }

  function onFilterChanged() {
    saveFilters();
    renderChips();
    renderTable();
  }

  // ---- filtros y orden ----

  function bindControls() {
    filterText.addEventListener("input", onFilterChanged);
    filterChannel.addEventListener("change", onFilterChanged);
    filterRssi.addEventListener("input", onFilterChanged);
    filterClients.addEventListener("change", onFilterChanged);

    for (const th of document.querySelectorAll("#networks-table th[data-key]")) {
      const btn = th.querySelector(".sort-btn");
      btn.addEventListener("click", () => {
        const key = th.dataset.key;
        if (sortKey === key) {
          sortDir = sortDir === "asc" ? "desc" : "asc";
        } else {
          sortKey = key;
          sortDir = key === "last_seen" || key === "rssi" ? "desc" : "asc";
        }
        saveFilters();
        renderTable();
      });
    }
  }

  // ---- arranque ----

  async function init() {
    loadFilters();
    bindControls();
    bindConsoleControls();
    renderChips();
    showSkeletons();
    try {
      const [nets, cls, cons] = await Promise.all([
        fetchJSON("/api/networks"),
        fetchJSON("/api/clients"),
        fetchJSON(`/api/console?limit=${CONSOLE_LIMIT}`),
      ]);
      for (const n of nets.networks) upsertNetwork(n);
      for (const c of cls) upsertClient(c);
      for (const line of cons.lines) {
        appendConsoleLine({
          ts: line.timestamp,
          type: line.event_type,
          text: line.raw_line,
        });
      }
    } catch (err) {
      console.error("carga inicial fallida:", err);
      showToast(`Carga inicial fallida: ${err.message}`, "error");
    } finally {
      hideSkeletons();
    }

    refreshStatus();
    setInterval(refreshStatus, STATUS_POLL_MS);

    renderTable();
    updateCounters();
    renderChannels();
    connectSSE();
  }

  init();
})();
