// Type-the-name confirm for removing a host from the console registry (023,
// Tier 2 — the DeleteProjectDialog pattern). The stakes are different from a
// project delete and the copy says so explicitly: removal only forgets the
// registry entry (and revokes nothing here — sync/CLI own revocation); the
// remote machine, its projects and its sessions are never touched.

import { useState } from "react";
import "./HostDetailPage.css";

interface RemoveHostDialogProps {
  hostName: string;
  busy: boolean;
  error: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}

export function RemoveHostDialog({
  hostName,
  busy,
  error,
  onConfirm,
  onCancel,
}: RemoveHostDialogProps): JSX.Element {
  const [typed, setTyped] = useState("");
  const armed = typed === hostName;

  return (
    <div className="hd-scrim" data-testid="remove-host-dialog">
      <div className="hd-modal hd-modal--danger">
        <div className="hd-modal-title">
          Remove <code>{hostName}</code> from the console?
        </div>
        <p className="hd-modal-body">
          This removes the host from the console&rsquo;s registry only. The machine itself is
          never touched — its projects, containers and sessions stay exactly as they are, and
          you can add it again later. Workstations pick the removal up on their next{" "}
          <code>remo web sync</code>.
        </p>
        <label className="hd-modal-typecheck">
          Type <code>{hostName}</code> to confirm:
          <input
            value={typed}
            data-testid="remove-host-input"
            autoComplete="off"
            spellCheck={false}
            onInput={(e) => setTyped((e.target as HTMLInputElement).value)}
          />
        </label>
        {error && <div className="hd-modal-danger-note">{error}</div>}
        <div className="hd-modal-actions">
          <button type="button" className="hd-btn" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button
            type="button"
            className="hd-btn hd-btn--danger"
            data-testid="remove-host-confirm"
            disabled={!armed || busy}
            onClick={onConfirm}
          >
            {busy ? "Removing…" : "Remove host"}
          </button>
        </div>
      </div>
    </div>
  );
}
