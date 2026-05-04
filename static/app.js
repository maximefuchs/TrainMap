// ── app.js ────────────────────────────────────────────────────────────────────
// Entry point — wires together all modules and owns the features that don't
// belong cleanly to a single sub-module:
//   • i18n: applying translations to the DOM
//   • Country selector: switches data source between France and Italy
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
const emptyText     = document.getElementById("empty-state-text");

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

  // Clear current results and reset state
  clearMap();
  selectedStation = null;
  input.value     = "";

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

// ── i18n ──────────────────────────────────────────────────────────────────────

// Pushes the current language's strings into every translatable DOM node,
// including <meta> tags used by search engines and social media previews.
// Called on page load, whenever the user switches language, and whenever the
// user switches country.
function applyLang() {
  const description = t("metaDescription", selectedCountry);

  document.documentElement.lang = currentLang;
  document.title                = t("pageTitle", selectedCountry);
  input.placeholder             = t("searchPlaceholder", selectedCountry);
  dateInput.title               = t("dateTitle");
  sidebarLabel.textContent      = t("sidebarHeader");
  emptyText.textContent         = t("emptyStateText");

  // Update <meta name="description"> and Open Graph tags so that search
  // engines and social-media link previews reflect the active language.
  document.querySelector('meta[name="description"]').setAttribute("content", description);
  document.querySelector('meta[property="og:title"]').setAttribute("content", t("pageTitle", selectedCountry));
  document.querySelector('meta[property="og:description"]').setAttribute("content", description);

  // Only overwrite the status bar when it is in the idle (no class) state;
  // leave active loading / error messages untouched.
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

// Default to today's date.
dateInput.value = new Date().toISOString().slice(0, 10);

// Step 3: date chosen → launch the search.
dateInput.addEventListener("change", () => {
  if (selectedStation) selectStation(selectedStation);
});

// ── Progress bar ──────────────────────────────────────────────────────────────

// Updates the progress bar width during route streaming.
// `current` and `total` are the route indices delivered by SSE "progress" events.
function setProgress(current, total) {
  if (total === 0) return;
  const pct = Math.round((current / total) * 100);
  progressWrap.hidden      = false;
  progressBar.style.width  = pct + "%";
}

// Animates the bar to 100 %, then hides it after a short delay so the user sees
// a satisfying "done" flash rather than an abrupt disappearance.
function hideProgress() {
  progressBar.style.width = "100%";
  setTimeout(() => {
    progressWrap.hidden     = true;
    progressBar.style.width = "0%";
  }, 400);
}

// ── Status bar ────────────────────────────────────────────────────────────────

// Displays a message in the top-bar status area.
// `type` maps to a CSS class: "" (idle) | "loading" | "ok" | "error"
function showStatus(msg, type = "") {
  status.textContent = msg;
  status.className   = type;
}

// ── Station selection & SSE streaming ────────────────────────────────────────

// Holds the station object from the last successful autocomplete selection.
// Used by the date-picker listener to re-run the search when the date changes.
let selectedStation = null;

// Step 2: called by autocomplete when the user picks a station.
// Places the origin marker and shifts focus to the date picker — does NOT
// launch the search yet (that only happens when the date is confirmed).
function onStationSelected(station) {
  selectedStation = station;

  clearMap();

  // Place the origin marker so the user gets immediate visual feedback.
  originMarker = L.marker([station.lat, station.lon], {
    icon: originIcon,
    zIndexOffset: 1000,
  })
    .addTo(map)
    .bindPopup(`<strong>${station.name}</strong><br>${t("originStation")}`);

  map.setView([station.lat, station.lon], 7, { animate: true });
  showStatus(t("statusPickDate"), "");

  // Step 2 done → move focus to the date picker
  dateInput.focus();
}

// Step 3 / main search flow: launches the SSE stream for the selected station
// and date, progressively rendering routes as they arrive.
async function selectStation(station) {
  selectedStation = station;

  showStatus(t("loadingConnections"), "loading");
  connList.innerHTML = `<div id="empty-state"><p>${t("loadingList")}</p></div>`;
  connCount.textContent = "0";
  fabCount.textContent  = "0";

  const dateParam = dateInput.value
    ? "&date=" + dateInput.value.replace(/-/g, "")
    : "";
  const url = `/api/connections/stream?station_id=${encodeURIComponent(station.id)}&country=${encodeURIComponent(selectedCountry)}${dateParam}`;

  const es = new EventSource(url);

  // "progress" events arrive once per route as the backend finishes fetching it.
  // We use them to drive the progress bar and keep the status text up to date.
  es.addEventListener("progress", (e) => {
    const { current, total } = JSON.parse(e.data);
    setProgress(current, total);
    showStatus(t("loadingProgress", current, total), "loading");
  });

  // "done" is sent once, carrying the full result payload, after all routes have
  // been processed. We close the stream and hand off to renderConnections().
  es.addEventListener("done", (e) => {
    es.close();
    hideProgress();

    const data  = JSON.parse(e.data);
    const conns = data.connections  || [];
    const paths = data.route_paths  || [];

    showStatus(t("connectionsFound", conns.length), "ok");

    const routeLabel      = t("routeCount", paths.length);
    connCount.textContent = routeLabel;
    fabCount.textContent  = paths.length;

    renderConnections(station, paths);

    // On mobile, peek the sidebar so the user knows results are available
    // without the sheet blocking the whole map.
    if (isMobile()) peekSidebar();
  });

  // "error" can be either a server-side error (e.data contains JSON) or a
  // network failure (e.data is empty/null).
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
