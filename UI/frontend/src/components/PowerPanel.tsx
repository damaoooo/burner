import { useEffect, useMemo, useRef, useState } from "react";
import {
  Chart as ChartJS,
  Decimation,
  Legend,
  LinearScale,
  LineElement,
  PointElement,
  Tooltip,
  type ChartData,
  type ChartOptions
} from "chart.js";
import { Line } from "react-chartjs-2";
import {
  extractErrorMessage,
  fetchPowerHistory,
  POWER_CSV_URL,
  startPowerMonitor,
  stopPowerMonitor
} from "../api/client";
import { useAppState } from "../state/AppState";
import type { PowerSample, PowerStatus, WsEvent } from "../types";
import { useChartTheme } from "./WaveformEditor";

ChartJS.register(LinearScale, PointElement, LineElement, Tooltip, Decimation, Legend);

export const POWER_EVENT = "burner-power";

const KEEP_SECONDS = 1800;
const WINDOWS = [60, 180, 600, 1800];
const INTERVALS = [50, 100, 250, 1000];
const SHOWN_KEY = "burner-power-nodes";
// Fixed categorical order (light / dark). A machine keeps its color and dash
// whatever else is ticked; past 8 machines the dash pattern tells them apart.
const NODE_COLORS = {
  light: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"],
  dark: ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]
};
const NODE_DASHES: number[][] = [[], [6, 4], [2, 3]];

function nodeStyle(index: number, dark: boolean) {
  const palette = dark ? NODE_COLORS.dark : NODE_COLORS.light;
  return {
    color: palette[index % palette.length],
    dash: NODE_DASHES[Math.floor(index / palette.length) % NODE_DASHES.length]
  };
}

function readShown(): string[] {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(SHOWN_KEY) ?? "[]");
    return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === "string") : [];
  } catch {
    return [];
  }
}

