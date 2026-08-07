// "Pair CLI to sync" affordance (012-web-adopt-pairing, US4 / FR-017), shown in
// Settings once the service has been adopted.
//
// Mints a fresh pairing code (origin="resync") through the same lifecycle and
// operator-auth gate as the awaiting-adoption page. The code value is held only
// in a ref and is NEVER rendered into the DOM (FR-015/FR-016); opening mints,
// closing (or unmounting, e.g. leaving Settings) ends the session best-effort
// (the idle TTL is the backstop).
//
// The clipboard is written as soon as the code is minted (#159). Every open
// mints a code that ROTATES the previous one, so a mint the operator didn't
// follow with a Copy click left a dead code in their clipboard — invisible,
// because the code is never displayed, and unrecoverable by reopening the page
// (that rotates again). Opening is a user gesture, so the write is normally
// permitted; if it is refused the Copy button becomes a loud, non-expiring
// "(required)" affordance instead of a quiet fallback.

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
  // Track "open" for the unmount cleanup below without stale-closure issues.
  const openRef = useRef(false);
  openRef.current = open;

  const close = useCallback(() => {
    setOpen(false);
    setMintState("idle");
    setCopyState("idle");
    codeRef.current = null;
    endPairing();
  }, []);

  const copyCode = useCallback(async () => {
    clearTimeout(copyResetHandle.current);
    const code = codeRef.current;
    if (!code) {
      setCopyState("failed");
      return;
    }
    const ok = await copyText(code);
    setCopyState(ok ? "copied" : "failed");
    // Only the success state auto-clears. "Copy required" must stay on screen
    // until the operator acts on it — this is the state where the clipboard
    // does NOT hold a usable code.
    if (ok) copyResetHandle.current = setTimeout(() => setCopyState("idle"), 2_000);
  }, []);

  const openAndMint = useCallback(() => {
    setOpen(true);
    setMintState("minting");
    // Clear any pending "Copied ✓" reset so it can never outlive the code it
    // referred to: this mint rotates that code away.
    clearTimeout(copyResetHandle.current);
    setCopyState("idle");
    void mintPairingCode("resync")
      .then((res) => {
        codeRef.current = res.code;
        setMintState("ready");
        void copyCode();
      })
      .catch((err) => {
        codeRef.current = null;
        setMintState(
          err instanceof ApiError && err.code === "forbidden" ? "unauthorized" : "error",
        );
      });
  }, [copyCode]);

  useEffect(() => () => clearTimeout(copyResetHandle.current), []);
  // End the pairing session if Settings is closed with a code still live.
  useEffect(() => () => {
    if (openRef.current) {
      endPairing();
    }
  }, []);

  return (
    <div className="pairsync">
      <button
        type="button"
        className="pairsync-btn"
        onClick={open ? close : openAndMint}
        data-testid="pair-to-sync"
        title="Mint a pairing code to run `remo web push` from your workstation"
      >
        {open ? "Cancel" : "Mint pairing code"}
      </button>

      {open && (
        <div className="pairsync-popover" role="dialog" aria-label="Pair CLI to sync">
          <p className="pairsync-body">
            Run <code>remo web push &lt;url&gt;</code> on your workstation and paste this code when
            prompted. The code is never shown — it goes straight to your clipboard.
            {copyState === "copied" && " It is on your clipboard now."}
            {copyState === "failed" &&
              " This browser refused the automatic copy, so you must click Copy below before pasting."}
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
              className={
                copyState === "failed" ? "pairsync-btn pairsync-btn-attention" : "pairsync-btn"
              }
              onClick={() => void copyCode()}
              disabled={mintState !== "ready"}
              data-testid="pairsync-copy"
            >
              {mintState !== "ready" && "Minting…"}
              {mintState === "ready" && copyState === "idle" && "Copy pairing code"}
              {mintState === "ready" && copyState === "copied" && "Copied ✓"}
              {mintState === "ready" && copyState === "failed" && "Copy pairing code (required)"}
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
