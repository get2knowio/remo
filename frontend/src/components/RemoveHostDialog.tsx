// Type-the-name confirm for removing a host from the console registry (023,
// Tier 2 — a TypeToConfirmDialog wrapper). The stakes are different from a
// project delete and the copy says so explicitly: removal only forgets the
// registry entry (and revokes nothing here — sync/CLI own revocation); the
// remote machine, its projects and its sessions are never touched.

import { TypeToConfirmDialog } from "./TypeToConfirmDialog";
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
  return (
    <TypeToConfirmDialog
      confirmText={hostName}
      title={
        <>
          Remove <code>{hostName}</code> from the console?
        </>
      }
      busy={busy}
      error={error}
      confirmLabel="Remove host"
      busyLabel="Removing…"
      testId="remove-host-dialog"
      inputTestId="remove-host-input"
      confirmTestId="remove-host-confirm"
      onConfirm={onConfirm}
      onCancel={onCancel}
    >
      <p className="hd-modal-body">
        This removes the host from the console&rsquo;s registry only. The machine itself is
        never touched — its projects, containers and sessions stay exactly as they are, and
        you can add it again later. Workstations pick the removal up on their next{" "}
        <code>remo web sync</code>.
      </p>
    </TypeToConfirmDialog>
  );
}
