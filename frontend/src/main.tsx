import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Dashboard } from "./components/Dashboard";

// Dashboard (US1) plus the grid/tab/focused terminal workspace (US2/US3)
// are both wired in here — Dashboard.tsx renders the workspace itself.
const container = document.getElementById("root");
if (!container) {
  throw new Error("Root element #root not found");
}

createRoot(container).render(
  <StrictMode>
    <Dashboard />
  </StrictMode>,
);
