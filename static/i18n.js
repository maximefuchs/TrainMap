// ── Translations ──────────────────────────────────────────────────────────────
const TRANSLATIONS = {
  en: {
    pageTitle:          "TrainMap — Direct Connections",
    searchPlaceholder:  (mode) => mode === "bus"
                          ? "Search a city…"
                          : "Search a city or station…",
    dateTitle:          "Schedule date",
    statusDefault:      "Search for a city or station to get started",
    statusPickDate:     "Station selected — pick a date then press Search",
    sidebarHeader:      "Routes",
    emptyStateText:     "Search for a station, then pick a date to see all cities reachable by direct train or bus.",
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
    modeTrain:          "Train",
    modeBus:            "Bus",
    dataCoverage:       "Train data: 🇫🇷 🇮🇹",
    metaDescription:    "Explore all cities reachable by direct train or bus from any station. Live schedules for France and Italy.",
  },
  fr: {
    pageTitle:          "TrainMap — Connexions directes",
    searchPlaceholder:  (mode) => mode === "bus"
                          ? "Rechercher une ville…"
                          : "Rechercher une ville ou une gare…",
    dateTitle:          "Date d'horaire",
    statusDefault:      "Recherchez une ville ou une gare pour commencer",
    statusPickDate:     "Gare sélectionnée — choisissez une date puis lancez la recherche",
    sidebarHeader:      "Lignes",
    emptyStateText:     "Cherchez une gare puis choisissez une date pour voir toutes les villes accessibles en train ou bus direct.",
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
    modeTrain:          "Train",
    modeBus:            "Bus",
    dataCoverage:       "Données train : 🇫🇷 🇮🇹",
    metaDescription:    "Explorez toutes les villes accessibles en train ou bus direct depuis n'importe quelle gare. Horaires en direct pour la France et l'Italie.",
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
