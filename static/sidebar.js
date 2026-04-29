// ── sidebar.js ────────────────────────────────────────────────────────────────
// Responsible for:
//   • Managing the three-state sidebar panel: closed → peek → open
//   • Wiring the FAB button, close button, backdrop, and drag handle
//   • Providing isMobile() and setSidebar() for use by other modules
//
// On desktop the sidebar is always visible as a fixed left panel.
// On mobile it slides up from the bottom as a sheet with three snap positions:
//   "closed" — fully hidden (only the FAB button is visible)
//   "peek"   — partially visible (~72 px tall), showing the route count
//   "open"   — fully open, covers most of the screen
// ─────────────────────────────────────────────────────────────────────────────

// DOM refs
const sidebar      = document.getElementById("sidebar");
const sidebarFab   = document.getElementById("sidebar-fab");
const fabCount     = document.getElementById("fab-count");
const sidebarClose = document.getElementById("sidebar-close");
const sidebarLabel = document.getElementById("sidebar-label");
const backdrop     = document.getElementById("sidebar-backdrop");
const sidebarHandle = document.getElementById("sidebar-handle");

// Returns true when the viewport is in "mobile" mode (≤ 600 px wide).
function isMobile() {
  return window.matchMedia("(max-width: 600px)").matches;
}

// Returns the current sidebar snap state as a string.
function sidebarState() {
  if (sidebar.classList.contains("open")) return "open";
  if (sidebar.classList.contains("peek")) return "peek";
  return "closed";
}

// Transitions the sidebar to the requested snap state and keeps the FAB /
// backdrop in sync.
function setSidebar(state) {
  sidebar.classList.remove("open", "peek");
  backdrop.classList.remove("visible");
  sidebarFab.classList.remove("hidden");

  if (state === "open") {
    sidebar.classList.add("open");
    backdrop.classList.add("visible");
    sidebarFab.classList.add("hidden");    // FAB is redundant when fully open
  } else if (state === "peek") {
    sidebar.classList.add("peek");
    sidebarFab.classList.add("hidden");    // peek tab replaces the FAB visually
  }
}

function openSidebar()  { setSidebar("open");   }
function peekSidebar()  { setSidebar("peek");   }
function closeSidebar() { setSidebar("closed"); }

// ── Tap interactions ──────────────────────────────────────────────────────────

// Tapping the drag handle cycles: peek → open, open → closed
sidebarHandle.addEventListener("click", () => {
  if (sidebarState() === "peek") openSidebar();
  else closeSidebar();
});

sidebarFab.addEventListener("click", openSidebar);
sidebarClose.addEventListener("click", closeSidebar);
backdrop.addEventListener("click", closeSidebar);   // tap outside → close

// ── Touch-drag on the handle ──────────────────────────────────────────────────
// Lets the user drag the sheet up or down with their finger.
// We track the delta from touchstart, live-translate the sheet, then snap on
// touchend based on whether the drag exceeded 25 % of the sheet height.
(function () {
  // Must match the CSS calc(100% - 72px) peek position.
  const PEEK_OFFSET = 72;

  let startY    = 0;   // clientY at the moment the finger touched
  let startedAt = "";  // sidebar state when the drag began

  sidebarHandle.addEventListener("touchstart", (e) => {
    startY    = e.touches[0].clientY;
    startedAt = sidebarState();
    // Disable CSS transition while dragging so the sheet follows the finger
    sidebar.style.transition = "none";
  }, { passive: true });

  sidebarHandle.addEventListener("touchmove", (e) => {
    const dy       = e.touches[0].clientY - startY;
    const sheetH   = sidebar.offsetHeight;
    // Convert the current state to a pixel offset from the bottom of the screen
    const baseOffset = startedAt === "open" ? 0 : sheetH - PEEK_OFFSET;
    // Clamp between 0 (fully open) and sheetH (fully hidden)
    const raw = Math.max(0, Math.min(sheetH, baseOffset + dy));
    sidebar.style.transform = `translateY(${raw}px)`;
  }, { passive: true });

  sidebarHandle.addEventListener("touchend", (e) => {
    // Restore CSS transition so the snap animation plays
    sidebar.style.transition = "";
    sidebar.style.transform  = "";

    const dy     = e.changedTouches[0].clientY - startY;
    const sheetH = sidebar.offsetHeight;
    const SNAP   = sheetH * 0.25;  // 25 % threshold to trigger a state change

    if (startedAt === "open") {
      // Dragging down from open: snap to closed if past threshold, else stay open
      setSidebar(dy > SNAP ? "closed" : "open");
    } else {
      // Dragging from peek: up → open, down → closed, small movement → stay peek
      if      (dy < -SNAP) setSidebar("open");
      else if (dy >  SNAP) setSidebar("closed");
      else                 setSidebar("peek");
    }
  });
})();
