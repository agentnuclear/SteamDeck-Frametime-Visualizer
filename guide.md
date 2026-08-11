# Guide: Reading MangoHud frametime captures (Steam Deck)

This guide explains the data captured by **mangoapp** (MangoHud's capture tool on
SteamOS / Steam Deck), how to read it effectively, and how to use the included
`frametime_viewer.py` application.

The capture format has two files per session:

| File | Contents |
|------|----------|
| `mangoapp_2026-08-11_13-04-14.csv` | Device info + one row **per rendered frame** |
| `mangoapp_2026-08-11_13-04-14_summary.csv` | One row of aggregate stats (percentiles, averages, peaks) |

---

## 1. Capturing the data

Enable logging in the MangoHud overlay (Steam Deck Game Mode or desktop), or run:

```
mangoapp -f 1 -l
```

MangoHud writes `mangoapp_<timestamp>.csv` plus a `_summary.csv` to its log
directory. Use the **full session, not a couple of seconds**:

- **< 30 s** – unusable for 1%/0.1% lows and bottleneck ratios.
- **< 2 min** – thermals and sustained load have not stabilized.
- **≥ 5 min** – enough for representative lows, average loads, and thermal behavior.
  Play the same section repeatedly if comparing settings.

---

## 2. File layout (per-frame log)

```
os,cpu,gpu,ram,kernel,driver,cpuscheduler        <- device header
SteamOS,AMD Custom APU 0932,,15160364,...,,powersave
fps,frametime,cpu_load,...,elapsed                <- metric header
81.5417,12.2637,67.4667,...,339251005            <- frame 1
60.8104,16.4446,67.4667,...,355777166            <- frame 2
...
```

Two header rows are followed by the data rows. Each data row is one frame.

---

## 3. Column reference (per-frame log)

| Column | Unit | Meaning | Sample rate |
|--------|------|---------|-------------|
| `fps` | FPS | Instantaneous frames/sec of this frame | per frame |
| `frametime` | ms | Time this frame took (`1000 / fps`) | per frame |
| `cpu_load` | % | CPU utilization (all cores) | ~1 Hz |
| `cpu_power` | W | CPU package power | ~1 Hz |
| `gpu_load` | % | GPU utilization | ~1 Hz |
| `cpu_temp` | °C | CPU temperature | ~1 Hz |
| `gpu_temp` | °C | GPU temperature | ~1 Hz |
| `gpu_core_clock` | MHz | GPU core clock | ~1 Hz |
| `gpu_mem_clock` | MHz | GPU memory clock | ~1 Hz |
| `gpu_vram_used` | GB | VRAM in use | ~1 Hz |
| `gpu_power` | W | GPU power | ~1 Hz |
| `ram_used` | GB | System RAM in use | ~1 Hz |
| `swap_used` | GB | Swap in use | ~1 Hz |
| `process_rss` | MB | RSS of monitored process | ~1 Hz |
| `cpu_mhz` | MHz | CPU core clock | ~1 Hz |
| `elapsed` | ns | Time since capture start | per frame |

### Important: two different sample rates

`fps` and `frametime` are **per frame** (one row per rendered frame). Everything
else is a **sensor reading** that MangoHud refreshes roughly once per second.
So `cpu_load` stays constant for ~30–60 rows, then jumps. This is normal.
Don't read more into these columns than their ~1 Hz resolution allows.

> In the sample capture, `gpu_load` shows only ~10 distinct values over 317 rows
> while `fps` shows 317. That is the expected stepped pattern.

---

## 4. Column reference (summary file)

| Column | Meaning |
|--------|---------|
| `0.1% Min FPS` | FPS floor: average of the slowest 0.1% of frames |
| `1% Min FPS` | Average of the slowest 1% of frames |
| `97% Percentile FPS` | FPS that 97% of frames reach or exceed |
| `Average FPS` | Mean FPS over the capture |
| `GPU Load` / `CPU Load` | Mean load |
| `Average Frame Time` | Mean frame time (ms) |
| `Average/Peak GPU Temp` | GPU temperature mean / max |
| `Average/Peak CPU Temp` | CPU temperature mean / max |
| `Average/Peak VRAM Used` | VRAM usage mean / max (GB) |
| `Average/Peak RAM Used` | RAM usage mean / max (GB) |
| `Average/Peak Swap Used` | Swap usage mean / max (GB) |

---

## 5. Reading the data effectively

Work down this list; each step rules out a category of problem.

### 5.1 FPS headline numbers
- **Average FPS** – overall feel. ~67–70 FPS in the sample.
- **97% percentile FPS** – what you get most of the time (95.3 FPS in sample).
- **1% low** – how bad the worst 1% of frames feel (38.4 FPS in sample).
- **0.1% low** – worst-case hitches (24.2 FPS in sample).

If `97%` ≈ `Average`, the game is steady. If the 1% low is far below the
average, there is periodic hitching.

