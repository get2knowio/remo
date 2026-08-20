// The detached-job panel: 2s polling, the state line + log tail (never a bare
// spinner), and the one-shot refresh once the job reaches a terminal state.

import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getJobStatus = vi.fn();

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  // Lazy closure: the factory is hoisted above the const initialization.
  return { ...actual, getJobStatus: (...args: unknown[]) => getJobStatus(...args) };
});

import { JobProgressPanel } from "./JobProgressPanel";

const JOB = { job_id: "j1", kind: "clone", project: "widget" };

function running(log: string): Record<string, unknown> {
  return { state: "running", exit_code: null, started_at: "", finished_at: "", log_tail: log };
}

const onFinished = vi.fn();
const onDismiss = vi.fn();

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("JobProgressPanel", () => {
  it("polls every 2s and shows the live log tail, not a bare spinner", async () => {
    getJobStatus.mockResolvedValue(running("Cloning into 'widget'…"));
    render(
      <JobProgressPanel instanceId="i-1" job={JOB} onFinished={onFinished} onDismiss={onDismiss} />,
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(getJobStatus).toHaveBeenCalledWith("i-1", "j1");
    expect(screen.getByTestId("job-state-line").textContent).toContain("Cloning widget…");
    expect(screen.getByTestId("job-log").textContent).toContain("Cloning into 'widget'…");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });
    expect(getJobStatus).toHaveBeenCalledTimes(2);
    expect(onFinished).not.toHaveBeenCalled();
  });

  it("stops polling and refreshes discovery once on success", async () => {
    getJobStatus.mockResolvedValueOnce(running("step 1"));
    getJobStatus.mockResolvedValue({
      state: "succeeded",
      exit_code: 0,
      started_at: "",
      finished_at: "",
      log_tail: "done",
    });
    render(
      <JobProgressPanel instanceId="i-1" job={JOB} onFinished={onFinished} onDismiss={onDismiss} />,
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });
    expect(screen.getByTestId("job-state-line").textContent).toContain("succeeded");
    expect(onFinished).toHaveBeenCalledTimes(1);

    const calls = getJobStatus.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(getJobStatus).toHaveBeenCalledTimes(calls); // terminal — no more polls
    expect(onFinished).toHaveBeenCalledTimes(1); // and refreshed exactly once

    // The dismiss affordance appears only once the job is over.
    screen.getByTestId("job-dismiss").click();
    expect(onDismiss).toHaveBeenCalled();
  });

  it("names the exit code on failure", async () => {
    getJobStatus.mockResolvedValue({
      state: "failed",
      exit_code: 5,
      started_at: "",
      finished_at: "",
      log_tail: "boom",
    });
    render(
      <JobProgressPanel instanceId="i-1" job={JOB} onFinished={onFinished} onDismiss={onDismiss} />,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByTestId("job-state-line").textContent).toContain("failed (exit 5)");
    expect(onFinished).toHaveBeenCalledTimes(1);
  });

  it("keeps the last status when a poll fails, and says the poll failed", async () => {
    getJobStatus.mockResolvedValueOnce(running("step 1"));
    getJobStatus.mockRejectedValue(new Error("gateway hiccup"));
    render(
      <JobProgressPanel instanceId="i-1" job={JOB} onFinished={onFinished} onDismiss={onDismiss} />,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });
    expect(screen.getByTestId("job-log").textContent).toContain("step 1");
    expect(screen.getByText(/status poll failed/)).toBeInTheDocument();
    expect(onFinished).not.toHaveBeenCalled();
  });
});

describe("JobProgressPanel registry-admin (023)", () => {
  it("uses a custom fetchStatus and configure wording", async () => {
    const fetchStatus = vi.fn().mockResolvedValue(
      { state: "running", exit_code: null, started_at: "", finished_at: "", log_tail: "PLAY [all]" },
    );
    render(
      <JobProgressPanel
        instanceId="i-1"
        job={{ job_id: "configure-abc", kind: "configure", project: "" }}
        fetchStatus={fetchStatus}
        onFinished={onFinished}
        onDismiss={onDismiss}
      />,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(fetchStatus).toHaveBeenCalledWith("i-1", "configure-abc");
    expect(getJobStatus).not.toHaveBeenCalled();
    expect(screen.getByTestId("job-state-line").textContent).toContain("Configuring host…");
    expect(screen.getByTestId("job-log").textContent).toContain("PLAY [all]");
  });

  it("words configure success and failure without a project", async () => {
    const fetchStatus = vi.fn().mockResolvedValue(
      { state: "failed", exit_code: 2, started_at: "", finished_at: "t", log_tail: "boom" },
    );
    render(
      <JobProgressPanel
        instanceId="i-1"
        job={{ job_id: "configure-x", kind: "configure", project: "" }}
        fetchStatus={fetchStatus}
        onFinished={onFinished}
        onDismiss={onDismiss}
      />,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByTestId("job-state-line").textContent).toContain("Configure failed (exit 2)");
  });
});
