import { fetchWaveform } from "../api/client";
import type { WaveformInfo } from "../types";

interface Props {
  waveforms: WaveformInfo[];
  value: string;
  onSelect: (name: string, waveform: WaveformInfo) => void;
  disabled?: boolean;
}

export default function WaveformSelector({ waveforms, value, onSelect, disabled }: Props) {
  const fixtures = waveforms.filter((waveform) => waveform.source === "fixtures");
  const custom = waveforms.filter((waveform) => waveform.source === "custom");
  const profileGroups = groupProfiles(waveforms);

  async function handleChange(nextName: string) {
    if (!nextName) {
      return;
    }
    const waveform = await fetchWaveform(nextName);
    onSelect(nextName, waveform);
  }

  return (
    <select
      className="field"
      value={value}
      disabled={disabled || waveforms.length === 0}
      onChange={(event) => void handleChange(event.target.value)}
    >
      <option value="">Select waveform</option>
      {fixtures.length > 0 && (
        <optgroup label="Fixtures">
          {fixtures.map((waveform) => (
            <option key={`fixtures:${waveform.name}`} value={waveform.name}>
              {waveform.name}
            </option>
          ))}
        </optgroup>
      )}
      {custom.length > 0 && (
        <optgroup label="Custom">
          {custom.map((waveform) => (
            <option key={`custom:${waveform.name}`} value={waveform.name}>
              {waveform.name}
            </option>
          ))}
        </optgroup>
      )}
      {profileGroups.map(([group, profiles]) => (
        <optgroup key={`profiles:${group}`} label={`Profiles · ${group || "other"}`}>
          {profiles.map((waveform) => (
            <option key={`profiles:${waveform.name}`} value={waveform.name}>
              {waveform.name}
            </option>
          ))}
        </optgroup>
      ))}
    </select>
  );
}

export function groupProfiles(waveforms: WaveformInfo[]): Array<[string, WaveformInfo[]]> {
  const groups = new Map<string, WaveformInfo[]>();
  for (const waveform of waveforms) {
    if (waveform.source !== "profiles") {
      continue;
    }
    const group = waveform.group ?? "";
    groups.set(group, [...(groups.get(group) ?? []), waveform]);
  }
  return [...groups.entries()];
}
