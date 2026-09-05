// m5wireless dashboard (Fase 4): vanilla JS, sin frameworks.
// - SSE en /api/events con reconexion controlada (sin polling).
// - Actualizacion incremental: cada evento parchea solo la fila afectada;
//   render completo solo al cambiar filtro/orden.
// - Filtros en cliente sobre el estado ya cargado (canal, RSSI min, texto);
//   para consultas historicas usar /api/networks?since=...

(() => {
  "use strict";

  const MAX_CONSOLE_LINES = 500;
  const RECONNECT_MS = 3000;
  const CONSOLE_LIMIT = 200;
  const STATUS_POLL_MS = 5000;

  // ---- estado ----
  const networks = new Map(); // bssid -> {bssid, ssid, channel, rssi, last_seen}
  const clients = new Map(); // mac -> {mac, bssid}
  const consoleLines = [];
  let sortKey = "last_seen";
  let sortDir = "desc";
  let es = null; // EventSource actual
  let reconnectTimer = null;

  // ---- DOM ----
  const $ = (id) => document.getElementById(id);
  const tbody = $("networks-body");
  const consoleEl = $("console");
  const channelsEl = $("channels");
  const statusEl = $("sse-status");
  const connStatusEl = $("conn-status");
  const filterText = $("filter-text");
  const filterChannel = $("filter-channel");
  const filterRssi = $("filter-rssi");

  // ---- utilidades ----

  function rssiClass(rssi) {
    if (rssi === null || rssi === undefined) return "";
    if (rssi >= -60) return "rssi-good";
    if (rssi >= -80) return "rssi-mid";
    return "rssi-bad";
  }

  function fmtTime(iso) {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString();
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

  // ---- consola ----

  function appendConsoleLine(line) {
    if (!line) return;
    const nearBottom =
      consoleEl.scrollHeight - consoleEl.scrollTop - consoleEl.clientHeight < 40;
    consoleLines.push(line);
    if (consoleLines.length > MAX_CONSOLE_LINES) {
      consoleLines.splice(0, consoleLines.length - MAX_CONSOLE_LINES);
    }
    consoleEl.textContent = consoleLines.join("\n");
    if (nearBottom) consoleEl.scrollTop = consoleEl.scrollHeight;
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

  function renderRow(v) {
    const tr = document.createElement("tr");
    tr.dataset.bssid = v.bssid;
    const cells = [
      v.ssid || "—",
      v.bssid,
      v.channel === null ? "—" : String(v.channel),
      v.rssi === null ? "—" : `${v.rssi} dBm`,
      String(v.n_clients),
      fmtTime(v.last_seen),
    ];
    cells.forEach((text, i) => {
      const td = document.createElement("td");
      td.textContent = text;
      if (i === 1) td.className = "mono";
      if (i === 2) badgeCell(td, text);
      if (i === 3) {
        td.className = "mono";
        badgeCell(td, text, v.rssi !== null ? rssiClass(v.rssi) : "");
      }
      if (i === 4) td.className = "num";
      if (i === 5) td.className = "time";
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

  // Fila de estado vacio: visible solo cuando no hay redes que mostrar.
  function syncEmptyState() {
    const empty =
      tbody.querySelectorAll("tr[data-bssid]").length === 0;
    let row = tbody.querySelector("#empty-state-row");
    if (empty && !row) {
      row = document.createElement("tr");
      row.id = "empty-state-row";
      const td = document.createElement("td");
      td.colSpan = 6;
      td.className = "empty-state";
      td.textContent =
        "Sin redes visibles todavía — conecta una fuente o ajusta los filtros.";
      row.appendChild(td);
      tbody.appendChild(row);
    } else if (!empty && row) {
      row.remove();
    }
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
    tds[3].className = "mono";
    badgeCell(tds[3], v.rssi === null ? "—" : `${v.rssi} dBm`, v.rssi !== null ? rssiClass(v.rssi) : "");
    tds[4].textContent = String(v.n_clients);
    tds[4].className = "num";
    tds[5].textContent = fmtTime(v.last_seen);
    tds[5].className = "time";
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

  // ---- contadores y distribucion por canal ----

  function updateCounters() {
    $("count-networks").textContent = String(networks.size);
    $("count-clients").textContent = clients.size;
  }

  function renderChannels() {
    const dist = {};
    for (const net of networks.values()) {
      if (net.channel !== null) dist[net.channel] = (dist[net.channel] || 0) + 1;
    }
    const entries = Object.entries(dist).map(([ch, n]) => [Number(ch), n]).sort((a, b) => a[0] - b[0]);
    const max = entries.length ? Math.max(...entries.map(([, n]) => n)) : 1;

    channelsEl.textContent = "";
    for (const [ch, n] of entries) {
      const li = document.createElement("li");
      const label = document.createElement("span");
      label.textContent = `Canal ${ch}`;
      const track = document.createElement("div");
      track.className = "bar-track";
      const fill = document.createElement("div");
      fill.className = "bar-fill";
      fill.style.width = `${Math.max(4, Math.round((n / max) * 100))}%`;
      track.appendChild(fill);
      const count = document.createElement("span");
      count.textContent = String(n);
      li.append(label, track, count);
      channelsEl.appendChild(li);
    }

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
      appendConsoleLine(data.raw_line);
    } else if (data.event === "client_associated") {
      upsertClient(data);
      updateCounters();
      if (data.bssid) patchRow(data.bssid); // refresca n_clients de la red
      appendConsoleLine(data.raw_line);
    }
  }

  // ---- estado de conexion (polling ligero de /api/status) ----

  async function refreshConnStatus() {
    let data;
    try {
      data = await fetchJSON("/api/status");
    } catch (_err) {
      connStatusEl.textContent = "fuente: sin datos";
      connStatusEl.className = "status status-offline";
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

  function setSseStatus(online) {
    statusEl.textContent = online ? "SSE: conectado" : "SSE: desconectado";
    statusEl.className = `status ${online ? "status-online" : "status-offline"}`;
  }

  function connectSSE() {
    if (es) es.close();
    es = new EventSource("/api/events");
    es.onopen = () => setSseStatus(true);
    es.onerror = () => {
      // Cierre explicito + reconexion controlada (no dependemos del auto-retry).
      setSseStatus(false);
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

  // ---- filtros y orden ----

  function bindControls() {
    filterText.addEventListener("input", renderTable);
    filterChannel.addEventListener("change", renderTable);
    filterRssi.addEventListener("input", renderTable);

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
        renderTable();
      });
    }
  }

  // ---- arranque ----

  async function init() {
    bindControls();
    try {
      const [nets, cls, cons] = await Promise.all([
        fetchJSON("/api/networks"),
        fetchJSON("/api/clients"),
        fetchJSON(`/api/console?limit=${CONSOLE_LIMIT}`),
      ]);
      for (const n of nets.networks) upsertNetwork(n);
      for (const c of cls) upsertClient(c);
      for (const line of cons.lines) appendConsoleLine(line.raw_line);
    } catch (err) {
      console.error("carga inicial fallida:", err);
    }
    fetchJSON("/api/health")
      .then((h) => {
        $("meta-source").textContent = h.source || h.store || "—";
      })
      .catch(() => {});

    refreshConnStatus();
    setInterval(refreshConnStatus, STATUS_POLL_MS);

    renderTable();
    updateCounters();
    renderChannels();
    connectSSE();
  }

  init();
})();
