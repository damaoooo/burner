import { extractErrorMessage, stopJobs } from "../api/client";
import { useAppState } from "../state/AppState";
import type { JobInfo, MachineState } from "../types";

interface Props {
  onToast: (message: string, kind?: "info" | "error" | "success") => void;
}

export default function SchedulePanel({ onToast }: Props) {
  const { state } = useAppState();
  const now = Date.now() / 1000;
  const scheduled = Object.values(state.burnJobs)
    .filter((job) => job.started_at > now)
    .sort((left, right) => left.started_at - right.started_at);

  async function cancelJob(job: JobInfo) {
    try {
      await stopJobs([job.job_id]);
      onToast("Schedule cancelled.", "success");
    } catch (error) {
      onToast(extractErrorMessage(error), "error");
    }
  }

  return (
    <section className="schedule-section">
      <div className="section-heading">
        <h2>Schedule Manager</h2>
        <span className="muted">{scheduled.length} pending</span>
      </div>
      {scheduled.length === 0 ? (
        <div className="empty-state compact">No scheduled jobs.</div>
      ) : (
        <div className="schedule-table manager-table">
          {scheduled.map((job) => (
            <div className="schedule-row manager-row" key={job.job_id}>
              <span>{machineName(job.machine_id, state.machines)}</span>
              <span>{formatLocal(job.started_at)}</span>
              <span>{formatLocal(job.started_at + job.duration_seconds)}</span>
              <span>{job.waveform_name ?? "waveform"}</span>
              <span>{job.job_id.slice(-8)}</span>
              <button type="button" className="danger-button compact-button" onClick={() => void cancelJob(job)}>
                Cancel
              </button>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function machineName(machineId: string, machines: Record<string, MachineState>): string {
  return machines[machineId]?.config.name ?? machineId;
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
