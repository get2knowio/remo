// One SessionTarget: project name, devcontainer badge, Zellij state,
// devcontainer running state, a multi-select checkbox (for "open selected",
// T047/FR-030), and an "Open" action that opens this target's terminal in
// the workspace (`workspace.openTarget`, wired by the caller).

import type { SessionTarget } from "../api/client";
import "./TargetCard.css";

const ZELLIJ_LABELS: Record<SessionTarget["zellij_state"], string> = {
  active: "● Zellij active",
  exited: "○ Zellij exited",
  absent: "— No Zellij session",
};

const DEVCONTAINER_LABELS: Record<SessionTarget["devcontainer_running"], string> = {
  running: "● Container running",
  stopped: "○ Container stopped",
  unknown: "? Container state unknown",
};

interface TargetCardProps {
  target: SessionTarget;
  isSelected: boolean;
  onToggleSelect: (targetId: string) => void;
  onOpen: (target: SessionTarget) => void;
}

export function TargetCard({ target, isSelected, onToggleSelect, onOpen }: TargetCardProps): JSX.Element {
  return (
    <div className="target-card" data-testid={`target-card-${target.id}`}>
      <div className="target-card-header">
        <input
          type="checkbox"
          className="target-card-select"
          data-testid={`target-select-${target.id}`}
          checked={isSelected}
          onChange={() => onToggleSelect(target.id)}
          aria-label={`Select ${target.project} for bulk-open`}
        />
        <span className="target-card-project">{target.project}</span>
        {target.has_devcontainer && (
          <span className="target-card-devcontainer-badge">devcontainer</span>
        )}
      </div>
      <div className="target-card-states">
        <span className={`target-card-zellij target-card-zellij--${target.zellij_state}`}>
          {ZELLIJ_LABELS[target.zellij_state]}
        </span>
        {target.has_devcontainer && (
          <span
            className={`target-card-devcontainer target-card-devcontainer--${target.devcontainer_running}`}
          >
            {DEVCONTAINER_LABELS[target.devcontainer_running]}
          </span>
        )}
      </div>
      <div className="target-card-actions">
        <button
          type="button"
          className="target-card-open-button"
          data-testid={`target-open-${target.id}`}
          onClick={() => onOpen(target)}
        >
          Open
        </button>
      </div>
    </div>
  );
}
