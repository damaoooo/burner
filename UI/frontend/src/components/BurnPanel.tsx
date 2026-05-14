import { useCallback, useEffect, useState } from "react";
import { applySamplingTime, extractErrorMessage } from "../api/client";
import ExpressionInput from "./ExpressionInput";
import WaveformEditor from "./WaveformEditor";
import WaveformSelector from "./WaveformSelector";
import { useAppState } from "../state/AppState";
import type { MachineState, Point, WaveformInfo } from "../types";

interface Props {
  onToast: (message: string, kind?: "info" | "error" | "success") => void;
}

type SamplingModalMode = "confirm" | "progress" | "result";

export default function BurnPanel({ onToast }: Props) {
  const { state, dispatch } = useAppState();
  const [samplingModal, setSamplingModal] = useState<SamplingModalMode | null>(null);
  const [pendingSamplingMs, setPendingSamplingMs] = useState<number | null>(null);
  const [pendingMachineIds, setPendingMachineIds] = useState<string[]>([]);
  const samplingValue = parseSamplingMs(state.samplingMs);
  const samplingDirty = samplingValue !== undefined && samplingValue !== state.appliedSamplingMs;
  const samplingRunning = state.samplingBuild.running;
  const connectedMachineIds = Object.values(state.machines)
    .filter((machine) => machine.connectionStatus === "connected")
    .map((machine) => machine.config.id);

  const handleGeneratedPoints = useCallback(
    (points: Point[]) => {
      dispatch({ type: "setGlobalWaveform", points });
    },
    [dispatch]
  );

  function handleGlobalSelect(name: string, waveform: WaveformInfo) {
    dispatch({ type: "setGlobalWaveform", points: waveform.points, name });
  }

  useEffect(() => {
    if (samplingModal === "progress" && !samplingRunning && state.samplingBuild.exitCode !== undefined) {
      setSamplingModal("result");
    }
  }, [samplingModal, samplingRunning, state.samplingBuild.exitCode]);

  function handleApplySampling() {
    if (samplingValue === undefined) {
      onToast("Sampling time must be an integer from 10 to 1000 ms.", "error");
      return;
    }
    if (!samplingDirty) {
      onToast("Sampling time is already applied.", "info");
      return;
    }
    if (connectedMachineIds.length === 0) {
      onToast("Connect at least one machine before applying sampling time.", "error");
      return;
    }

    setPendingSamplingMs(samplingValue);
    setPendingMachineIds(connectedMachineIds);
    setSamplingModal("confirm");
  }

  async function confirmApplySampling() {
    if (pendingSamplingMs === null || pendingMachineIds.length === 0) {
      return;
    }
    dispatch({ type: "startSamplingBuild", machineIds: pendingMachineIds, samplingMs: pendingSamplingMs });
    setSamplingModal("progress");
    try {
      await applySamplingTime(pendingSamplingMs, pendingMachineIds);
    } catch (error) {
      const message = extractErrorMessage(error);
      dispatch({ type: "samplingBuildFailedToStart", message });
      setSamplingModal("result");
    }
  }

  return (
    <>
      <section className="control-band">
        <div className="control-grid">
          <div className="waveform-area">
            <div className="section-heading">
              <h2>Waveform</h2>
              <WaveformSelector
                waveforms={state.waveforms}
                value={state.globalWaveformName}
                onSelect={handleGlobalSelect}
              />
            </div>
            <WaveformEditor
              points={state.globalWaveform}
              onChange={(points, name) => dispatch({ type: "setGlobalWaveform", points, name })}
              onSaved={(name, points) => dispatch({ type: "setGlobalWaveform", points, name })}
              onToast={onToast}
            />
            <ExpressionInput onPointsGenerated={handleGeneratedPoints} />
          </div>

          <div className="run-params">
            <h2>Run Parameters</h2>
            <div className="mode-caption">
              {state.runMode === "realtime" ? "Realtime mode" : "Schedule mode"}
            </div>
            <label className="label" htmlFor="duration-input">
              Duration (s)
            </label>
            <input
              id="duration-input"
              className="field"
              type="number"
              min={1}
              step={1}
              disabled={samplingRunning}
              value={state.duration}
              onChange={(event) => dispatch({ type: "setBurnParams", duration: event.target.value })}
            />
            <label className="label" htmlFor="period-input">
              Period (s)
            </label>
            <input
              id="period-input"
              className="field"
              type="number"
              min={0.01}
              step={0.01}
              disabled={samplingRunning}
              value={state.period}
              onChange={(event) => dispatch({ type: "setBurnParams", period: event.target.value })}
            />
            {state.runMode === "schedule" && (
              <>
                <label className="label" htmlFor="scheduled-start-input">
                  Start Time
                </label>
                <input
                  id="scheduled-start-input"
                  className="field"
                  type="datetime-local"
                  step={1}
                  min={formatLocalDateTime(new Date())}
                  disabled={samplingRunning}
                  value={state.scheduledStartLocal}
                  onChange={(event) =>
                    dispatch({
                      type: "setBurnParams",
                      scheduledStartLocal: event.target.value
                    })
                  }
                />
              </>
            )}
            <div className="sampling-row">
              <div>
                <label className="label" htmlFor="sampling-input">
                  Sampling Time (ms)
                </label>
                <input
                  id="sampling-input"
                  className={`field ${samplingValue === undefined ? "field-error" : ""}`}
                  type="number"
                  min={10}
                  max={1000}
                  step={1}
                  disabled={samplingRunning}
                  value={state.samplingMs}
                  onChange={(event) => dispatch({ type: "setBurnParams", samplingMs: event.target.value })}
                />
              </div>
              <div className="caution-box">
                <strong>Cautions</strong>
                <span>Changing this rebuilds CPU/GPU burn backends on remote machines and may take time.</span>
              </div>
            </div>
            <button
              type="button"
              className={`primary-button sampling-apply-button ${!samplingDirty && samplingValue !== undefined ? "sampling-applied-button" : ""}`}
              disabled={samplingRunning || samplingValue === undefined}
              onClick={() => void handleApplySampling()}
            >
              {samplingRunning ? "Rebuilding" : samplingDirty ? "Apply Sampling Time" : `Applied ${state.appliedSamplingMs} ms`}
            </button>
            <label className="toggle-row">
              <input
                type="checkbox"
                disabled={samplingRunning}
                checked={state.usePerMachineWaveform}
                onChange={(event) =>
                  dispatch({ type: "setUsePerMachineWaveform", value: event.target.checked })
                }
              />
              <span>Per-machine waveform</span>
            </label>
          </div>
        </div>
      </section>

      {samplingModal && (
        <SamplingApplyModal
          mode={samplingModal}
          samplingMs={pendingSamplingMs ?? samplingValue ?? state.appliedSamplingMs}
          machineIds={pendingMachineIds}
          machines={state.machines}
          running={samplingRunning}
          exitCode={state.samplingBuild.exitCode}
          message={state.samplingBuild.message}
          onCancel={() => setSamplingModal(null)}
          onConfirm={() => void confirmApplySampling()}
          onClose={() => setSamplingModal(null)}
        />
      )}
    </>
  );
}

