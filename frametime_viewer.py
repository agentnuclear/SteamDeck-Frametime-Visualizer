#!/usr/bin/env python3
"""Frametime visualizer for MangoHud / mangoapp Steam Deck CSV captures.

A standalone Dear ImGui (DearPyGui) application that lets you open a frametime
log CSV, view charts of fps / frametime / loads / temps / clocks / memory, and
get a window with a computed summary, bottleneck analysis and data-quality
caveats for the captured data.

Run:  python frametime_viewer.py [path/to/log.csv]
"""

import csv
import math
import sys
import os
from pathlib import Path

import numpy as np
import dearpygui.dearpygui as dpg

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------
DEVICE_KEYS = ("os", "cpu", "gpu", "ram", "kernel", "driver", "cpuscheduler")

SERIES_COLORS = {
    "fps": (90, 170, 255, 255),
    "frametime": (255, 170, 60, 255),
    "cpu_load": (110, 220, 110, 255),
    "gpu_load": (230, 120, 255, 255),
    "cpu_power": (130, 210, 130, 255),
    "gpu_power": (255, 120, 120, 255),
    "cpu_temp": (255, 190, 90, 255),
    "gpu_temp": (255, 130, 90, 255),
    "gpu_core_clock": (80, 220, 220, 255),
    "gpu_mem_clock": (190, 150, 255, 255),
    "cpu_mhz": (140, 240, 140, 255),
    "ram_used": (110, 160, 255, 255),
    "gpu_vram_used": (255, 140, 220, 255),
    "swap_used": (255, 100, 100, 255),
    "process_rss": (180, 180, 180, 255),
}

STUTTER_MS = [16.667, 33.333, 50.0]  # 60/30/20 FPS refresh lines
STUTTER_LABELS = ["16.7ms (60 FPS)", "33.3ms (30 FPS)", "50.0ms (20 FPS)"]

GPU_THROTTLE_C = 85.0
CPU_THROTTLE_C = 90.0
LOAD_HIGH = 90.0


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------
def _to_float(v):
    try:
        f = float(str(v).strip())
        if math.isnan(f) or math.isinf(f):
            return math.nan
        return f
    except (TypeError, ValueError):
        return math.nan


class Capture:
    def __init__(self):
        self.path = None
        self.summary_path = None
        self.device = {}
        self.metric_header = []
        self.metrics = {}          # name -> np.ndarray (float, NaN if missing)
        self.n_rows = 0
        self.times = None          # seconds, from elapsed
        self.summary = {}
        self.raw_summary = {}

    def metric(self, *names):
        for n in names:
            if n in self.metrics and self.metrics[n] is not None:
                return self.metrics[n]
        return None

    @property
    def duration_s(self):
        if self.times is not None and len(self.times):
            return float(self.times[-1] - self.times[0])
        return 0.0


def parse_capture(path):
    cap = Capture()
    cap.path = str(path)
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        rows = [r for r in reader if r and any(c.strip() for c in r)]

    # Locate the metric header row (first cell is a known metric name).
    metric_idx = None
    for i, row in enumerate(rows):
        if row and row[0].strip().lower() in ("fps", "frametime", "frametimes", "time"):
            if any(c.strip().lower() in ("fps", "frametime", "cpu_load", "gpu_load") for c in row):
                metric_idx = i
                break
    if metric_idx is None:
        raise ValueError("Could not locate the metric header row (fps/frametime columns).")

    # Device info block: a header row whose cells are known keys, followed by
    # value rows (e.g. row "os,cpu,gpu,ram,..." then row "SteamOS,AMD...,...")
    dev_header = None
    for i in range(metric_idx):
        row = rows[i]
        key = row[0].strip().lower() if row else ""
        if key in DEVICE_KEYS:
            dev_header = [c.strip().lower() for c in row]
            val_idx = i + 1
            break
    if dev_header and val_idx < metric_idx:
        for k, v in zip(dev_header, rows[val_idx]):
            cap.device[k] = v.strip()

    cap.metric_header = [c.strip().lower() for c in rows[metric_idx]]
    header = cap.metric_header

    cols = {}
    for j, name in enumerate(header):
        values = []
        for row in rows[metric_idx + 1:]:
            v = row[j] if j < len(row) else ""
            values.append(_to_float(v))
        cols[name] = values

    cap.n_rows = len(rows) - (metric_idx + 1)
    for name, values in cols.items():
        arr = np.array(values, dtype=np.float64)
        cap.metrics[name] = arr

    # Time axis from the elapsed column (nanoseconds) if available.
    elapsed = cap.metric("elapsed", "elapsed_time", "time")
    if elapsed is not None and len(elapsed) > 1:
        cap.times = (elapsed - elapsed[0]) / 1e9
    else:
        cap.times = np.arange(cap.n_rows, dtype=np.float64)

    return cap


def parse_summary(path):
    summary = {}
    raw = {}
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        rows = [r for r in reader if r and any(c.strip() for c in r)]
    if len(rows) < 2:
        return raw, summary
    keys = rows[0]
    for j, key in enumerate(keys):
        name = key.strip()
        raw[name] = rows[1][j] if j < len(rows[1]) else ""
        f = _to_float(rows[1][j]) if j < len(rows[1]) else math.nan
        summary[name] = f if not math.isnan(f) else None
    return raw, summary


def find_companion_summary(path):
    p = Path(path)
    cands = [
        p.with_name(p.stem + "_summary" + p.suffix),
        p.with_name(p.stem + "_summary.csv"),
    ]
    for c in cands:
        if c.is_file():
            return str(c)
    return None


