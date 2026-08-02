import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./theme/tokens.css";
import "./theme/fonts";
import { AppRoot } from "./components/AppRoot";
import { restoreUploadedFonts } from "./state/fonts";
import { initSettings } from "./state/settings";

// AppRoot gates on the service state (011-web-adopt): the awaiting-adoption
// page while unconfigured, otherwise the full console shell — dashboard (US1)
// plus the grid/tab/focused terminal workspace (US2/US3).
const container = document.getElementById("root");
if (!container) {
  throw new Error("Root element #root not found");
}

function mount(): void {
  createRoot(container!).render(
    <StrictMode>
      <AppRoot />
    </StrictMode>,
  );
}

// Apply persisted settings (site theme + accent + terminal font CSS vars on
// <html>) and re-register any uploaded Nerd Fonts before first paint, then
// mount. The font restore is allowed to fail (allSettled) — a missing uploaded
// font must never keep the console from starting.
initSettings();
void Promise.allSettled([restoreUploadedFonts()]).then(mount);
