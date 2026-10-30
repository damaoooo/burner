import { useEffect, useMemo, useState } from "react";
import { extractErrorMessage, fetchProfileSummary, fetchWaveforms, importProfiles } from "../api/client";
import { useAppState } from "../state/AppState";
import type { ProfileLibrarySummary, WaveformInfo } from "../types";
import { groupProfiles } from "./WaveformSelector";

interface Props {
  onToast: (message: string, kind?: "info" | "error" | "success") => void;
}

type AssignOrder = "sequential" | "shuffle";

export default function ProfileLibrary({ onToast }: Props) {
  const { state, dispatch } = useAppState();
  const [summary, setSummary] = useState<ProfileLibrarySummary | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [resolutionMs, setResolutionMs] = useState("10");
  const [importing, setImporting] = useState(false);
  const [group, setGroup] = useState("");
  const [order, setOrder] = useState<AssignOrder>("sequential");
  const [startIndex, setStartIndex] = useState("1");
  const [seed, setSeed] = useState("1");

  const groups = useMemo(() => groupProfiles(state.waveforms), [state.waveforms]);
  const profiles = useMemo(
    () => groups.filter(([name]) => !group || name === group).flatMap(([, items]) => items),
    [group, groups]
  );
  const profilesByName = useMemo(
    () => new Map(groups.flatMap(([, items]) => items).map((profile) => [profile.name, profile])),
    [groups]
  );
  const [mappingText, setMappingText] = useState("");
  const enabledMachines = Object.values(state.machines).filter((machine) => machine.burnEnabled);
  const resolution = summary?.resolution_ms;
  const samplingTooSlow = resolution !== undefined && state.appliedSamplingMs > resolution * 2;

  useEffect(() => {
    void fetchProfileSummary()
      .then(setSummary)
      .catch(() => undefined);
  }, []);

  function mappedProfile(machineId: string): string {
    if (!state.usePerMachineWaveform) {
      return "";
    }
    const name = state.perMachineWaveformNames[machineId] ?? "";
    return profilesByName.has(name) ? name : "";
  }

  function assignProfiles(pairs: Array<[string, WaveformInfo]>) {
    dispatch({ type: "setUsePerMachineWaveform", value: true });
    for (const [machineId, profile] of pairs) {
      dispatch({ type: "setPerMachineWaveform", machineId, points: profile.points, name: profile.name });
    }
    const period = pairs[0]?.[1].periodSeconds;
    if (period) {
      dispatch({ type: "setBurnParams", period: String(period) });
    }
  }

  function assignOne(machineId: string, name: string) {
    const profile = profilesByName.get(name);
    if (profile) {
      assignProfiles([[machineId, profile]]);
      return;
    }
    dispatch({
      type: "setPerMachineWaveform",
      machineId,
      points: state.globalWaveform,
      name: state.globalWaveformName
    });
  }

  function applyMappingText() {
    const machinesByKey = new Map<string, string>();
    for (const machine of Object.values(state.machines)) {
      machinesByKey.set(machine.config.id, machine.config.id);
      machinesByKey.set(machine.config.name, machine.config.id);
    }
    const pairs: Array<[string, WaveformInfo]> = [];
    const errors: string[] = [];
    mappingText.split(/\r?\n/).forEach((line, index) => {
      const text = line.replace(/#.*/, "").trim();
      if (!text) {
        return;
      }
      const parts = text.split(/[\s,;:=\t]+/).filter(Boolean);
      const machineId = machinesByKey.get(parts[0]);
      const profile = profilesByName.get(parts[1] ?? "");
      if (parts.length !== 2 || !machineId || !profile) {
        errors.push(`line ${index + 1}: ${!machineId ? "unknown machine" : "unknown profile"} "${text}"`);
        return;
      }
      pairs.push([machineId, profile]);
    });
    if (errors.length > 0) {
      onToast(errors.slice(0, 3).join("; ") + (errors.length > 3 ? ` (+${errors.length - 3} more)` : ""), "error");
      return;
    }
    if (pairs.length === 0) {
      onToast("Mapping is empty.", "error");
      return;
    }
    assignProfiles(pairs);
    const disabled = pairs.filter(([machineId]) => !state.machines[machineId]?.burnEnabled).length;
    onToast(
      `Mapped ${pairs.length} machines.${disabled > 0 ? ` ${disabled} of them are not enabled for burn.` : ""}`,
      disabled > 0 ? "info" : "success"
    );
  }

  function exportMappingText() {
    const lines = enabledMachines
      .map((machine) => [machine.config.name, mappedProfile(machine.config.id)])
      .filter(([, profile]) => profile)
      .map(([name, profile]) => `${name} ${profile}`);
    setMappingText(lines.join("\n"));
    if (lines.length === 0) {
      onToast("No enabled machine has a profile yet.", "info");
    }
  }

  async function handleImport() {
    const resolutionValue = Number(resolutionMs);
    if (!file) {
      onToast("Choose a profile CSV first.", "error");
      return;
    }
    if (!Number.isInteger(resolutionValue) || resolutionValue < 1 || resolutionValue > 1000) {
      onToast("Resolution must be an integer from 1 to 1000 ms.", "error");
      return;
    }
    setImporting(true);
    try {
      const imported = await importProfiles(file, resolutionValue);
      setSummary(imported);
      dispatch({ type: "setWaveforms", waveforms: await fetchWaveforms() });
      onToast(`Imported ${imported.count} profiles.`, "success");
    } catch (error) {
      onToast(extractErrorMessage(error), "error");
    } finally {
      setImporting(false);
    }
  }

  function handleAssign() {
    if (profiles.length === 0) {
      onToast("Import profiles first.", "error");
      return;
    }
    if (enabledMachines.length === 0) {
      onToast("Enable at least one machine.", "error");
      return;
    }
    const start = Number(startIndex);
    if (!Number.isInteger(start) || start < 1 || start > profiles.length) {
      onToast(`Start index must be from 1 to ${profiles.length}.`, "error");
      return;
    }
    const seedValue = Number(seed);
    if (order === "shuffle" && !Number.isInteger(seedValue)) {
      onToast("Seed must be an integer.", "error");
      return;
    }

    const ordered = order === "shuffle" ? shuffle(profiles, seedValue) : profiles;
    assignProfiles(
      enabledMachines.map((machine, index): [string, WaveformInfo] => [
        machine.config.id,
        ordered[(start - 1 + index) % ordered.length]
      ])
    );
    const wrapped = enabledMachines.length > ordered.length ? " Some profiles repeat." : "";
    onToast(`Assigned profiles to ${enabledMachines.length} machines.${wrapped}`, "success");
  }

  return (
    <div className="profile-library">
      <div className="profile-library-head">
        <h3>Power Profiles</h3>
        <span className="profile-library-summary">
          {summary?.imported
            ? `${summary.count} profiles (${Object.entries(summary.groups ?? {})
                .map(([name, count]) => `${name || "other"} ${count}`)
                .join(", ")}) · period ${summary.period_seconds} s · ${summary.resolution_ms} ms · ${summary.source}`
            : "No profiles imported"}
        </span>
      </div>
      <p className="profile-library-note">
        Each profile is scaled to its own peak: the peak is 100% burn.
      </p>

      <div className="profile-library-row">
        <input
          className="field"
          type="file"
          accept=".csv,text/csv"
          disabled={importing}
          onChange={(event) => setFile(event.target.files?.[0] ?? null)}
        />
        <label className="profile-library-inline">
          <span>Resolution (ms)</span>
          <input
            className="field"
            type="number"
            min={1}
            max={1000}
            step={1}
            disabled={importing}
            value={resolutionMs}
            onChange={(event) => setResolutionMs(event.target.value)}
          />
        </label>
        <button
          type="button"
          className="secondary-button"
          disabled={importing || !file}
          onClick={() => void handleImport()}
        >
          {importing ? "Importing" : "Import CSV"}
        </button>
      </div>

      <div className="profile-library-row">
        <label className="profile-library-inline">
          <span>Group</span>
          <select className="field" value={group} onChange={(event) => setGroup(event.target.value)}>
            <option value="">All</option>
            {groups.map(([name, items]) => (
              <option key={name} value={name}>
                {name || "other"} ({items.length})
              </option>
            ))}
          </select>
        </label>
        <label className="profile-library-inline">
          <span>Order</span>
          <select className="field" value={order} onChange={(event) => setOrder(event.target.value as AssignOrder)}>
            <option value="sequential">Sequential</option>
            <option value="shuffle">Shuffle</option>
          </select>
        </label>
        <label className="profile-library-inline">
          <span>Start #</span>
          <input
            className="field"
            type="number"
            min={1}
            step={1}
            value={startIndex}
            onChange={(event) => setStartIndex(event.target.value)}
          />
        </label>
        {order === "shuffle" && (
          <label className="profile-library-inline">
            <span>Seed</span>
            <input
              className="field"
              type="number"
              step={1}
              value={seed}
              onChange={(event) => setSeed(event.target.value)}
            />
          </label>
        )}
        <button
          type="button"
          className="secondary-button"
          disabled={profiles.length === 0}
          onClick={handleAssign}
        >
          Assign to {enabledMachines.length} enabled machines
        </button>
      </div>

      <div className="profile-map">
        <div className="profile-map-head">
          <strong>Machine → Profile</strong>
          <span className="muted">Pick a column per machine, or paste one "machine profile" pair per line.</span>
        </div>
        {enabledMachines.length === 0 ? (
          <p className="muted">Enable machines on the Machines tab to map them.</p>
        ) : (
          <div className="profile-map-table">
            {enabledMachines.map((machine) => (
              <label className="profile-map-row" key={machine.config.id}>
                <span className="profile-map-name">{machine.config.name}</span>
                <select
                  className="field"
                  value={mappedProfile(machine.config.id)}
                  onChange={(event) => assignOne(machine.config.id, event.target.value)}
                >
                  <option value="">(global waveform)</option>
                  {groups.map(([name, items]) => (
                    <optgroup key={name} label={name || "other"}>
                      {items.map((profile) => (
                        <option key={profile.name} value={profile.name}>
                          {profile.name}
                        </option>
                      ))}
                    </optgroup>
                  ))}
                </select>
              </label>
            ))}
          </div>
        )}
        <textarea
          className="field profile-map-text"
          rows={4}
          spellCheck={false}
          placeholder={"node-15 cl003\nnode-16 bg042"}
          value={mappingText}
          onChange={(event) => setMappingText(event.target.value)}
        />
        <div className="profile-library-row">
          <button type="button" className="secondary-button" onClick={applyMappingText}>
            Apply text
          </button>
          <button type="button" className="secondary-button" onClick={exportMappingText}>
            Show current mapping
          </button>
        </div>
      </div>

      {samplingTooSlow && (
        <p className="profile-library-warning">
          Applied sampling time is {state.appliedSamplingMs} ms. Apply {resolution! * 2} ms or less so short
          profile steps are not smoothed out.
        </p>
      )}
    </div>
  );
}

function shuffle<T>(items: T[], seed: number): T[] {
  const result = [...items];
  let value = seed >>> 0;
  const random = () => {
    // mulberry32
    value = (value + 0x6d2b79f5) >>> 0;
    let t = value;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  for (let index = result.length - 1; index > 0; index -= 1) {
    const swap = Math.floor(random() * (index + 1));
    [result[index], result[swap]] = [result[swap], result[index]];
  }
  return result;
}
