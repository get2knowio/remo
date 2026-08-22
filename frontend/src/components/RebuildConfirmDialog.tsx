// Explicit-consequence confirm for a devcontainer rebuild (plan §2.4).
//
// Codespaces vocabulary, because "rebuild" undersells what happens: files in
// the workspace folder are kept, everything outside it is recreated, and the
// running session closes mid-keystroke. A warning row is added when the
// target's git state shows work that only exists on that host.

import { useState } from "react";
import type { SessionTarget } from "../api/client";
import "./HostDetailPage.css";

interface RebuildConfirmDialogProps {
  project: string;
  /** The live target when discovery has one — source of the git warning. */
  target: SessionTarget | null;
  onConfirm: (noCache: boolean) => void;
  onCancel: () => void;
}

export function RebuildConfirmDialog({
  project,
  target,
  onConfirm,
  onCancel,
}: RebuildConfirmDialogProps): JSX.Element {
  const [noCache, setNoCache] = useState(false);
  const dirty = target !== null && (target.git_dirty || target.git_ahead > 0);

  return (
    <div className="hd-scrim" data-testid="rebuild-dialog">
      <div className="hd-modal">
        <div className="hd-modal-title">
          Rebuild container for <code>{project}</code>?
        </div>
        <p className="hd-modal-body">
          Files in the workspace folder are kept. Everything outside it — installed packages,
          global config, anything written elsewhere in the container — is lost, and the running
          session will close.
        </p>
        {dirty && (
          <div className="hd-modal-warn" data-testid="rebuild-git-warning">
            ⚠ <code>{project}</code> has
            {target.git_dirty ? " uncommitted changes" : ""}
            {target.git_dirty && target.git_ahead > 0 ? " and" : ""}
            {target.git_ahead > 0 ? ` ${target.git_ahead} unpushed commit${target.git_ahead === 1 ? "" : "s"}` : ""}
            . They live in the workspace folder and survive the rebuild — but commit or push first
            if you want them safe beyond this host.
          </div>
        )}
        <label className="hd-modal-check">
          <input
            type="checkbox"
            checked={noCache}
            data-testid="rebuild-no-cache"
            onChange={(e) => setNoCache(e.target.checked)}
          />
          Rebuild without cache (slower; ignores cached image layers)
        </label>
        <div className="hd-modal-actions">
          <button type="button" className="hd-btn" onClick={onCancel}>
            Cancel
          </button>
          <button
            type="button"
            className="hd-btn hd-btn--warn"
            data-testid="rebuild-confirm"
            onClick={() => onConfirm(noCache)}
          >
            Rebuild container
          </button>
        </div>
      </div>
    </div>
  );
}
