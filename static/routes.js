// ── routes.js ─────────────────────────────────────────────────────────────────
// Responsible for:
//   • Rendering route polylines and stop markers on the map
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

// Each entry is built once per search result:
//   { poly: L.Polyline, hitPoly: L.Polyline, markers: L.Marker[], color, path }
let routes = [];

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
// Ten visually distinct colours cycling for up to 10 routes; wraps beyond that.
const ROUTE_PALETTE = [
  "#38bdf8", "#f472b6", "#a78bfa", "#34d399", "#fbbf24",
  "#fb923c", "#e879f9", "#4ade80", "#f87171", "#60a5fa",
];

function routeColor(idx) {
  return ROUTE_PALETTE[idx % ROUTE_PALETTE.length];
}

// ── Leaflet icon helpers ──────────────────────────────────────────────────────

// Small filled circle used for intermediate stops along a route.
// Rendered as a divIcon so we can colour it dynamically per route.
function circleIcon(color) {
  return L.divIcon({
    className: "",   // prevent Leaflet from adding default white box styles
    html: `<div style="
      width:14px; height:14px; border-radius:50%;
      background:${color}; border:2px solid #fff;
      box-shadow:0 0 6px ${color}88;
    "></div>`,
    iconSize:   [14, 14],
    iconAnchor: [7, 7],
  });
}

// Slightly larger blue circle used for the origin station.
// Created once and reused across searches.
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

// Magnifying-glass SVG used in both map popups and sidebar stop rows.
const EXPLORE_ICON = `<svg class="explore-icon" viewBox="0 0 24 24" fill="none"
  stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
  <circle cx="11" cy="11" r="7"/>
  <line x1="16.5" y1="16.5" x2="22" y2="22"/>
</svg>`;

// ── Popup HTML ────────────────────────────────────────────────────────────────

// Builds the inner HTML for a stop marker popup.
// The "explore from here" button is wired via delegated listeners below.
function stationPopupHtml(s) {
  return `<div class="popup-station">
    <strong>${s.name}</strong>
    <button class="popup-btn-explore" title="${t("exploreFrom")}"
            data-id="${s.id}" data-name="${s.name}"
            data-lat="${s.lat}" data-lon="${s.lon}">${EXPLORE_ICON}</button>
  </div>`;
}

// ── Delegated click listeners ─────────────────────────────────────────────────

// Clicking the "explore" button inside a map popup triggers a new search from
// that stop. We listen on the whole map container to catch popup button clicks
// without needing to wire each popup individually.
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

