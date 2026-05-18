import { useState } from "react";
import { extractErrorMessage, releaseAllocation, submitAllocation } from "../api/client";
import { useAppState } from "../state/AppState";
import type { SlurmAllocation } from "../types";

interface Props {
  allocation: SlurmAllocation;
  onAllocationChange: (allocation: SlurmAllocation) => void;
  onToast: (message: string, kind?: "info" | "error" | "success") => void;
}

export default function AllocationPanel({ allocation, onAllocationChange, onToast }: Props) {
  const { state, dispatch } = useAppState();
  const [nodes, setNodes] = useState("1");
  const [timeLimit, setTimeLimit] = useState("05:00:00");
  const [submitting, setSubmitting] = useState(false);
  const [releasing, setReleasing] = useState(false);
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
    setSubmitting(true);
    try {
      const next = await submitAllocation(nodeCount, timeLimit, pollMs);
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
      onToast("SLURM allocation released.", "success");
    } catch (error) {
      onToast(extractErrorMessage(error), "error");
    } finally {
      setReleasing(false);
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
