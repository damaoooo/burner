import { useEffect, useMemo, useState } from "react";
import { extractErrorMessage, fetchMachines, startBurn, startBurnAll, stopBurn } from "../api/client";
import { useAppState } from "../state/AppState";
import type { BurnStartAllRequest, BurnStartRequest, JobInfo, MachineApiRecord, RunMode, SyncMode } from "../types";

interface Props {
  onToast: (message: string, kind?: "info" | "error" | "success") => void;
  readyNodeCount?: number;
  totalNodeCount?: number;
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
  payload?: BurnStartRequest;
  allPayload?: BurnStartAllRequest;
  planned: PlannedWindow[];
}

const CLUSTER_START_THRESHOLD = 50;

export default function GlobalBurnBar({ onToast, readyNodeCount, totalNodeCount }: Props) {
  const { state, dispatch } = useAppState();
  const [progressNow, setProgressNow] = useState(Date.now() / 1000);
  const [progressStartedAt, setProgressStartedAt] = useState<number | null>(null);
  const [pendingStart, setPendingStart] = useState<PendingStart | null>(null);
  const [conflicts, setConflicts] = useState<string[] | null>(null);
  const [scheduledResult, setScheduledResult] = useState<JobInfo[] | null>(null);

  const jobs = useMemo(() => Object.values(state.burnJobs), [state.burnJobs]);
  const activeJobs = jobs.filter((job) => isActive(job, progressNow));
  const scheduledCount = jobs
    .filter((job) => job.started_at > progressNow)
    .reduce((total, job) => total + (job.node_count ?? 1), 0);
  const hasJobs = jobs.length > 0;
  const parsedSamplingMs = parseSamplingMs(state.samplingMs);
  const pageReadyCount = Object.values(state.machines).filter((machine) => machine.connectionStatus === "connected").length;
  const readyCount = readyNodeCount ?? pageReadyCount;
  const startDisabled = parsedSamplingMs === undefined || readyCount === 0;

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
    if (state.samplingBuild.running) {
      onToast("Sampling rebuild is running. Wait for it to finish before starting burn.", "error");
      return;
    }
    if (parsedSamplingMs === undefined) {
      onToast("Worker polling must be an integer from 10 to 1000 ms.", "error");
      return;
    }
    const clusterStart = shouldUseClusterStart(readyCount, totalNodeCount);
    const plan = clusterStart ? buildClusterPendingStart() : await buildDetailedPendingStart();
    if (!plan) {
      return;
    }

    const overlapMessages = findOverlapMessages(plan.planned, jobs);
    if (overlapMessages.length > 0) {
      setConflicts(overlapMessages);
      return;
    }

    if ((plan.allPayload ?? plan.payload)?.sync_mode === "scheduled") {
      setPendingStart(plan);
      return;
    }

    await submitStart(plan);
  }

  async function buildDetailedPendingStart(): Promise<PendingStart | undefined> {
    let machines;
    try {
      machines = await fetchMachines(0, 5000);
    } catch (error) {
      onToast(extractErrorMessage(error), "error");
      return undefined;
    }
    return buildPendingStart(machines);
  }

  function buildPendingStart(records: MachineApiRecord[]): PendingStart | undefined {
    const selected = records.filter((machine) => machine.connection_status === "connected");
    if (selected.length === 0) {
      onToast("Wait for the SLURM workers to become ready.", "error");
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

    const syncMode = requestSyncMode(state.runMode, selected);
    const startTimeUtc = syncMode === "scheduled" ? toUtcIso(state.scheduledStartLocal) : undefined;
    if (syncMode === "scheduled" && !startTimeUtc) {
      onToast("Choose a scheduled start time in the future.", "error");
      return undefined;
    }

    const baseStart = baseStartSeconds(syncMode, state.scheduledStartLocal);
    if (baseStart === undefined) {
      onToast("Choose a scheduled start time in the future.", "error");
      return undefined;
    }

    const machines = selected.map((machine) => {
      const waveformName = state.usePerMachineWaveform
        ? state.perMachineWaveformNames[machine.id] || state.globalWaveformName
        : state.globalWaveformName;
      return {
        id: machine.id,
        enabled: true,
        burn_cpu: true,
        burn_gpu: false,
        delay_seconds: 0,
        waveform_name: waveformName
      };
    });

    if (machines.some((machine) => !machine.waveform_name)) {
      onToast("Select or save a waveform first.", "error");
      return undefined;
    }
    const planned = selected.map((machine, index) => {
      const request = machines[index];
      const start = baseStart;
      return {
        machineId: machine.id,
        machineName: machine.name,
        start,
        end: start + durationSeconds,
        delaySeconds: 0,
        waveformName: request.waveform_name
      };
    });

    return {
      payload: {
        sync_mode: syncMode,
        start_time_utc: startTimeUtc,
        duration: `${durationSeconds}s`,
        period,
        tick_seconds: (parsedSamplingMs ?? 100) / 1000,
        machines
      },
      planned
    };
  }

  function buildClusterPendingStart(): PendingStart | undefined {
    if (!totalNodeCount || readyCount < totalNodeCount) {
      onToast(`Wait for all SLURM workers. ${readyCount}/${totalNodeCount ?? 0} are ready.`, "error");
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

    if (!state.globalWaveformName) {
      onToast("Select or save a waveform first.", "error");
      return undefined;
    }

    const syncMode = state.runMode === "schedule" ? "scheduled" : "immediate";
    const startTimeUtc = syncMode === "scheduled" ? toUtcIso(state.scheduledStartLocal) : undefined;
    if (syncMode === "scheduled" && !startTimeUtc) {
      onToast("Choose a scheduled start time in the future.", "error");
      return undefined;
    }

    const baseStart = baseStartSeconds(syncMode, state.scheduledStartLocal);
    if (baseStart === undefined) {
      onToast("Choose a scheduled start time in the future.", "error");
      return undefined;
    }

    return {
      allPayload: {
        sync_mode: syncMode,
        start_time_utc: startTimeUtc,
        duration: `${durationSeconds}s`,
        period,
        tick_seconds: (parsedSamplingMs ?? 100) / 1000,
        waveform_name: state.globalWaveformName
      },
      planned: [
        {
          machineId: "__shaheen_cluster__",
          machineName: `${totalNodeCount} nodes`,
          start: baseStart,
          end: baseStart + durationSeconds,
          delaySeconds: 0,
          waveformName: state.globalWaveformName
        }
      ]
    };
  }

  async function submitStart(plan: PendingStart) {
    try {
      const started = plan.allPayload ? await startBurnAll(plan.allPayload) : await startBurn(plan.payload as BurnStartRequest);
      started.forEach((job: JobInfo) => dispatch({ type: "burnStarted", job }));
      if ((plan.allPayload ?? plan.payload)?.sync_mode === "scheduled") {
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
      dispatch({ type: "setBurnJobs", jobs: [] });
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
          disabled={startDisabled}
          title={startDisabled ? disabledReason(parsedSamplingMs, readyCount, totalNodeCount) : undefined}
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
          <button type="button" className="danger-button" disabled={state.samplingBuild.running} onClick={() => void handleStop()}>
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
  const visible = planned.slice(0, 50);
  return (
    <div className="schedule-table">
      {visible.map((item) => (
        <div className="schedule-row" key={item.machineId}>
          <span>{item.machineName}</span>
          <span>{formatLocal(item.start)}</span>
          <span>{formatLocal(item.end)}</span>
          <span>{item.delaySeconds.toFixed(2)}s delay</span>
          <span>{item.waveformName}</span>
        </div>
      ))}
      {planned.length > visible.length && (
        <div className="schedule-row">
          <span>{planned.length - visible.length} more nodes</span>
          <span />
          <span />
          <span />
          <span />
        </div>
      )}
    </div>
  );
}

function JobSummary({ jobs }: { jobs: JobInfo[] }) {
  const visible = jobs.slice(0, 50);
  return (
    <div className="schedule-table">
      {visible.map((job) => (
        <div className="schedule-row" key={job.job_id}>
          <span>{job.node_count ? `${job.node_count} nodes` : job.machine_id}</span>
          <span>{formatLocal(job.started_at)}</span>
          <span>{formatLocal(job.started_at + job.duration_seconds)}</span>
          <span>{job.waveform_name ?? "waveform"}</span>
          <span>{job.job_id.slice(-8)}</span>
        </div>
      ))}
      {jobs.length > visible.length && (
        <div className="schedule-row">
          <span>{jobs.length - visible.length} more jobs</span>
          <span />
          <span />
          <span />
          <span />
        </div>
      )}
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

function requestSyncMode(runMode: RunMode, _selected: MachineApiRecord[]): SyncMode {
  if (runMode === "schedule") {
    return "scheduled";
  }
  return "immediate";
}

function shouldUseClusterStart(readyCount: number, totalCount: number | undefined): boolean {
  return Boolean(totalCount && totalCount > CLUSTER_START_THRESHOLD && readyCount >= totalCount);
}

function parseSamplingMs(value: string): number | undefined {
  const trimmed = value.trim();
  if (!/^[0-9]+$/.test(trimmed)) {
    return undefined;
  }
  const amount = Number(trimmed);
  if (!Number.isInteger(amount) || amount < 10 || amount > 1000) {
    return undefined;
  }
  return amount;
}

function disabledReason(parsedSamplingMs: number | undefined, readyCount: number, totalCount: number | undefined): string {
  if (parsedSamplingMs === undefined) {
    return "Worker polling must be 10-1000 ms";
  }
  if (readyCount === 0 && totalCount) {
    return `Waiting for SLURM workers. 0/${totalCount} are ready.`;
  }
  if (readyCount === 0) {
    return "No ready SLURM workers";
  }
  return "";
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