def find_companion_main(summary_path):
    p = Path(summary_path)
    stem = p.stem
    if stem.endswith("_summary"):
        cand = p.with_name(stem[:-len("_summary")] + p.suffix)
        if cand.is_file():
            return str(cand)
    return None


# --------------------------------------------------------------------------
# Statistics
# --------------------------------------------------------------------------
def percentile_of_sorted_frametimes(ft_ms, pct):
    """MangoHud-style low: average of the slowest pct% of frame times."""
    if ft_ms is None or len(ft_ms) == 0:
        return None, None
    s = np.sort(ft_ms[~np.isnan(ft_ms)])
    if len(s) == 0:
        return None, None
    n = max(1, int(round(len(s) * pct / 100.0)))
    slow = s[-n:]
    ms = float(slow.mean())
    fps = 1000.0 / ms if ms > 0 else 0.0
    return ms, fps


def compute_stats(cap):
    ft = cap.metric("frametime", "frametimes")
    fps = cap.metric("fps")
    if ft is None and fps is not None:
        ft = 1000.0 / np.maximum(fps, 1e-9)
        ft[np.isnan(fps)] = np.nan

    s = {}
    s["frametime"] = ft
    s["fps"] = fps

    if ft is not None:
        f = ft[~np.isnan(ft)]
        s["ft_avg_ms"] = float(np.mean(f))
        s["ft_p50_ms"] = float(np.median(f))
        s["ft_p95_ms"] = float(np.percentile(f, 95))
        s["ft_p99_ms"] = float(np.percentile(f, 99))
        s["ft_max_ms"] = float(np.max(f))
        s["ft_std_ms"] = float(np.std(f))
        for pct in (0.1, 1.0):
            ms, fp = percentile_of_sorted_frametimes(f, pct)
            s["low%g_ms" % pct] = ms
            s["low%g_fps" % pct] = fp
        med = float(np.median(f)) if len(f) else 16.667
        thr = max(med * 2.0, 16.667)
        s["stutter_thresh_ms"] = thr
        spikes = f[f > thr]
        s["n_spike_frames"] = int(len(spikes))
        s["spike_pct"] = float(100.0 * len(spikes) / len(f)) if len(f) else 0.0
        s["worst_spike_ms"] = float(np.max(spikes)) if len(spikes) else 0.0
        over = ft > thr
        runs = 0
        prev = False
        for v in over:
            if v and not prev:
                runs += 1
            prev = bool(v)
        s["n_stutters"] = runs
        s["n_over_1667"] = int(np.sum(f > 16.667))
        s["n_over_3333"] = int(np.sum(f > 33.333))
        s["n_over_5000"] = int(np.sum(f > 50.0))
        lo, hi = np.percentile(f, [1, 99])
        s["ft_hist_lo"] = max(0.0, float(lo * 0.5))
        s["ft_hist_hi"] = min(float(hi * 1.25), max(float(np.max(f)) * 1.05, 100.0))

    if fps is not None:
        fp = fps[~np.isnan(fps)]
        if len(fp):
            s["fps_avg"] = float(np.mean(fp))
            s["fps_p50"] = float(np.median(fp))
            s["fps_p97"] = float(np.percentile(fp, 97))
            s["fps_min"] = float(np.min(fp))
            s["fps_max"] = float(np.max(fp))

    def stat(name, *aliases):
        arr = cap.metric(name, *aliases)
        if arr is None:
            return None
        v = arr[~np.isnan(arr)]
        if len(v) == 0:
            return None
        return {
            "avg": float(np.mean(v)),
            "min": float(np.min(v)),
            "max": float(np.max(v)),
            "p50": float(np.median(v)),
            "unique": int(len(np.unique(v))),
            "last": float(v[-1]),
        }

    s["cpu_load"] = stat("cpu_load", "cpu_avg_load", "cpuload")
    s["gpu_load"] = stat("gpu_load", "gpu_avg_load", "gpuload")
    s["cpu_power"] = stat("cpu_power")
    s["gpu_power"] = stat("gpu_power")
    s["cpu_temp"] = stat("cpu_temp")
    s["gpu_temp"] = stat("gpu_temp")
    s["gpu_core_clock"] = stat("gpu_core_clock", "gpu_coreclk")
    s["gpu_mem_clock"] = stat("gpu_mem_clock", "gpu_memclk")
    s["cpu_mhz"] = stat("cpu_mhz", "cpu_core_clock", "cpuclock")
    s["ram_used"] = stat("ram_used", "vram_all")
    s["vram_used"] = stat("gpu_vram_used", "gpu_vram_used")
    s["swap_used"] = stat("swap_used")
    s["rss"] = stat("process_rss", "proc_rss")

    # GPU/CPU bound estimation (based on sampled loads).
    g = cap.metric("gpu_load")
    c = cap.metric("cpu_load")
    if g is not None and c is not None:
        valid = ~(np.isnan(g) | np.isnan(c))
        gg, cc = g[valid], c[valid]
        if len(gg):
            n = len(gg)
            s["gpu_bound_pct"] = float(100.0 * np.sum((gg >= LOAD_HIGH) & (cc < LOAD_HIGH)) / n)
            s["cpu_bound_pct"] = float(100.0 * np.sum((cc >= LOAD_HIGH) & (gg < LOAD_HIGH)) / n)
            s["both_high_pct"] = float(100.0 * np.sum((gg >= LOAD_HIGH) & (cc >= LOAD_HIGH)) / n)
            s["idle_pct"] = float(100.0 * np.sum((gg < LOAD_HIGH) & (cc < LOAD_HIGH)) / n)

    s["total_ram_kb"] = None
    try:
        if cap.device.get("ram"):
            s["total_ram_kb"] = float(cap.device["ram"])
    except (TypeError, ValueError):
        pass
    return s


