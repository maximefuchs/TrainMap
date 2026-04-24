// ── Map setup ────────────────────────────────────────────────────────────────
const map = L.map("map", { center: [46.8, 2.3], zoom: 6 });

L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
  attribution: '&copy; <a href="https://carto.com/">CARTO</a>',
  maxZoom: 19,
}).addTo(map);

// ── State ────────────────────────────────────────────────────────────────────
let originMarker = null;
let stopMarkers = [];        // { marker, stopId, routeIndices[] }
let routePolylines = [];     // one Leaflet polyline per route path
let selectedStation = null;
let activeStopRow = null;
let debounceTimer = null;

// stopId → array of route indices that include this stop
let stopRouteMap = {};

// ── DOM refs ─────────────────────────────────────────────────────────────────
const input = document.getElementById("station-input");
const ac = document.getElementById("autocomplete");
const status = document.getElementById("status");
const connList = document.getElementById("connections-list");
const connCount = document.getElementById("conn-count");
const dateInput = document.getElementById("date-input");

// Default date picker to today
dateInput.value = new Date().toISOString().slice(0, 10);

// Reload connections when date changes (if a station is already selected)
dateInput.addEventListener("change", () => {
  if (selectedStation) selectStation(selectedStation);
});

// ── Route color palette (distinct, readable on dark map) ─────────────────────
const ROUTE_PALETTE = [
  "#38bdf8", // sky blue
  "#f472b6", // pink
  "#a78bfa", // violet
  "#34d399", // emerald
  "#fbbf24", // amber
  "#fb923c", // orange
  "#e879f9", // fuchsia
  "#4ade80", // green
  "#f87171", // red
  "#60a5fa", // blue
];

function routeColor(idx) {
  return ROUTE_PALETTE[idx % ROUTE_PALETTE.length];
}

function circleIcon(color) {
  return L.divIcon({
    className: "",
    html: `<div style="
      width:14px;height:14px;border-radius:50%;
      background:${color};border:2px solid #fff;
      box-shadow:0 0 6px ${color}88;
    "></div>`,
    iconSize: [14, 14],
    iconAnchor: [7, 7],
  });
}

const originIcon = L.divIcon({
  className: "",
  html: `<div style="
    width:18px;height:18px;border-radius:50%;
    background:#38bdf8;border:3px solid #fff;
    box-shadow:0 0 12px #38bdf888;
  "></div>`,
  iconSize: [18, 18],
  iconAnchor: [9, 9],
});

// ── Autocomplete ─────────────────────────────────────────────────────────────
input.addEventListener("input", () => {
  clearTimeout(debounceTimer);
  const q = input.value.trim();
  if (q.length < 2) { ac.style.display = "none"; return; }
  debounceTimer = setTimeout(() => fetchSuggestions(q), 250);
});

input.addEventListener("blur", () => setTimeout(() => { ac.style.display = "none"; }, 150));
input.addEventListener("focus", () => { if (ac.innerHTML) ac.style.display = "block"; });

async function fetchSuggestions(q) {
  try {
    const r = await fetch(`/api/stations?q=${encodeURIComponent(q)}`);
    const data = await r.json();
    if (!r.ok) { showStatus(data.detail || "API error", "error"); return; }
    renderSuggestions(data.stations || []);
  } catch (e) {
    showStatus("Network error", "error");
  }
}

function renderSuggestions(stations) {
  if (!stations.length) { ac.style.display = "none"; return; }
  ac.innerHTML = stations.map(s =>
    `<div class="ac-item" data-id="${s.id}" data-name="${s.name}" data-lat="${s.lat}" data-lon="${s.lon}">
      ${s.name}
    </div>`
  ).join("");
  ac.style.display = "block";

  ac.querySelectorAll(".ac-item").forEach(el => {
    el.addEventListener("mousedown", () => {
      selectStation({
        id: el.dataset.id,
        name: el.dataset.name,
        lat: parseFloat(el.dataset.lat),
        lon: parseFloat(el.dataset.lon),
      });
      input.value = el.dataset.name;
      ac.style.display = "none";
    });
  });
}

