// ── Map setup ────────────────────────────────────────────────────────────────
const map = L.map("map", { center: [46.8, 2.3], zoom: 6 });

L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
  attribution: '&copy; <a href="https://carto.com/">CARTO</a>',
  maxZoom: 19,
}).addTo(map);

// ── State ────────────────────────────────────────────────────────────────────
let originMarker  = null;
let selectedStation = null;
let activeStopRow   = null;
let debounceTimer   = null;

// Built once per search result; one entry per route path.
// { poly: L.Polyline, markers: L.Marker[], color: string }
let routes = [];

// Index of the currently highlighted route, or null.
let activeRouteIdx = null;

// Re-entrancy guard: prevents toggle listeners from calling selectRoute/
// deselectRoute while we are already in the middle of one.
let _changingRoute = false;

// ── DOM refs ─────────────────────────────────────────────────────────────────
const input        = document.getElementById("station-input");
const ac           = document.getElementById("autocomplete");
const status       = document.getElementById("status");
const connList     = document.getElementById("connections-list");
const connCount    = document.getElementById("conn-count");
const sidebarLabel = document.getElementById("sidebar-label");
const emptyText    = document.getElementById("empty-state-text");
const dateInput    = document.getElementById("date-input");
const progressWrap = document.getElementById("progress-bar-wrap");
const progressBar  = document.getElementById("progress-bar");
const langSelect   = document.getElementById("lang-select");
const sidebar      = document.getElementById("sidebar");
const sidebarFab   = document.getElementById("sidebar-fab");
const fabCount     = document.getElementById("fab-count");
const sidebarClose = document.getElementById("sidebar-close");
const backdrop     = document.getElementById("sidebar-backdrop");

// ── Mobile sidebar sheet ──────────────────────────────────────────────────────
function openSidebar()  {
  sidebar.classList.add("open");
  backdrop.classList.add("visible");
  sidebarFab.classList.add("hidden");
}
function closeSidebar() {
  sidebar.classList.remove("open");
  backdrop.classList.remove("visible");
  sidebarFab.classList.remove("hidden");
}

sidebarFab.addEventListener("click", openSidebar);
sidebarClose.addEventListener("click", closeSidebar);
backdrop.addEventListener("click", closeSidebar);

// ── i18n application ──────────────────────────────────────────────────────────
function applyLang() {
  document.documentElement.lang = currentLang;
  document.title                = t("pageTitle");
  input.placeholder             = t("searchPlaceholder");
  dateInput.title               = t("dateTitle");
  sidebarLabel.textContent      = t("sidebarHeader");
  emptyText.textContent         = t("emptyStateText");

  // Status: only update the default message, not an active loading/error state
  if (!status.className || status.className === "") {
    status.textContent = t("statusDefault");
  }

  langSelect.value = currentLang;
}

langSelect.addEventListener("change", () => { setLang(langSelect.value); applyLang(); });

// Apply on load
applyLang();

function setProgress(current, total) {
  if (total === 0) return;
  const pct = Math.round((current / total) * 100);
  progressWrap.hidden = false;
  progressBar.style.width = pct + "%";
}

function hideProgress() {
  progressBar.style.width = "100%";
  setTimeout(() => {
    progressWrap.hidden = true;
    progressBar.style.width = "0%";
  }, 400);
}

dateInput.value = new Date().toISOString().slice(0, 10);
dateInput.addEventListener("change", () => {
  if (selectedStation) selectStation(selectedStation);
});

// ── Route color palette ───────────────────────────────────────────────────────
const ROUTE_PALETTE = [
  "#38bdf8", "#f472b6", "#a78bfa", "#34d399", "#fbbf24",
  "#fb923c", "#e879f9", "#4ade80", "#f87171", "#60a5fa",
];
function routeColor(idx) { return ROUTE_PALETTE[idx % ROUTE_PALETTE.length]; }

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
input.addEventListener("blur",  () => setTimeout(() => { ac.style.display = "none"; }, 150));
input.addEventListener("focus", () => { if (ac.innerHTML) ac.style.display = "block"; });

