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
    </select>
  );
}
