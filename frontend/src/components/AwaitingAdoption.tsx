// Awaiting-adoption page (012-web-adopt-pairing; supersedes the 011 static-token
// page).
//
// Rendered by AppRoot instead of the console shell while GET /api/v1/ready
// reports status "unconfigured". On mount it mints an ephemeral pairing code
// (rotation-on-open, FR-003) and holds it ONLY in a non-rendered ref — the
// value never enters the DOM (FR-015/FR-016). The operator clicks "Copy pairing
// code", pastes it into `remo web adopt <origin>` on their workstation, and the
// browser flips to the dashboard the moment adoption completes.
//
// The code is fetched at runtime (never embedded in the served bundle) and the
// page best-effort ends the session on hide/unload (FR-004); the server's idle
// TTL is the authoritative backstop.

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, endPairing, mintPairingCode } from "../api/client";
import { copyText } from "../lib/clipboard";
import { useHealth } from "../state/health";
import "./AwaitingAdoption.css";

// The shared health store polls every 10s; while awaiting adoption we nudge
// it faster so the flip to the dashboard lands within a few seconds of
// `remo web adopt` finishing. pollOnce() is guarded against overlap, so the
// extra ticks are safe alongside the shared interval.
const ADOPTION_POLL_NUDGE_MS = 3_000;

type MintState = "minting" | "ready" | "unauthorized" | "error";

export function AwaitingAdoption(): JSX.Element {
  const { retry } = useHealth();
  const command = `remo web adopt ${window.location.origin}`;

  // The pairing code lives ONLY here — never in React state, never in the DOM.
  const codeRef = useRef<string | null>(null);
  const [mintState, setMintState] = useState<MintState>("minting");
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const copyResetHandle = useRef<ReturnType<typeof setTimeout>>();

  // Mint on open (rotation-on-open, FR-003). The response code is stashed in the
  // ref; only expires_in / auth status drive rendering.
  useEffect(() => {
    let cancelled = false;
    void mintPairingCode("adopt")
      .then((res) => {
        if (cancelled) return;
        codeRef.current = res.code;
        setMintState("ready");
      })
      .catch((err) => {
        if (cancelled) return;
        codeRef.current = null;
        // 403 => reached without operator auth (forward-auth proxy front door);
        // anything else => a generic retry affordance.
        setMintState(err instanceof ApiError && err.code === "forbidden" ? "unauthorized" : "error");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Best-effort end on hide/unload (FR-004); idle TTL is the real backstop.
  useEffect(() => {
    const onHide = () => {
      if (document.visibilityState === "hidden") endPairing();
    };
    window.addEventListener("pagehide", endPairing);
    document.addEventListener("visibilitychange", onHide);
    return () => {
      window.removeEventListener("pagehide", endPairing);
      document.removeEventListener("visibilitychange", onHide);
    };
  }, []);

  useEffect(() => {
    const handle = setInterval(() => void retry(), ADOPTION_POLL_NUDGE_MS);
    return () => clearInterval(handle);
  }, [retry]);

  useEffect(() => () => clearTimeout(copyResetHandle.current), []);

  const onCopy = useCallback(() => {
    const code = codeRef.current;
    if (!code) {
      setCopyState("failed");
      return;
    }
    void copyText(code).then((ok) => {
      setCopyState(ok ? "copied" : "failed");
      clearTimeout(copyResetHandle.current);
      copyResetHandle.current = setTimeout(() => setCopyState("idle"), 2_000);
    });
  }, []);

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
          instance registry, run the command below, then paste the pairing code when prompted —
          no instance data or terminals are available until adoption completes.
        </p>

        <div className="adopt-command">
          <code className="adopt-command-text">{command}</code>
        </div>

        {mintState === "unauthorized" ? (
          <p className="adopt-body adopt-error">
            You are not signed in. This service mints pairing codes only for an authenticated
            operator — sign in through your access proxy, then reload this page.
          </p>
        ) : mintState === "error" ? (
          <p className="adopt-body adopt-error">
            Could not mint a pairing code. Reload this page to try again.
          </p>
        ) : (
          <div className="adopt-command">
            <span className="adopt-command-text">Pairing code ready (hidden)</span>
            <button
              type="button"
              className="adopt-copy-btn"
              onClick={onCopy}
              disabled={mintState !== "ready"}
              data-testid="adopt-copy"
            >
              {mintState !== "ready" && "…"}
              {mintState === "ready" && copyState === "idle" && "Copy pairing code"}
              {mintState === "ready" && copyState === "copied" && "Copied ✓"}
              {mintState === "ready" && copyState === "failed" && "Copy failed"}
            </button>
          </div>
        )}

        <div className="adopt-waiting">
          <span className="adopt-waiting-dot" />
          waiting for adoption…
        </div>
      </div>
    </div>
  );
}
