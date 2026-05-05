// ── autocomplete.js ───────────────────────────────────────────────────────────
// Responsible for:
//   • Listening to the station search input
//   • Debouncing keystrokes and fetching suggestions from /api/stations
//   • Rendering the dropdown and wiring each item to selectStation()
//
// Depends on: selectStation() defined in app.js, t() from i18n.js
// ─────────────────────────────────────────────────────────────────────────────

// DOM refs
const input = document.getElementById("station-input");
const ac    = document.getElementById("autocomplete");

let debounceTimer = null;

// ── Input listeners ───────────────────────────────────────────────────────────

// Show a spinner immediately on each keystroke, then debounce the actual fetch
// so we don't hammer the API while the user is still typing.
input.addEventListener("input", () => {
  clearTimeout(debounceTimer);
  const q = input.value.trim();

  if (q.length < 2) {
    ac.style.display = "none";
    return;
  }

  // Optimistic spinner — visible right away while we wait for the debounce
  ac.innerHTML     = `<div class="ac-loading"><span class="ac-spinner"></span></div>`;
  ac.style.display = "block";

  debounceTimer = setTimeout(() => fetchSuggestions(q), 250);
});

// Hide the dropdown when the input loses focus.
// The 150 ms delay gives mousedown on a suggestion time to fire first.
input.addEventListener("blur",  () => setTimeout(() => { ac.style.display = "none"; }, 150));

// Re-show the dropdown if the input is re-focused and already has content.
input.addEventListener("focus", () => { if (ac.innerHTML) ac.style.display = "block"; });

// ── API fetch ─────────────────────────────────────────────────────────────────

async function fetchSuggestions(q) {
  try {
    const res  = await fetch(`/api/stations?q=${encodeURIComponent(q)}&country=${encodeURIComponent(selectedCountry)}&mode=${encodeURIComponent(selectedMode)}`);
    const data = await res.json();

    if (!res.ok) {
      showStatus(data.detail || "API error", "error");
      return;
    }

    renderSuggestions(data.stations || []);
  } catch {
    showStatus(t("connectionError"), "error");
  }
}

// ── Rendering ─────────────────────────────────────────────────────────────────

// The SNCF API sometimes returns station names like "Paris (Paris)" where the
// qualifier in parentheses is just a repetition of the city name. This strips
// those redundant qualifiers while keeping meaningful ones (e.g. "Lyon (Rhône)").
function cleanStationName(name) {
  return name.replace(/\s*\(([^)]+)\)$/, (_, qualifier) => {
    const base = name.replace(/\s*\([^)]+\)$/, "").trim();
    return qualifier.trim().toLowerCase() === base.toLowerCase()
      ? ""
      : ` (${qualifier})`;
  }).trim();
}

function renderSuggestions(stations) {
  if (!stations.length) {
    ac.style.display = "none";
    return;
  }

  ac.innerHTML = stations.map(s => {
    const display = cleanStationName(s.name);
    return `<div class="ac-item"
                 data-id="${s.id}"
                 data-name="${display}"
                 data-lat="${s.lat}"
                 data-lon="${s.lon}">${display}</div>`;
  }).join("");

  ac.style.display = "block";

  // Wire each suggestion: mousedown fires before blur, so the item is clickable
  // even though the input loses focus at the same time.
  ac.querySelectorAll(".ac-item").forEach(el => {
    el.addEventListener("mousedown", () => {
      const station = {
        id:  el.dataset.id,
        name: el.dataset.name,
        lat: parseFloat(el.dataset.lat),
        lon: parseFloat(el.dataset.lon),
      };
      input.value      = station.name;
      ac.style.display = "none";
      onStationSelected(station);
    });
  });
}
