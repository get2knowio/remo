// Grid layout for the terminal workspace (T046, US3, FR-031).
//
// Renders every open `TerminalCard` simultaneously in a CSS grid — nothing
// is ever hidden in grid mode, so "hidden terminals remain connected" (US3
// scenario 3) is trivially satisfied: there ARE no hidden terminals here,
// only visually de-emphasized ones (see `TerminalCard`'s `isFocused` prop,
// which drives the highlighted-border affordance and gates whether THIS
// card's keystrokes get forwarded).
//
// Input routing design: grid mode's natural interaction model is "click the
// terminal you want to type into", like a tiling window manager. Rather than
// bypass the workspace's `focusedTargetId` and gate purely on local DOM
// focus, this view still funnels clicks through `onFocus` (-> ultimately
// `workspace.setFocused`) so grid mode shares ONE focus source of truth with
// tab/focused mode and the keyboard-cycling shortcut (T048,
// `Ctrl+Shift+ArrowLeft`/`Ctrl+Shift+ArrowRight` — see
// `state/useKeyboardSwitching.ts` for the shortcut-choice rationale). That
// keeps "only one terminal receives keystrokes at a time" true and
// consistent no matter which layout mode or input method (click vs.
// shortcut) changed focus most recently.
//
// `openTargets` is expected to already be resolved/joined against live
// discovery data by the caller (Dashboard) — see workspace.ts's design note
// on why the workspace store itself only tracks target IDs, not full
// `SessionTarget` objects.

import type { SessionTarget } from "../api/client";
import { TerminalCard } from "./TerminalCard";
import "./GridView.css";

interface GridViewProps {
  openTargets: SessionTarget[];
  focusedTargetId: string | null;
  onFocus: (targetId: string) => void;
  onClose: (targetId: string) => void;
}

export function GridView({ openTargets, focusedTargetId, onFocus, onClose }: GridViewProps): JSX.Element {
  return (
    <div className="grid-view">
      {openTargets.map((target) => (
        <TerminalCard
          key={target.id}
          target={target}
          isFocused={target.id === focusedTargetId}
          onFocusRequest={() => onFocus(target.id)}
          onClose={() => onClose(target.id)}
        />
      ))}
    </div>
  );
}
