import { useEffect, useMemo, useRef, useState } from "react";
import {
  CategoryScale,
  Chart as ChartJS,
  LinearScale,
  LineElement,
  PointElement,
  Tooltip,
  type ChartData,
  type ChartOptions
} from "chart.js";
import dragDataPlugin from "chartjs-plugin-dragdata";
import { Line } from "react-chartjs-2";
import { extractErrorMessage, saveWaveform } from "../api/client";
import { normalizePoints } from "../state/AppState";
import type { Point } from "../types";

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Tooltip, dragDataPlugin);

interface Props {
  points: Point[];
  onChange: (points: Point[], name?: string) => void;
  onSaved: (name: string, points: Point[]) => void;
  onToast: (message: string, kind?: "info" | "error" | "success") => void;
}

interface MenuState {
  x: number;
  y: number;
  index: number;
}

const EPS = 0.0001;

export default function WaveformEditor({ points, onChange, onSaved, onToast }: Props) {
  const chartRef = useRef<ChartJS<"line", Point[], unknown> | null>(null);
  const [menu, setMenu] = useState<MenuState | null>(null);
  const [saveOpen, setSaveOpen] = useState(false);
  const [saveName, setSaveName] = useState("");
  const chartTheme = useChartTheme();

  const data = useMemo<ChartData<"line", Point[]>>(
    () => ({
      datasets: [
        {
          label: "Load",
          data: points,
          borderColor: chartTheme.line,
          backgroundColor: chartTheme.line,
          pointBackgroundColor: chartTheme.point,
          pointBorderColor: chartTheme.pointBorder,
          pointBorderWidth: 1.5,
          pointRadius: 4,
          pointHoverRadius: 6,
          borderWidth: 2,
          tension: 0,
          fill: false
        }
      ]
    }),
    [chartTheme, points]
  );

  const options = useMemo<ChartOptions<"line">>(
    () =>
      ({
        responsive: true,
        maintainAspectRatio: false,
        parsing: false,
        animation: false,
        normalized: true,
        scales: {
          x: {
            type: "linear",
            min: 0,
            max: 1,
            border: { color: chartTheme.axis },
            grid: { color: chartTheme.grid },
            title: {
              display: true,
              text: "Cycle Position (0-1)",
              color: chartTheme.title,
              font: { size: 12, weight: 600 },
              padding: { top: 8 }
            },
            ticks: { color: chartTheme.tick, stepSize: 0.25 }
          },
          y: {
            type: "linear",
            min: 0,
            max: 1,
            border: { color: chartTheme.axis },
            grid: { color: chartTheme.grid },
            title: {
              display: true,
              text: "Load (0-1, 1.0 = 100%)",
              color: chartTheme.title,
              font: { size: 12, weight: 600 },
              padding: { bottom: 8 }
            },
            ticks: { color: chartTheme.tick, stepSize: 0.25 }
          }
        },
        plugins: {
          tooltip: {
            backgroundColor: chartTheme.tooltipBg,
            bodyColor: chartTheme.tooltipText,
            borderColor: chartTheme.tooltipBorder,
            borderWidth: 1,
            titleColor: chartTheme.tooltipText,
            callbacks: {
              label: (context) => {
                const point = context.raw as Point;
                return `Cycle Position ${point.x.toFixed(3)}, Load ${point.y.toFixed(3)} (${Math.round(point.y * 100)}%)`;
              }
            }
          },
          dragData: {
            round: 4,
            showTooltip: true,
            dragX: true,
            onDragStart: () => true,
            onDrag: (_event: unknown, _datasetIndex: number, index: number, value: Point) => {
              const next = [...points];
              const left = index === 0 ? 0 : next[index - 1].x + EPS;
              const right = index === next.length - 1 ? 1 : next[index + 1].x - EPS;
              value.x = index === 0 ? 0 : index === next.length - 1 ? 1 : clamp(value.x, left, right);
              value.y = clamp(value.y, 0, 1);
              return true;
            },
            onDragEnd: (_event: unknown, _datasetIndex: number, index: number, value: Point) => {
              const next = [...points];
              const left = index === 0 ? 0 : next[index - 1].x + EPS;
              const right = index === next.length - 1 ? 1 : next[index + 1].x - EPS;
              next[index] = {
                x: index === 0 ? 0 : index === next.length - 1 ? 1 : clamp(value.x, left, right),
                y: clamp(value.y, 0, 1)
              };
              onChange(normalizePoints(next));
            }
          }
        },
        onClick: (event) => {
          const chart = chartRef.current;
          if (!chart || !event.native) {
            return;
          }
          const elements = chart.getElementsAtEventForMode(
            event.native,
            "nearest",
            { intersect: true },
            false
          );
          if (elements.length > 0) {
            return;
          }
          const rect = chart.canvas.getBoundingClientRect();
          const nativeEvent = event.native as MouseEvent;
          const x = chart.scales.x.getValueForPixel(nativeEvent.clientX - rect.left);
          const y = chart.scales.y.getValueForPixel(nativeEvent.clientY - rect.top);
          if (typeof x !== "number" || typeof y !== "number") {
            return;
          }
          const next = normalizePoints([...points, { x: clamp(x, 0, 1), y: clamp(y, 0, 1) }]);
          onChange(next);
        }
      }) as ChartOptions<"line">,
    [chartTheme, onChange, points]
  );

  function handleContextMenu(event: React.MouseEvent<HTMLDivElement>) {
    event.preventDefault();
    const chart = chartRef.current;
    if (!chart) {
      return;
    }
    const elements = chart.getElementsAtEventForMode(
      event.nativeEvent,
      "nearest",
      { intersect: true },
      false
    );
    if (elements.length === 0) {
      setMenu(null);
      return;
    }
    const index = elements[0].index;
    if (index === 0 || index === points.length - 1) {
      setMenu(null);
      return;
    }
    const rect = event.currentTarget.getBoundingClientRect();
    setMenu({
      x: event.clientX - rect.left,
      y: event.clientY - rect.top,
      index
    });
  }

  function deletePoint(index: number) {
    onChange(points.filter((_, pointIndex) => pointIndex !== index));
    setMenu(null);
  }

  async function handleSave() {
    const name = saveName.trim();
    if (!name) {
      onToast("Enter a waveform name.", "error");
      return;
    }
    try {
      const saved = await saveWaveform(name, points);
      onSaved(saved.name, saved.points);
      setSaveOpen(false);
      setSaveName("");
      onToast("Waveform saved.", "success");
    } catch (error) {
      onToast(extractErrorMessage(error), "error");
    }
  }

  return (
    <div className="waveform-editor">
      <div className="waveform-toolbar">
        <button type="button" className="secondary-button" onClick={() => setSaveOpen(true)}>
          Save CSV
        </button>
        <span className="muted">{points.length} points</span>
      </div>
      <div className="chart-shell" onContextMenu={handleContextMenu} onClick={() => setMenu(null)}>
        <Line<Point[], unknown>
          ref={(chart) => {
            chartRef.current = chart ?? null;
          }}
          data={data}
          options={options}
        />
        {menu && (
          <button
            type="button"
            className="context-menu"
            style={{ left: menu.x, top: menu.y }}
            onClick={(event) => {
              event.stopPropagation();
              deletePoint(menu.index);
            }}
          >
            Delete point
          </button>
        )}
      </div>
      {saveOpen && (
        <div className="modal-backdrop" role="dialog" aria-modal="true">
          <div className="modal">
            <h3>Save Waveform</h3>
            <input
              className="field"
              value={saveName}
              onChange={(event) => setSaveName(event.target.value)}
              placeholder="my_wave"
              autoFocus
            />
            <div className="modal-actions">
              <button type="button" className="secondary-button" onClick={() => setSaveOpen(false)}>
                Cancel
              </button>
              <button type="button" className="primary-button" onClick={() => void handleSave()}>
                Save
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

interface ChartTheme {
  axis: string;
  grid: string;
  line: string;
  point: string;
  pointBorder: string;
  tick: string;
  title: string;
  tooltipBg: string;
  tooltipBorder: string;
  tooltipText: string;
}

function useChartTheme(): ChartTheme {
  const [theme, setTheme] = useState<ChartTheme>(() => readChartTheme());

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const update = () => setTheme(readChartTheme());
    update();
    media.addEventListener("change", update);
    window.addEventListener("burner-theme-change", update);
    return () => {
      media.removeEventListener("change", update);
      window.removeEventListener("burner-theme-change", update);
    };
  }, []);

  return theme;
}

function readChartTheme(): ChartTheme {
  return {
    axis: cssVar("--chart-axis", "#aab3c2"),
    grid: cssVar("--chart-grid", "#d8dee8"),
    line: cssVar("--chart-line", "#2563eb"),
    point: cssVar("--chart-point", "#111827"),
    pointBorder: cssVar("--chart-point-border", "#ffffff"),
    tick: cssVar("--chart-tick", "#64748b"),
    title: cssVar("--chart-title", "#1f2937"),
    tooltipBg: cssVar("--chart-tooltip-bg", "#111827"),
    tooltipBorder: cssVar("--chart-tooltip-border", "#2563eb"),
    tooltipText: cssVar("--chart-tooltip-text", "#ffffff")
  };
}

function cssVar(name: string, fallback: string): string {
  if (typeof document === "undefined") {
    return fallback;
  }
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}