async function fetchSuggestions(q) {
  try {
    const r    = await fetch(`/api/stations?q=${encodeURIComponent(q)}`);
    const data = await r.json();
    if (!r.ok) { showStatus(data.detail || "API error", "error"); return; }
    renderSuggestions(data.stations || []);
  } catch (e) { showStatus("Network error", "error"); }
}

function renderSuggestions(stations) {
  if (!stations.length) { ac.style.display = "none"; return; }
  ac.innerHTML = stations.map(s =>
    `<div class="ac-item" data-id="${s.id}" data-name="${s.name}"
          data-lat="${s.lat}" data-lon="${s.lon}">${s.name}</div>`
  ).join("");
  ac.style.display = "block";
  ac.querySelectorAll(".ac-item").forEach(el => {
    el.addEventListener("mousedown", () => {
      selectStation({ id: el.dataset.id, name: el.dataset.name,
                      lat: parseFloat(el.dataset.lat), lon: parseFloat(el.dataset.lon) });
      input.value = el.dataset.name;
      ac.style.display = "none";
    });
  });
}

// ── Select station & load connections ────────────────────────────────────────
async function selectStation(station) {
  selectedStation = station;
  clearMap();

  originMarker = L.marker([station.lat, station.lon], { icon: originIcon, zIndexOffset: 1000 })
    .addTo(map)
    .bindPopup(`<strong>${station.name}</strong><br>${t("originStation")}`);

  map.setView([station.lat, station.lon], 7, { animate: true });
  showStatus(t("loadingConnections"), "loading");
  connList.innerHTML = `<div id="empty-state"><p>${t("loadingList")}</p></div>`;
  connCount.textContent = "0";
  fabCount.textContent  = "0";

  const dateParam = dateInput.value ? "&date=" + dateInput.value.replace(/-/g, "") : "";
  const url = `/api/connections/stream?station_id=${encodeURIComponent(station.id)}${dateParam}`;

  const es = new EventSource(url);

  es.addEventListener("progress", (e) => {
    const { current, total, message } = JSON.parse(e.data);
    setProgress(current, total);
    showStatus(t("loadingProgress", current, total), "loading");
  });

  es.addEventListener("done", (e) => {
    es.close();
    hideProgress();
    const data = JSON.parse(e.data);
    const conns = data.connections || [];
    const paths = data.route_paths || [];
    showStatus(t("connectionsFound", conns.length), "ok");
    const routeLabel = t("routeCount", paths.length);
    connCount.textContent = routeLabel;
    fabCount.textContent  = paths.length;
    renderConnections(station, paths);
    // On mobile, auto-open the sidebar once results are in
    if (window.matchMedia("(max-width: 600px)").matches) openSidebar();
  });

  es.addEventListener("error", (e) => {
    es.close();
    hideProgress();
    if (e.data) {
      const { detail } = JSON.parse(e.data);
      showStatus(`${t("errorPrefix")}: ${detail}`, "error");
    } else {
      showStatus(t("connectionError"), "error");
    }
  });
}

