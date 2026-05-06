// ── app.js ────────────────────────────────────────────────────────────────────
// Entry point — wires together all modules and owns the features that don't
// belong cleanly to a single sub-module:
//   • i18n: applying translations to the DOM
//   • Mode toggle: switches between Train and Bus transport modes
//   • Date picker: default value + triggering a re-fetch on change
//   • Progress bar: show/hide/update during SSE streaming
//   • Status bar: display info / loading / error messages
//   • selectStation(): the main search flow (clears map, opens SSE stream,
//     delegates rendering to routes.js)
//
// Load order in index.html must be:
//   i18n.js → map.js → sidebar.js → autocomplete.js → routes.js → app.js
// ─────────────────────────────────────────────────────────────────────────────

// DOM refs shared across several modules
const status        = document.getElementById("status");
const dateInput     = document.getElementById("date-input");
const progressWrap  = document.getElementById("progress-bar-wrap");
const progressBar   = document.getElementById("progress-bar");
const langSelect    = document.getElementById("lang-select");
const searchBtn     = document.getElementById("search-btn");
const modeBtnTrain  = document.getElementById("mode-train");
const modeBtnBus    = document.getElementById("mode-bus");
const dataCoverageEl = document.getElementById("data-coverage");

// Initial pan-Europe map view
map.setView([48, 10], 5);

// ── Mode toggle (Train / Bus) ─────────────────────────────────────────────────

const _savedMode = localStorage.getItem("mode");
let selectedMode = (_savedMode === "train" || _savedMode === "bus") ? _savedMode : "train";

function _applyModeToggle() {
  modeBtnTrain.classList.toggle("active", selectedMode === "train");
  modeBtnBus.classList.toggle("active",   selectedMode === "bus");
  modeBtnTrain.setAttribute("aria-pressed", selectedMode === "train");
  modeBtnBus.setAttribute("aria-pressed",   selectedMode === "bus");
}
_applyModeToggle();

function _onModeSwitch(newMode) {
  if (selectedMode === newMode) return;
  selectedMode = newMode;
  localStorage.setItem("mode", selectedMode);
  _applyModeToggle();

  // Cancel in-flight stream and reset everything
  cancelActiveStream();
  _pickerOpenedForStation = false;
  clearMap();
  selectedStation = null;
  input.value        = "";
  ac.innerHTML       = "";
  ac.style.display   = "none";
  connCount.textContent = "0";
  fabCount.textContent  = "0";
  connList.innerHTML    = `<div id="empty-state"><p id="empty-state-text">${t("emptyStateText")}</p></div>`;
  showStatus(t("statusDefault"), "");
  updateSearchBtn();
  applyLang();
  input.focus();
}

modeBtnTrain.addEventListener("click", () => _onModeSwitch("train"));
modeBtnBus.addEventListener("click",   () => _onModeSwitch("bus"));

// ── i18n ──────────────────────────────────────────────────────────────────────

function applyLang() {
  const description = t("metaDescription");

  document.documentElement.lang = currentLang;
  document.title                = t("pageTitle");
  input.placeholder             = t("searchPlaceholder", selectedMode);
  dateInput.title               = t("dateTitle");
  sidebarLabel.textContent      = t("sidebarHeader");
  modeBtnTrain.textContent      = t("modeTrain");
  modeBtnBus.textContent        = t("modeBus");
  if (dataCoverageEl) {
    dataCoverageEl.textContent = selectedMode === "bus" ? "" : t("dataCoverage");
    dataCoverageEl.hidden = selectedMode === "bus";
  }
  const currentEmptyText = document.getElementById("empty-state-text");
  if (currentEmptyText) currentEmptyText.textContent = t("emptyStateText");

  document.querySelector('meta[name="description"]').setAttribute("content", description);
  document.querySelector('meta[property="og:title"]').setAttribute("content", t("pageTitle"));
  document.querySelector('meta[property="og:description"]').setAttribute("content", description);

  if (!status.className) {
    status.textContent = t("statusDefault");
  }

  langSelect.value = currentLang;
}

langSelect.addEventListener("change", () => {
  setLang(langSelect.value);
  applyLang();
});

applyLang();   // apply on first load

// ── Date picker ───────────────────────────────────────────────────────────────

const _today = new Date().toISOString().slice(0, 10);
dateInput.min   = _today;
dateInput.value = _today;

let _pickerOpenedForStation = false;

dateInput.addEventListener("change", () => {
  _pickerOpenedForStation = false;
  dateInput.blur();
  updateSearchBtn();
});

dateInput.addEventListener("focusout", () => {
  _pickerOpenedForStation = false;
});

// ── Search button ─────────────────────────────────────────────────────────────

function updateSearchBtn() {
  searchBtn.disabled = !(selectedStation && dateInput.value);
}

function _setSearchLoading(loading) {
  searchBtn.classList.toggle("loading", loading);
  searchBtn.classList.remove("confirm");
  searchBtn.disabled = false;
  searchBtn.setAttribute("aria-label", loading ? "Stop" : "Search");
  if (!loading) updateSearchBtn();
}

function _stopSearch() {
  cancelActiveStream();
  _setSearchLoading(false);
  showStatus(t("statusDefault"), "");
  hideProgress();
}

