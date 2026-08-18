// Settings ▸ Diagnostics: copy a read-only snapshot of what the console is
// currently doing, for pasting into a bug report.
//
// The blob itself (and the contract for what it may never contain) lives in
// state/diagnostics.ts. This component is only the affordance: resolve the
// service version first — the snapshot builder is synchronous and cannot await
// it — then write the JSON to the clipboard.
//
// `window.__remo.diagnostics()` is the same data without the UI, for when the
// console is too broken to reach Settings; it is named here so the devtools
// path is discoverable rather than folklore.

import { useCallback, useEffect, useRef, useState } from "react";
import { copyText } from "../lib/clipboard";
import { collectDiagnostics, ensureServiceVersion } from "../state/diagnostics";
import "./DiagnosticsSection.css";

export function DiagnosticsSection(): JSX.Element {
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const [version, setVersion] = useState<string | null>(null);
  const resetHandle = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => {
    let live = true;
    void ensureServiceVersion().then((v) => {
      if (live) {
        setVersion(v);
      }
    });
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => () => clearTimeout(resetHandle.current), []);

  const copy = useCallback(async () => {
    clearTimeout(resetHandle.current);
    // Await the version so a snapshot copied from here names the build, even
    // on the first click after a cold load.
    setVersion(await ensureServiceVersion());
    // copyText returns false rather than throwing: this console commonly runs
    // over plain http on a LAN, where the async clipboard API is unavailable.
    const ok = await copyText(JSON.stringify(collectDiagnostics(), null, 2));
    setCopyState(ok ? "copied" : "failed");
    resetHandle.current = setTimeout(() => setCopyState("idle"), 2_000);
  }, []);

  return (
    <div className="diagnostics">
      <button
        type="button"
        className="diagnostics-btn"
        data-testid="copy-diagnostics"
        onClick={() => void copy()}
      >
        {copyState === "copied"
          ? "Copied ✓"
          : copyState === "failed"
            ? "Copy refused — select and copy from devtools"
            : "Copy diagnostics"}
      </button>
      <p className="diagnostics-meta">
        {version ? `Service v${version}` : "Service version unavailable"}
        {" · "}
        in devtools: <code>copy(JSON.stringify(__remo.diagnostics(), null, 2))</code>
      </p>
    </div>
  );
}
