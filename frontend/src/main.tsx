import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Dashboard } from "./components/Dashboard";
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
      <Dashboard />
    </StrictMode>,
  );
}

// Load the ghostty-web WASM engine (the default terminal renderer, decision #6)
// BEFORE mounting, so terminals can be constructed synchronously. initRenderers
// never rejects — it falls back to xterm.js on failure — so `.finally` always
// mounts the app.
void initRenderers().finally(mount);
