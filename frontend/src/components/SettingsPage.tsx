// Full-screen Settings page: terminal font, grid display mode, font size,
// ligatures, accent color, and Nerd-Font upload. All preferences are stored in
// this browser (FR-034) and applied live to every open terminal.

import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { listUploadedFonts, registerUploadedFont } from "../state/fonts";
import {
  ACCENT_OPTIONS,
  FONT_OPTIONS,
  MAX_TERM_SIZE,
  MIN_TERM_SIZE,
  RENDERER_OPTIONS,
  settingsActions,
  useSettings,
  type FontOption,
} from "../state/settings";
import { PairToSync } from "./PairToSync";
import "./SettingsPage.css";

interface SettingsPageProps {
  onClose: () => void;
}

const GRID_MODES = [
  {
    value: false,
    title: "Actual size",
    desc: "Keep the font fixed; scroll and clip tiles that don’t fit.",
  },
  {
    value: true,
    title: "Scale to fit",
    desc: "Shrink each terminal so more of the session is visible at a glance.",
  },
];

export function SettingsPage({ onClose }: SettingsPageProps): JSX.Element {
  const settings = useSettings();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [uploaded, setUploaded] = useState<string[]>([]);
  const [uploadError, setUploadError] = useState<string | null>(null);

  useEffect(() => {
    void listUploadedFonts().then(setUploaded);
  }, []);

  const fontOptions: FontOption[] = [
    ...FONT_OPTIONS,
    ...uploaded.map((name) => ({
      label: name,
      css: `'${name}', monospace`,
      tag: "Uploaded",
      bundled: true,
    })),
  ];

  async function onUpload(e: ChangeEvent<HTMLInputElement>): Promise<void> {
    const file = e.target.files?.[0];
    if (!file) {
      return;
    }
    setUploadError(null);
    try {
      const name = await registerUploadedFont(file);
      setUploaded((prev) => (prev.includes(name) ? prev : [...prev, name]));
      settingsActions.setTermFont(`'${name}', monospace`);
      settingsActions.setNerdFontName(name);
    } catch (error) {
      setUploadError(error instanceof Error ? error.message : "Could not read that font file.");
    } finally {
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    }
  }

  return (
    <div className="settings" data-testid="settings-page">
      <div className="settings-topbar">
        <button type="button" className="settings-back" onClick={onClose}>
          ‹ Back
        </button>
        <span className="settings-title">Settings</span>
        <span className="settings-note">stored in this browser</span>
      </div>

      <div className="settings-scroll">
        <div className="settings-inner">
          {/* Accent */}
          <section>
            <div className="settings-heading">Accent color</div>
            <p className="settings-sub">Tints the console chrome and focus rings.</p>
            <div className="settings-accents">
              {ACCENT_OPTIONS.map((a) => (
                <button
                  key={a}
                  type="button"
                  className={`settings-accent${settings.accent === a ? " settings-accent--on" : ""}`}
                  style={{ background: a }}
                  aria-label={`Accent ${a}`}
                  onClick={() => settingsActions.setAccent(a)}
                />
              ))}
            </div>
          </section>

          {/* Terminal font */}
          <section>
            <div className="settings-heading">Terminal font</div>
            <p className="settings-sub">Applied live to every browser terminal. Monospaced only.</p>
            <div className="settings-fonts">
              {fontOptions.map((f) => {
                const selected = settings.termFontCss === f.css;
                return (
                  <button
                    key={f.css}
                    type="button"
                    className={`settings-font${selected ? " settings-font--on" : ""}`}
                    onClick={() => settingsActions.setTermFont(f.css)}
                  >
                    <div className="settings-font-head">
                      <span className="settings-radio">{selected ? "✓" : ""}</span>
                      <span className="settings-font-label">{f.label}</span>
                      <span className="settings-font-tag">{f.tag}</span>
                    </div>
                    <div className="settings-font-preview" style={{ fontFamily: f.css }}>
                      $ git commit -m &quot;=&gt; fix&quot;
                    </div>
                  </button>
                );
              })}
            </div>
          </section>

          {/* Grid display */}
          <section>
            <div className="settings-heading">Grid display</div>
            <p className="settings-sub">How terminals behave when several share the screen.</p>
            <div className="settings-gridmodes">
              {GRID_MODES.map((g) => {
                const selected = settings.gridFit === g.value;
                return (
                  <button
                    key={g.title}
                    type="button"
                    className={`settings-gridmode${selected ? " settings-gridmode--on" : ""}`}
                    onClick={() => settingsActions.setGridFit(g.value)}
                  >
                    <span className="settings-radio">{selected ? "✓" : ""}</span>
                    <span>
                      <span className="settings-gridmode-title">{g.title}</span>
                      <span className="settings-gridmode-desc">{g.desc}</span>
                    </span>
                  </button>
                );
              })}
            </div>
          </section>

          {/* Size + ligatures */}
          <section className="settings-row2">
            <div className="settings-col">
              <div className="settings-row2-head">
                <span className="settings-heading">Font size</span>
                <span className="settings-value">{settings.termSizeNum}px</span>
              </div>
              <input
                type="range"
                min={MIN_TERM_SIZE}
                max={MAX_TERM_SIZE}
                step={1}
                value={settings.termSizeNum}
                onChange={(e) => settingsActions.setTermSize(Number(e.target.value))}
                className="settings-range"
              />
            </div>
            <div className="settings-col">
              <div className="settings-heading">Ligatures</div>
              <button
                type="button"
                className={`settings-liga${settings.termLiga ? " settings-liga--on" : ""}`}
                onClick={() => settingsActions.toggleLiga()}
              >
                <span className="settings-liga-track">
                  <span className="settings-liga-knob" />
                </span>
                Program ligatures (→ ⇒ ≠ ✓)
              </button>
            </div>
          </section>

          {/* Nerd fonts */}
          <section>
            <div className="settings-heading">Nerd Fonts</div>
            <p className="settings-sub">
              Browsers can’t read fonts installed on the instance, so upload a patched Nerd Font
              once — it’s registered in this browser and offered as a font choice above.
            </p>
            <label className="settings-upload">
              <span className="settings-upload-icon">⭳</span>
              <span className="settings-upload-title">
                Drop a patched Nerd Font here, or click to browse
              </span>
              <span className="settings-upload-hint">
                JetBrainsMono Nerd Font · FiraCode Nerd Font · Hack Nerd Font …
              </span>
              <input
                ref={fileInputRef}
                type="file"
                accept=".ttf,.otf,.woff,.woff2"
                onChange={(e) => void onUpload(e)}
                hidden
              />
            </label>
            {uploadError && <p className="settings-upload-error">{uploadError}</p>}
          </section>

          {/* Terminal engine */}
          <section>
            <div className="settings-heading">Terminal engine</div>
            <p className="settings-sub">
              Which browser terminal emulator renders your sessions. Switching rebuilds each open
              terminal and reconnects to the same remote session.
            </p>
            <div className="settings-gridmodes">
              {RENDERER_OPTIONS.map((r) => {
                const selected = settings.renderer === r.value;
                return (
                  <button
                    key={r.value}
                    type="button"
                    data-testid={`renderer-${r.value}`}
                    className={`settings-gridmode${selected ? " settings-gridmode--on" : ""}`}
                    onClick={() => settingsActions.setRenderer(r.value)}
                  >
                    <span className="settings-radio">{selected ? "✓" : ""}</span>
                    <span>
                      <span className="settings-gridmode-title">
                        {r.label} <span className="settings-font-tag">{r.tag}</span>
                      </span>
                      <span className="settings-gridmode-desc">{r.desc}</span>
                    </span>
                  </button>
                );
              })}
            </div>
          </section>

          {/* Pair CLI to sync (post-adoption re-sync) */}
          <section>
            <div className="settings-heading">Pair CLI to sync</div>
            <p className="settings-sub">
              Mint a one-time code to run <code>remo web push &lt;url&gt;</code> from your
              workstation and push registry / host-key updates to this service. The code is copied
              to your clipboard — it is never shown.
            </p>
            <PairToSync />
          </section>
        </div>
      </div>
    </div>
  );
}