// Same for the "explore" buttons embedded inside sidebar stop rows.
connList.addEventListener("click", (e) => {
  const btn = e.target.closest(".stop-btn-explore");
  if (!btn) return;

  // Prevent the click from also firing the stop-row's onclick (activateStop)
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

// ── Render connections ────────────────────────────────────────────────────────

// Called once the SSE stream has delivered all route paths for a search.
// Builds both the map layers (polylines + markers) and the sidebar accordion.
function renderConnections(origin, paths) {
  if (!paths.length) {
    connList.innerHTML = `<div id="empty-state"><p>${t("noConnections")}</p></div>`;
    return;
  }

  // ── Map layers ──────────────────────────────────────────────────────────────
  routes = paths.map((path, idx) => {
    const color   = routeColor(idx);
    const latlngs = path.stops.map(s => [s.lat, s.lon]);

    // Thin coloured polyline — the visual track of the route
    const poly = L.polyline(latlngs, { color, weight: 3, opacity: 0.75 }).addTo(map);

    // Transparent fat polyline layered on top purely to enlarge the click/tap area.
    // Without this, thin lines are very hard to tap on mobile.
    const hitPoly = L.polyline(latlngs, {
      color, weight: 20, opacity: 0, interactive: true,
    }).addTo(map);
    hitPoly.bindTooltip(path.line_code || t("trainFallback"), {
      sticky: true, className: "route-tooltip",
    });
    hitPoly.on("click", () => selectRoute(idx));

    // Stop markers are created here but NOT added to the map yet.
    // They are only shown when the user selects this specific route,
    // to avoid cluttering the map when all routes are visible at once.
    const markers = path.stops
      .filter(s => s.id !== origin.id)   // origin already has its own marker
      .map(s => {
        const m = L.marker([s.lat, s.lon], { icon: circleIcon(color) });

        // Store the stop id so we can look up this marker from the sidebar
        m._stopId  = s.id;
        m._station = s;

        m.bindPopup(stationPopupHtml(s));

        // When a marker is clicked, highlight the matching row in the sidebar
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
  });

  // ── Sidebar accordions ──────────────────────────────────────────────────────
  connList.innerHTML = paths.map((path, idx) => {
    const color     = routeColor(idx);
    const stopCount = path.stops.length;
    const firstName = path.stops[0]?.name || "";
    const lastName  = path.stops[stopCount - 1]?.name || "";
    const lineCode  = path.line_code || t("trainFallback");

    // Header label: "Paris — Lyon (TGV INOUI)"
    const label = `${firstName} — ${lastName} `
      + `<span style="opacity:.6;font-weight:400">(${lineCode})</span>`;

    // Build the vertical stop timeline inside the accordion body
    const stopsHtml = path.stops.map((s, si) => {
      const isOrigin = s.id === origin.id;
      const isFirst  = si === 0;
      const isLast   = si === stopCount - 1;

      // Connecting lines above/below each dot; transparent at the endpoints
      const lineAbove = isFirst ? "transparent" : `${color}88`;
      const lineBelow = isLast  ? "transparent" : `${color}88`;

      // Origin dot is inverted (white fill, coloured border) to stand out
      const dotStyle = isOrigin
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
          <div class="stop-name${isOrigin ? " origin" : ""}">${s.name}</div>
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
  }).join("");

  // Wire accordion toggles: opening an accordion selects the route on the map;
  // closing it deselects (resets all routes to their default appearance).
  connList.querySelectorAll("details.route-section").forEach((det, idx) => {
    det.addEventListener("toggle", () => {
      if (_changingRoute) return;   // guard against programmatic open/close
      if (det.open) {
        selectRoute(idx);
      } else if (activeRouteIdx === idx) {
        deselectRoute();
      }
    });
  });
}

// ── Route selection ───────────────────────────────────────────────────────────

// Highlights one route: thickens its polyline, dims the rest, shows its stop
// markers, fits the map to its bounds, and opens its accordion.
function selectRoute(idx) {
  if (_changingRoute || activeRouteIdx === idx) return;
  _changingRoute = true;
  activeRouteIdx = idx;

  // On mobile: if the sidebar is fully closed, peek it so the user knows
  // there are results without it blocking the whole map
  if (isMobile() && sidebarState() === "closed") peekSidebar();

  // Visual feedback: active route is thick and fully opaque; others are dimmed
  routes.forEach((r, i) => {
    r.poly.setStyle(i === idx
      ? { weight: 4, opacity: 1 }
      : { weight: 2, opacity: 0.2 }
    );
  });

  // Show only this route's stop markers (remove all others first)
  routes.forEach(r => r.markers.forEach(m => map.removeLayer(m)));
  routes[idx].markers.forEach(m => m.addTo(map));

  // Zoom the map to fit the selected route with some padding
  map.fitBounds(routes[idx].poly.getBounds(), { padding: [60, 60] });

  // Sync sidebar: open this accordion, close all siblings
  const activeDet = document.getElementById(`route-${idx}`);
  connList.querySelectorAll("details.route-section").forEach(det => {
    det.open = (det === activeDet);
  });
  if (activeDet) activeDet.scrollIntoView({ block: "nearest", behavior: "smooth" });

  _changingRoute = false;
}

// Resets the map to the "all routes" view: restores polyline styles, removes
// markers, closes all accordions, and zooms to fit every route at once.
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

// Called when the user clicks a stop row in the sidebar.
// Highlights the row, pans the map to the matching marker, and opens its popup.
function activateStop(stopId, routeIdx) {
  // Update sidebar highlight
  if (activeStopRow) activeStopRow.classList.remove("active");
  const row = connList.querySelector(
    `[data-stop-id="${CSS.escape(stopId)}"][data-route-idx="${routeIdx}"]`
  );
  if (row) {
    row.classList.add("active");
    row.scrollIntoView({ block: "nearest", behavior: "smooth" });
    activeStopRow = row;
  }

  // Pan to the marker and open its popup
  const marker = routes[routeIdx]?.markers.find(m => m._stopId === stopId);
  if (marker) {
    map.panTo(marker.getLatLng(), { animate: true });
    marker.openPopup();
  }
}

// ── Clear map ─────────────────────────────────────────────────────────────────

// Removes all route layers and resets state. Called at the start of each new
// search so stale data from a previous station doesn't linger on the map.
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

  routes         = [];
  activeRouteIdx = null;
  activeStopRow  = null;
  _changingRoute = false;
}
