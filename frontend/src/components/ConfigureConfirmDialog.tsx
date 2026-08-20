// Explicit-consequence confirm for configuring a host from the console (023,
// Tier 1 — the RebuildConfirmDialog pattern). Mirrors the CLI prompt's
// consequence copy: this is a real provisioning pass, not a toggle.

import "./HostDetailPage.css";

interface ConfigureConfirmDialogProps {
  hostName: string;
  hostUser: string;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfigureConfirmDialog({
  hostName,
  hostUser,
  onConfirm,
  onCancel,
}: ConfigureConfirmDialogProps): JSX.Element {
  return (
    <div className="hd-scrim" data-testid="configure-dialog">
      <div className="hd-modal">
        <div className="hd-modal-title">
          Configure <code>{hostName}</code>?
        </div>
        <p className="hd-modal-body">
          remo will apt-upgrade the system, install Docker, Node.js, zellij and its host tools,
          and give <code>{hostUser}</code> passwordless sudo. This runs as a background job and
          takes several minutes; the log streams below while it runs.
        </p>
        <div className="hd-modal-actions">
          <button type="button" className="hd-btn" onClick={onCancel}>
            Cancel
          </button>
          <button
            type="button"
            className="hd-btn hd-btn--primary"
            data-testid="configure-confirm"
            onClick={onConfirm}
          >
            Configure host
          </button>
        </div>
      </div>
    </div>
  );
}
