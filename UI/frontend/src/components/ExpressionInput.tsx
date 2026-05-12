import { useEffect, useMemo, useState } from "react";
import { evaluate } from "mathjs";
import type { Point } from "../types";

interface Props {
  onPointsGenerated: (points: Point[]) => void;
}

const FUNCTIONS = ["sin", "cos", "tan", "sqrt", "log", "abs", "exp"];

export default function ExpressionInput({ onPointsGenerated }: Props) {
  const [expression, setExpression] = useState("");
  const [error, setError] = useState("");
  const [warning, setWarning] = useState("");

  const normalized = useMemo(() => preprocessExpression(expression), [expression]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      if (!expression.trim()) {
        setError("");
        setWarning("");
        return;
      }
      try {
        const points = sampleExpression(normalized);
        const hasOutOfRange = points.some((point) => point.y < 0 || point.y > 1);
        setError("");
        setWarning(hasOutOfRange ? "Some values are outside [0,1]; burner will clamp them." : "");
        onPointsGenerated(
          points.map((point) => ({
            x: point.x,
            y: Math.max(0, Math.min(1, point.y))
          }))
        );
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
        setWarning("");
      }
    }, 500);

    return () => window.clearTimeout(timer);
  }, [expression, normalized, onPointsGenerated]);

  return (
    <div className="expression-block">
      <label className="label" htmlFor="expression-input">
        y = f(x)
      </label>
      <input
        id="expression-input"
        className={`field expression-input ${error ? "field-error" : ""}`}
        value={expression}
        onChange={(event) => setExpression(event.target.value)}
        placeholder="sin(2*pi*x)*0.5+0.5"
        spellCheck={false}
      />
      {error && <div className="inline-error">{error}</div>}
      {warning && <div className="inline-warning">{warning}</div>}
    </div>
  );
}

function preprocessExpression(input: string): string {
  let output = input.trim();
  output = output.replace(/(\d)([A-Za-z])/g, "$1*$2");
  output = output.replace(/([A-Za-z])(\d)/g, "$1$2");
  for (const name of FUNCTIONS) {
    const pattern = new RegExp(`\\b${name}((?:[0-9.]+\\*)?x|[0-9.]+(?:\\*x)?)\\b`, "g");
    output = output.replace(pattern, `${name}($1)`);
  }
  return output;
}

function sampleExpression(expression: string): Point[] {
  const points: Point[] = [];
  for (let index = 0; index < 64; index += 1) {
    const x = index / 63;
    const value = evaluate(expression, { x, pi: Math.PI });
    const y = typeof value === "number" ? value : Number(value);
    if (!Number.isFinite(y)) {
      throw new Error("Expression result is not a finite number.");
    }
    points.push({ x, y });
  }
  return points;
}
