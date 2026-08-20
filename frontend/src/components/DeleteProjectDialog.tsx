// Type-the-name delete confirm for a project (plan §2.4 confirm ladder).
//
// Deleting a project removes the directory, its containers and its session —
// unrecoverable from the console — so this is a Tier-2 TypeToConfirmDialog
// with project-specific stakes copy (plus the dirty-work warning).

import { TypeToConfirmDialog } from "./TypeToConfirmDialog";
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
  return (
    <TypeToConfirmDialog
      confirmText={project}
      title={
        <>
          Delete <code>{project}</code>?
        </>
      }
      busy={busy}
      error={error}
      confirmLabel="Delete project"
      busyLabel="Deleting…"
      testId="delete-dialog"
      inputTestId="delete-name-input"
      confirmTestId="delete-confirm"
      onConfirm={onConfirm}
      onCancel={onCancel}
    >
      <p className="hd-modal-body">
        This removes the project directory, its containers and its session from the host.
        There is no undo from here.
      </p>
      {dirty && (
        <div className="hd-modal-danger-note" data-testid="delete-dirty-warning">
          ⚠ This project has uncommitted or unpushed changes — they will be destroyed.
        </div>
      )}
    </TypeToConfirmDialog>
  );
}