function isDarkTheme(): boolean {
  const theme = document.documentElement.dataset.theme;
  if (theme === "dark" || theme === "light") {
    return theme === "dark";
  }
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

interface Props {
  onToast: (message: string, kind?: "info" | "error" | "success") => void;
  visible: boolean;
}

type PowerWsEvent = Extract<WsEvent, { event: "power_samples" | "power_status" }>;

export default function PowerPanel({ onToast, visible }: Props) {
  const { state } = useAppState();
  const chartTheme = useChartTheme();
  const samples = useRef<PowerSample[]>([]);
  const dirty = useRef(false);
  // The window listener below is registered once, so it reads this ref
  // rather than the (stale) status state.
  const wasRunning = useRef(false);
  const [status, setStatus] = useState<PowerStatus | null>(null);
  const [latest, setLatest] = useState<Record<string, number>>({});
  const [version, setVersion] = useState(0);
  const [windowSeconds, setWindowSeconds] = useState(180);
  const [intervalMs, setIntervalMs] = useState(100);
  const [fromZero, setFromZero] = useState(false);
  const [busy, setBusy] = useState(false);
  const [shown, setShown] = useState<string[]>(() => readShown());

  useEffect(() => {
    try {
      window.localStorage.setItem(SHOWN_KEY, JSON.stringify(shown));
    } catch {
      // Remembering the selection is a convenience only.
    }
  }, [shown]);

  useEffect(() => {
    void fetchPowerHistory(KEEP_SECONDS)
      .then((history) => {
        samples.current = history.samples;
        wasRunning.current = history.status.running;
        setStatus(history.status);
        setLatest(history.status.latest);
        setVersion((value) => value + 1);
      })
      .catch(() => undefined);

    function onPower(event: Event) {
      const payload = (event as CustomEvent<PowerWsEvent>).detail;
      if (payload.event === "power_samples") {
        const merged = samples.current.concat(payload.samples);
        const cutoff = merged[merged.length - 1][0] - KEEP_SECONDS;
        samples.current = merged[0][0] < cutoff ? merged.filter((row) => row[0] >= cutoff) : merged;
        setLatest(payload.latest);
        dirty.current = true;
      } else {
        const { event: _event, ...next } = payload;
        if (next.running && !wasRunning.current) {
          samples.current = [];
        }
        wasRunning.current = next.running;
        setStatus(next);
        setLatest(next.latest);
      }
    }
    window.addEventListener(POWER_EVENT, onPower);
    return () => window.removeEventListener(POWER_EVENT, onPower);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Redraw at most 4 times a second, and only while the tab is shown.
  useEffect(() => {
    if (!visible) {
      return undefined;
    }
    setVersion((value) => value + 1);
    const timer = window.setInterval(() => {
      if (dirty.current) {
        dirty.current = false;
        setVersion((value) => value + 1);
      }
    }, 250);
    return () => window.clearInterval(timer);
  }, [visible]);

  const machineCount = status?.machines.length ?? 0;
  const view = useMemo(() => {
    const all = samples.current;
    const end = all.length > 0 ? all[all.length - 1][0] : 0;
    const shown = all.filter((row) => row[0] >= end - windowSeconds);
    const complete = shown.filter((row) => machineCount === 0 || row[2] === machineCount);
    const watts = complete.map((row) => row[1]);
    const lastSecond = complete.filter((row) => row[0] >= end - 1).map((row) => row[1]);
    return {
      rows: shown,
      complete,
      incomplete: shown.length - complete.length,
      now: average(lastSecond),
      mean: average(watts),
      min: watts.length ? Math.min(...watts) : undefined,
      max: watts.length ? Math.max(...watts) : undefined
    };
    // version changes whenever samples.current has new rows
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version, windowSeconds, machineCount]);

  const data = useMemo<ChartData<"line", { x: number; y: number }[]>>(
    () => ({
      datasets: [
        {
          label: "Total CPU power",
          data: view.complete.map((row) => ({ x: row[0], y: row[1] })),
          borderColor: chartTheme.line,
          backgroundColor: chartTheme.line,
          borderWidth: 1.5,
          pointRadius: 0,
          tension: 0
        }
      ]
    }),
    [chartTheme, view]
  );

  const options = useMemo(
    () => chartOptions(chartTheme, fromZero, "Total CPU package power (W)", false),
    [chartTheme, fromZero]
  );

  const machineIds = useMemo(() => status?.machines ?? [], [status]);
  const nodeData = useMemo<ChartData<"line", { x: number; y: number }[]>>(() => {
    const dark = isDarkTheme();
    return {
      datasets: machineIds
        .map((id, index) => ({ id, index }))
        .filter(({ id }) => shown.includes(id))
        .map(({ id, index }) => {
          const style = nodeStyle(index, dark);
          const points: { x: number; y: number }[] = [];
          for (const row of view.rows) {
            const value = row[3]?.[index];
            if (value !== null && value !== undefined) {
              points.push({ x: row[0], y: value });
            }
          }
          return {
            label: state.machines[id]?.config.name ?? id,
            data: points,
            borderColor: style.color,
            backgroundColor: style.color,
            borderDash: style.dash,
            borderWidth: 1.5,
            pointRadius: 0,
            tension: 0
          };
        })
    };
    // chartTheme changes with the page theme, which changes the palette
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, shown, machineIds, state.machines, chartTheme]);

  const nodeOptions = useMemo(
    () => chartOptions(chartTheme, fromZero, "Per-machine CPU package power (W)", true),
    [chartTheme, fromZero]
  );

  function toggleNode(id: string) {
    setShown((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]));
  }

  async function handleStart() {
    setBusy(true);
    try {
      samples.current = [];
      const next = await startPowerMonitor(intervalMs);
      wasRunning.current = true;
      setStatus(next);
      setVersion((value) => value + 1);
      onToast(`Power monitor started on ${next.machines.length} machines.`, "success");
    } catch (error) {
      onToast(extractErrorMessage(error), "error");
    } finally {
      setBusy(false);
    }
  }

  async function handleStop() {
    setBusy(true);
    try {
      const next = await stopPowerMonitor();
      wasRunning.current = false;
      setStatus(next);
    } catch (error) {
      onToast(extractErrorMessage(error), "error");
    } finally {
      setBusy(false);
    }
  }

  const running = status?.running ?? false;
  const connected = Object.values(state.machines).filter((machine) => machine.connectionStatus === "connected").length;
  const errors = Object.entries(status?.errors ?? {});
  const dark = isDarkTheme();
  const nodeRows = machineIds.map((id, index) => ({
    id,
    name: state.machines[id]?.config.name ?? id,
    watts: latest[id],
    style: nodeStyle(index, dark)
  }));

  return (
    <section className="workload-panel power-panel">
      <div className="section-heading">
        <div>
          <h2>Power</h2>
          <span className="muted">
            Live sum of CPU package power (RAPL) across machines. Excludes memory, fans and PSU losses.
          </span>
        </div>
        <div className="workload-actions">
          <label className="compact-number">
            <span>Sample</span>
            <select
              className="field"
              value={intervalMs}
              disabled={running || busy}
              onChange={(event) => setIntervalMs(Number(event.target.value))}
            >
              {INTERVALS.map((value) => (
                <option key={value} value={value}>
                  {value} ms
                </option>
              ))}
            </select>
          </label>
          {running ? (
            <button type="button" className="danger-button" disabled={busy} onClick={() => void handleStop()}>
              Stop Monitor
            </button>
          ) : (
            <button
              type="button"
              className="primary-button"
              disabled={busy || connected === 0}
              title={connected === 0 ? "Connect machines first" : undefined}
              onClick={() => void handleStart()}
            >
              Start Monitor
            </button>
          )}
          <a className="secondary-button power-download" href={POWER_CSV_URL} download>
            Download CSV
          </a>
        </div>
      </div>

      <div className="power-stats">
        <Stat label="Now (1 s avg)" value={view.now} strong />
        <Stat label={`Mean (${formatWindow(windowSeconds)})`} value={view.mean} />
        <Stat label="Min" value={view.min} />
        <Stat label="Max" value={view.max} />
        <div className="power-stat">
          <span>Machines</span>
          <strong>{machineCount}</strong>
          <small>{running ? `sampling every ${Math.round((status?.interval ?? 0) * 1000)} ms` : "stopped"}</small>
        </div>
      </div>

      <div className="power-controls">
        <div className="power-window" role="group" aria-label="time window">
          {WINDOWS.map((value) => (
            <button
              key={value}
              type="button"
              className={value === windowSeconds ? "tab-button selected" : "tab-button"}
              onClick={() => setWindowSeconds(value)}
            >
              {formatWindow(value)}
            </button>
          ))}
        </div>
        <label className="toggle-row">
          <input type="checkbox" checked={fromZero} onChange={(event) => setFromZero(event.target.checked)} />
          <span>Y axis from 0</span>
        </label>
        {view.incomplete > 0 && (
          <span className="muted">{view.incomplete} samples hidden (not every machine reported)</span>
        )}
      </div>

      <div className="chart-shell power-chart">
        {view.complete.length > 0 ? (
          <Line data={data} options={options} />
        ) : (
          <div className="empty-state">
            {running ? "Waiting for the first samples..." : "Connect machines, then press Start Monitor."}
          </div>
        )}
      </div>

      {errors.length > 0 && (
        <ul className="conflict-list">
          {errors.map(([id, message]) => (
            <li key={id}>
              {state.machines[id]?.config.name ?? id}: {message}
            </li>
          ))}
        </ul>
      )}

      {nodeRows.length > 0 && (
        <>
          <div className="power-controls">
            <strong>Per machine</strong>
            <span className="muted">Tick machines to plot them below.</span>
            <button type="button" className="secondary-button" onClick={() => setShown(machineIds)}>
              All
            </button>
            <button type="button" className="secondary-button" onClick={() => setShown([])}>
              None
            </button>
          </div>
          <div className="power-nodes">
            {nodeRows.map((row) => (
              <label className={shown.includes(row.id) ? "power-node selected" : "power-node"} key={row.id}>
                <input type="checkbox" checked={shown.includes(row.id)} onChange={() => toggleNode(row.id)} />
                <svg className="power-swatch" width="22" height="8" aria-hidden="true">
                  <line
                    x1="1"
                    y1="4"
                    x2="21"
                    y2="4"
                    stroke={row.style.color}
                    strokeWidth="2.5"
                    strokeDasharray={row.style.dash.join(" ")}
                  />
                </svg>
                <span>{row.name}</span>
                <strong>{row.watts === undefined ? "-" : `${Math.round(row.watts)} W`}</strong>
              </label>
            ))}
          </div>
          {nodeData.datasets.length > 0 && (
            <div className="chart-shell power-chart">
              <Line data={nodeData} options={nodeOptions} />
            </div>
          )}
        </>
      )}
    </section>
  );
}

function chartOptions(
  theme: ReturnType<typeof useChartTheme>,
  fromZero: boolean,
  yTitle: string,
  legend: boolean
): ChartOptions<"line"> {
  return {
    animation: false,
    parsing: false,
    normalized: true,
    maintainAspectRatio: false,
    interaction: { mode: "nearest", axis: "x", intersect: false },
    plugins: {
      legend: { display: legend, labels: { color: theme.tick, boxHeight: 2, usePointStyle: false } },
      decimation: { enabled: true, algorithm: "min-max" },
      tooltip: {
        backgroundColor: theme.tooltipBg,
        borderColor: theme.tooltipBorder,
        borderWidth: 1,
        titleColor: theme.tooltipText,
        bodyColor: theme.tooltipText,
        callbacks: {
          title: (items) => (items.length ? formatClock(items[0].parsed.x ?? 0, true) : ""),
          label: (item) => `${item.dataset.label ?? ""}: ${Math.round(item.parsed.y ?? 0)} W`
        }
      }
    },
    scales: {
      x: {
        type: "linear",
        border: { color: theme.axis },
        grid: { color: theme.grid },
        ticks: { color: theme.tick, maxTicksLimit: 8, callback: (value) => formatClock(Number(value)) }
      },
      y: {
        beginAtZero: fromZero,
        border: { color: theme.axis },
        grid: { color: theme.grid },
        title: { display: true, text: yTitle, color: theme.title },
        ticks: { color: theme.tick }
      }
    }
  };
}

function Stat({ label, value, strong }: { label: string; value: number | undefined; strong?: boolean }) {
  return (
    <div className={strong ? "power-stat primary" : "power-stat"}>
      <span>{label}</span>
      <strong>{value === undefined ? "-" : `${Math.round(value)} W`}</strong>
    </div>
  );
}

function average(values: number[]): number | undefined {
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : undefined;
}

function formatWindow(seconds: number): string {
  return seconds < 60 ? `${seconds} s` : `${seconds / 60} min`;
}

function formatClock(epochSeconds: number, withMs = false): string {
  const date = new Date(epochSeconds * 1000);
  const text = date.toLocaleTimeString(undefined, { hour12: false });
  return withMs ? `${text}.${String(date.getMilliseconds()).padStart(3, "0")}` : text;
}
