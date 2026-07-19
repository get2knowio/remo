// Best-effort browser (F11-style) fullscreen helpers.
//
// These are a thin, defensive wrapper over the Fullscreen API. The web-app's
// "fullscreen" terminal mode is primarily an *app-viewport* takeover (the shell
// hides its chrome via a CSS class keyed on `workspace.maximizedId`); requesting
// true browser fullscreen on top of that is a best-effort enhancement:
//
//   - `requestBrowserFullscreen()` MUST be called from within a user-gesture
//     handler (a click or keydown) — browsers reject it otherwise. On rejection
//     the app-viewport takeover still stands, so the caller needs no fallback.
//   - Exiting is centralized in AppShell (an effect keyed on `maximizedId`), so
//     every exit path — the restore button, `f`, Esc, closing the card, or
//     opening another session — funnels through `exitBrowserFullscreen()`.
//
// Both calls are idempotent: they guard on `document.fullscreenElement`, so
// double-invoking (e.g. our exit effect firing after a native Esc already left
// fullscreen) is a no-op and never loops.

export function requestBrowserFullscreen(): void {
  if (typeof document === "undefined") {
    return;
  }
  const el = document.documentElement;
  if (!document.fullscreenElement && el.requestFullscreen) {
    // Swallow rejections: no gesture / disallowed by the browser just means we
    // stay in the app-viewport takeover.
    void el.requestFullscreen().catch(() => {});
  }
}

export function exitBrowserFullscreen(): void {
  if (typeof document === "undefined") {
    return;
  }
  if (document.fullscreenElement && document.exitFullscreen) {
    void document.exitFullscreen().catch(() => {});
  }
}