searchBtn.addEventListener("click", () => {
  if (!searchBtn.classList.contains("loading")) {
    if (selectedStation && dateInput.value) selectStation(selectedStation);
    return;
  }

  // On touch devices (no hover): first tap enters confirm state, second tap stops
  const isTouch = !window.matchMedia("(hover: hover)").matches;
  if (isTouch && !searchBtn.classList.contains("confirm")) {
    searchBtn.classList.add("confirm");
    return;
  }

  _stopSearch();
});

// On touch: reset confirm state if the user taps anywhere else
document.addEventListener("touchstart", (e) => {
  if (!searchBtn.contains(e.target)) {
    searchBtn.classList.remove("confirm");
  }
}, { passive: true });

// ── Progress bar ──────────────────────────────────────────────────────────────

function setProgress(current, total) {
  if (total === 0) return;
  const pct = Math.round((current / total) * 100);
  progressWrap.hidden      = false;
  progressBar.style.width  = pct + "%";
}

function hideProgress() {
  progressBar.style.width = "100%";
  setTimeout(() => {
    progressWrap.hidden     = true;
    progressBar.style.width = "0%";
  }, 400);
}

// ── Status bar ────────────────────────────────────────────────────────────────

function showStatus(msg, type = "") {
  status.textContent = msg;
  status.className   = type;
}

// ── Station selection & SSE streaming ────────────────────────────────────────

let selectedStation = null;
let activeStream = null;

function cancelActiveStream() {
  if (activeStream) {
    activeStream.close();
    activeStream = null;
  }
  hideProgress();
}

function onStationSelected(station) {
  selectedStation = station;

  clearMap();

  originMarker = L.marker([station.lat, station.lon], {
    icon: originIcon,
    zIndexOffset: 1000,
  })
    .addTo(map)
    .bindPopup(`<strong>${station.name}</strong><br>${t("originStation")}`);

  map.setView([station.lat, station.lon], 7, { animate: true });
  showStatus(t("statusPickDate"), "");
  updateSearchBtn();

  _pickerOpenedForStation = true;
  try {
    dateInput.showPicker();
  } catch {
    dateInput.focus();
  }
}

async function selectStation(station) {
  selectedStation = station;

  cancelActiveStream();
  clearMap();

  originMarker = L.marker([station.lat, station.lon], {
    icon: originIcon,
    zIndexOffset: 1000,
  })
    .addTo(map)
    .bindPopup(`<strong>${station.name}</strong><br>${t("originStation")}`);

  showStatus(t("loadingConnections"), "loading");
  connList.innerHTML = `<div id="empty-state"><p>${t("loadingList")}</p></div>`;
  connCount.textContent = "0";
  fabCount.textContent  = "0";

  const dateParam = dateInput.value
    ? "&date=" + dateInput.value.replace(/-/g, "")
    : "";
  const country = station.country || "fr";
  const url = `/api/connections/stream?station_id=${encodeURIComponent(station.id)}&country=${encodeURIComponent(country)}&mode=${encodeURIComponent(selectedMode)}${dateParam}`;

  cancelActiveStream();

  const es = new EventSource(url);
  activeStream = es;
  _setSearchLoading(true);

  es.addEventListener("progress", (e) => {
    const { current, total } = JSON.parse(e.data);
    setProgress(current, total);
    showStatus(t("loadingProgress", current, total), "loading");
  });

  es.addEventListener("route", (e) => {
    // Both modes: each route arrives individually as it is resolved.
    const { route_path, connection } = JSON.parse(e.data);
    if (!route_path) return;

    // Patch origin coords
    if (route_path.stops && route_path.stops.length > 0) {
      const first = route_path.stops[0];
      if ((!first.lat && !first.lon) || first.id === station.id) {
        first.lat  = station.lat;
        first.lon  = station.lon;
        first.name = first.name || station.name;
      }
    }

    addRoute(station, route_path);

    // Update running counts
    const count = routes.length;
    connCount.textContent = t("routeCount", count);
    fabCount.textContent  = count;
  });

  es.addEventListener("done", (e) => {
    es.close();
    activeStream = null;
    _setSearchLoading(false);
    updateSearchBtn();
    hideProgress();

    const data  = JSON.parse(e.data);
    const conns = data.connections || [];

    // Routes were already rendered incrementally via 'route' events for both modes.
    // On 'done', just update final counts and status. If no routes were streamed
    // (e.g. no route_paths available), fall back to dot-only bus rendering.
    if (routes.length === 0 && conns.length) {
      renderConnections(station, [], conns, selectedMode);
    }

    const displayCount    = routes.length || conns.length;
    connCount.textContent = t("routeCount", displayCount);
    fabCount.textContent  = displayCount;
    showStatus(t("connectionsFound", conns.length || routes.length), "ok");

    if (isMobile()) peekSidebar();
  });

  es.addEventListener("error", (e) => {
    es.close();
    activeStream = null;
    _setSearchLoading(false);
    updateSearchBtn();
    hideProgress();
    if (e.data) {
      const { detail } = JSON.parse(e.data);
      showStatus(`${t("errorPrefix")}: ${detail}`, "error");
    } else {
      showStatus(t("connectionError"), "error");
    }
  });
}
