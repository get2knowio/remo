// Full-screen "Add host" wizard (023) — the HostDetailPage/SettingsPage
// overlay precedent (absolute inset 0, ‹ Back, no router). Rendered only when
// `features.registry_admin` is on.
//
// Three steps, mirroring the CLI's trust bootstrap for a host the service has
// never been able to reach (deploy-key pattern):
//
//   1. register — `remo add` via the service's embedded CLI; the response
//      carries the paste-one-liner that authorizes the service key.
//   2. trust    — scan the host's SSH keys, show the fingerprints, and only
//      on the operator's explicit confirmation record EXACTLY those lines in
//      the service trust store (the client echoes what was shown — no blind
//      re-scan window). A mismatch against an existing record is a hard stop.
//   3. verify   — prove the service key actually authenticates; auth_failed
//      loops with guidance until the one-liner has been run on the host.
//
// Ends with an optional "Configure now" that starts the detached configure
// job (remo-host/zellij/docker install) and streams its log via
// JobProgressPanel (fetchStatus = the registry-jobs poller).

import { useState } from "react";
import {
  addHost,
  ApiError,
  getRegistryJobStatus,
  scanHostKey,
  startConfigure,
  trustHostKey,
  verifyHost,
  type AddHostResponse,
  type JobAccepted,
  type ScanKeyResponse,
  type VerifyHostResponse,
} from "../api/client";
import { useDiscovery } from "../state/discovery";
import { JobProgressPanel } from "./JobProgressPanel";
import "./HostDetailPage.css";
import "./AddHostPage.css";

type Step = "register" | "trust" | "verify" | "done";

interface AddHostPageProps {
  onClose: () => void;
}

function errorText(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    return error.remediation ? `${error.message} — ${error.remediation}` : error.message;
  }
  return error instanceof Error ? error.message : fallback;
}

function CopyBlock({ text, testid }: { text: string; testid: string }): JSX.Element {
  const [copied, setCopied] = useState(false);
  return (
    <div className="ah-copy" data-testid={testid}>
      <pre className="ah-copy-text">{text}</pre>
      <button
        type="button"
        className="hd-btn"
        data-testid={`${testid}-button`}
        onClick={() => {
          void navigator.clipboard?.writeText(text).then(() => {
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
          });
        }}
      >
        {copied ? "Copied ✓" : "Copy"}
      </button>
    </div>
  );
}

