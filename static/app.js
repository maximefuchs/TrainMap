// ── app.js ────────────────────────────────────────────────────────────────────
// Entry point — wires together all modules and owns the features that don't
// belong cleanly to a single sub-module:
//   • i18n: applying translations to the DOM
//   • Country selector: switches data source between France and Italy
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
const countrySelect = document.getElementById("country-select");
const searchBtn     = document.getElementById("search-btn");
const modeBtnTrain  = document.getElementById("mode-train");
const modeBtnBus    = document.getElementById("mode-bus");

// ── Country selection ─────────────────────────────────────────────────────────

// Default map centres and zoom levels per country
const COUNTRY_MAP_VIEW = {
  fr: { center: [46.5, 2.5],  zoom: 6 },
  it: { center: [42.5, 12.5], zoom: 6 },
};

// Persisted across sessions; defaults to France
const _savedCountry = localStorage.getItem("country");
let selectedCountry = (_savedCountry === "fr" || _savedCountry === "it")
  ? _savedCountry
  : "fr";

countrySelect.value = selectedCountry;

countrySelect.addEventListener("change", () => {
  selectedCountry = countrySelect.value;
  localStorage.setItem("country", selectedCountry);

  // Cancel any in-flight search before clearing the map
  cancelActiveStream();
  _pickerOpenedForStation = false;

  // Clear map layers, markers, and sidebar route list
  clearMap();
  selectedStation = null;

  // Clear the station input and collapse the autocomplete dropdown
  input.value        = "";
  ac.innerHTML       = "";
  ac.style.display   = "none";

  // Reset sidebar counts and list to empty state
  connCount.textContent = "0";
  fabCount.textContent  = "0";
  connList.innerHTML    = `<div id="empty-state"><p id="empty-state-text">${t("emptyStateText")}</p></div>`;

  // Reset status bar to idle
  showStatus(t("statusDefault"), "");
  updateSearchBtn();

  // Re-centre the map for the new country
  const view = COUNTRY_MAP_VIEW[selectedCountry] || COUNTRY_MAP_VIEW.fr;
  map.setView(view.center, view.zoom, { animate: true });

  // Update all translatable strings (placeholders, title, meta…)
  applyLang();

  // Step 1 done → move focus to the station search
  input.focus();
});

// Set initial map view for the persisted country
(function () {
  const view = COUNTRY_MAP_VIEW[selectedCountry] || COUNTRY_MAP_VIEW.fr;
  map.setView(view.center, view.zoom);
})();

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
  const description = t("metaDescription", selectedCountry);

  document.documentElement.lang = currentLang;
  document.title                = t("pageTitle", selectedCountry);
  input.placeholder             = t("searchPlaceholder", selectedCountry, selectedMode);
  dateInput.title               = t("dateTitle");
  sidebarLabel.textContent      = t("sidebarHeader");
  modeBtnTrain.textContent      = t("modeTrain");
  modeBtnBus.textContent        = t("modeBus");
  const currentEmptyText = document.getElementById("empty-state-text");
  if (currentEmptyText) currentEmptyText.textContent = t("emptyStateText");

  document.querySelector('meta[name="description"]').setAttribute("content", description);
  document.querySelector('meta[property="og:title"]').setAttribute("content", t("pageTitle", selectedCountry));
  document.querySelector('meta[property="og:description"]').setAttribute("content", description);

  if (!status.className) {
    status.textContent = t("statusDefault");
  }

  langSelect.value    = currentLang;
  countrySelect.value = selectedCountry;
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

searchBtn.addEventListener("click", () => {
  if (selectedStation && dateInput.value) selectStation(selectedStation);
});

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
  const url = `/api/connections/stream?station_id=${encodeURIComponent(station.id)}&country=${encodeURIComponent(selectedCountry)}&mode=${encodeURIComponent(selectedMode)}${dateParam}`;

  cancelActiveStream();

  const es = new EventSource(url);
  activeStream = es;

  es.addEventListener("progress", (e) => {
    const { current, total } = JSON.parse(e.data);
    setProgress(current, total);
    showStatus(t("loadingProgress", current, total), "loading");
  });

  es.addEventListener("done", (e) => {
    es.close();
    activeStream = null;
    hideProgress();

    const data  = JSON.parse(e.data);
    const conns = data.connections  || [];
    const paths = data.route_paths  || [];

    // The backend resolves origin coords from the city cache. For bus mode the
    // cache is built without a country filter and may miss some cities. Patch
    // the first stop of every route path with the known coords from the
    // autocomplete selection so the polyline always starts at the right place.
    paths.forEach(path => {
      if (path.stops && path.stops.length > 0) {
        path.stops[0].lat  = station.lat;
        path.stops[0].lon  = station.lon;
        path.stops[0].name = path.stops[0].name || station.name;
      }
    });

    showStatus(t("connectionsFound", conns.length), "ok");

    const displayCount    = paths.length || conns.length;
    const routeLabel      = t("routeCount", displayCount);
    connCount.textContent = routeLabel;
    fabCount.textContent  = displayCount;

    renderConnections(station, paths, conns, selectedMode);

    if (isMobile()) peekSidebar();
  });

  es.addEventListener("error", (e) => {
    es.close();
    activeStream = null;
    hideProgress();
    if (e.data) {
      const { detail } = JSON.parse(e.data);
      showStatus(`${t("errorPrefix")}: ${detail}`, "error");
    } else {
      showStatus(t("connectionError"), "error");
    }
  });
}