def build_bottleneck_report(cap, stats):
    verdict = "Unable to determine (insufficient data)"
    points = []
    g = stats.get("gpu_load")
    c = stats.get("cpu_load")
    if g and c:
        ga, ca = g["avg"], c["avg"]
        gp = stats.get("gpu_bound_pct", 0.0)
        cp = stats.get("cpu_bound_pct", 0.0)
        points.append(
            "Average GPU load %.0f%% vs CPU load %.0f%% "
            "(GPU-bound %.0f%% / CPU-bound %.0f%% of sampled time)."
            % (ga, ca, gp, cp)
        )
        if gp >= 60:
            verdict = "GPU-bound"
        elif cp >= 60:
            verdict = "CPU-bound"
        elif gp > 35 and gp > cp:
            verdict = "Mostly GPU-bound"
        elif cp > 35 and cp > gp:
            verdict = "Mostly CPU-bound"
        elif gp > 35 and cp > 35:
            verdict = "GPU & CPU both heavily loaded"
        else:
            verdict = "Neither GPU nor CPU saturated (likely vsync / frame cap limited)"

    if "n_stutters" in stats:
        points.append(
            "%d stutter event(s) / %d spike frame(s) (%.1f%% of frames) above %.1fms; "
            "%d frame(s) worse than 33.3ms; worst frame %.1fms."
            % (stats["n_stutters"], stats["n_spike_frames"], stats["spike_pct"],
               stats["stutter_thresh_ms"], stats["n_over_3333"], stats["worst_spike_ms"])
        )
        if stats["n_stutters"] >= 10:
            points.append("Stutter count is high relative to capture length - check vsync/turbo/thermal behavior.")

    gt = stats.get("gpu_temp")
    ct = stats.get("cpu_temp")
    if gt and gt["max"] >= GPU_THROTTLE_C:
        points.append("GPU temp peaked at %.0fC (>= %.0fC) - thermal throttling likely." % (gt["max"], GPU_THROTTLE_C))
    elif gt and gt["avg"] >= 70:
        points.append("GPU temp averaged %.0fC - warm but below throttle threshold." % gt["avg"])
    if ct and ct["max"] >= CPU_THROTTLE_C:
        points.append("CPU temp peaked at %.0fC (>= %.0fC) - CPU thermal throttling likely." % (ct["max"], CPU_THROTTLE_C))
    elif ct and ct["avg"] >= 80:
        points.append("CPU temp averaged %.0fC - warm but below throttle threshold." % ct["avg"])

    ram = stats.get("ram_used")
    vram = stats.get("vram_used")
    swap = stats.get("swap_used")
    total_kb = stats.get("total_ram_kb")
    if ram:
        total_gb = (total_kb / (1024 ** 2)) if total_kb else None
        if total_gb:
            frac = ram["max"] / total_gb
            points.append("Peak RAM usage %.2fGB of ~%.1fGB total (%.0f%%)." % (ram["max"], total_gb, frac * 100))
            if frac >= 0.95:
                points.append("RAM near full capacity - expect swap pressure / stutter.")
        else:
            points.append("Peak RAM usage %.2fGB (total unknown)." % ram["max"])
    if vram:
        points.append("Peak VRAM usage %.2fGB (shared-memory APU pool)." % vram["max"])
    if swap:
        if swap["max"] > 0.05:
            points.append("Swap usage peaked at %.2fGB - memory pressure may be present." % swap["max"])
        else:
            points.append("Swap usage essentially flat (near zero) - no evident memory pressure.")

    gcc = stats.get("gpu_core_clock")
    cm = stats.get("cpu_mhz")
    if gcc:
        points.append("GPU core clock ranged %.0f-%.0fMHz (avg %.0fMHz)." % (gcc["min"], gcc["max"], gcc["avg"]))
    if cm:
        points.append("CPU clock ranged %.0f-%.0fMHz (avg %.0fMHz)." % (cm["min"], cm["max"], cm["avg"]))

    return verdict, points


def build_data_quality_notes(cap, stats):
    notes = []
    dur = cap.duration_s
    n = cap.n_rows
    if n:
        notes.append("Capture length ~%.1fs across %d frames." % (dur, n))
        if dur < 30:
            notes.append("Capture is SHORT (< 30s): percentiles / 1%% lows and bottleneck ratios are not statistically reliable yet.")
        if dur < 120:
            notes.append("Capture under 2 minutes: thermal behavior and sustained load trends may not have stabilized.")
    if cap.summary_path is None:
        notes.append("Companion _summary.csv not found - MangoHud percentile values unavailable, computed locally instead.")

    step_cols = []
    for name in ("cpu_load", "gpu_load", "cpu_power", "cpu_temp", "gpu_temp",
                 "gpu_core_clock", "gpu_mem_clock", "gpu_vram_used", "ram_used", "cpu_mhz"):
        st = stats.get(name)
        if st and n > 0:
            ratio = st["unique"] / n
            if ratio < 0.5 and st["unique"] < n:
                step_cols.append("%s (%d unique / %d rows)" % (name, st["unique"], n))
    if step_cols:
        notes.append("Sensor columns update much slower than the per-frame rate (stepped data): " +
                     ", ".join(step_cols) +
                     ". Loads/temps/clocks were polled roughly every ~1s, not per frame.")

    gp = stats.get("gpu_power")
    if gp and gp["unique"] == 1:
        notes.append("gpu_power is constant - the GPU power sensor is unavailable/broken on this device; power data is not meaningful.")
    rss = stats.get("rss")
    if rss and rss["unique"] == 1 and rss["max"] == 0.0:
        notes.append("process_rss is all zeros - per-process memory was not captured (MangoHud needed a process to monitor).")
    swap = stats.get("swap_used")
    if swap and swap["unique"] == 1:
        notes.append("swap_used is flat - no swap activity was recorded during this capture.")
    vram = stats.get("vram_used")
    if vram and vram["unique"] == 1:
        notes.append("gpu_vram_used is constant - VRAM sensor appeared not to update during the capture.")
    if not cap.device.get("gpu"):
        notes.append("Device info does not include a GPU name (MangoHud didn't report one on this system).")
    if not cap.device.get("driver"):
        notes.append("Device info does not include a GPU driver string.")

    if len(cap.metrics.get("cpu_load", [])) and len(cap.metrics.get("cpu_load")):
        cl = stats.get("cpu_load")
        if cl and cl["avg"] == cl["min"] == cl["max"]:
            notes.append("cpu_load is constant for the whole capture - the CPU sensor may not be updating.")
    if len(cap.metrics.get("gpu_temp", [])) and len(cap.metrics.get("gpu_temp")):
        gt = stats.get("gpu_temp")
        if gt and gt["unique"] == 1:
            notes.append("gpu_temp shows a single value - temperature sensor appears static in this capture.")

    return notes