export function AddHostPage({ onClose }: AddHostPageProps): JSX.Element {
  const discovery = useDiscovery();

  const [step, setStep] = useState<Step>("register");
  const [name, setName] = useState("");
  const [target, setTarget] = useState("");
  const [user, setUser] = useState("");
  const [port, setPort] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [added, setAdded] = useState<AddHostResponse | null>(null);
  const [scan, setScan] = useState<ScanKeyResponse | null>(null);
  const [scanBusy, setScanBusy] = useState(false);
  const [trusted, setTrusted] = useState(false);
  const [verify, setVerify] = useState<VerifyHostResponse | null>(null);
  const [job, setJob] = useState<JobAccepted | null>(null);

  const submitRegister = async (): Promise<void> => {
    if (busy || !name.trim() || !target.trim()) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const portNumber = port.trim() ? Number.parseInt(port.trim(), 10) : undefined;
      const response = await addHost({
        name: name.trim(),
        target: target.trim(),
        user: user.trim() || null,
        port: Number.isNaN(portNumber ?? 0) ? null : (portNumber ?? null),
      });
      setAdded(response);
      setStep("trust");
      void runScan(response.instance_id);
    } catch (e) {
      setError(errorText(e, "Could not register the host"));
    } finally {
      setBusy(false);
    }
  };

  const runScan = async (instanceId: string): Promise<void> => {
    setScanBusy(true);
    setScan(null);
    setError(null);
    try {
      const result = await scanHostKey(instanceId);
      setScan(result);
      if (result.status === "trusted") {
        // Already recorded (e.g. a re-added host): nothing to confirm.
        setTrusted(true);
      }
    } catch (e) {
      setError(errorText(e, "Key scan failed"));
    } finally {
      setScanBusy(false);
    }
  };

  const confirmTrust = async (): Promise<void> => {
    if (!added || !scan || busy) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await trustHostKey(added.instance_id, scan.lines ?? []);
      setTrusted(true);
    } catch (e) {
      setError(errorText(e, "Could not record the keys"));
    } finally {
      setBusy(false);
    }
  };

  const runVerify = async (): Promise<void> => {
    if (!added || busy) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await verifyHost(added.instance_id);
      setVerify(result);
      if (result.status === "ok") {
        setStep("done");
        void discovery.refresh(added.instance_id);
      }
    } catch (e) {
      setError(errorText(e, "Verification failed"));
    } finally {
      setBusy(false);
    }
  };

  const startConfigureJob = async (): Promise<void> => {
    if (!added || busy) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      setJob(await startConfigure(added.instance_id));
    } catch (e) {
      setError(errorText(e, "Could not start configure"));
    } finally {
      setBusy(false);
    }
  };

  const stepIndex = { register: 1, trust: 2, verify: 3, done: 3 }[step];

  return (
    <div className="hd ah" data-testid="add-host-page">
      <div className="hd-topbar">
        <button type="button" className="hd-back" data-testid="add-host-back" onClick={onClose}>
          ‹ Back
        </button>
        <span className="hd-title">Add host</span>
        <span className="ah-steps" data-testid="add-host-step">
          step {stepIndex} of 3
        </span>
      </div>

      <div className="hd-scroll">
        <div className="hd-inner ah-inner">
          {step === "register" && (
            <section>
              <div className="hd-heading">1 · Register</div>
              <p className="hd-sub">
                Any SSH-reachable machine can be a remo host. This registers it in the
                service&rsquo;s registry (nothing touches the machine yet) — the same as{" "}
                <code>remo add</code> on a workstation.
              </p>
              <form
                className="ah-form"
                onSubmit={(e) => {
                  e.preventDefault();
                  void submitRegister();
                }}
              >
                <label className="ah-field">
                  Name
                  <input
                    className="hd-input"
                    value={name}
                    data-testid="add-host-name"
                    placeholder="mbp"
                    autoComplete="off"
                    spellCheck={false}
                    onInput={(e) => setName((e.target as HTMLInputElement).value)}
                  />
                </label>
                <label className="ah-field">
                  Target
                  <input
                    className="hd-input"
                    value={target}
                    data-testid="add-host-target"
                    placeholder="[user@]host[:port]"
                    autoComplete="off"
                    spellCheck={false}
                    onInput={(e) => setTarget((e.target as HTMLInputElement).value)}
                  />
                </label>
                <div className="ah-row">
                  <label className="ah-field">
                    User <span className="ah-optional">(optional)</span>
                    <input
                      className="hd-input"
                      value={user}
                      data-testid="add-host-user"
                      placeholder="remo"
                      autoComplete="off"
                      onInput={(e) => setUser((e.target as HTMLInputElement).value)}
                    />
                  </label>
                  <label className="ah-field ah-field--port">
                    Port <span className="ah-optional">(optional)</span>
                    <input
                      className="hd-input"
                      value={port}
                      data-testid="add-host-port"
                      placeholder="22"
                      inputMode="numeric"
                      autoComplete="off"
                      onInput={(e) => setPort((e.target as HTMLInputElement).value)}
                    />
                  </label>
                </div>
                <button
                  type="submit"
                  className="hd-btn hd-btn--primary"
                  data-testid="add-host-submit"
                  disabled={busy || !name.trim() || !target.trim()}
                >
                  {busy ? "Registering…" : "Register host"}
                </button>
              </form>
            </section>
          )}

          {step !== "register" && added && (
            <section>
              <div className="hd-heading">Authorize the console on the host</div>
              <p className="hd-sub">
                Run this once on <code>{added.user}@{added.host}</code> (as{" "}
                <code>{added.user}</code>). It installs the console&rsquo;s public key in{" "}
                <code>~/.ssh/authorized_keys</code>; re-running it is a no-op.
              </p>
              <CopyBlock text={added.authorize_command} testid="authorize-command" />
            </section>
          )}

          {step === "trust" && added && (
            <section>
              <div className="hd-heading">2 · Trust the host&rsquo;s keys</div>
              {scanBusy ? (
                <p className="hd-sub">
                  <span className="rail-spin">⟳</span> Scanning {added.host}&rsquo;s SSH keys…
                </p>
              ) : scan === null ? (
                <p className="hd-sub">Waiting for scan…</p>
              ) : scan.status === "mismatch" ? (
                <div className="hd-nudge ah-mismatch" data-testid="scan-mismatch">
                  <div className="hd-nudge-title">✋ Key mismatch — stop</div>
                  <p>
                    {added.host} presented a key that does NOT match what this console already
                    trusts. That is what a machine-in-the-middle looks like. Do not proceed
                    until you have investigated; if the host was legitimately rebuilt, remove
                    it here and add it again.
                  </p>
                  <p className="ah-detail">{scan.detail}</p>
                </div>
              ) : scan.status === "unreachable" ? (
                <div data-testid="scan-unreachable">
                  <p className="hd-sub">
                    Could not reach {added.host} to scan its keys: {scan.detail}
                  </p>
                  <button
                    type="button"
                    className="hd-btn"
                    data-testid="scan-retry"
                    onClick={() => void runScan(added.instance_id)}
                  >
                    ⟳ Retry scan
                  </button>
                </div>
              ) : trusted ? (
                <div data-testid="trust-confirmed">
                  <p className="hd-sub">✓ Host keys recorded in the console&rsquo;s trust store.</p>
                  <button
                    type="button"
                    className="hd-btn hd-btn--primary"
                    data-testid="trust-continue"
                    onClick={() => setStep("verify")}
                  >
                    Continue
                  </button>
                </div>
              ) : (
                <div data-testid="scan-fingerprints">
                  <p className="hd-sub">
                    {added.host} presented these key fingerprints. Confirming means the console
                    will trust <em>whoever holds these keys</em> as this host — check them
                    against the machine itself if you can.
                  </p>
                  <pre className="ah-fingerprints">{(scan.fingerprints ?? []).join("\n")}</pre>
                  <button
                    type="button"
                    className="hd-btn hd-btn--primary"
                    data-testid="trust-confirm"
                    disabled={busy}
                    onClick={() => void confirmTrust()}
                  >
                    {busy ? "Recording…" : "These match — trust this host"}
                  </button>
                </div>
              )}
            </section>
          )}

          {step === "verify" && added && (
            <section>
              <div className="hd-heading">3 · Verify access</div>
              <p className="hd-sub">
                Once the authorize command has been run on the host, verify that the console
                can log in with its own key.
              </p>
              {verify && verify.status !== "ok" && (
                <div className="hd-nudge" data-testid="verify-failure">
                  <div className="hd-nudge-title">
                    {verify.status === "auth_failed" ? "Not authorized yet" : "Unreachable"}
                  </div>
                  <p>{verify.detail}</p>
                </div>
              )}
              <button
                type="button"
                className="hd-btn hd-btn--primary"
                data-testid="verify-run"
                disabled={busy}
                onClick={() => void runVerify()}
              >
                {busy ? "Verifying…" : "I've run the command — verify"}
              </button>
            </section>
          )}

          {step === "done" && added && (
            <section data-testid="add-host-done">
              <div className="hd-heading">✓ Host added</div>
              <p className="hd-sub">
                The console can now reach <code>{added.name}</code>. It has no remo host tools
                yet — configure installs them (remo-host, zellij, Docker, dev tools) so it can
                run sessions.
              </p>
              {job === null ? (
                <div className="ah-done-actions">
                  <button
                    type="button"
                    className="hd-btn hd-btn--primary"
                    data-testid="configure-now"
                    disabled={busy}
                    onClick={() => void startConfigureJob()}
                  >
                    {busy ? "Starting…" : "Configure now"}
                  </button>
                  <button type="button" className="hd-btn" data-testid="add-host-finish" onClick={onClose}>
                    Finish
                  </button>
                </div>
              ) : (
                <>
                  <JobProgressPanel
                    instanceId={added.instance_id}
                    job={job}
                    fetchStatus={getRegistryJobStatus}
                    onFinished={() => void discovery.refresh(added.instance_id)}
                    onDismiss={() => setJob(null)}
                  />
                  <button type="button" className="hd-btn" data-testid="add-host-finish" onClick={onClose}>
                    Close
                  </button>
                </>
              )}
            </section>
          )}

          {error && <div className="hd-error" data-testid="add-host-error">{error}</div>}
        </div>
      </div>
    </div>
  );
}