// ── Render routes ─────────────────────────────────────────────────────────────
function renderConnections(origin, paths) {
  if (!paths.length) {
    connList.innerHTML = `<div id="empty-state"><p>${t("noConnections")}</p></div>`;
    return;
  }

  routes = paths.map((path, idx) => {
    const color   = routeColor(idx);
    const latlngs = path.stops.map(s => [s.lat, s.lon]);

    // Polyline — clicking selects the route
    const poly = L.polyline(latlngs, { color, weight: 3, opacity: 0.75 }).addTo(map);
    poly.bindTooltip(path.line_code || t("trainFallback"), { sticky: true, className: "route-tooltip" });
    poly.on("click", () => selectRoute(idx));

    // Markers — created but NOT added to map until the route is selected
    const markers = path.stops
      .filter(s => s.id !== origin.id)
      .map(s => {
        const m = L.marker([s.lat, s.lon], { icon: circleIcon(color) });
        m._stopId = s.id;
        m.bindPopup(`<strong>${s.name}</strong>`);
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

    return { poly, markers, color, path };
  });

  // ── Sidebar accordions ────────────────────────────────────────────────────
  connList.innerHTML = paths.map((path, idx) => {
    const color     = routeColor(idx);
    const stopCount = path.stops.length;
    const firstName = path.stops[0]?.name || "";
    const lastName  = path.stops[stopCount - 1]?.name || "";
    const lineCode  = path.line_code || t("trainFallback");
    const label = `${firstName} — ${lastName} <span style="opacity:.6;font-weight:400">(${lineCode})</span>`;

    const stopsHtml = path.stops.map((s, si) => {
      const isOrigin = s.id === origin.id;
      const isFirst  = si === 0;
      const isLast   = si === stopCount - 1;
      const lineAbove = isFirst ? "transparent" : `${color}88`;
      const lineBelow = isLast  ? "transparent" : `${color}88`;
      const dotStyle  = isOrigin
        ? `background:#fff;border-color:${color};`
        : `background:${color};border-color:#fff;`;

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

  // Accordion toggle → select / deselect route
  connList.querySelectorAll("details.route-section").forEach((det, idx) => {
    det.addEventListener("toggle", () => {
      if (_changingRoute) return;
      if (det.open) {
        selectRoute(idx);
      } else if (activeRouteIdx === idx) {
        deselectRoute();
      }
    });
  });
}

// ── Route selection ───────────────────────────────────────────────────────────
function selectRoute(idx) {
  if (_changingRoute || activeRouteIdx === idx) return;
  _changingRoute = true;
  activeRouteIdx = idx;

  // Highlight the active polyline, dim all others
  routes.forEach((r, i) => {
    r.poly.setStyle(i === idx
      ? { weight: 4, opacity: 1 }
      : { weight: 2, opacity: 0.2 }
    );
  });

  // Swap markers: remove all, add this route's
  routes.forEach(r => r.markers.forEach(m => map.removeLayer(m)));
  routes[idx].markers.forEach(m => m.addTo(map));

  // Fit the map to the route
  map.fitBounds(routes[idx].poly.getBounds(), { padding: [60, 60] });

  // Sync accordion: open this one, close siblings, scroll into view
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

  // Restore all polylines
  routes.forEach(r => r.poly.setStyle({ weight: 3, opacity: 0.75 }));

  // Hide all markers
  routes.forEach(r => r.markers.forEach(m => map.removeLayer(m)));

  // Close all accordions
  connList.querySelectorAll("details.route-section").forEach(d => { d.open = false; });

  // Zoom out to fit all routes
  if (routes.length) {
    const allBounds = routes.map(r => r.poly.getBounds());
    const combined  = allBounds.reduce((acc, b) => acc.extend(b));
    map.fitBounds(combined, { padding: [60, 60] });
  }

  _changingRoute = false;
}

// ── Open a route accordion from a map popup link ──────────────────────────────
function openRouteAccordion(idx) {
  selectRoute(idx);
}

// ── Activate a stop row from sidebar click ────────────────────────────────────
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
  // Pan map to the corresponding marker and open its popup
  const m = routes[routeIdx]?.markers.find(mk => mk._stopId === stopId);
  if (m) {
    map.panTo(m.getLatLng(), { animate: true });
    m.openPopup();
  }
}

// ── Utilities ─────────────────────────────────────────────────────────────────
function clearMap() {
  if (originMarker) { map.removeLayer(originMarker); originMarker = null; }
  routes.forEach(r => {
    map.removeLayer(r.poly);
    r.markers.forEach(m => map.removeLayer(m));
  });
  routes        = [];
  activeRouteIdx  = null;
  activeStopRow   = null;
  _changingRoute  = false;
}

function showStatus(msg, type = "") {
  status.textContent = msg;
  status.className   = type;
}
