import { useState } from "react";
import { downloadLoadCsv, extractErrorMessage, releaseAllocation, submitAllocation } from "../api/client";
import { useAppState } from "../state/AppState";
import type { SlurmAllocation } from "../types";

interface Props {
  allocation: SlurmAllocation;
  refreshMs: number;
  onRefreshMsChange: (value: number) => void;
  onAllocationChange: (allocation: SlurmAllocation) => void;
  onToast: (message: string, kind?: "info" | "error" | "success") => void;
}

export default function AllocationPanel({ allocation, refreshMs, onRefreshMsChange, onAllocationChange, onToast }: Props) {
  const { state, dispatch } = useAppState();
  const [nodes, setNodes] = useState("1");
  const [timeLimit, setTimeLimit] = useState("05:00:00");
  const [submitting, setSubmitting] = useState(false);
  const [releasing, setReleasing] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const pollMs = parsePollMs(state.samplingMs);
  const active = allocation.active;

  async function handleSubmit() {
    const nodeCount = parseNodeCount(nodes);
    if (nodeCount === undefined) {
      onToast("Node count must be a positive integer.", "error");
      return;
    }
    if (!pollMs) {
      onToast("Worker polling must be an integer from 10 to 1000 ms.", "error");
      return;
    }
    if (!isValidRefreshMs(refreshMs)) {
      onToast("UI refresh must be an integer from 30 to 10000 ms.", "error");
      return;
    }
    setSubmitting(true);
    try {
      const next = await submitAllocation(nodeCount, timeLimit, pollMs, refreshMs);
      onAllocationChange(next);
      dispatch({ type: "setBurnParams", samplingMs: String(pollMs) });
      onToast("SLURM allocation submitted.", "success");
    } catch (error) {
      onToast(extractErrorMessage(error), "error");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleRelease() {
    setReleasing(true);
    try {
      const next = await releaseAllocation();
      onAllocationChange(next);
      dispatch({ type: "setMachines", machines: [] });
      dispatch({ type: "setBurnJobs", jobs: [] });
      onToast("SLURM allocation released.", "success");
    } catch (error) {
      onToast(extractErrorMessage(error), "error");
    } finally {
      setReleasing(false);
    }
  }

  async function handleDownloadCsv() {
    setDownloading(true);
    try {
      await downloadLoadCsv();
      onToast("Load CSV download started.", "success");
    } catch (error) {
      onToast(extractErrorMessage(error), "error");
    } finally {
      setDownloading(false);
    }
  }

  return (
    <section className="allocation-panel">
      <div className="section-heading">
        <h2>SLURM Allocation</h2>
        <span className="muted">{allocation.status}</span>
      </div>
      <div className="allocation-grid">
        <label className="label">
          Nodes
          <input
            className="field"
            type="number"
            min={1}
            step={1}
            disabled={active || submitting}
            value={nodes}
            onChange={(event) => setNodes(event.target.value)}
          />
        </label>
        <label className="label">
          Time Limit
          <input
            className="field"
            type="text"
            disabled={active || submitting}
            value={timeLimit}
            onChange={(event) => setTimeLimit(event.target.value)}
            placeholder="05:00:00"
          />
        </label>
        <label className="label">
          Worker Polling (ms)
          <input
            className={`field ${pollMs ? "" : "field-error"}`}
            type="number"
            min={10}
            max={1000}
            step={1}
            disabled={active || submitting}
            value={state.samplingMs}
            onChange={(event) => dispatch({ type: "setBurnParams", samplingMs: event.target.value })}
          />
        </label>
        <label className="label">
          Sample / UI Refresh (ms)
          <input
            className="field"
            type="number"
            min={30}
            max={10000}
            step={10}
            value={refreshMs}
            onChange={(event) => {
              const parsed = Number(event.target.value);
              if (isValidRefreshMs(parsed)) {
                onRefreshMsChange(parsed);
              }
            }}
          />
        </label>
        <div className="allocation-actions">
          <button
            type="button"
            className="primary-button"
            disabled={active || submitting}
            onClick={() => void handleSubmit()}
          >
            {submitting ? "Submitting" : "Submit Allocation"}
          </button>
          <button
            type="button"
            className="danger-button"
            disabled={!active || releasing}
            onClick={() => void handleRelease()}
          >
            {releasing ? "Releasing" : "Release Nodes"}
          </button>
          <button
            type="button"
            className="secondary-button"
            disabled={downloading}
            onClick={() => void handleDownloadCsv()}
          >
            {downloading ? "Downloading" : "Download Load CSV"}
          </button>
        </div>
      </div>
      <div className="allocation-status">
        {allocation.job_id ? (
          <>
            <span>Job {allocation.job_id}</span>
            <span>
              Ready {allocation.nodes_ready ?? 0}/{allocation.nodes_requested ?? 0}
            </span>
            <span>{allocation.time_limit}</span>
            <span>{allocation.poll_ms} ms polling</span>
            <span>{allocation.sample_ms ?? refreshMs} ms samples</span>
          </>
        ) : (
          <span>No active SLURM allocation.</span>
        )}
      </div>
    </section>
  );
}

function parseNodeCount(value: string): number | undefined {
  if (!/^[1-9][0-9]*$/.test(value.trim())) {
    return undefined;
  }
  return Number(value);
}

function parsePollMs(value: string): number | undefined {
  if (!/^[0-9]+$/.test(value.trim())) {
    return undefined;
  }
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < 10 || parsed > 1000) {
    return undefined;
  }
  return parsed;
}

function isValidRefreshMs(value: number): boolean {
  return Number.isInteger(value) && value >= 30 && value <= 10000;
}
