import { useEffect, useState } from "react";
import { connectMachine, disconnectMachine, extractErrorMessage, refreshHwInfo } from "../api/client";
import { useAppState } from "../state/AppState";
import type { MachineState, WaveformInfo } from "../types";
import UpdatePanel from "./UpdatePanel";
import WaveformSelector from "./WaveformSelector";

interface Props {
  machine: MachineState;
  onToast: (message: string, kind?: "info" | "error" | "success") => void;
}

export default function MachineCard({ machine, onToast }: Props) {
  const { state, dispatch } = useAppState();
  const [showUpdate, setShowUpdate] = useState(false);
  const [showError, setShowError] = useState(false);
  const machineJobs = Object.values(state.burnJobs).filter((job) => job.machine_id === machine.config.id);
  const isBurning = machineJobs.length > 0;
  const connected = machine.connectionStatus === "connected";
  const samplingRunning = state.samplingBuild.running;

  useEffect(() => {
    if (machine.connectionStatus === "error" && machine.errorMessage) {
      setShowError(true);
    }
  }, [machine.connectionStatus, machine.errorMessage]);

  async function handleConnect() {
    try {
      await connectMachine(machine.config.id);
    } catch (error) {
      onToast(extractErrorMessage(error), "error");
    }
  }

  async function handleDisconnect() {
    try {
      await disconnectMachine(machine.config.id);
    } catch (error) {
      onToast(extractErrorMessage(error), "error");
    }
  }

  async function handleRefreshHw() {
    try {
      await refreshHwInfo(machine.config.id);
    } catch (error) {
      onToast(extractErrorMessage(error), "error");
    }
  }

  function handleWaveformSelect(name: string, waveform: WaveformInfo) {
    dispatch({
      type: "setPerMachineWaveform",
      machineId: machine.config.id,
      points: waveform.points,
      name
    });
  }

  return (
    <article className={`machine-card ${isBurning ? "burning" : ""}`}>
      <div className="machine-header">
        <div>
          <div className="machine-title-row">
            <span className={`status-dot ${machine.connectionStatus}`} />
            <h3>{machine.config.name}</h3>
          </div>
          <div className="machine-subtitle">
            {machine.config.host}:{machine.config.port}
          </div>
        </div>
        <label className="enable-switch">
          <input
            type="checkbox"
            disabled={samplingRunning}
            checked={machine.burnEnabled}
            onChange={(event) =>
              dispatch({
                type: "setMachineOption",
                machineId: machine.config.id,
                key: "burnEnabled",
                value: event.target.checked
              })
            }
          />
          <span>Enabled</span>
        </label>
      </div>

      <div className="button-row">
        {connected ? (
          <button
            type="button"
            className="secondary-button"
            disabled={samplingRunning}
            onClick={() => void handleDisconnect()}
          >
            Disconnect
          </button>
        ) : (
          <button
            type="button"
            className="primary-button"
            disabled={machine.connectionStatus === "connecting" || samplingRunning}
            onClick={() => void handleConnect()}
          >
            {machine.connectionStatus === "connecting" ? "Connecting" : "Connect"}
          </button>
        )}
        <button
          type="button"
          className="secondary-button"
          disabled={!connected || samplingRunning}
          onClick={() => void handleRefreshHw()}
        >
          Refresh Hardware
        </button>
      </div>

      <div className="hardware-block">
        <div className="hardware-row">
          <span className="hardware-label">CPU</span>
          <span>{machine.hwInfo?.cpu_model || "Unknown"}</span>
        </div>
        <div className="hardware-row">
          <span className="hardware-label">CPU TDP</span>
          <span>{machine.hwInfo?.cpu_tdp ?? machine.config.cpu_tdp} W</span>
        </div>
        <div className="hardware-row">
          <span className="hardware-label">GPU TDP</span>
          <span>{machine.hwInfo?.gpu_tdp ?? machine.config.gpu_tdp} W / GPU</span>
        </div>
        <div className="gpu-list">
          {(machine.hwInfo?.gpus ?? []).length > 0 ? (
            machine.hwInfo?.gpus.map((gpu) => (
              <div className="hardware-row" key={gpu.index}>
                <span className="hardware-label">GPU {gpu.index}</span>
                <span>
                  {gpu.name} · {gpu.tdp_watts} W
                </span>
              </div>
            ))
          ) : (
            <div className="hardware-row">
              <span className="hardware-label">GPU</span>
              <span>No GPU detected</span>
            </div>
          )}
        </div>
      </div>

      <div className="machine-options">
        <label className="toggle-row">
          <input
            type="checkbox"
            disabled={samplingRunning}
            checked={machine.burnCpu}
            onChange={(event) =>
              dispatch({
                type: "setMachineOption",
                machineId: machine.config.id,
                key: "burnCpu",
                value: event.target.checked
              })
            }
          />
          <span>Burn CPU</span>
        </label>
        <label className="toggle-row">
          <input
            type="checkbox"
            disabled={samplingRunning}
            checked={machine.burnGpu}
            onChange={(event) =>
              dispatch({
                type: "setMachineOption",
                machineId: machine.config.id,
                key: "burnGpu",
                value: event.target.checked
              })
            }
          />
          <span>Burn GPU</span>
        </label>
        <label className="delay-field">
          <span>Delay (s)</span>
          <input
            className="field"
            type="number"
            min={0}
            step={0.01}
            disabled={samplingRunning}
            value={machine.delaySeconds}
            onChange={(event) =>
              dispatch({
                type: "setMachineDelay",
                machineId: machine.config.id,
                value: Number(event.target.value)
              })
            }
          />
        </label>
        {state.usePerMachineWaveform && (
          <WaveformSelector
            waveforms={state.waveforms}
            value={state.perMachineWaveformNames[machine.config.id] || state.globalWaveformName}
            onSelect={handleWaveformSelect}
          />
        )}
      </div>

      <div className="machine-footer">
        <button
          type="button"
          className="secondary-button"
          disabled={isBurning || !connected || samplingRunning}
          onClick={() => setShowUpdate((value) => !value)}
        >
          Update
        </button>
        {isBurning && <span className="burn-pill">{machineJobs.length} job{machineJobs.length > 1 ? "s" : ""}</span>}
      </div>

      {showUpdate && (
        <UpdatePanel machineId={machine.config.id} disabled={isBurning || !connected || samplingRunning} onToast={onToast} />
      )}

      {showError && machine.errorMessage && (
        <div className="modal-backdrop" role="dialog" aria-modal="true">
          <div className="modal">
            <h3>Connection Failed</h3>
            <p className="modal-message">{machine.errorMessage}</p>
            <div className="modal-actions">
              <button type="button" className="primary-button" onClick={() => setShowError(false)}>
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </article>
  );
}