# --------------------------------------------------------------------------
# Dear ImGui UI
# --------------------------------------------------------------------------
STUTTER_COL = (255, 90, 90, 255)
VERDICT_GOOD = (140, 220, 140, 255)
VERDICT_WARN = (255, 200, 100, 255)
VERDICT_BAD = (255, 110, 110, 255)
THRESH_COL = (255, 255, 255, 60)


def _fmt(v, nd=2, unit="", empty="n/a"):
    if v is None:
        return empty
    try:
        f = float(v)
    except (TypeError, ValueError):
        return empty
    if math.isnan(f):
        return empty
    return ("{:,.%df}" % nd).format(f) + unit


def _fmt_int(v, empty="n/a"):
    if v is None:
        return empty
    return "{:,}".format(int(v))


def _series_theme(color, kind="line"):
    with dpg.theme() as t:
        if kind == "line":
            with dpg.theme_component(dpg.mvLineSeries):
                dpg.add_theme_color(dpg.mvPlotCol_Line, color)
        else:
            with dpg.theme_component(dpg.mvScatterSeries):
                dpg.add_theme_color(dpg.mvPlotCol_MarkerFill, color)
                dpg.add_theme_color(dpg.mvPlotCol_MarkerOutline, (0, 0, 0, 255))
                dpg.add_theme_style(dpg.mvPlotStyleVar_Marker, dpg.mvPlotMarker_Circle, category=dpg.mvThemeCat_Plots)
    return t


def _line(axis, x, y, label, color):
    t = _series_theme(color, "line")
    tag = dpg.add_line_series(x, y, label=label, parent=axis)
    dpg.bind_item_theme(tag, t)
    return tag


def _scatter(axis, x, y, label, color):
    t = _series_theme(color, "scatter")
    tag = dpg.add_scatter_series(x, y, label=label, parent=axis)
    dpg.bind_item_theme(tag, t)
    return tag


def _hthreshold(axis, x0, x1, y, label):
    t = _series_theme(THRESH_COL, "line")
    tag = dpg.add_line_series([x0, x1], [y, y], label=label, parent=axis)
    dpg.bind_item_theme(tag, t)
    return tag


def _new_plot(parent, label, xlabel, ylabel, height=240):
    with dpg.group(parent=parent):
        dpg.add_text(label)
        with dpg.plot(label=label, height=height, width=-1) as plot_tag:
            dpg.add_plot_legend()
            xa = dpg.add_plot_axis(dpg.mvXAxis, label=xlabel)
            ya = dpg.add_plot_axis(dpg.mvYAxis, label=ylabel)
    return plot_tag, xa, ya


