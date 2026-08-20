// The one type-the-name danger dialog (the GitHub repo-delete pattern): the
// destructive button only arms once the operator has typed the exact target
// name. DeleteProjectDialog and RemoveHostDialog are thin wrappers supplying
// copy, labels and test ids — the consent mechanics live here once.
//
// Console-owned shape (not service-derived).

import { ReactNode, useState } from "react";
import "./HostDetailPage.css";

interface TypeToConfirmDialogProps {
  /** The exact string the operator must type to arm the confirm button. */
  confirmText: string;
  title: ReactNode;
  /** Body copy (paragraphs / warnings) rendered above the type-check input. */
  children: ReactNode;
  busy: boolean;
  error: string | null;
  /** Confirm-button label while idle, e.g. "Delete project". */
  confirmLabel: string;
  /** Confirm-button label while busy, e.g. "Deleting…". */
  busyLabel: string;
  testId: string;
  inputTestId: string;
  confirmTestId: string;
  onConfirm: () => void;
  onCancel: () => void;
}

export function TypeToConfirmDialog({
  confirmText,
  title,
  children,
  busy,
  error,
  confirmLabel,
  busyLabel,
  testId,
  inputTestId,
  confirmTestId,
  onConfirm,
  onCancel,
}: TypeToConfirmDialogProps): JSX.Element {
  const [typed, setTyped] = useState("");
  const armed = typed === confirmText;

  return (
    <div className="hd-scrim" data-testid={testId}>
      <div className="hd-modal hd-modal--danger">
        <div className="hd-modal-title">{title}</div>
        {children}
        <label className="hd-modal-typecheck">
          Type <code>{confirmText}</code> to confirm:
          <input
            value={typed}
            data-testid={inputTestId}
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
            data-testid={confirmTestId}
            disabled={!armed || busy}
            onClick={onConfirm}
          >
            {busy ? busyLabel : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
