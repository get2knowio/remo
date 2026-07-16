import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./theme/tokens.css";
import "./theme/fonts";
import { AppShell } from "./components/AppShell";
import { restoreUploadedFonts } from "./state/fonts";
import { initSettings } from "./state/settings";
import { initRenderers } from "./terminal/defaultRenderer";

// Dashboard (US1) plus the grid/tab/focused terminal workspace (US2/US3)
// are both wired in here — Dashboard.tsx renders the workspace itself.
const container = document.getElementById("root");
if (!container) {
  throw new Error("Root element #root not found");
}

function mount(): void {
  createRoot(container!).render(
    <StrictMode>
      <AppShell />
    </StrictMode>,
  );
}

// Apply persisted settings (accent + terminal font CSS vars on <html>) and
// re-register any uploaded Nerd Fonts before first paint, then load the
// ghostty-web WASM engine BEFORE mounting so a terminal can be constructed
// synchronously if the user has opted into ghostty (xterm.js is the default
// engine and needs no such init). initRenderers never rejects — it forces
// xterm.js on failure — so this always mounts the app.
initSettings();
void Promise.allSettled([restoreUploadedFonts(), initRenderers()]).then(mount);
