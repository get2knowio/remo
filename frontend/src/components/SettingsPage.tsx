// Full-screen Settings page: site light/dark mode, accent color, terminal font,
// terminal color theme, grid display mode, font size, ligatures, and Nerd-Font
// upload. All preferences are stored in this browser (FR-034) and applied live
// to every open terminal.

import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { listUploadedFonts, registerUploadedFont } from "../state/fonts";
import {
  ACCENT_OPTIONS,
  FONT_OPTIONS,
  MASTER_SPLIT_OPTIONS,
  MAX_FOCUS_DWELL_MS,
  MAX_TERM_SIZE,
  MIN_FOCUS_DWELL_MS,
  MIN_TERM_SIZE,
  effectiveTerminalTheme,
  settingsActions,
  SITE_THEME_OPTIONS,
  useSettings,
  type FontOption,
} from "../state/settings";
import { AUTO_TERMINAL_THEME, TERMINAL_THEMES } from "../theme/terminalThemes";
import { PairToSync } from "./PairToSync";
import "./SettingsPage.css";

interface SettingsPageProps {
  onClose: () => void;
}

/** The six ANSI hues shown as dots in a theme preview. */
const ANSI_PREVIEW_KEYS = ["red", "green", "yellow", "blue", "magenta", "cyan"] as const;

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
  const autoSelected = settings.termTheme === AUTO_TERMINAL_THEME;
  // What "auto" resolves to for the site mode in effect — the preview subject.
  const autoTheme = effectiveTerminalTheme({ ...settings, termTheme: AUTO_TERMINAL_THEME });

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
          {/* Appearance (site light/dark) */}
          <section>
            <div className="settings-heading">Appearance</div>
            <p className="settings-sub">
              The console&rsquo;s own light/dark theme. Terminal colors are set separately, below.
            </p>
            <div className="settings-gridmodes">
              {SITE_THEME_OPTIONS.map((o) => {
                const selected = settings.themeMode === o.value;
                return (
                  <button
                    key={o.value}
                    type="button"
                    data-testid={`site-theme-${o.value}`}
                    className={`settings-gridmode${selected ? " settings-gridmode--on" : ""}`}
                    onClick={() => settingsActions.setThemeMode(o.value)}
                  >
                    <span className="settings-radio">{selected ? "✓" : ""}</span>
                    <span>
                      <span className="settings-gridmode-title">
                        {o.icon} {o.label}
                      </span>
                      <span className="settings-gridmode-desc">{o.desc}</span>
                    </span>
                  </button>
                );
              })}
            </div>
          </section>

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

          {/* Terminal theme */}
          <section>
            <div className="settings-heading">Terminal theme</div>
            <p className="settings-sub">
              The color scheme inside every terminal. Applied live, without dropping the session.
              Any individual terminal can override this from the swatch in its header.
            </p>
            <div className="settings-termthemes">
              {/* "Follow site theme" is the default, and previews whichever of
               * the two site-matched palettes is in effect right now. */}
              <button
                type="button"
                data-testid="term-theme-auto"
                className={`settings-termtheme${autoSelected ? " settings-termtheme--on" : ""}`}
                onClick={() => settingsActions.setTermTheme(AUTO_TERMINAL_THEME)}
              >
                <div className="settings-font-head">
                  <span className="settings-radio">{autoSelected ? "✓" : ""}</span>
                  <span className="settings-font-label">Follow site theme</span>
                  <span className="settings-font-tag">{autoTheme.variant === "dark" ? "Dark" : "Light"}</span>
                </div>
                <div
                  className="settings-termtheme-preview"
                  style={{
                    background: autoTheme.colors.background,
                    color: autoTheme.colors.foreground,
                    borderColor: autoTheme.colors.brightBlack,
                  }}
                >
                  <div className="settings-termtheme-dots">
                    {ANSI_PREVIEW_KEYS.map((key) => (
                      <span key={key} style={{ background: autoTheme.colors[key] }} />
                    ))}
                  </div>
                  <span style={{ color: autoTheme.colors.green }}>$</span>{" "}
                  <span style={{ color: autoTheme.colors.blue }}>remo</span> shell{" "}
                  <span style={{ color: autoTheme.colors.yellow }}>web</span>
                </div>
              </button>
              {TERMINAL_THEMES.map((th) => {
                const selected = settings.termTheme === th.id;
                return (
                  <button
                    key={th.id}
                    type="button"
                    data-testid={`term-theme-${th.id}`}
                    className={`settings-termtheme${selected ? " settings-termtheme--on" : ""}`}
                    onClick={() => settingsActions.setTermTheme(th.id)}
                  >
                    <div className="settings-font-head">
                      <span className="settings-radio">{selected ? "✓" : ""}</span>
                      <span className="settings-font-label">{th.label}</span>
                      <span className="settings-font-tag">
                        {th.variant === "dark" ? "Dark" : "Light"}
                      </span>
                    </div>
                    {/* The preview is painted from the theme data itself, so a
                     * palette edit can't drift from what it advertises. */}
                    <div
                      className="settings-termtheme-preview"
                      style={{
                        background: th.colors.background,
                        color: th.colors.foreground,
                        borderColor: th.colors.brightBlack,
                      }}
                    >
                      <div className="settings-termtheme-dots">
                        {ANSI_PREVIEW_KEYS.map((key) => (
                          <span key={key} style={{ background: th.colors[key] }} />
                        ))}
                      </div>
                      <span style={{ color: th.colors.green }}>$</span>{" "}
                      <span style={{ color: th.colors.blue }}>remo</span> shell{" "}
                      <span style={{ color: th.colors.yellow }}>web</span>
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

          {/* Tiling split */}
          <section>
            <div className="settings-heading">Tiling split</div>
            <p className="settings-sub">
              How much of the pane a terminal takes when you tile it to an edge (stack / master).
              Applies immediately to a tiling you already have.
            </p>
            <div className="settings-gridmodes">
              {MASTER_SPLIT_OPTIONS.map((o) => {
                const selected = settings.masterSplit === o.value;
                return (
                  <button
                    key={o.value}
                    type="button"
                    data-testid={`master-split-${o.label.replace(/\s\/\s/, "-")}`}
                    className={`settings-gridmode${selected ? " settings-gridmode--on" : ""}`}
                    onClick={() => settingsActions.setMasterSplit(o.value)}
                  >
                    <span className="settings-radio">{selected ? "✓" : ""}</span>
                    <span>
                      <span className="settings-gridmode-title">{o.label}</span>
                      <span className="settings-gridmode-desc">{o.desc}</span>
                    </span>
                  </button>
                );
              })}
            </div>
          </section>

          {/* Focus-follows-mouse dwell */}
          <section>
            <div className="settings-row2-head">
              <span className="settings-heading">Focus dwell</span>
              <span className="settings-value">
                {settings.focusDwellMs === 0 ? "instant" : `${settings.focusDwellMs} ms`}
              </span>
            </div>
            <p className="settings-sub">
              In a grid, how long the pointer must rest on a tile before it takes focus
              (focus-follows-mouse). Lower is snappier; higher is calmer. 0 = instant.
            </p>
            <input
              type="range"
              min={MIN_FOCUS_DWELL_MS}
              max={MAX_FOCUS_DWELL_MS}
              step={20}
              value={settings.focusDwellMs}
              onChange={(e) => settingsActions.setFocusDwell(Number(e.target.value))}
              className="settings-range"
              data-testid="focus-dwell-range"
            />
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
