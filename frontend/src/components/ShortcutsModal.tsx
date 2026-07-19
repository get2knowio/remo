// Keyboard-shortcuts reference modal (toggled by `?`).

import "./ShortcutsModal.css";

interface ShortcutsModalProps {
  onClose: () => void;
}

const SHORTCUTS: { desc: string; key: string }[] = [
  { desc: "Open session 1–9 (single)", key: "1 – 9" },
  { desc: "Add session to grid", key: "⌘ 1–9" },
  { desc: "Fullscreen focused terminal", key: "f" },
  { desc: "Exit fullscreen / collapse grid", key: "esc" },
  { desc: "Show this panel", key: "?" },
];

export function ShortcutsModal({ onClose }: ShortcutsModalProps): JSX.Element {
  return (
    <div className="shortcuts-backdrop" data-testid="shortcuts-modal" onClick={onClose}>
      <div className="shortcuts-card" onClick={(e) => e.stopPropagation()}>
        <div className="shortcuts-title">Keyboard shortcuts</div>
        {SHORTCUTS.map((s) => (
          <div className="shortcuts-row" key={s.desc}>
            <span className="shortcuts-desc">{s.desc}</span>
            <kbd className="shortcuts-key">{s.key}</kbd>
          </div>
        ))}
      </div>
    </div>
  );
}
