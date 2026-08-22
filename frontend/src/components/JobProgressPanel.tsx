// Progress panel for a detached remo-host job (clone / rebuild — plan §2.4).
//
// Never a bare spinner: devcontainer builds take minutes, so the panel polls
// `GET /hosts/{id}/jobs/{job_id}` every 2s and shows the state line plus the
// job's live log tail in an auto-scrolled <pre>. On a terminal state it stops
// polling and asks the caller to re-discover the instance (`onFinished`), so
// the projects table reflects the clone/rebuild without a manual refresh.

import { useEffect, useRef, useState } from "react";
import { getJobStatus, type JobAccepted, type JobStatus } from "../api/client";
import "./HostDetailPage.css";

const JOB_POLL_INTERVAL_MS = 2_000;

interface JobProgressPanelProps {
  instanceId: string;
  job: JobAccepted;
  /** Status poller override (023): registry-admin jobs live on the SERVICE
   * (`GET /registry/jobs/{id}`), not the host — pass `getRegistryJobStatus`.
   * Defaults to the host-job poller, leaving existing call sites unchanged. */
  fetchStatus?: (instanceId: string, jobId: string) => Promise<JobStatus>;
  /** Called once, when the job reaches succeeded/failed — the caller should
   * `discovery.refresh(instanceId)` so the projects table catches up. */
  onFinished: () => void;
  onDismiss: () => void;
}

function stateLine(job: JobAccepted, status: JobStatus | null): string {
  // Configure jobs (023) have no project — the host itself is the subject.
  if (job.kind === "configure") {
    if (status === null || status.state === "running") {
      return "Configuring host…";
    }
    return status.state === "succeeded"
      ? "Configure succeeded — host tools installed"
      : `Configure failed${status.exit_code == null ? "" : ` (exit ${status.exit_code})`}`;
  }
  const verb = job.kind === "rebuild" ? "Rebuilding" : "Cloning";
  if (status === null || status.state === "running") {
    return `${verb} ${job.project}…`;
  }
  if (status.state === "succeeded") {
    return `${job.kind === "rebuild" ? "Rebuild" : "Clone"} of ${job.project} succeeded`;
  }
  const exit = status.exit_code === null || status.exit_code === undefined
    ? ""
    : ` (exit ${status.exit_code})`;
  return `${job.kind === "rebuild" ? "Rebuild" : "Clone"} of ${job.project} failed${exit}`;
}

export function JobProgressPanel({
  instanceId,
  job,
  fetchStatus = getJobStatus,
  onFinished,
  onDismiss,
}: JobProgressPanelProps): JSX.Element {
  const [status, setStatus] = useState<JobStatus | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);
  const logRef = useRef<HTMLPreElement | null>(null);
  const finishedRef = useRef(false);
  const onFinishedRef = useRef(onFinished);
  onFinishedRef.current = onFinished;

  useEffect(() => {
    finishedRef.current = false;
    let disposed = false;
    let inFlight = false;

    const poll = async (): Promise<void> => {
      if (disposed || finishedRef.current || inFlight) {
        return;
      }
      // Hidden tab: skip the tick (the hostStats precedent) — polling a log
      // tail nobody can see is pure waste. The visibilitychange handler
      // refetches the moment the operator returns.
      if (typeof document !== "undefined" && document.visibilityState === "hidden") {
        return;
      }
      inFlight = true;
      try {
        const next = await fetchStatus(instanceId, job.job_id);
        if (disposed) {
          return;
        }
        setStatus(next);
        setPollError(null);
        if (next.state !== "running" && !finishedRef.current) {
          finishedRef.current = true;
          onFinishedRef.current();
        }
      } catch (error) {
        if (!disposed) {
          // Keep the last status; a transient poll failure is not a job failure.
          setPollError(error instanceof Error ? error.message : "Could not read job status");
        }
      } finally {
        inFlight = false;
      }
    };

    void poll();
    const interval = setInterval(() => void poll(), JOB_POLL_INTERVAL_MS);
    const onVisibility = (): void => {
      if (document.visibilityState === "visible") {
        void poll();
      }
    };
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", onVisibility);
    }
    return () => {
      disposed = true;
      clearInterval(interval);
      if (typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", onVisibility);
      }
    };
  }, [instanceId, job.job_id, fetchStatus]);

  // Auto-scroll: the log tail is append-mostly, and the newest line is the one
  // the operator is waiting on.
  useEffect(() => {
    const el = logRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [status?.log_tail]);

  const terminal = status !== null && status.state !== "running";
  const failed = status?.state === "failed";

  return (
    <div
      className={`hd-job${failed ? " hd-job--failed" : ""}`}
      data-testid={`job-panel-${job.job_id}`}
    >
      <div className="hd-job-head">
        <span className="hd-job-state" data-testid="job-state-line">
          {!terminal && <span className="rail-spin">⟳</span>} {stateLine(job, status)}
        </span>
        {terminal && (
          <button type="button" className="hd-btn" data-testid="job-dismiss" onClick={onDismiss}>
            Dismiss
          </button>
        )}
      </div>
      <pre className="hd-job-log" ref={logRef} data-testid="job-log">
        {status?.log_tail || "waiting for output…"}
      </pre>
      {pollError && <div className="hd-job-pollerror">status poll failed: {pollError}</div>}
    </div>
  );
}
