import { useEffect, useMemo, useState } from "react";
import { extractErrorMessage, startBurn, stopBurn } from "../api/client";
import { useAppState } from "../state/AppState";
import type { BurnStartRequest, JobInfo } from "../types";

interface Props {
  onToast: (message: string, kind?: "info" | "error" | "success") => void;
}

interface PlannedWindow {
  machineId: string;
  machineName: string;
  start: number;
  end: number;
  delaySeconds: number;
  waveformName: string;
}

interface PendingStart {
  payload: BurnStartRequest;
  planned: PlannedWindow[];
}

export default function GlobalBurnBar({ onToast }: Props) {
  const { state, dispatch } = useAppState();
  const [progressNow, setProgressNow] = useState(Date.now() / 1000);
  const [progressStartedAt, setProgressStartedAt] = useState<number | null>(null);
  const [pendingStart, setPendingStart] = useState<PendingStart | null>(null);
  const [conflicts, setConflicts] = useState<string[] | null>(null);
  const [scheduledResult, setScheduledResult] = useState<JobInfo[] | null>(null);

  const jobs = useMemo(() => Object.values(state.burnJobs), [state.burnJobs]);
  const activeJobs = jobs.filter((job) => isActive(job, progressNow));
  const scheduledCount = jobs.filter((job) => job.started_at > progressNow).length;
  const hasJobs = jobs.length > 0;

  useEffect(() => {
    if (activeJobs.length > 0 && progressStartedAt === null) {
      setProgressStartedAt(Date.now() / 1000);
    }
    if (activeJobs.length === 0) {
      setProgressStartedAt(null);
    }
  }, [activeJobs.length, progressStartedAt]);

  useEffect(() => {
    if (!hasJobs) {
      return undefined;
    }
    const timer = window.setInterval(() => setProgressNow(Date.now() / 1000), 100);
    return () => window.clearInterval(timer);
  }, [hasJobs]);

  const progress = useMemo(() => {
    if (activeJobs.length === 0 || progressStartedAt === null) {
      return 0;
    }
    const end = Math.max(...activeJobs.map((job) => job.started_at + job.duration_seconds));
    const total = Math.max(0.001, end - progressStartedAt);
    return Math.max(0, Math.min(1, (progressNow - progressStartedAt) / total));
  }, [activeJobs, progressNow, progressStartedAt]);

  async function handleStart() {
    const plan = buildPendingStart();
    if (!plan) {
      return;
    }

    const overlapMessages = findOverlapMessages(plan.planned, jobs);
    if (overlapMessages.length > 0) {
      setConflicts(overlapMessages);
      return;
    }

    if (plan.payload.sync_mode === "scheduled") {
      setPendingStart(plan);
      return;
    }

    await submitStart(plan);
  }

  function buildPendingStart(): PendingStart | undefined {
    const selected = Object.values(state.machines).filter(
      (machine) => machine.burnEnabled && machine.connectionStatus === "connected"
    );
    if (selected.length === 0) {
      onToast("Select at least one connected machine.", "error");
      return undefined;
    }

    const durationSeconds = parseDurationSeconds(state.duration);
    if (durationSeconds === undefined) {
      onToast("Duration must be a positive whole number of seconds.", "error");
      return undefined;
    }

    const period = formatPeriodSeconds(state.period);
    if (!period) {
      onToast("Period must be a positive number of seconds.", "error");
      return undefined;
    }

    const startTimeUtc = state.syncMode === "scheduled" ? toUtcIso(state.scheduledStartLocal) : undefined;
    if (state.syncMode === "scheduled" && !startTimeUtc) {
      onToast("Choose a scheduled start time in the future.", "error");
      return undefined;
    }

    const baseStart = baseStartSeconds(state.syncMode, state.scheduledStartLocal);
    if (baseStart === undefined) {
      onToast("Choose a scheduled start time in the future.", "error");
      return undefined;
    }

    const machines = selected.map((machine) => {
      const waveformName = state.usePerMachineWaveform
        ? state.perMachineWaveformNames[machine.config.id] || state.globalWaveformName
        : state.globalWaveformName;
      return {
        id: machine.config.id,
        enabled: machine.burnEnabled,
        burn_cpu: machine.burnCpu,
        burn_gpu: machine.burnGpu,
        delay_seconds: state.syncMode === "immediate" ? 0 : Math.max(0, machine.delaySeconds),
        waveform_name: waveformName
      };
    });

    if (machines.some((machine) => !machine.waveform_name)) {
      onToast("Select or save a waveform first.", "error");
      return undefined;
    }
    if (machines.some((machine) => !machine.burn_cpu && !machine.burn_gpu)) {
      onToast("Every enabled machine must burn CPU or GPU.", "error");
      return undefined;
    }

    const planned = selected.map((machine, index) => {
      const request = machines[index];
      const start = baseStart + request.delay_seconds;
      return {
        machineId: machine.config.id,
        machineName: machine.config.name,
        start,
        end: start + durationSeconds,
        delaySeconds: request.delay_seconds,
        waveformName: request.waveform_name
      };
    });

    return {
      payload: {
        sync_mode: state.syncMode,
        start_time_utc: startTimeUtc,
        duration: `${durationSeconds}s`,
        period,
        machines
      },
      planned
    };
  }

  async function submitStart(plan: PendingStart) {
    try {
      const started = await startBurn(plan.payload);
      started.forEach((job: JobInfo) => dispatch({ type: "burnStarted", job }));
      if (plan.payload.sync_mode === "scheduled") {
        setScheduledResult(started);
      } else {
        onToast("Burn started.", "success");
      }
      setPendingStart(null);
    } catch (error) {
      setConflicts([extractErrorMessage(error)]);
    }
  }

  async function handleStop() {
    try {
      await stopBurn("all");
    } catch (error) {
      onToast(extractErrorMessage(error), "error");
    }
  }

  return (
    <>
      <div className="global-burn-bar">
        <button
          type="button"
          className="burn-button"
          onClick={() => void handleStart()}
        >
          Start Burn
        </button>
        {activeJobs.length > 0 ? (
          <>
            <div className="progress-track" aria-label="burn progress">
              <div className="progress-fill" style={{ width: `${progress * 100}%` }} />
            </div>
            <span className="progress-text">{Math.round(progress * 100)}%</span>
          </>
        ) : (
          <span className="progress-text">{scheduledCount > 0 ? `${scheduledCount} scheduled` : "idle"}</span>
        )}
        {hasJobs && (
          <button type="button" className="danger-button" onClick={() => void handleStop()}>
            Stop All
          </button>
        )}
      </div>

      {pendingStart && (
        <div className="modal-backdrop" role="dialog" aria-modal="true">
          <div className="modal wide-modal">
            <h3>Confirm Schedule</h3>
            <ScheduleSummary planned={pendingStart.planned} />
            <div className="modal-actions">
              <button type="button" className="secondary-button" onClick={() => setPendingStart(null)}>
                Cancel
              </button>
              <button type="button" className="primary-button" onClick={() => void submitStart(pendingStart)}>
                Confirm Schedule
              </button>
            </div>
          </div>
        </div>
      )}

      {conflicts && (
        <div className="modal-backdrop" role="dialog" aria-modal="true">
          <div className="modal wide-modal">
            <h3>Schedule Conflict</h3>
            <p className="modal-message">A job cannot overlap another job on the same machine, including the 5 second grace window after completion.</p>
            <ul className="conflict-list">
              {conflicts.map((message) => (
                <li key={message}>{message}</li>
              ))}
            </ul>
            <div className="modal-actions">
              <button type="button" className="primary-button" onClick={() => setConflicts(null)}>
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {scheduledResult && (
        <div className="modal-backdrop" role="dialog" aria-modal="true">
          <div className="modal wide-modal">
            <h3>Schedules Created</h3>
            <JobSummary jobs={scheduledResult} />
            <div className="modal-actions">
              <button type="button" className="primary-button" onClick={() => setScheduledResult(null)}>
                Done
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

function ScheduleSummary({ planned }: { planned: PlannedWindow[] }) {
  return (
    <div className="schedule-table">
      {planned.map((item) => (
        <div className="schedule-row" key={item.machineId}>
          <span>{item.machineName}</span>
          <span>{formatLocal(item.start)}</span>
          <span>{formatLocal(item.end)}</span>
          <span>{item.delaySeconds.toFixed(2)}s delay</span>
          <span>{item.waveformName}</span>
        </div>
      ))}
    </div>
  );
}

function JobSummary({ jobs }: { jobs: JobInfo[] }) {
  return (
    <div className="schedule-table">
      {jobs.map((job) => (
        <div className="schedule-row" key={job.job_id}>
          <span>{job.machine_id}</span>
          <span>{formatLocal(job.started_at)}</span>
          <span>{formatLocal(job.started_at + job.duration_seconds)}</span>
          <span>{job.waveform_name ?? "waveform"}</span>
          <span>{job.job_id.slice(-8)}</span>
        </div>
      ))}
    </div>
  );
}

function findOverlapMessages(planned: PlannedWindow[], existingJobs: JobInfo[]): string[] {
  const messages = [];
  for (const item of planned) {
    for (const job of existingJobs) {
      if (job.machine_id !== item.machineId) {
        continue;
      }
      const existingStart = job.started_at;
      const existingEnd = job.started_at + job.duration_seconds;
      if (windowsOverlap(item.start, item.end, existingStart, existingEnd)) {
        messages.push(
          `${item.machineName}: requested ${formatLocal(item.start)} - ${formatLocal(item.end)} conflicts with ${job.job_id.slice(-8)} (${formatLocal(existingStart)} - ${formatLocal(existingEnd)}).`
        );
      }
    }
  }
  return messages;
}

function windowsOverlap(newStart: number, newEnd: number, existingStart: number, existingEnd: number): boolean {
  const grace = 5;
  return newStart < existingEnd + grace && existingStart < newEnd + grace;
}

function isActive(job: JobInfo, now: number): boolean {
  return job.started_at <= now && now < job.started_at + job.duration_seconds;
}

function baseStartSeconds(syncMode: string, scheduledLocal: string): number | undefined {
  if (syncMode === "immediate") {
    return Date.now() / 1000;
  }
  if (syncMode === "delayed") {
    return Date.now() / 1000 + 5;
  }
  const parsed = Date.parse(scheduledLocal);
  if (!Number.isFinite(parsed) || parsed <= Date.now()) {
    return undefined;
  }
  return parsed / 1000;
}

function toUtcIso(localValue: string): string | undefined {
  if (!localValue) {
    return undefined;
  }
  const date = new Date(localValue);
  if (!Number.isFinite(date.getTime()) || date.getTime() <= Date.now()) {
    return undefined;
  }
  return date.toISOString().replace(/\.\d{3}Z$/, "Z");
}

function parseDurationSeconds(value: string): number | undefined {
  const trimmed = value.trim();
  if (!/^[1-9][0-9]*$/.test(trimmed)) {
    return undefined;
  }
  return Number(trimmed);
}

function formatPeriodSeconds(value: string): string | undefined {
  const trimmed = value.trim();
  if (!/^([0-9]+(?:\.[0-9]+)?|\.[0-9]+)$/.test(trimmed)) {
    return undefined;
  }
  const amount = Number(trimmed);
  if (!Number.isFinite(amount) || amount <= 0) {
    return undefined;
  }
  return `${amount}s`;
}

function formatLocal(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleString(undefined, {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit"
  });
}
