// Type-the-name delete confirm for a project (plan §2.4 confirm ladder).
//
// Deleting a project removes the directory, its containers and its session —
// unrecoverable from the console — so the destructive button only arms once
// the operator has typed the project's exact name (the GitHub repo-delete
// pattern). Danger tokens throughout.

import { useState } from "react";
import "./HostDetailPage.css";

interface DeleteProjectDialogProps {
  project: string;
  /** True when the target shows uncommitted changes or unpushed commits. */
  dirty: boolean;
  /** True while the (synchronous) delete request is in flight. */
  busy: boolean;
  error: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}

export function DeleteProjectDialog({
  project,
  dirty,
  busy,
  error,
  onConfirm,
  onCancel,
}: DeleteProjectDialogProps): JSX.Element {
  const [typed, setTyped] = useState("");
  const armed = typed === project;

  return (
    <div className="hd-scrim" data-testid="delete-dialog">
      <div className="hd-modal hd-modal--danger">
        <div className="hd-modal-title">
          Delete <code>{project}</code>?
        </div>
        <p className="hd-modal-body">
          This removes the project directory, its containers and its session from the host.
          There is no undo from here.
        </p>
        {dirty && (
          <div className="hd-modal-danger-note" data-testid="delete-dirty-warning">
            ⚠ This project has uncommitted or unpushed changes — they will be destroyed.
          </div>
        )}
        <label className="hd-modal-typecheck">
          Type <code>{project}</code> to confirm:
          <input
            value={typed}
            data-testid="delete-name-input"
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
            data-testid="delete-confirm"
            disabled={!armed || busy}
            onClick={onConfirm}
          >
            {busy ? "Deleting…" : "Delete project"}
          </button>
        </div>
      </div>
    </div>
  );
}