### 5.2 Frametime and stutters
- Base refresh: **16.67 ms = 60 FPS**, **33.33 ms = 30 FPS**, **50 ms = 20 FPS**.
- Count frames **above 16.7 ms** (missed a 60 Hz frame), **above 33.3 ms** etc.
- A **stutter** is a spike that exceeds ~2× the median frame time and recovers.
- In the sample: median ~14 ms, 2 spike frames, worst single frame 41 ms
  (= one dropped frame). That is a rare hiccup, not a systemic problem.

### 5.3 Bottleneck: is it CPU, GPU, or the cap?
Compare average loads and the time each is pinned near 100%:

- **GPU-bound** – `gpu_load` sits at ~90%+ while CPU has headroom.
- **CPU-bound** – `cpu_load` pinned, GPU idle-ish. CPU clock pinned high.
- **Cap/vsync-bound** – **both** loads moderate (~70%) and GPU clock low.
  This is the sample's verdict: GPU ~70%, CPU ~70%, GPU core clock only
  ~387–776 MHz on a chip that can boost far higher → the frame cap/vsync is
  the limit, not the silicon.

### 5.4 Thermal throttling
- Steam Deck GPU throttles around **~85 °C**, CPU around **~90–95 °C**.
- If temps sit near/above those and clocks drop stepwise, it's thermal.
- Sample: GPU 72 °C, CPU ~79 °C → comfortable, no throttling.

### 5.5 Memory pressure
- Steam Deck has a **16 GB shared pool** (~14.5 GB usable); VRAM and RAM come
  from the same chip.
- RAM near 95%+ or growing swap (`swap_used` trending up) → memory pressure,
  which shows up as stutters.
- Sample: 9.94 GB RAM (69%), 0.93 GB VRAM, flat swap → no pressure.

### 5.6 Cross-check with clocks
- `cpu_mhz` near its max (sample: 3378–3500 MHz) under load → CPU is working.
- `gpu_core_clock` low while `gpu_load` is high → GPU is *capped*, not struggling.

---

## 6. Worked example (the sample capture)

| Check | Value | Reading |
|-------|-------|---------|
| Average FPS | 69.8 | fine |
| 97% / 1% / 0.1% FPS | 95.5 / 38.4 / 24.2 | smooth with rare hitches |
| Stutters | 2 events, worst 41 ms | one dropped frame, not systemic |
| GPU vs CPU load | 70% vs 70% | neither saturated |
| GPU core clock | 387–776 MHz | low → capped/vsync-limited |
| Temps | 72 / 79 °C | no thermal throttling |
| RAM / VRAM / swap | 9.9 / 0.9 / 0.06 GB | no memory pressure |
| **Verdict** | — | **frame cap / vsync is the bottleneck, not CPU/GPU/thermals/memory** |

---

## 7. Data quality caveats (the "cons" of these captures)

Read these before trusting any conclusion:

1. **Duration** – a 4.7 s capture (like the sample) cannot produce reliable
   lows or bottleneck ratios. Re-capture for minutes.
2. **Stepped sensors** – loads/temps/clocks update ~1 Hz; only `fps` and
   `frametime` are true per-frame values. Don't over-analyze ~1 Hz data.
3. **Broken `gpu_power`** – constant value (1 W in the sample) means the sensor
   is unsupported; ignore GPU power readings.
4. **`process_rss` all zeros** – process memory wasn't captured (MangoHud had
   no process to monitor). Ignore it here.
5. **Missing `gpu` / `driver` strings** – MangoHud didn't report them on SteamOS;
   the GPU is `AMD Custom APU 0932`.
6. **Flat `swap_used`** – no swap activity was recorded, so it says nothing
   about swap capacity.

---

## 8. Using `frametime_viewer.py`

```
python frametime_viewer.py            # auto-loads the newest mangoapp_*.csv
python frametime_viewer.py <file.csv> # open a specific capture
```

- **File → Open log CSV…** loads a capture (companion `_summary.csv` is read
  automatically). **File → Open summary CSV…** swaps in a different summary.
- **Tabs**: FPS & Frametime (with spike highlights and 60/30/20 FPS lines +
  a histogram), Loads & Power, Temps & Clocks, Memory. Hover/zoom/pan plots.
- **Summary & Analysis window**: computed stats, the raw MangoHud summary,
  a bottleneck verdict, and the data-quality caveats listed above.
- **View → Toggle Summary window** hides/shows it.

Run `python frametime_viewer.py --selftest` to print all computed statistics
and analysis for a capture without opening the GUI.

---

## 9. Quick glossary

| Term | Meaning |
|------|---------|
| Frame time | ms per rendered frame; `1000 / FPS` |
| 1% low | mean FPS of the slowest 1% of frames |
| 0.1% low | mean FPS of the slowest 0.1% of frames |
| Stutter / hitch | a frame-time spike well above the median that recovers |
| Stepped data | a column that only changes ~1×/s instead of every frame |
| Throttle | hardware lowering clocks to stay under a temperature/power limit |
| Vsync / frame cap | limiting output to a fixed refresh; makes both loads idle |
