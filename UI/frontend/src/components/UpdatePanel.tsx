import { extractErrorMessage, runUpdate } from "../api/client";
import { useAppState } from "../state/AppState";

interface Props {
  machineId: string;
  disabled: boolean;
  onToast: (message: string, kind?: "info" | "error" | "success") => void;
}

export default function UpdatePanel({ machineId, disabled, onToast }: Props) {
  const { state, dispatch } = useAppState();
  const logs = state.updateLogs[machineId] ?? [];
  const status = state.updateStatus[machineId] ?? "idle";

  async function handleUpdate() {
    dispatch({ type: "clearUpdateLog", machineId });
    dispatch({ type: "setUpdateRunning", machineId });
    try {
      await runUpdate(machineId);
    } catch (error) {
      dispatch({ type: "setUpdateDone", machineId, exitCode: 1 });
      onToast(extractErrorMessage(error), "error");
    }
  }

  return (
    <div className="update-panel">
      <div className="update-actions">
        <button
          type="button"
          className="secondary-button"
          disabled={disabled || status === "running"}
          onClick={() => void handleUpdate()}
        >
          Check Updates
        </button>
        <span className={`update-status ${status}`}>{renderStatus(status)}</span>
      </div>
      <pre className="log-box">{logs.join("\n")}</pre>
    </div>
  );
}

function renderStatus(status: string): string {
  if (status === "running") {
    return "Running";
  }
  if (status === "success") {
    return "Success";
  }
  if (status === "failed") {
    return "Failed";
  }
  return "Idle";
}
