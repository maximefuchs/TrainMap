// ── Translations ──────────────────────────────────────────────────────────────
const TRANSLATIONS = {
  en: {
    pageTitle:          "Train Map — Direct Connections in France",
    searchPlaceholder:  "Search a French city or station…",
    dateTitle:          "Schedule date",
    statusDefault:      "Enter a city to explore connections",
    sidebarHeader:      "Routes",
    emptyStateText:     "Search for a station above to see all cities reachable by direct train.",
    loadingConnections: "Loading connections…",
    loadingList:        "Loading…",
    loadingProgress:    (current, total) => `Fetching routes… ${current}/${total}`,
    connectionsFound:   (n) => `${n} direct connection${n !== 1 ? "s" : ""} found`,
    routeCount:         (n) => `${n} route${n !== 1 ? "s" : ""}`,
    noConnections:      "No direct train connections found.",
    originStation:      "Origin station",
    trainFallback:      "Train",
    stopCount:          (n) => `${n} stop${n !== 1 ? "s" : ""}`,
    errorPrefix:        "Error",
    connectionError:    "Connection error",
    exploreFrom:        "Explore from here",
  },
  fr: {
    pageTitle:          "Train Map — Connexions directes en France",
    searchPlaceholder:  "Rechercher une ville ou une gare…",
    dateTitle:          "Date d'horaire",
    statusDefault:      "Entrez une ville pour explorer les connexions",
    sidebarHeader:      "Lignes",
    emptyStateText:     "Recherchez une gare ci-dessus pour voir toutes les villes accessibles en train direct.",
    loadingConnections: "Chargement des connexions…",
    loadingList:        "Chargement…",
    loadingProgress:    (current, total) => `Récupération des lignes… ${current}/${total}`,
    connectionsFound:   (n) => `${n} connexion${n !== 1 ? "s" : ""} directe${n !== 1 ? "s" : ""} trouvée${n !== 1 ? "s" : ""}`,
    routeCount:         (n) => `${n} ligne${n !== 1 ? "s" : ""}`,
    noConnections:      "Aucune connexion ferroviaire directe trouvée.",
    originStation:      "Gare de départ",
    trainFallback:      "Train",
    stopCount:          (n) => `${n} arrêt${n !== 1 ? "s" : ""}`,
    errorPrefix:        "Erreur",
    connectionError:    "Erreur de connexion",
    exploreFrom:        "Explorer depuis ici",
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
