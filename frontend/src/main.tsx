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
// ghostty-web WASM engine (the default renderer, decision #6) BEFORE mounting
// so terminals can be constructed synchronously. initRenderers never rejects —
// it falls back to xterm.js on failure — so `.finally` always mounts the app.
initSettings();
void Promise.allSettled([restoreUploadedFonts(), initRenderers()]).then(mount);
