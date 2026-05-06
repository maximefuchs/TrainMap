// ── routes.js ─────────────────────────────────────────────────────────────────
// Responsible for:
//   • Rendering route polylines and stop markers on the map (train mode)
//   • Rendering destination dot markers on the map (bus mode)
//   • Building the sidebar accordion list of routes and stops
//   • Handling route selection / deselection (highlight, zoom, sync accordion)
//   • Handling stop activation (highlight sidebar row, pan map, open popup)
//   • Clearing all map layers when a new search starts
//
// Depends on: map (map.js), sidebar helpers (sidebar.js),
//             t() / currentLang (i18n.js), showStatus() (app.js)
// ─────────────────────────────────────────────────────────────────────────────

// DOM refs
const connList  = document.getElementById("connections-list");
const connCount = document.getElementById("conn-count");

// ── Route state ───────────────────────────────────────────────────────────────

// Each entry is built once per search result (train mode):
//   { poly: L.Polyline, hitPoly: L.Polyline, markers: L.Marker[], color, path }
let routes = [];

// Bus mode dot markers (no polylines)
let busMarkers = [];

// Index of the currently highlighted route, or null when none is selected.
let activeRouteIdx = null;

// Tracks the currently highlighted stop row in the sidebar so we can un-highlight
// it when a different stop is activated.
let activeStopRow = null;

// Re-entrancy guard: the accordion "toggle" event fires when we programmatically
// open/close <details> elements inside selectRoute(). Without this flag we would
// get infinite recursion (toggle → selectRoute → toggle → …).
let _changingRoute = false;

// ── Colour palette ────────────────────────────────────────────────────────────
const ROUTE_PALETTE = [
  "#38bdf8", "#f472b6", "#a78bfa", "#34d399", "#fbbf24",
  "#fb923c", "#e879f9", "#4ade80", "#f87171", "#60a5fa",
];

function routeColor(idx) {
  return ROUTE_PALETTE[idx % ROUTE_PALETTE.length];
}

// ── Leaflet icon helpers ──────────────────────────────────────────────────────

function circleIcon(color) {
  return L.divIcon({
    className: "",
    html: `<div style="
      width:14px; height:14px; border-radius:50%;
      background:${color}; border:2px solid #fff;
      box-shadow:0 0 6px ${color}88;
    "></div>`,
    iconSize:   [14, 14],
    iconAnchor: [7, 7],
  });
}

function circleIconHovered(color) {
  return L.divIcon({
    className: "",
    html: `<div style="
      width:14px; height:14px; border-radius:50%;
      background:${color}; border:2px solid ${color};
      box-shadow:0 0 10px ${color}cc;
      outline: 2px solid #fff;
    "></div>`,
    iconSize:   [14, 14],
    iconAnchor: [7, 7],
  });
}

// Bus destination dot — slightly larger, distinct green colour
function busDestIcon() {
  return L.divIcon({
    className: "",
    html: `<div style="
      width:12px; height:12px; border-radius:50%;
      background:#22c55e; border:2px solid #fff;
      box-shadow:0 0 6px #22c55e88;
    "></div>`,
    iconSize:   [12, 12],
    iconAnchor: [6, 6],
  });
}

// Enlarged version for sidebar hover
function busDestIconHover() {
  return L.divIcon({
    className: "",
    html: `<div style="
      width:18px; height:18px; border-radius:50%;
      background:#22c55e; border:2px solid #fff;
      box-shadow:0 0 10px #22c55ecc;
    "></div>`,
    iconSize:   [18, 18],
    iconAnchor: [9, 9],
  });
}

const originIcon = L.divIcon({
  className: "",
  html: `<div style="
    width:18px; height:18px; border-radius:50%;
    background:#38bdf8; border:3px solid #fff;
    box-shadow:0 0 12px #38bdf888;
  "></div>`,
  iconSize:   [18, 18],
  iconAnchor: [9, 9],
});

const EXPLORE_ICON = `<svg class="explore-icon" viewBox="0 0 24 24" fill="none"
  stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
  <circle cx="11" cy="11" r="7"/>
  <line x1="16.5" y1="16.5" x2="22" y2="22"/>
</svg>`;

// ── Popup HTML ────────────────────────────────────────────────────────────────

