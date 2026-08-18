// Layout breakpoints shared between the shell and anything that has to REPORT
// what the shell decided.
//
// A leaf module on purpose: the diagnostics snapshot (state/diagnostics.ts) is
// reached from inside TerminalCard, so importing this from AppShell.tsx would
// close a cycle (AppShell -> WorkspacePane -> TerminalCard -> diagnostics ->
// AppShell). Duplicating the number instead would be worse: the snapshot would
// eventually disagree with the layout it claims to describe.

/** Below this viewport width the shell switches to its narrow layout (the rail
 * becomes an overlay, and a master tiling flattens to the uniform grid). */
export const NARROW_BREAKPOINT = 820;
