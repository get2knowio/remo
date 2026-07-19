// Dashboard "Pair CLI to sync" affordance (012-web-adopt-pairing, US4 / FR-017).
//
// Mints a fresh pairing code (origin="resync") through the same lifecycle and
// operator-auth gate as the awaiting-adoption page, and offers a Copy button.
// The code value is held only in a ref and is NEVER rendered into the DOM
// (FR-015/FR-016); opening the popover mints, closing it ends the session
// best-effort (the idle TTL is the backstop).

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, endPairing, mintPairingCode } from "../api/client";
import { copyText } from "../lib/clipboard";
import "./PairToSync.css";

type MintState = "idle" | "minting" | "ready" | "unauthorized" | "error";

export function PairToSync(): JSX.Element {
  const [open, setOpen] = useState(false);
  const [mintState, setMintState] = useState<MintState>("idle");
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const codeRef = useRef<string | null>(null);
  const copyResetHandle = useRef<ReturnType<typeof setTimeout>>();

  const close = useCallback(() => {
    setOpen(false);
    setMintState("idle");
    setCopyState("idle");
    codeRef.current = null;
    endPairing();
  }, []);

  const openAndMint = useCallback(() => {
    setOpen(true);
    setMintState("minting");
    setCopyState("idle");
    void mintPairingCode("resync")
      .then((res) => {
        codeRef.current = res.code;
        setMintState("ready");
      })
      .catch((err) => {
        codeRef.current = null;
        setMintState(
          err instanceof ApiError && err.code === "forbidden" ? "unauthorized" : "error",
        );
      });
  }, []);

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
    <div className="pairsync">
      <button
        type="button"
        className="topbar-btn"
        onClick={open ? close : openAndMint}
        data-testid="pair-to-sync"
        title="Mint a pairing code to run `remo web push` from your workstation"
      >
        Pair CLI to sync
      </button>

      {open && (
        <div className="pairsync-popover" role="dialog" aria-label="Pair CLI to sync">
          <p className="pairsync-body">
            Run <code>remo web push &lt;url&gt;</code> on your workstation and paste this code when
            prompted. The code is copied to your clipboard — it is never shown.
          </p>

          {mintState === "unauthorized" ? (
            <p className="pairsync-error">
              You are not signed in. Sign in through your access proxy, then try again.
            </p>
          ) : mintState === "error" ? (
            <p className="pairsync-error">Could not mint a code. Close and try again.</p>
          ) : (
            <button
              type="button"
              className="topbar-btn"
              onClick={onCopy}
              disabled={mintState !== "ready"}
              data-testid="pairsync-copy"
            >
              {mintState !== "ready" && "Minting…"}
              {mintState === "ready" && copyState === "idle" && "Copy pairing code"}
              {mintState === "ready" && copyState === "copied" && "Copied ✓"}
              {mintState === "ready" && copyState === "failed" && "Copy failed"}
            </button>
          )}

          <button type="button" className="pairsync-close" onClick={close}>
            Done
          </button>
        </div>
      )}
    </div>
  );
}
