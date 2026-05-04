// ── Translations ──────────────────────────────────────────────────────────────
const TRANSLATIONS = {
  en: {
    pageTitle:          (country) => country === "it"
                          ? "Train Map — Direct Connections in Italy"
                          : "Train Map — Direct Connections in France",
    searchPlaceholder:  (country, mode) => {
                          if (mode === "bus") return country === "it"
                            ? "Search an Italian city…"
                            : "Search a French city…";
                          return country === "it"
                            ? "Search an Italian city or station…"
                            : "Search a French city or station…";
                        },
    dateTitle:          "Schedule date",
    statusDefault:      "Pick a country, then search for a city",
    statusPickDate:     "Station selected — pick a date then press Search",
    sidebarHeader:      "Routes",
    emptyStateText:     "Select a country, search for a station, then pick a date to see all cities reachable by direct train.",
    loadingConnections: "Loading connections…",
    loadingList:        "Loading…",
    loadingProgress:    (current, total) => `Fetching routes… ${current}/${total}`,
    connectionsFound:   (n) => `${n} direct connection${n !== 1 ? "s" : ""} found`,
    routeCount:         (n) => `${n} route${n !== 1 ? "s" : ""}`,
    noConnections:      "No direct connections found.",
    originStation:      "Origin station",
    trainFallback:      "Train",
    stopCount:          (n) => `${n} stop${n !== 1 ? "s" : ""}`,
    errorPrefix:        "Error",
    connectionError:    "Connection error",
    exploreFrom:        "Explore from here",
    countryLabel:       "Country",
    modeTrain:          "Train",
    modeBus:            "Bus",
    metaDescription:    (country) => country === "it"
                          ? "Explore all cities reachable by direct train from any Italian station. Live schedules powered by the Navitia API."
                          : "Explore all cities reachable by direct train from any French station. Live schedules powered by the SNCF API.",
  },
  fr: {
    pageTitle:          (country) => country === "it"
                          ? "Train Map — Connexions directes en Italie"
                          : "Train Map — Connexions directes en France",
    searchPlaceholder:  (country, mode) => {
                          if (mode === "bus") return country === "it"
                            ? "Rechercher une ville en Italie…"
                            : "Rechercher une ville…";
                          return country === "it"
                            ? "Rechercher une ville ou une gare en Italie…"
                            : "Rechercher une ville ou une gare…";
                        },
    dateTitle:          "Date d'horaire",
    statusDefault:      "Choisissez un pays, puis cherchez une ville",
    statusPickDate:     "Gare sélectionnée — choisissez une date puis lancez la recherche",
    sidebarHeader:      "Lignes",
    emptyStateText:     "Sélectionnez un pays, cherchez une gare, puis choisissez une date pour voir toutes les villes accessibles en train direct.",
    loadingConnections: "Chargement des connexions…",
    loadingList:        "Chargement…",
    loadingProgress:    (current, total) => `Récupération des lignes… ${current}/${total}`,
    connectionsFound:   (n) => `${n} connexion${n !== 1 ? "s" : ""} directe${n !== 1 ? "s" : ""} trouvée${n !== 1 ? "s" : ""}`,
    routeCount:         (n) => `${n} ligne${n !== 1 ? "s" : ""}`,
    noConnections:      "Aucune connexion directe trouvée.",
    originStation:      "Gare de départ",
    trainFallback:      "Train",
    stopCount:          (n) => `${n} arrêt${n !== 1 ? "s" : ""}`,
    errorPrefix:        "Erreur",
    connectionError:    "Erreur de connexion",
    exploreFrom:        "Explorer depuis ici",
    countryLabel:       "Pays",
    modeTrain:          "Train",
    modeBus:            "Bus",
    metaDescription:    (country) => country === "it"
                          ? "Explorez toutes les villes accessibles en train direct depuis n'importe quelle gare italienne. Horaires en temps réel via l'API Navitia."
                          : "Explorez toutes les villes accessibles en train direct depuis n'importe quelle gare française. Horaires en temps réel via l'API SNCF.",
  },
};

// Detect browser language, default to English
const _saved = localStorage.getItem("lang");
const _browser = navigator.language?.startsWith("fr") ? "fr" : "en";
let currentLang = (_saved === "fr" || _saved === "en") ? _saved : _browser;

function t(key, ...args) {
  const entry = TRANSLATIONS[currentLang][key] ?? TRANSLATIONS.en[key];
  return typeof entry === "function" ? entry(...args) : entry;
}

function setLang(lang) {
  currentLang = lang;
  localStorage.setItem("lang", lang);
}
