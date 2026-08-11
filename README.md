<div align="center">

# Frametime Viewer (Steam Deck)

**A standalone Dear ImGui desktop app to open, visualize and analyze MangoHud / mangoapp frametime captures from Steam Deck.**

`Python 3` · `DearPyGui (ImGui)` · `NumPy` · no other dependencies

</div>

---

## What it does

Steam Deck's overlay (MangoHud / mangoapp) logs two CSVs per session: a
**per-frame frametime log** and a companion **summary file** with percentiles,
averages and peaks. This tool loads them and turns them into:

- 📈 **Interactive charts** – FPS & frametime over time (with stutter spikes
  highlighted and 60/30/20 FPS threshold lines), a frametime histogram,
  CPU/GPU load & power, temperatures (with throttle lines), clocks and memory.
  Hover any chart to get a **cursor-following value readout** (time + every
  series' value at the nearest sample point).
- 📊 **Summary & Analysis window** – computed statistics (avg FPS, 0.1%/1% lows,
  97th percentile, stutter counts), the raw MangoHud summary values, a
  **bottleneck verdict** (CPU-bound / GPU-bound / vsync-capped / thermal /
  memory), and a list of **data-quality caveats** for the capture.

All charts are native Dear ImGui plots with hover, zoom and pan.

## Why it exists

Frametime CSVs are dense and hard to read by eye. This tool was built to make
the analysis quick: open a capture, glance at the charts, read the verdict.

> **Note on tooling:** this project was developed with **opencode** — an AI
> coding agent — used to speed up development and the data analysis process
> (parsing, statistics, and interpreting the captured frametime data).

## Features

- **Open & auto-detect** – open any log CSV; the matching `*_summary.csv` is
  loaded automatically.
- **Per-frame vs sensor data** – distinguishes true per-frame `fps`/`frametime`
  from the ~1 Hz stepped sensor columns.
- **Stutter analysis** – counts events and frames above 16.7/33.3/50 ms.
- **Bottleneck analysis** – GPU vs CPU load ratios, thermal throttle checks,
  clock behavior and memory pressure.
- **Data-quality checks** – flags short captures, broken sensors (e.g. flat
  `gpu_power`), unrecorded `process_rss`, missing GPU/driver strings.
- **Cross-platform** – Windows, macOS and Linux (anywhere Python + DearPyGui runs).

## Requirements

- Python 3.9+
- `dearpygui`
- `numpy`

```bash
pip install dearpygui numpy
```

## Usage

```bash
python frametime_viewer.py                  # auto-loads the newest mangoapp_*.csv
python frametime_viewer.py path/to/log.csv  # open a specific capture
```

| Command | What it does |
|---------|--------------|
| `python frametime_viewer.py` | Launch GUI, auto-load the newest capture in the folder |
| `python frametime_viewer.py <file.csv>` | Launch GUI with a specific capture |
| `python frametime_viewer.py --selftest` | Print all computed stats + analysis (no GUI) |
| `python frametime_viewer.py --smoke` | Build the UI, load, render a few frames, close |

### In the GUI

- **File → Open log CSV…** loads a capture (companion summary auto-loaded).
- **File → Open summary CSV…** loads a summary for the current log.
- **Tabs** – `FPS & Frametime`, `Loads & Power`, `Temps & Clocks`, `Memory`.
- **Hover tooltips** – move the cursor over any chart for a live readout of all
  series at the nearest sample time.
- **Summary & Analysis** window – stats, bottleneck verdict, data-quality notes.
- **View → Toggle Summary window** – show/hide the analysis window.

## Data format

Expected MangoHud/mangoapp output (two rows of headers, then one row per frame):

```csv
os,cpu,gpu,ram,kernel,driver,cpuscheduler
SteamOS,AMD Custom APU 0932,,15160364,6.16.12-...-neptune...,,powersave
fps,frametime,cpu_load,cpu_power,gpu_load,cpu_temp,gpu_temp,gpu_core_clock,gpu_mem_clock,gpu_vram_used,gpu_power,ram_used,swap_used,process_rss,cpu_mhz,elapsed
81.5417,12.2637,67.4667,9.71525,52,78,72,514,800,0.926655,1,9.93916,0.0574417,0,3500,339251005
...
```

See **[`guide.md`](guide.md)** for a full column reference, how to read the data
effectively, and a worked example of interpreting a capture.

## Project structure

```
frametime_viewer.py  # the application (parsing, stats, analysis, ImGui UI)
guide.md             # guide to understanding and reading the captured data
```

## License

MIT