function SamplingApplyModal({
  mode,
  samplingMs,
  machineIds,
  machines,
  running,
  exitCode,
  message,
  onCancel,
  onConfirm,
  onClose
}: {
  mode: SamplingModalMode;
  samplingMs: number;
  machineIds: string[];
  machines: Record<string, MachineState>;
  running: boolean;
  exitCode?: number;
  message?: string;
  onCancel: () => void;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const isResult = mode === "result";
  const success = isResult && exitCode === 0;
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="modal wide-modal sampling-modal">
        {mode === "confirm" && (
          <>
            <h3>Apply Sampling Time?</h3>
            <p className="modal-message">
              This will reset, pull, SCP local patched files, and rebuild burn backends on connected machines.
            </p>
            <div className="sampling-confirm-grid">
              <span>Sampling</span>
              <strong>{samplingMs} ms</strong>
              <span>Machines</span>
              <strong>{machineIds.map((machineId) => machineName(machineId, machines)).join(", ")}</strong>
            </div>
            <div className="modal-actions">
              <button type="button" className="secondary-button" onClick={onCancel}>
                Cancel
              </button>
              <button type="button" className="primary-button" onClick={onConfirm}>
                Confirm Apply
              </button>
            </div>
          </>
        )}
        {mode === "progress" && (
          <>
            <h3>Applying Sampling Time</h3>
            <p className="modal-message">Rebuilding remote burn backends with {samplingMs} ms sampling.</p>
            <SamplingBuildProgress machines={machines} />
          </>
        )}
        {isResult && (
          <>
            <h3>{success ? "Sampling Time Applied" : "Sampling Rebuild Failed"}</h3>
            <p className={`modal-message ${success ? "success-text" : "error-text"}`}>
              {success ? `All target machines rebuilt with ${samplingMs} ms sampling.` : message || "At least one remote rebuild failed."}
            </p>
            <SamplingBuildProgress machines={machines} />
            <div className="modal-actions">
              <button type="button" className="primary-button" disabled={running} onClick={onClose}>
                Done
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function SamplingBuildProgress({ machines }: { machines: Record<string, MachineState> }) {
  const { state } = useAppState();
  const statuses = Object.entries(state.samplingBuild.machines);
  if (!state.samplingBuild.running && statuses.length === 0) {
    return null;
  }

  const average =
    statuses.length === 0
      ? 0
      : statuses.reduce((total, [, status]) => total + status.progress, 0) / statuses.length;
  const current =
    statuses.find(([, status]) => status.status === "running") ??
    statuses.find(([, status]) => status.status === "queued") ??
    statuses[0];
  const logs = statuses.flatMap(([machineId, status]) =>
    status.logs.map((line) => `${machineName(machineId, machines)}: ${line}`)
  );

  return (
    <div className="sampling-progress-panel">
      <div className="sampling-progress-header">
        <span>{state.samplingBuild.running ? "Rebuild in progress" : "Last rebuild"}</span>
        <strong>{Math.round(average * 100)}%</strong>
      </div>
      <div className="progress-track" aria-label="sampling rebuild progress">
        <div className="progress-fill" style={{ width: `${average * 100}%` }} />
      </div>
      {current && (
        <div className="sampling-current">
          {machineName(current[0], machines)} · {current[1].step}
        </div>
      )}
      {logs.length > 0 && <pre className="log-box compact-log">{logs.slice(-40).join("\n")}</pre>}
    </div>
  );
}

function machineName(machineId: string, machines: Record<string, MachineState>): string {
  return machines[machineId]?.config.name ?? machineId;
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

function formatLocalDateTime(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return [
    date.getFullYear(),
    "-",
    pad(date.getMonth() + 1),
    "-",
    pad(date.getDate()),
    "T",
    pad(date.getHours()),
    ":",
    pad(date.getMinutes()),
    ":",
    pad(date.getSeconds())
  ].join("");
}