// ── Select station & load connections ────────────────────────────────────────
async function selectStation(station) {
  selectedStation = station;
  clearMap();

  // Place origin marker
  originMarker = L.marker([station.lat, station.lon], { icon: originIcon, zIndexOffset: 1000 })
    .addTo(map)
    .bindPopup(`<strong>${station.name}</strong><br>Origin station`);

  map.setView([station.lat, station.lon], 7, { animate: true });

  showStatus("Loading connections…", "loading");
  connList.innerHTML = `<div id="empty-state"><p>Loading…</p></div>`;
  connCount.textContent = "0";

  try {
    const dateParam = dateInput.value
      ? "&date=" + dateInput.value.replace(/-/g, "")
      : "";
    const r = await fetch(`/api/connections?station_id=${encodeURIComponent(station.id)}${dateParam}`);
    const data = await r.json();
    if (!r.ok) { showStatus(data.detail || "API error", "error"); return; }

    const conns = data.connections || [];
    const paths = data.route_paths || [];
    showStatus(`${conns.length} direct connections found`, "ok");
    connCount.textContent = `${paths.length} route${paths.length !== 1 ? "s" : ""}`;
    renderConnections(station, conns, paths);
  } catch (e) {
    console.error("selectStation error:", e);
    showStatus(`Error: ${e.message}`, "error");
  }
}

function renderConnections(origin, conns, paths) {
  if (!paths.length) {
    connList.innerHTML = `<div id="empty-state"><p>No direct train connections found.</p></div>`;
    return;
  }

  // Build stopId → [routeIdx, …] index
  stopRouteMap = {};
  paths.forEach((path, idx) => {
    path.stops.forEach(s => {
      if (!stopRouteMap[s.id]) stopRouteMap[s.id] = [];
      if (!stopRouteMap[s.id].includes(idx)) stopRouteMap[s.id].push(idx);
    });
  });

  // Draw polylines + markers, one pass per route
  // Collect unique stops across all routes for marker deduplication
  const markerByStopId = {};

  paths.forEach((path, idx) => {
    const color = routeColor(idx);
    const latlngs = path.stops.map(s => [s.lat, s.lon]);

    const poly = L.polyline(latlngs, { color, weight: 3, opacity: 0.75 }).addTo(map);
    poly.bindTooltip(path.line_code || "Train", { sticky: true, className: "route-tooltip" });
    poly.on("mouseover", () => poly.setStyle({ weight: 5, opacity: 1 }));
    poly.on("mouseout",  () => poly.setStyle({ weight: 3, opacity: 0.75 }));
    routePolylines.push(poly);

    path.stops.forEach(s => {
      if (markerByStopId[s.id]) return; // already placed by an earlier route
      const isOrigin = s.id === origin.id;
      if (isOrigin) return; // origin has its own marker
      const marker = L.marker([s.lat, s.lon], {
        icon: circleIcon(color),
        zIndexOffset: 0,
      }).addTo(map);
      // Popup content is built lazily on first open so stopRouteMap is fully populated
      marker.on("click", () => {
        const routeIndices = stopRouteMap[s.id] || [];
        const lineLinks = routeIndices.map(ri => {
          const p = paths[ri];
          const lc = p.line_code || "Train";
          const fn = p.stops[0]?.name || "";
          const ln = p.stops[p.stops.length - 1]?.name || "";
          const c  = routeColor(ri);
          return `<div style="margin-top:5px">
            <a href="#" onclick="openRouteAccordion(${ri});return false;"
               style="color:${c};text-decoration:none;font-weight:600">
              ▶ ${fn} — ${ln} <span style="opacity:.7">(${lc})</span>
            </a>
          </div>`;
        }).join("");
        marker.bindPopup(`<strong>${s.name}</strong>${lineLinks}`).openPopup();
      });
      markerByStopId[s.id] = marker;
      stopMarkers.push({ marker, stopId: s.id });
    });
  });

  // Build sidebar accordions
  connList.innerHTML = paths.map((path, idx) => {
    const color = routeColor(idx);
    const stopCount = path.stops.length;
    const firstName = path.stops[0]?.name || "";
    const lastName  = path.stops[stopCount - 1]?.name || "";
    const lineCode  = path.line_code || "Train";
    const label = `${firstName} — ${lastName} <span style="opacity:.6;font-weight:400">(${lineCode})</span>`;

    const stopsHtml = path.stops.map((s, si) => {
      const isOrigin = s.id === origin.id;
      const isFirst = si === 0;
      const isLast  = si === stopCount - 1;
      const trackLineAbove = !isFirst
        ? `<div class="stop-track-line" style="background:${color}88"></div>`
        : `<div class="stop-track-line" style="background:transparent"></div>`;
      const trackLineBelow = !isLast
        ? `<div class="stop-track-line" style="background:${color}88"></div>`
        : `<div class="stop-track-line" style="background:transparent"></div>`;
      const dotStyle = isOrigin
        ? `background:#fff;border-color:${color};`
        : `background:${color};border-color:#fff;`;

      return `
        <div class="stop-row${isOrigin ? " active" : ""}"
             data-stop-id="${s.id}"
             data-route-idx="${idx}"
             onclick="activateStop('${s.id}', ${idx})">
          <div class="stop-track">
            ${trackLineAbove}
            <div class="stop-dot${isOrigin ? " origin" : ""}" style="${dotStyle}"></div>
            ${trackLineBelow}
          </div>
          <div class="stop-name${isOrigin ? " origin" : ""}">${s.name}</div>
        </div>`;
    }).join("");

    return `
      <details class="route-section" id="route-${idx}">
        <summary class="route-summary">
          <div class="route-swatch" style="background:${color}"></div>
          <span class="route-label">${label}</span>
          <span class="route-count">${stopCount} stops</span>
          <span class="route-chevron">▶</span>
        </summary>
        <div class="stop-list">${stopsHtml}</div>
      </details>`;
  }).join("");

  // Close all other accordions when one opens
  connList.querySelectorAll("details.route-section").forEach(det => {
    det.addEventListener("toggle", () => {
      if (det.open) {
        connList.querySelectorAll("details.route-section").forEach(other => {
          if (other !== det) other.open = false;
        });
      }
    });
  });
}

