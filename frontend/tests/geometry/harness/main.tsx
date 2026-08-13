// Fixture app for the browser geometry suite (tests/geometry/*.spec.ts).
//
// Mounts the REAL TerminalCard — and therefore the real fit loop
// (src/terminal/fitLoop.ts), the real XtermRenderer, and the real
// TerminalCard.css/WorkspacePane.css box chain — inside the real grid geometry
// `paneLayout()` emits. Nothing under test is re-implemented here; this file
// only supplies the surrounding pane and a handle for the spec to drive it.
//
// jsdom cannot stand in for this: it has no layout engine, so every element
// measures 0x0 and every geometry assertion would pass vacuously. That is why
// this suite exists as a browser test at all.
//
// The backend is intercepted by the spec (page.route + page.routeWebSocket)
// rather than stubbed here, so TerminalConnection runs its real code path and
// the spec can read the resize frames the card actually sends.

import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import { DndContext } from "@dnd-kit/core";
import { TerminalCard } from "../../../src/components/TerminalCard";
import { paneLayout } from "../../../src/components/masterLayout";
import { settingsActions, useSettings } from "../../../src/state/settings";
import type { SessionTarget } from "../../../src/api/client";
import "../../../src/theme/tokens.css";
import "../../../src/components/WorkspacePane.css";

/** Per-card geometry, read back off the DOM.
 *
 * Deliberately renderer-agnostic: it measures `.xterm-screen`, which both of
 * xterm's renderers size to the cell grid, rather than counting `.xterm-rows`
 * children — those exist only under the DOM renderer, and the console runs the
 * WebGL one wherever a GPU context is available, so a row-counting harness
 * silently measures nothing on exactly the setup users have.
 *
 * The row COUNT is not read here at all. The spec takes it from the resize
 * frames the card actually sent, which makes the assertion a three-way
 * agreement — what the emulator painted, what the box allows, and what the
 * remote was told — instead of the emulator marking its own homework. */
export interface CardStats {
  id: string;
  visible: boolean;
  /** Content-box height of `.terminal-card-surface`. */
  surfaceHeight: number;
  /** Painted height of `.xterm-screen`. */
  screenHeight: number;
  /** Row elements the DOM renderer painted, or 0 under WebGL (which draws to a
   * canvas and has no `.xterm-rows`). An INDEPENDENT read of the emulator's own
   * grid — without it, a cell height derived from the reported rows makes the
   * whole assertion circular, and a card that fitted correctly but never told
   * the remote would still look self-consistent. The suite runs a
   * `--disable-gpu` project precisely so this is populated somewhere. */
  paintedRows: number;
}

export interface GeometryHandle {
  setChrome(show: boolean): Promise<void>;
  maximize(id: string | null): Promise<void>;
  settle(): Promise<void>;
  stats(): CardStats[];
}

const TARGET_IDS = ["t0", "t1", "t2", "t3"];

const targets = TARGET_IDS.map(
  (id, i) =>
    ({
      id,
      project: `proj${i}`,
      instance_type: "incus",
      instance_name: "box",
    }) as unknown as SessionTarget,
);

/** Two animation frames: any fit queued by the last mutation has run and
 * painted. The fit loop coalesces to one rAF, so a single frame can be early. */
const settle = (): Promise<void> =>
  new Promise((resolve) =>
    requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
  );

function Harness(): React.JSX.Element {
  const [maximized, setMaximized] = useState<string | null>(null);
  const settings = useSettings();

  const win = window as unknown as {
    __setMaximized: (id: string | null) => void;
    __chromeShown: boolean;
  };
  win.__setMaximized = setMaximized;
  win.__chromeShown = settings.showTileChrome;

  const visible = TARGET_IDS;
  const paneMode = maximized ? "single" : "grid";
  const pane =
    paneMode === "grid" ? paneLayout({ kind: "grid" }, visible, false, 0.6) : null;

  return (
    <DndContext>
      <div
        className={`workspace-body workspace-body--${paneMode}`}
        style={pane?.container}
        data-testid="workspace-body"
      >
        {targets.map((target) => (
          <TerminalCard
            key={target.id}
            target={target}
            mode={paneMode}
            gridArea={pane?.areaById.get(target.id)}
            isVisible={maximized ? target.id === maximized : true}
            isFocused={target.id === "t0"}
            viewState={
              maximized === target.id
                ? "fullscreen"
                : paneMode === "grid"
                  ? "grid"
                  : "normal"
            }
            onClose={() => {}}
            onNormal={() => {}}
            onToggleFullscreen={() => {}}
          />
        ))}
      </div>
    </DndContext>
  );
}

function measure(): CardStats[] {
  return targets.map((target) => {
    const card = document.querySelector<HTMLElement>(
      `[data-testid="terminal-card-${target.id}"]`,
    );
    const surface = document.querySelector<HTMLElement>(
      `[data-testid="terminal-surface-${target.id}"]`,
    );
    const blank: CardStats = {
      id: target.id,
      visible: false,
      surfaceHeight: 0,
      screenHeight: 0,
      paintedRows: 0,
    };
    if (!card || !surface) {
      return blank;
    }
    const visible = card.style.display !== "none";
    const screen = surface.querySelector<HTMLElement>(".xterm-screen");
    if (!screen) {
      return { ...blank, visible };
    }
    return {
      id: target.id,
      visible,
      surfaceHeight: Number(parseFloat(getComputedStyle(surface).height).toFixed(3)),
      screenHeight: Number(screen.getBoundingClientRect().height.toFixed(3)),
      paintedRows: surface.querySelectorAll(".xterm-rows > div").length,
    };
  });
}

const handle: GeometryHandle = {
  async setChrome(show: boolean) {
    const win = window as unknown as { __chromeShown: boolean };
    if (win.__chromeShown !== show) {
      settingsActions.toggleTileChrome();
    }
    await settle();
  },
  async maximize(id: string | null) {
    (window as unknown as { __setMaximized: (v: string | null) => void }).__setMaximized(
      id,
    );
    await settle();
  },
  async settle() {
    await settle();
    await settle();
  },
  stats: measure,
};

(window as unknown as { __geometry: GeometryHandle }).__geometry = handle;

createRoot(document.getElementById("harness") as HTMLElement).render(
  <StrictMode>
    <Harness />
  </StrictMode>,
);