function stationPopupHtml(s) {
  return `<div class="popup-station">
    <strong>${s.name}</strong>
    <button class="popup-btn-explore" title="${t("exploreFrom")}"
            data-id="${s.id}" data-name="${s.name}"
            data-lat="${s.lat}" data-lon="${s.lon}">${EXPLORE_ICON}</button>
  </div>`;
}

function busPopupHtml(conn) {
  const lines = conn.lines || [];
  const timesHtml = lines.slice(0, 5).map(l =>
    `<div style="font-size:.82em;opacity:.75">${l.departure_time} → ${l.arrival_time}</div>`
  ).join("");
  const more = lines.length > 5
    ? `<div style="font-size:.78em;opacity:.5">+${lines.length - 5} more</div>` : "";
  return `<div class="popup-station">
    <strong>${conn.name}</strong>${timesHtml}${more}
    <button class="popup-btn-explore" title="${t("exploreFrom")}"
            data-id="${conn.id}" data-name="${conn.name}"
            data-lat="${conn.lat}" data-lon="${conn.lon}">${EXPLORE_ICON}</button>
  </div>`;
}

// ── Delegated click listeners ─────────────────────────────────────────────────

document.getElementById("map").addEventListener("click", (e) => {
  const btn = e.target.closest(".popup-btn-explore");
  if (!btn) return;

  const station = {
    id:  btn.dataset.id,
    name: btn.dataset.name,
    lat: parseFloat(btn.dataset.lat),
    lon: parseFloat(btn.dataset.lon),
  };

  document.getElementById("station-input").value = station.name;
  map.closePopup();
  selectStation(station);
});

connList.addEventListener("click", (e) => {
  const btn = e.target.closest(".stop-btn-explore");
  if (!btn) return;

  e.stopPropagation();

  const station = {
    id:  btn.dataset.id,
    name: btn.dataset.name,
    lat: parseFloat(btn.dataset.lat),
    lon: parseFloat(btn.dataset.lon),
  };

  document.getElementById("station-input").value = station.name;
  selectStation(station);
});

// ── Shared per-route helpers ──────────────────────────────────────────────────

