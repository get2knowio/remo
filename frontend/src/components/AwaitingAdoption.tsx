// Awaiting-adoption page (011-web-adopt, FR-004 / research R12).
//
// Rendered by AppRoot instead of the console shell while GET /api/v1/ready
// reports status "unconfigured": a short explanation, the copy-pastable
// `remo web adopt <origin>` command pre-filled with this page's origin, and a
// subtle "waiting for adoption…" pulse. Deliberately minimal — no instance
// data, no terminals, and no public key display (identity retrieval stays
// behind the adoption token; the CLI fetches it).
//
// The shared health poll keeps running while this page is mounted (plus a
// faster local nudge below), so the moment adoption completes the root gate
// flips to the full dashboard with no manual refresh.

import { useCallback, useEffect, useRef, useState } from "react";
import { useHealth } from "../state/health";
import "./AwaitingAdoption.css";

// The shared health store polls every 10s; while awaiting adoption we nudge
// it faster so the flip to the dashboard lands within a few seconds of
// `remo web adopt` finishing. pollOnce() is guarded against overlap, so the
// extra ticks are safe alongside the shared interval.
const ADOPTION_POLL_NUDGE_MS = 3_000;

async function copyText(text: string): Promise<boolean> {
  // navigator.clipboard requires a secure context; this console commonly runs
  // over plain http on a trusted LAN, so fall back to execCommand.
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // fall through to the legacy path
  }
  try {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    const ok = document.execCommand("copy");
    textarea.remove();
    return ok;
  } catch {
    return false;
  }
}

export function AwaitingAdoption(): JSX.Element {
  const { retry } = useHealth();
  const command = `remo web adopt ${window.location.origin}`;

  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const copyResetHandle = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => {
    const handle = setInterval(() => void retry(), ADOPTION_POLL_NUDGE_MS);
    return () => clearInterval(handle);
  }, [retry]);

  useEffect(() => () => clearTimeout(copyResetHandle.current), []);

  const onCopy = useCallback(() => {
    void copyText(command).then((ok) => {
      setCopyState(ok ? "copied" : "failed");
      clearTimeout(copyResetHandle.current);
      copyResetHandle.current = setTimeout(() => setCopyState("idle"), 2_000);
    });
  }, [command]);

  return (
    <div className="adopt-page" data-testid="awaiting-adoption">
      <div className="adopt-card">
        <div className="adopt-brand">
          <span className="adopt-brand-dot" />
          <span className="adopt-brand-name">remo</span>
          <span className="adopt-brand-badge">web console</span>
        </div>

        <div className="adopt-title">This remo-web service is awaiting adoption</div>
        <p className="adopt-body">
          It is running without configuration. From a workstation with the remo CLI and your
          instance registry, run the command below to pair this service — no instance data or
          terminals are available until adoption completes.
        </p>

        <div className="adopt-command">
          <code className="adopt-command-text">{command}</code>
          <button
            type="button"
            className="adopt-copy-btn"
            onClick={onCopy}
            data-testid="adopt-copy"
          >
            {copyState === "idle" && "Copy"}
            {copyState === "copied" && "Copied ✓"}
            {copyState === "failed" && "Copy failed"}
          </button>
        </div>

        <div className="adopt-waiting">
          <span className="adopt-waiting-dot" />
          waiting for adoption…
        </div>
      </div>
    </div>
  );
}