// ── Activate a stop from sidebar click ───────────────────────────────────────
// routeIdx is the specific accordion to open (the one that was clicked).
function activateStop(stopId, routeIdx) {
  // Highlight the clicked row
  if (activeStopRow) activeStopRow.classList.remove("active");
  const row = connList.querySelector(
    `[data-stop-id="${CSS.escape(stopId)}"][data-route-idx="${routeIdx}"]`
  );
  if (row) {
    row.classList.add("active");
    row.scrollIntoView({ block: "nearest", behavior: "smooth" });
    activeStopRow = row;
  }

  // Pan map to this stop's marker
  const found = stopMarkers.find(m => m.stopId === stopId);
  if (found) {
    map.panTo(found.marker.getLatLng(), { animate: true });
  } else if (selectedStation && stopId === selectedStation.id) {
    map.panTo([selectedStation.lat, selectedStation.lon], { animate: true });
  }
}

// ── Open a route accordion from a popup link ─────────────────────────────────
function openRouteAccordion(routeIdx) {
  const details = document.getElementById(`route-${routeIdx}`);
  if (details) {
    details.open = true;
    details.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
}

// ── Utilities ─────────────────────────────────────────────────────────────────
function clearMap() {
  if (originMarker) { map.removeLayer(originMarker); originMarker = null; }
  stopMarkers.forEach(({ marker }) => map.removeLayer(marker));
  stopMarkers = [];
  routePolylines.forEach(p => map.removeLayer(p));
  routePolylines = [];
  activeStopRow = null;
  stopRouteMap = {};
}

function showStatus(msg, type = "") {
  status.textContent = msg;
  status.className = type;
}