function _buildRouteMapLayer(origin, path, idx) {
  const color   = routeColor(idx);
  const latlngs = path.stops.map(s => [s.lat, s.lon]);

  const poly = L.polyline(latlngs, { color, weight: 3, opacity: 0.75 }).addTo(map);

  const hitPoly = L.polyline(latlngs, {
    color, weight: 20, opacity: 0, interactive: true,
  }).addTo(map);
  const firstName = path.stops[0]?.name || "";
  const lastName  = path.stops[path.stops.length - 1]?.name || "";
  hitPoly.bindTooltip(`${firstName} → ${lastName}`, {
    sticky: true, className: "route-tooltip",
  });
  hitPoly.on("click", () => selectRoute(idx));
  hitPoly.on("mouseover", () => {
    const r = routes[idx];
    if (!r) return;
    if (activeRouteIdx !== null && activeRouteIdx !== idx) {
      r.poly.setStyle({ weight: 4, opacity: 0.85 });
    } else if (activeRouteIdx === null) {
      r.poly.setStyle({ weight: 5, opacity: 1 });
    }
    if (activeRouteIdx === idx) hitPoly.closeTooltip();
    const det = document.getElementById(`route-${idx}`);
    if (det) {
      det.querySelector(".route-summary")?.classList.add("hovered");
      det.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  });
  hitPoly.on("mouseout", () => {
    const r = routes[idx];
    if (!r) return;
    if (activeRouteIdx !== null && activeRouteIdx !== idx) {
      r.poly.setStyle({ weight: 2, opacity: 0.2 });
    } else if (activeRouteIdx === null) {
      r.poly.setStyle({ weight: 3, opacity: 0.75 });
    }
    const det = document.getElementById(`route-${idx}`);
    if (det) det.querySelector(".route-summary")?.classList.remove("hovered");
  });

  const markers = path.stops
    .filter(s => s.id !== origin.id)
    .map(s => {
      const m = L.marker([s.lat, s.lon], { icon: circleIcon(color) });
      m._stopId  = s.id;
      m._station = s;
      m.bindPopup(stationPopupHtml(s));
      m.bindTooltip(s.name, { className: "route-tooltip" });

      let hoveredStopRow = null;
      m.on("mouseover", () => {
        const row = connList.querySelector(
          `[data-stop-id="${CSS.escape(s.id)}"][data-route-idx="${idx}"]`
        );
        if (row) {
          row.classList.add("hovered");
          row.scrollIntoView({ block: "nearest", behavior: "smooth" });
          hoveredStopRow = row;
        }
      });
      m.on("mouseout", () => {
        if (hoveredStopRow) {
          hoveredStopRow.classList.remove("hovered");
          hoveredStopRow = null;
        }
      });
      m.on("click", () => {
        if (activeStopRow) activeStopRow.classList.remove("active");
        const row = connList.querySelector(
          `[data-stop-id="${CSS.escape(s.id)}"][data-route-idx="${idx}"]`
        );
        if (row) {
          row.classList.add("active");
          row.scrollIntoView({ block: "nearest", behavior: "smooth" });
          activeStopRow = row;
        }
      });
      return m;
    });

  return { poly, hitPoly, markers, color, path };
}

function _buildRouteSidebarHtml(origin, path, idx) {
  const color     = routeColor(idx);
  const stopCount = path.stops.length;
  const firstName = path.stops[0]?.name || "";
  const lastName  = path.stops[stopCount - 1]?.name || "";
  const lineCode  = path.line_code || t("trainFallback");

  const timeTag = (path.departure_time && path.arrival_time)
    ? `<span class="route-times">${path.departure_time} → ${path.arrival_time}</span>`
    : "";

  const label = `${firstName} — ${lastName} `
    + `<span style="opacity:.6;font-weight:400">(${lineCode})</span>`
    + timeTag;

  const stopsHtml = path.stops.map((s, si) => {
    const isOrigin = s.id === origin.id;
    const isFirst  = si === 0;
    const isLast   = si === stopCount - 1;
    const lineAbove = isFirst ? "transparent" : `${color}88`;
    const lineBelow = isLast  ? "transparent" : `${color}88`;
    const dotStyle  = isOrigin
      ? `background:#fff; border-color:${color};`
      : `background:${color}; border-color:#fff;`;

    return `
      <div class="stop-row${isOrigin ? " active" : ""}"
           data-stop-id="${s.id}" data-route-idx="${idx}"
           onclick="activateStop('${s.id}', ${idx})">
        <div class="stop-track">
          <div class="stop-track-line" style="background:${lineAbove}"></div>
          <div class="stop-dot${isOrigin ? " origin" : ""}" style="${dotStyle}"></div>
          <div class="stop-track-line" style="background:${lineBelow}"></div>
        </div>
        <div class="stop-name${isOrigin ? " origin" : ""}">${s.name}${s.departure_time ? `<span class="stop-time">${s.departure_time}</span>` : ""}</div>
        ${isOrigin ? "" : `
          <button class="stop-btn-explore" title="${t("exploreFrom")}"
                  data-id="${s.id}" data-name="${s.name}"
                  data-lat="${s.lat}" data-lon="${s.lon}">${EXPLORE_ICON}</button>`}
      </div>`;
  }).join("");

  return `
    <details class="route-section" id="route-${idx}">
      <summary class="route-summary">
        <div class="route-swatch" style="background:${color}"></div>
        <span class="route-label">${label}</span>
        <span class="route-count">${t("stopCount", stopCount)}</span>
        <span class="route-chevron">▶</span>
      </summary>
      <div class="stop-list">${stopsHtml}</div>
    </details>`;
}

function _attachRouteListeners(det, idx) {
  det.addEventListener("toggle", () => {
    if (_changingRoute) return;
    if (det.open) {
      selectRoute(idx);
    } else if (activeRouteIdx === idx) {
      deselectRoute();
    }
  });

  det.addEventListener("mouseenter", () => {
    const r = routes[idx];
    if (!r) return;
    if (activeRouteIdx !== null && activeRouteIdx !== idx) {
      r.poly.setStyle({ weight: 4, opacity: 0.85 });
    } else if (activeRouteIdx === null) {
      r.poly.setStyle({ weight: 5, opacity: 1 });
    }
  });

  det.addEventListener("mouseleave", () => {
    const r = routes[idx];
    if (!r) return;
    if (activeRouteIdx !== null && activeRouteIdx !== idx) {
      r.poly.setStyle({ weight: 2, opacity: 0.2 });
    } else if (activeRouteIdx === null) {
      r.poly.setStyle({ weight: 3, opacity: 0.75 });
    }
  });

  // Stop row hover → highlight the corresponding map marker
  det.querySelectorAll(".stop-row[data-stop-id]").forEach(row => {
    const stopId = row.dataset.stopId;
    row.addEventListener("mouseenter", () => {
      const r = routes[idx];
      if (!r) return;
      const m = r.markers.find(mk => mk._stopId === stopId);
      if (m) m.setIcon(circleIconHovered(r.color));
    });
    row.addEventListener("mouseleave", () => {
      const r = routes[idx];
      if (!r) return;
      const m = r.markers.find(mk => mk._stopId === stopId);
      if (m) m.setIcon(circleIcon(r.color));
    });
  });
}

// ── Render connections (train mode — called once on 'done') ───────────────────

// Called once the SSE stream has delivered all route paths for a search.
// `paths` is the route_paths array (non-empty when routes could be reconstructed).
// `conns` is the raw connections array (fallback when paths is empty).
// `mode` is "train" or "bus".
function renderConnections(origin, paths, conns = [], mode = "train") {
  // If we have route paths (train always, bus when grouping succeeded), draw polylines.
  // Fall back to dot-only rendering only when paths is truly empty.
  if (!paths.length) {
    if (conns.length) {
      _renderBusConnections(origin, conns);
    } else {
      connList.innerHTML = `<div id="empty-state"><p>${t("noConnections")}</p></div>`;
    }
    return;
  }

  // ── Map layers ──────────────────────────────────────────────────────────────
  routes = paths.map((path, idx) => _buildRouteMapLayer(origin, path, idx));

  // ── Sidebar accordions ──────────────────────────────────────────────────────
  connList.innerHTML = paths.map((path, idx) => _buildRouteSidebarHtml(origin, path, idx)).join("");

  connList.querySelectorAll("details.route-section").forEach((det, idx) => {
    _attachRouteListeners(det, idx);
  });
}

// ── Add a single route incrementally (bus mode progressive streaming) ─────────

// Called for each 'route' SSE event. Appends one route to the map and sidebar
// without clearing existing content.
function addRoute(origin, path) {
  // Remove the empty-state placeholder on first route
  const empty = document.getElementById("empty-state");
  if (empty) empty.remove();

  const idx = routes.length;
  const route = _buildRouteMapLayer(origin, path, idx);
  routes.push(route);

  const html = _buildRouteSidebarHtml(origin, path, idx);
  connList.insertAdjacentHTML("beforeend", html);
  const det = document.getElementById(`route-${idx}`);
  if (det) _attachRouteListeners(det, idx);
}

// ── Bus mode rendering ────────────────────────────────────────────────────────

function _renderBusConnections(origin, conns) {
  if (!conns.length) {
    connList.innerHTML = `<div id="empty-state"><p>${t("noConnections")}</p></div>`;
    return;
  }

  // Place a dot marker for each reachable city
  busMarkers = conns.map(conn => {
    const m = L.marker([conn.lat, conn.lon], { icon: busDestIcon() });
    m._connId = conn.id;
    m.bindPopup(busPopupHtml(conn));
    m.addTo(map);
    return m;
  });

  // Sidebar: one accordion per city, with all departure times listed inside
  connList.innerHTML = conns.map((conn, idx) => {
    const lines = conn.lines || [];
    const firstLine = lines[0];
    // Summary shows city name + first departure time
    const summaryTime = firstLine
      ? `<span class="bus-times">${firstLine.departure_time} → ${firstLine.arrival_time}</span>`
      : "";
    // Body lists every trip
    const tripsHtml = lines.map(l =>
      `<div class="bus-trip-row">
        <span class="bus-trip-time">${l.departure_time}</span>
        <span class="bus-trip-arrow">→</span>
        <span class="bus-trip-time">${l.arrival_time}</span>
      </div>`
    ).join("");

    return `
      <details class="bus-conn-details" id="bus-conn-${idx}">
        <summary class="bus-conn-row" data-conn-idx="${idx}"
                 onclick="_activateBusConn(${idx})">
          <div class="bus-dot-swatch"></div>
          <div class="bus-conn-info">
            <span class="bus-conn-name">${conn.name}</span>
            ${summaryTime}
          </div>
          <button class="stop-btn-explore" title="${t("exploreFrom")}"
                  data-id="${conn.id}" data-name="${conn.name}"
                  data-lat="${conn.lat}" data-lon="${conn.lon}"
                  onclick="event.stopPropagation()">${EXPLORE_ICON}</button>
          ${lines.length > 1 ? `<span class="bus-trip-count">${lines.length}×</span>` : ""}
          <span class="route-chevron">▶</span>
        </summary>
        ${lines.length > 1 ? `<div class="bus-trips-body">${tripsHtml}</div>` : ""}
      </details>`;
  }).join("");

  // Hover highlight: enlarge dot marker when hovering a bus row
  connList.querySelectorAll("details.bus-conn-details").forEach((det, idx) => {
    det.addEventListener("mouseenter", () => {
      const m = busMarkers[idx];
      if (m) m.setIcon(busDestIconHover());
    });
    det.addEventListener("mouseleave", () => {
      const m = busMarkers[idx];
      if (m) m.setIcon(busDestIcon());
    });
  });

  // Fit map to include origin + all destinations
  if (conns.length) {
    const allLatLngs = [
      [origin.lat, origin.lon],
      ...conns.map(c => [c.lat, c.lon]),
    ];
    map.fitBounds(L.latLngBounds(allLatLngs), { padding: [60, 60] });
  }
}

// Highlight a bus connection row and pan to its marker
function _activateBusConn(idx) {
  // Un-highlight previous
  connList.querySelectorAll(".bus-conn-row.active").forEach(r => r.classList.remove("active"));
  const summary = connList.querySelector(`.bus-conn-row[data-conn-idx="${idx}"]`);
  if (summary) {
    summary.classList.add("active");
    summary.closest("details")?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
  const m = busMarkers[idx];
  if (m) {
    map.panTo(m.getLatLng(), { animate: true });
    m.openPopup();
  }
}

// ── Route selection ───────────────────────────────────────────────────────────

function selectRoute(idx) {
  if (_changingRoute || activeRouteIdx === idx) return;
  _changingRoute = true;
  activeRouteIdx = idx;

  if (isMobile() && sidebarState() === "closed") peekSidebar();

  routes.forEach((r, i) => {
    r.poly.setStyle(i === idx
      ? { weight: 4, opacity: 1 }
      : { weight: 2, opacity: 0.2 }
    );
  });

  routes.forEach(r => r.markers.forEach(m => map.removeLayer(m)));
  routes[idx].markers.forEach(m => m.addTo(map));

  map.fitBounds(routes[idx].poly.getBounds(), { padding: [60, 60] });

  const activeDet = document.getElementById(`route-${idx}`);
  connList.querySelectorAll("details.route-section").forEach(det => {
    det.open = (det === activeDet);
  });
  if (activeDet) activeDet.scrollIntoView({ block: "nearest", behavior: "smooth" });

  _changingRoute = false;
}

function deselectRoute() {
  if (_changingRoute) return;
  _changingRoute = true;
  activeRouteIdx = null;

  routes.forEach(r => r.poly.setStyle({ weight: 3, opacity: 0.75 }));
  routes.forEach(r => r.markers.forEach(m => map.removeLayer(m)));
  connList.querySelectorAll("details.route-section").forEach(d => { d.open = false; });

  if (routes.length) {
    const combined = routes
      .map(r => r.poly.getBounds())
      .reduce((acc, b) => acc.extend(b));
    map.fitBounds(combined, { padding: [60, 60] });
  }

  _changingRoute = false;
}

// ── Stop activation ───────────────────────────────────────────────────────────

function activateStop(stopId, routeIdx) {
  if (activeStopRow) activeStopRow.classList.remove("active");
  const row = connList.querySelector(
    `[data-stop-id="${CSS.escape(stopId)}"][data-route-idx="${routeIdx}"]`
  );
  if (row) {
    row.classList.add("active");
    row.scrollIntoView({ block: "nearest", behavior: "smooth" });
    activeStopRow = row;
  }

  const marker = routes[routeIdx]?.markers.find(m => m._stopId === stopId);
  if (marker) {
    map.panTo(marker.getLatLng(), { animate: true });
    marker.openPopup();
  }
}

// ── Clear map ─────────────────────────────────────────────────────────────────

let originMarker = null;

function clearMap() {
  if (originMarker) {
    map.removeLayer(originMarker);
    originMarker = null;
  }

  routes.forEach(r => {
    map.removeLayer(r.poly);
    map.removeLayer(r.hitPoly);
    r.markers.forEach(m => map.removeLayer(m));
  });
  routes = [];

  busMarkers.forEach(m => map.removeLayer(m));
  busMarkers = [];

  activeRouteIdx = null;
  activeStopRow  = null;
  _changingRoute = false;
}

