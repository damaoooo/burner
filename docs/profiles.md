# Power profiles

The WebUI can drive CPU (or GPU) burn from recorded power profiles instead of
hand-drawn waveforms. A profile library is imported once from a wide CSV and
each profile then shows up as a normal waveform under **Profiles · <group>**.

## Input format

```csv
t_s,cl001,cl002,...,bg001,...
0.000,0.99455,2.35603,...
0.002,1.33687,2.32463,...
...
150.000,...
```

- Column 1 is time in seconds, strictly increasing.
- Every other column is one profile. Names may only contain letters, numbers,
  `_` and `-`. The leading letters (`cl`, `bg`, ...) become the group name.
- Values are power in any unit and must not be negative.
- The time column spans exactly one period: the last row marks the period end
  and is not part of the curve. A `0.000`-`150.000` file has a 150 s period.

## Conversion

For every profile the importer:

1. averages samples into `resolution_ms` bins (default 10 ms);
2. scales it to `0..1` with the selected normalization:
   - `global` (default): divides every profile by the highest bin across all
     profiles. Relative sizes between profiles are kept; only the largest
     profile reaches 100% burn. The divisor is stored as `scale` in the index.
   - `self`: divides each profile by its own peak bin, so every profile peaks
     at 100% burn and relative sizes are lost;
3. writes `UI/profiles/<name>.csv` in the normal curve format, with points at
   bin centers.

`UI/profiles/index.json` stores the period, groups, peaks and a 750-point
preview used by the browser. Imported files are git-ignored. A new import
replaces the previous library.

## Importing

From the WebUI, use **Power Profiles → Import CSV** under the waveform editor.

From a shell on the WebUI host:

```bash
python scripts/import_profiles.py /path/to/workload_profiles.csv --resolution-ms 10 --normalize global
```

## Running profiles

- Selecting a profile, globally or per machine, sets **Period** to the profile
  period.
- **Assign to enabled machines** turns on per-machine waveforms and gives each
  enabled machine a different profile from the chosen group, either
  sequentially from a start index or shuffled with a seed. Profiles repeat if
  there are more machines than profiles.
- Short profile steps need a fast control loop. Apply a sampling time of at
  most twice the import resolution (20 ms for the default 10 ms import); the
  panel warns when the applied sampling time is slower.
- Every start shares one start time across machines, so profiles that move
  together in the source data move together on the machines. **Realtime**
  starts all machines together about 5 s after the click (measured from when
  the waveforms have been copied); **Schedule** starts them at the chosen
  local time. A machine that launches late skips ahead to stay in phase.

Burn intensity sets CPU utilization through `lookbusy`, not watts. Power at a
given utilization depends on the machine, so measure with `./watcher` if the
result must match the source profile in watts.