class ViewerApp:
    def __init__(self, initial_path=None):
        self.cap = None
        self.stats = None
        self.verdict = None
        self.report_points = []
        self.dq_notes = []
        self.load_counter = 0

        dpg.create_context()
        dpg.create_viewport(title="Frametime Visualizer (Steam Deck / MangoHud)", width=1500, height=950)
        self._build_static_ui()

        if initial_path and Path(initial_path).is_file():
            self.load(initial_path)
        else:
            self._auto_load()

        dpg.setup_dearpygui()
        dpg.show_viewport()
        dpg.start_dearpygui()
        dpg.destroy_context()

    # ------------------------------------------------------------------
    # Static UI (menus, windows, tabs)
    # ------------------------------------------------------------------
    def _build_static_ui(self):
        with dpg.window(tag="main", label="Frametime Visualizer", no_close=True):
            with dpg.menu_bar():
                with dpg.menu(label="File"):
                    dpg.add_menu_item(label="Open log CSV...", callback=self._open_log_dialog)
                    dpg.add_menu_item(label="Open summary CSV...", callback=self._open_summary_dialog)
                    dpg.add_separator()
                    dpg.add_menu_item(label="Exit", callback=self._quit)
                with dpg.menu(label="View"):
                    dpg.add_menu_item(label="Toggle Summary window", callback=lambda: dpg.configure_item(
                        "summary_win", show=not dpg.get_item_configuration("summary_win")["show"]))
                with dpg.menu(label="Help"):
                    dpg.add_menu_item(label="About", callback=self._show_about)

            dpg.add_text("No file loaded.", tag="status_text")

            with dpg.tab_bar():
                with dpg.tab(label="FPS & Frametime"):
                    with dpg.child_window(tag="tab_fps_body", height=0):
                        dpg.add_text("Open a log CSV to visualize.", tag="tab_fps_empty")
                with dpg.tab(label="Loads & Power"):
                    with dpg.child_window(tag="tab_loads_body", height=0):
                        dpg.add_text("Open a log CSV to visualize.", tag="tab_loads_empty")
                with dpg.tab(label="Temps & Clocks"):
                    with dpg.child_window(tag="tab_temps_body", height=0):
                        dpg.add_text("Open a log CSV to visualize.", tag="tab_temps_empty")
                with dpg.tab(label="Memory"):
                    with dpg.child_window(tag="tab_mem_body", height=0):
                        dpg.add_text("Open a log CSV to visualize.", tag="tab_mem_empty")

        with dpg.window(tag="summary_win", label="Summary & Analysis", width=470, height=880,
                        pos=(1015, 30), show=True):
            with dpg.child_window(tag="summary_body", width=0, height=0):
                dpg.add_text("No data.", tag="summary_empty")

        with dpg.file_dialog(directory_selector=False, show=False, callback=self._on_open_log,
                             tag="fd_log", width=760, height=480, label="Open frametime log CSV") as fd:
            dpg.add_file_extension(".csv")
            dpg.add_file_extension(".*")
        with dpg.file_dialog(directory_selector=False, show=False, callback=self._on_open_summary,
                             tag="fd_summary", width=760, height=480, label="Open MangoHud summary CSV") as fd:
            dpg.add_file_extension(".csv")
            dpg.add_file_extension(".*")

    # ------------------------------------------------------------------
    # File dialogs / actions
    # ------------------------------------------------------------------
    def _default_dir(self):
        if self.cap and self.cap.path:
            return os.path.dirname(self.cap.path)
        return str(Path.home())

    def _open_log_dialog(self):
        dpg.configure_item("fd_log", default_path=self._default_dir())
        dpg.show_item("fd_log")

    def _open_summary_dialog(self):
        dpg.configure_item("fd_summary", default_path=self._default_dir())
        dpg.show_item("fd_summary")

    def _on_open_log(self, sender, app_data):
        p = app_data.get("file_path_name") if isinstance(app_data, dict) else None
        if p:
            self.load(p)

    def _on_open_summary(self, sender, app_data):
        p = app_data.get("file_path_name") if isinstance(app_data, dict) else None
        if p:
            self.load_summary_only(p)

    def _quit(self):
        dpg.destroy_context()
        raise SystemExit(0)

    def _show_about(self):
        with dpg.window(tag="about_win", label="About", width=470, height=370, show=True, modal=True,
                        pos=(520, 280), no_resize=True):
            dpg.add_text(
                "Frametime Visualizer\n\n"
                "Standalone Dear ImGui (DearPyGui) app for MangoHud / mangoapp\n"
                "frametime CSVs captured on Steam Deck.\n\n"
                "Open a log CSV; a companion *_summary.csv is loaded automatically.\n"
                "Charts: FPS / frametime (with stutter highlights), loads, power,\n"
                "temperatures, clocks and memory. The Summary window gives\n"
                "computed statistics, a bottleneck verdict and data-quality caveats.\n\n"
                "Tip: captures shorter than ~2 minutes give unreliable 1%/0.1% lows.")

    def _auto_load(self):
        here = Path(__file__).resolve().parent
        cands = sorted(here.glob("mangoapp_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
        for c in cands:
            if c.name.endswith("_summary.csv"):
                continue
            try:
                self.load(str(c))
                return
            except Exception:
                continue

    # ------------------------------------------------------------------
    # Load / rebuild
    # ------------------------------------------------------------------
    def load(self, path):
        try:
            cap = parse_capture(path)
        except Exception as exc:
            self._report_error("Could not parse %s\n\n%s" % (path, exc))
            return
        cap.summary_path = find_companion_summary(path)
        if cap.summary_path:
            cap.raw_summary, cap.summary = parse_summary(cap.summary_path)
        self.cap = cap
        self._analyze()
        self._rebuild()
        dpg.set_value("status_text", "Loaded: %s" % os.path.basename(path))

    def load_summary_only(self, path):
        if self.cap is None:
            self._report_error("Open a log CSV first, then a summary CSV, or open the log directly.")
            return
        try:
            self.cap.raw_summary, self.cap.summary = parse_summary(path)
            self.cap.summary_path = str(path)
        except Exception as exc:
            self._report_error("Could not parse summary CSV: %s" % exc)
            return
        self._rebuild()
        dpg.set_value("status_text", "Summary loaded: %s" % os.path.basename(path))

    def _analyze(self):
        self.stats = compute_stats(self.cap)
        self.verdict, self.report_points = build_bottleneck_report(self.cap, self.stats)
        self.dq_notes = build_data_quality_notes(self.cap, self.stats)

    def _report_error(self, msg):
        dpg.set_value("status_text", "ERROR: %s" % msg.splitlines()[0])
        print(msg, file=sys.stderr)

    def _rebuild(self):
        self.load_counter += 1
        p = "L%d_" % self.load_counter
        for body in ("tab_fps_body", "tab_loads_body", "tab_temps_body", "tab_mem_body"):
            dpg.delete_item(body, children_only=True)
        dpg.delete_item("summary_body", children_only=True)
        if self.cap is None:
            return
        self._build_fps_tab(p)
        self._build_loads_tab(p)
        self._build_temps_tab(p)
        self._build_mem_tab(p)
        self._build_summary(p)

    # ------------------------------------------------------------------
    # Chart builders
    # ------------------------------------------------------------------
    def _build_fps_tab(self, p):
        cap, s = self.cap, self.stats
        t = cap.times if cap.times is not None else np.arange(cap.n_rows)
        body = "tab_fps_body"
        dpg.push_container_stack(body)

        fps = cap.metric("fps")
        ft = cap.metric("frametime")
        if ft is None and fps is not None:
            ft = 1000.0 / np.maximum(fps, 1e-9)

        # --- FPS over time ---
        plot, xa, ya = _new_plot(body, "FPS over time", "Time (s)", "FPS", height=210)
        if fps is not None:
            m = ~np.isnan(fps)
            _line(ya, t[m], fps[m], "fps", SERIES_COLORS["fps"])
            if ft is not None:
                bad = ft > 16.667
                mb = bad & ~np.isnan(fps)
                if np.any(mb):
                    _scatter(ya, t[mb], fps[mb], "stutter frame", STUTTER_COL)
            for y in (30.0, 60.0):
                _hthreshold(ya, float(t[0]), float(t[-1]), y, "%d FPS" % y)

        # --- Frametime over time ---
        plot, xa, ya = _new_plot(body, "Frametime (ms) over time", "Time (s)", "Frame time (ms)", height=210)
        if ft is not None:
            m = ~np.isnan(ft)
            _line(ya, t[m], ft[m], "frametime", SERIES_COLORS["frametime"])
            thr = max(s.get("stutter_thresh_ms", 16.667), 16.667)
            spike = ft > thr
            ms = spike & ~np.isnan(ft)
            if np.any(ms):
                _scatter(ya, t[ms], ft[ms], "spike", STUTTER_COL)
            x0, x1 = float(t[0]), float(t[-1])
            for y, lab in zip(STUTTER_MS, STUTTER_LABELS):
                _hthreshold(ya, x0, x1, y, lab)

        # --- Frametime histogram ---
        plot, xa, ya = _new_plot(body, "Frametime distribution", "Frame time (ms)", "Frame count", height=210)
        if ft is not None:
            f = ft[~np.isnan(ft)]
            if len(f):
                lo = s.get("ft_hist_lo", 0.0)
                hi = s.get("ft_hist_hi", 100.0)
                if hi <= lo:
                    hi = lo + 1.0
                bins = np.linspace(lo, hi, 41)
                counts, edges = np.histogram(f, bins=bins)
                counts = counts.astype(np.float64)
                widths = np.diff(edges)
                centers = edges[:-1] + widths / 2.0
                t0 = _series_theme((150, 180, 220, 255), "line")
                btag = dpg.add_bar_series(centers, counts, weight=float(widths[0]), label="frames",
                                          parent=ya)
                dpg.bind_item_theme(btag, t0)
                if 16.667 >= lo:
                    _hthreshold(ya, lo, hi, counts.max() * 0.15, "16.7ms")
        dpg.pop_container_stack()

    def _build_loads_tab(self, p):
        cap = self.cap
        t = cap.times if cap.times is not None else np.arange(cap.n_rows)
        body = "tab_loads_body"
        dpg.push_container_stack(body)

        plot, xa, ya = _new_plot(body, "CPU / GPU load (%)", "Time (s)", "Load (%)", height=240)
        g = cap.metric("gpu_load")
        c = cap.metric("cpu_load")
        if g is not None:
            m = ~np.isnan(g)
            _line(ya, t[m], g[m], "gpu_load", SERIES_COLORS["gpu_load"])
        if c is not None:
            m = ~np.isnan(c)
            _line(ya, t[m], c[m], "cpu_load", SERIES_COLORS["cpu_load"])
        if (g is not None and np.any(~np.isnan(g))) or (c is not None and np.any(~np.isnan(c))):
            _hthreshold(ya, float(t[0]), float(t[-1]), 90.0, "90% high-load")

        plot, xa, ya = _new_plot(body, "CPU / GPU power (W)", "Time (s)", "Power (W)", height=240)
        g = cap.metric("gpu_power")
        c = cap.metric("cpu_power")
        if g is not None:
            m = ~np.isnan(g)
            _line(ya, t[m], g[m], "gpu_power", SERIES_COLORS["gpu_power"])
        if c is not None:
            m = ~np.isnan(c)
            _line(ya, t[m], c[m], "cpu_power", SERIES_COLORS["cpu_power"])

        

    def _build_temps_tab(self, p):
        cap = self.cap
        t = cap.times if cap.times is not None else np.arange(cap.n_rows)
        body = "tab_temps_body"
        dpg.push_container_stack(body)

        plot, xa, ya = _new_plot(body, "Temperatures (C)", "Time (s)", "Temperature (C)", height=240)
        g = cap.metric("gpu_temp")
        c = cap.metric("cpu_temp")
        if g is not None:
            m = ~np.isnan(g)
            _line(ya, t[m], g[m], "gpu_temp", SERIES_COLORS["gpu_temp"])
        if c is not None:
            m = ~np.isnan(c)
            _line(ya, t[m], c[m], "cpu_temp", SERIES_COLORS["cpu_temp"])
        _hthreshold(ya, float(t[0]), float(t[-1]), GPU_THROTTLE_C, "GPU throttle ~%dC" % GPU_THROTTLE_C)
        _hthreshold(ya, float(t[0]), float(t[-1]), CPU_THROTTLE_C, "CPU throttle ~%dC" % CPU_THROTTLE_C)

        plot, xa, ya = _new_plot(body, "Clocks (MHz)", "Time (s)", "MHz", height=240)
        gcc = cap.metric("gpu_core_clock")
        gmc = cap.metric("gpu_mem_clock")
        cm = cap.metric("cpu_mhz", "cpu_core_clock")
        if gcc is not None:
            m = ~np.isnan(gcc)
            _line(ya, t[m], gcc[m], "gpu_core_clock", SERIES_COLORS["gpu_core_clock"])
        if gmc is not None:
            m = ~np.isnan(gmc)
            _line(ya, t[m], gmc[m], "gpu_mem_clock", SERIES_COLORS["gpu_mem_clock"])
        if cm is not None:
            m = ~np.isnan(cm)
            _line(ya, t[m], cm[m], "cpu_mhz", SERIES_COLORS["cpu_mhz"])
        dpg.pop_container_stack()

    def _build_mem_tab(self, p):
        cap = self.cap
        t = cap.times if cap.times is not None else np.arange(cap.n_rows)
        body = "tab_mem_body"
        dpg.push_container_stack(body)

        plot, xa, ya = _new_plot(body, "Memory usage (GB)", "Time (s)", "GB", height=260)
        for name, label in (("ram_used", "ram_used"), ("gpu_vram_used", "gpu_vram_used"),
                            ("swap_used", "swap_used"), ("process_rss", "process_rss")):
            arr = cap.metric(name)
            if arr is None:
                continue
            m = ~np.isnan(arr)
            if np.any(m):
                _line(ya, t[m], arr[m], label, SERIES_COLORS.get(name, (180, 180, 180, 255)))

        total_kb = self.stats.get("total_ram_kb")
        if total_kb:
            _hthreshold(ya, float(t[0]), float(t[-1]), total_kb / (1024 ** 2),
                        "total RAM %.1fGB" % (total_kb / (1024 ** 2)))
        dpg.pop_container_stack()

        

    # ------------------------------------------------------------------
    # Summary window
    # ------------------------------------------------------------------
    def _kv_table(self, parent, rows, p, n):
        with dpg.table(parent=parent, tag="%st%d" % (p, n), header_row=True,
                       borders_innerH=True, borders_innerV=True, resizable=True, row_background=True):
            dpg.add_table_column(label="Metric")
            dpg.add_table_column(label="Value")
            for k, v in rows:
                with dpg.table_row():
                    dpg.add_text(k)
                    dpg.add_text(v)

    def _build_summary(self, p):
        cap, s = self.cap, self.stats
        body = "summary_body"
        dpg.push_container_stack(body)

        dpg.add_text(os.path.basename(cap.path) if cap.path else "unknown file", color=(200, 220, 255, 255))
        dpg.add_text("Frames: %s   Duration: ~%.1fs" % (_fmt_int(cap.n_rows), cap.duration_s))
        if cap.summary_path:
            dpg.add_text("MangoHud summary: %s" % os.path.basename(cap.summary_path),
                         color=(150, 200, 150, 255))
        dpg.add_spacer(height=4)

        if cap.device:
            rows = []
            for k in DEVICE_KEYS:
                v = cap.device.get(k, "")
                if k == "ram" and v:
                    try:
                        v = "%.2f GB (%s KB)" % (float(v) / (1024 ** 2), _fmt_int(float(v), "?"))
                    except (TypeError, ValueError):
                        pass
                rows.append((k, v))
            with dpg.collapsing_header(label="Device info", default_open=False):
                self._kv_table(body, rows, p, 0)

        ft_rows = []
        if "fps_avg" in s:
            ft_rows.append(("Average FPS", _fmt(s["fps_avg"])))
            ft_rows.append(("Median FPS", _fmt(s.get("fps_p50"))))
            ft_rows.append(("97th percentile FPS", _fmt(s.get("fps_p97"))))
            ft_rows.append(("Min / Max FPS", "%s / %s" % (_fmt(s.get("fps_min")), _fmt(s.get("fps_max")))))
        if "ft_avg_ms" in s:
            ft_rows.append(("Average frame time", _fmt(s["ft_avg_ms"], 2, " ms")))
            ft_rows.append(("Median frame time", _fmt(s.get("ft_p50_ms"), 2, " ms")))
            ft_rows.append(("P95 / P99 frame time", "%s / %s ms" % (_fmt(s.get("ft_p95_ms")), _fmt(s.get("ft_p99_ms")))))
            ft_rows.append(("Max frame time", _fmt(s.get("ft_max_ms"), 2, " ms")))
            ft_rows.append(("Frame time std dev", _fmt(s.get("ft_std_ms"), 2, " ms")))
            ft_rows.append(("0.1%% low", "%s ms  ->  %s FPS" % (_fmt(s.get("low0.1_ms"), 2), _fmt(s.get("low0.1_fps")))))
            ft_rows.append(("1%% low", "%s ms  ->  %s FPS" % (_fmt(s.get("low1_ms"), 2), _fmt(s.get("low1_fps")))))
            ft_rows.append(("Stutter events (spikes)", "%s  (%s frames = %.1f%%)" % (
                _fmt_int(s.get("n_stutters")), _fmt_int(s.get("n_spike_frames")), s.get("spike_pct", 0.0))))
            ft_rows.append(("Frames > 16.7ms / 33.3ms / 50ms",
                            "%s / %s / %s" % (_fmt_int(s.get("n_over_1667")), _fmt_int(s.get("n_over_3333")),
                                              _fmt_int(s.get("n_over_5000")))))
        if ft_rows:
            dpg.add_text("Frametime & FPS")
            self._kv_table(body, ft_rows, p, 1)

        ld_rows = []
        ld_meta = [
            ("cpu_load", "CPU load", "%"),
            ("gpu_load", "GPU load", "%"),
            ("cpu_power", "CPU power", " W"),
            ("gpu_power", "GPU power", " W"),
            ("cpu_temp", "CPU temp", " C"),
            ("gpu_temp", "GPU temp", " C"),
            ("gpu_core_clock", "GPU core clock", " MHz"),
            ("gpu_mem_clock", "GPU mem clock", " MHz"),
            ("cpu_mhz", "CPU clock", " MHz"),
            ("ram_used", "RAM used", " GB"),
            ("vram_used", "VRAM used", " GB"),
            ("swap_used", "Swap used", " GB"),
        ]
        for key, label, unit in ld_meta:
            st = s.get(key)
            if st:
                ld_rows.append((label, "avg %s  max %s" % (_fmt(st["avg"], 2, unit), _fmt(st["max"], 2, unit))))
        if ld_rows:
            dpg.add_text("Loads / Temps / Clocks / Memory (avg & max)")
            self._kv_table(body, ld_rows, p, 2)

        if cap.summary:
            srows = []
            for k, v in cap.summary.items():
                if v is None:
                    continue
                srows.append((k, _fmt(v, 4)))
            with dpg.collapsing_header(label="MangoHud summary values (%d)" % len(srows),
                                       default_open=False):
                self._kv_table(body, srows, p, 3)

        dpg.add_text("Bottleneck analysis")
        color = VERDICT_WARN
        if "Neither" in self.verdict:
            color = VERDICT_GOOD
        elif "heavily loaded" in self.verdict:
            color = VERDICT_BAD
        dpg.add_text(self.verdict, color=color, wrap=430)
        for pt in self.report_points:
            dpg.add_text(pt, bullet=True, wrap=430)

        dpg.add_spacer(height=6)
        dpg.add_text("Data quality caveats (cons of this capture)")
        for note in self.dq_notes:
            dpg.add_text(note, bullet=True, wrap=430, color=(200, 200, 210, 255))
        dpg.pop_container_stack()

# --------------------------------------------------------------------------
# Selftest (no GUI): validate parsing & stats against files
# --------------------------------------------------------------------------
def selftest(paths):
    if not paths:
        here = Path(__file__).resolve().parent
        paths = sorted(here.glob("mangoapp_*.csv"))
    for path in paths:
        path = str(path)
        if path.endswith("_summary.csv"):
            continue
        print("=" * 78)
        print("FILE:", path)
        try:
            cap = parse_capture(path)
        except Exception as exc:
            print("  PARSE ERROR:", exc)
            continue
        comp = find_companion_summary(path)
        if comp:
            cap.summary_path = comp
            cap.raw_summary, cap.summary = parse_summary(comp)
        stats = compute_stats(cap)
        verdict, points = build_bottleneck_report(cap, stats)
        print("  device:", {k: v for k, v in cap.device.items() if v})
        print("  rows: %d  duration: %.2fs" % (cap.n_rows, cap.duration_s))
        print("  metric columns:", ", ".join(cap.metric_header))
        print("  avg FPS: %.2f  p97: %.2f  min: %.2f  max: %.2f"
              % (stats.get("fps_avg", float("nan")), stats.get("fps_p97", float("nan")),
                 stats.get("fps_min", float("nan")), stats.get("fps_max", float("nan"))))
        print("  avg FT: %.2f ms  p99: %.2f  max: %.2f"
              % (stats.get("ft_avg_ms", float("nan")), stats.get("ft_p99_ms", float("nan")),
                 stats.get("ft_max_ms", float("nan"))))
        print("  0.1%% low: %.2f ms / %.2f FPS   1%% low: %.2f ms / %.2f FPS"
              % (stats.get("low0.1_ms", 0) or 0, stats.get("low0.1_fps", 0) or 0,
                 stats.get("low1_ms", 0) or 0, stats.get("low1_fps", 0) or 0))
        print("  stutters: %s  spikes: %s frames (%.2f%%)  worst: %.2f ms"
              % (stats.get("n_stutters"), stats.get("n_spike_frames"),
                 stats.get("spike_pct", 0), stats.get("worst_spike_ms", 0) or 0))
        print("  gpu_load avg/max: %s/%s   cpu_load avg/max: %s/%s"
              % (_fmt(stats.get("gpu_load") and stats["gpu_load"]["avg"]),
                 _fmt(stats.get("gpu_load") and stats["gpu_load"]["max"]),
                 _fmt(stats.get("cpu_load") and stats["cpu_load"]["avg"]),
                 _fmt(stats.get("cpu_load") and stats["cpu_load"]["max"])))
        print("  VERDICT:", verdict)
        for pt in points:
            print("    *", pt)
        print("  DATA-QUALITY NOTES:")
        for note in build_data_quality_notes(cap, stats):
            print("    -", note)
        if cap.summary:
            print("  MangoHud summary values:")
            for k, v in cap.summary.items():
                if v is not None:
                    print("    %-26s %s" % (k, _fmt(v, 4)))
        print()


def smoke_test(path=None):
    """Build the full UI, load a file, render a few frames, then close."""
    dpg.create_context()
    dpg.create_viewport(title="smoke", width=1280, height=820)
    app = ViewerApp.__new__(ViewerApp)
    app.cap = None
    app.stats = None
    app.verdict = None
    app.report_points = []
    app.dq_notes = []
    app.load_counter = 0
    app._build_static_ui()
    if path and Path(path).is_file():
        app.load(path)
    else:
        app._auto_load()
    dpg.setup_dearpygui()
    dpg.show_viewport()
    for _ in range(8):
        dpg.render_dearpygui_frame()
    dpg.destroy_context()
    print("SMOKE OK")


def main():
    args = sys.argv[1:]
    if "--selftest" in args:
        selftest([a for a in args if a != "--selftest"])
        return
    if "--smoke" in args:
        p = next((a for a in args if not a.startswith("-") and Path(a).suffix.lower() == ".csv"), None)
        smoke_test(p)
        return
    path = None
    for a in args:
        if not a.startswith("-") and Path(a).suffix.lower() == ".csv":
            path = a
            break
    ViewerApp(path)


if __name__ == "__main__":
    main()
