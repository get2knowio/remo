// A plain login shell on the host itself, embedded in the host detail page
// (plan §2.4). Deliberately NOT a workspace TerminalCard: that component is
// coupled to SessionTarget (workspace store keys, theme-override pruning
// against discovery.targets, dnd ids, the rail join) and a synthetic target
// would fight all of it. The reusable terminal core is the trio composed
// here — TerminalConnection ({kind:"host_shell"}) + createDefaultRenderer +
// createFitLoop — matching Proxmox's host-shell-in-host-context model.
//
// A plain `exit` is a neutral outcome (the operator finished), not an error.

import { useEffect, useRef, useState } from "react";
import type { TypedError } from "../api/client";
import {
  effectiveTerminalTheme,
  getSettings,
  terminalFontOptions,
} from "../state/settings";
import { createDefaultRenderer } from "../terminal/defaultRenderer";
import { createFitLoop } from "../terminal/fitLoop";
import { TerminalConnection, type TerminalConnectionState } from "../terminal/TerminalConnection";
import "./HostDetailPage.css";

const DEFAULT_COLS = 80;
const DEFAULT_ROWS = 24;

interface HostShellPanelProps {
  instanceId: string;
  instanceName: string;
  onClose: () => void;
}

export function HostShellPanel({
  instanceId,
  instanceName,
  onClose,
}: HostShellPanelProps): JSX.Element {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [state, setState] = useState<TerminalConnectionState>("connecting");
  const [exitCode, setExitCode] = useState<number | null>(null);
  const [error, setError] = useState<TypedError | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) {
      return undefined;
    }
    setState("connecting");
    setExitCode(null);
    setError(null);

    // Seeded from the settings store at mount; a shell panel is short-lived,
    // so live font/theme re-application (TerminalCard's job) is not carried.
    const settings = getSettings();
    const adapter = createDefaultRenderer(
      terminalFontOptions(settings),
      effectiveTerminalTheme(settings).colors,
    );
    adapter.open(container);

    const connection = new TerminalConnection(
      { kind: "host_shell", instanceId },
      DEFAULT_COLS,
      DEFAULT_ROWS,
      {
        onData: (data) => adapter.write(data),
        onExit: (code) => setExitCode(code),
        onError: (typedError) => setError(typedError),
        onStateChange: setState,
      },
    );

    const fitLoop = createFitLoop({
      getAdapter: () => adapter,
      getContainer: () => container,
      onGridChange: (dims) => connection.sendResize(dims.cols, dims.rows),
    });
    const unsubscribeInput = adapter.onData((data) => connection.sendInput(data));
    const observer = new ResizeObserver(() => fitLoop.schedule());
    observer.observe(container);

    void connection.connect();
    fitLoop.schedule();
    adapter.focus();

    return () => {
      observer.disconnect();
      unsubscribeInput();
      fitLoop.dispose();
      void connection.close();
      adapter.dispose();
    };
  }, [instanceId]);

  const exited = exitCode !== null;

  return (
    <div className="hd-shell" data-testid="host-shell-panel">
      <div className="hd-shell-head">
        <span className="hd-shell-title">
          &gt;_ Shell on <code>{instanceName}</code>
        </span>
        <span className="hd-shell-state">{exited ? "exited" : state}</span>
        <button type="button" className="hd-btn" data-testid="host-shell-close" onClick={onClose}>
          Close
        </button>
      </div>
      <div className="hd-shell-body">
        <div className="hd-shell-surface" ref={containerRef} data-testid="host-shell-surface" />
        {exited && (
          <div className="hd-shell-note" data-testid="host-shell-exited">
            shell exited
            {exitCode !== 0 ? ` (status ${exitCode})` : ""} — close this panel, or reopen the
            shell from the header
          </div>
        )}
        {!exited && error && (
          <div className="hd-shell-note hd-shell-note--error">
            {error.message}
            {error.remediation ? ` — ${error.remediation}` : ""}
          </div>
        )}
      </div>
    </div>
  );
}
