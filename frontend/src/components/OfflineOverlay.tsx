// Full-screen overlay shown when the web service is unreachable. Terminals
// reattach to their remote Zellij sessions automatically once it returns.

import "./OfflineOverlay.css";

interface OfflineOverlayProps {
  onRetry: () => void;
}

export function OfflineOverlay({ onRetry }: OfflineOverlayProps): JSX.Element {
  return (
    <div className="offline-overlay" data-testid="offline-overlay">
      <div className="offline-card">
        <div className="offline-icon">◐</div>
        <div className="offline-title">Remo web service is unreachable</div>
        <p className="offline-body">
          The Docker service is offline. Terminals reattach to their remote Zellij sessions
          automatically once it returns — nothing on the instances was lost.
        </p>
        <button type="button" className="offline-retry" onClick={onRetry}>
          Retry connection
        </button>
      </div>
    </div>
  );
}
