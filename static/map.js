// ── map.js ────────────────────────────────────────────────────────────────────
// Responsible for:
//   • Initialising the Leaflet map instance
//   • Loading the OpenStreetMap tile layer
//   • Exposing map-level helpers used by other modules (clearMap, fitAll)
//
// The `map` constant is declared on `window` so every other script can access
// it without an import system (we are using plain <script> tags).
// ─────────────────────────────────────────────────────────────────────────────

// Centre on metropolitan France, zoom level 6 gives a good country-wide view.
const map = L.map("map", { center: [46.8, 2.3], zoom: 6 });

// OpenStreetMap standard tiles.
// Using three subdomains (a/b/c) distributes tile requests across OSM's CDN,
// which slightly speeds up initial load and avoids per-subdomain rate limits.
// The tile pane is darkened via CSS (filter: brightness) in style.css so that
// the coloured route polylines stand out more clearly against the basemap.
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  maxZoom: 19,
  subdomains: ["a", "b", "c"],
}).addTo(map);
