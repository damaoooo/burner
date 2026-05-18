import { useCallback } from "react";
import ExpressionInput from "./ExpressionInput";
import WaveformEditor from "./WaveformEditor";
import WaveformSelector from "./WaveformSelector";
import { useAppState } from "../state/AppState";
import type { Point, WaveformInfo } from "../types";

interface Props {
  onToast: (message: string, kind?: "info" | "error" | "success") => void;
}

export default function BurnPanel({ onToast }: Props) {
  const { state, dispatch } = useAppState();

  const handleGeneratedPoints = useCallback(
    (points: Point[]) => {
      dispatch({ type: "setGlobalWaveform", points });
    },
    [dispatch]
  );

  function handleGlobalSelect(name: string, waveform: WaveformInfo) {
    dispatch({ type: "setGlobalWaveform", points: waveform.points, name });
  }

  return (
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
            {state.runMode === "realtime" ? "Realtime start uses a barrier and near-future UTC timestamp." : "Schedule mode"}
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
          <div className="caution-box">
            <strong>Shaheen CPU-only</strong>
            <span>GPU burn controls are disabled. Worker polling is fixed when the SLURM allocation is submitted.</span>
          </div>
        </div>
      </div>
    </section>
  );
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
