// Self-hosted terminal/UI fonts (CSP-safe).
//
// The app's Content-Security-Policy is `default-src 'self'` with no external
// hosts (src/remo_cli/web/app.py), so Google-Fonts <link>s would be blocked.
// These @fontsource packages bundle woff2 files that Vite emits as same-origin
// assets — no CDN, CSP-compatible. Imported for side effects only (they inject
// @font-face rules). Hack / Cascadia Code stay "bring-your-own" (system-
// installed or uploaded via the Settings Nerd-Font uploader).

import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/500.css";
import "@fontsource/ibm-plex-mono/600.css";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/fira-code/400.css";
import "@fontsource/source-code-pro/400.css";
