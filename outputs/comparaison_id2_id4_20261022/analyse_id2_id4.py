"""Compare the two recorded Id settings; never alter the source CSV files."""
from pathlib import Path
import csv
import json
import statistics

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
RUNS = (
    ("daq_log_20261022_110036.csv", "Id demandé : 2 A", "#3568a0"),
    ("daq_log_20261022_150559.csv", "Id demandé : 4 A", "#bf5928"),
)
MEASUREMENTS = (
    "d6t_temp_c", "ds18b20_temp_c", "motor_speed_mech_rpm",
    "motor_id_a", "motor_iq_a", "motor_ud_v", "motor_uq_v",
    "load_setpoint_a", "id2_a2", "iq2_a2", "i2_a2",
)


def window_stats(frame):
    if len(frame) < 2:
        return None
    return {
        "n": int(len(frame)),
        **{key: float(frame[key].mean()) for key in MEASUREMENTS},
        "sampled_dq_norm_equivalent_a": float(np.sqrt(frame.i2_a2.mean())),
        "d6t_slope_c_min": float(np.polyfit(frame.elapsed_min, frame.d6t_temp_c, 1)[0]),
        "speed_std_rpm": float(frame.motor_speed_mech_rpm.std()),
        "id_std_a": float(frame.motor_id_a.std()),
        "iq_std_a": float(frame.motor_iq_a.std()),
    }


frames = []
summary = []
for name, label, color in RUNS:
    source = ROOT / "datalogging" / "logs" / name
    data = pd.read_csv(source, sep=";").apply(pd.to_numeric, errors="raise")
    if not np.isfinite(data.to_numpy()).all():
        raise ValueError(f"Nonfinite measurement in {source.name}")
    intervals = data.stm32_time_ms.diff().dropna()
    if not intervals.gt(0).all():
        raise ValueError(f"Nonmonotonic timestamp in {source.name}")
    data["elapsed_min"] = (data.stm32_time_ms - data.stm32_time_ms.iloc[0]) / 60000
    duration = float(data.elapsed_min.iloc[-1])
    initial = data[data.elapsed_min.lt(1 / 60)]
    t_initial = float(initial.d6t_temp_c.median())
    data["rise_c"] = data.d6t_temp_c - t_initial
    data["d6t_minus_ds18_c"] = data.d6t_temp_c - data.ds18b20_temp_c
    data["id2_a2"] = data.motor_id_a ** 2
    data["iq2_a2"] = data.motor_iq_a ** 2
    data["i2_a2"] = data.id2_a2 + data.iq2_a2
    data["norm_a"] = np.sqrt(data.i2_a2)
    times = {}
    for minute in (5, 10, 15, 20, 25, 30, 35, 40):
        if minute > duration:
            continue
        around = data[(data.elapsed_min - minute).abs().le(5 / 60)]
        times[str(minute)] = {
            "d6t_c": float(around.d6t_temp_c.median()),
            "rise_c": float(around.rise_c.median()),
            "ds18_c": float(around.ds18b20_temp_c.median()),
            "d6t_minus_ds18_c": float(around.d6t_minus_ds18_c.median()),
        }
    row = {
        "source": str(source), "label": label, "rows": len(data),
        "duration_min": duration, "initial_d6t_c": t_initial,
        "initial_ds18_c": float(initial.ds18b20_temp_c.median()),
        "final_minute_d6t_c": float(data.loc[data.elapsed_min.ge(duration - 1), "d6t_temp_c"].mean()),
        "max_d6t_c": float(data.d6t_temp_c.max()),
        "max_speed_rpm": float(data.motor_speed_mech_rpm.max()),
        "max_abs_iq_a": float(data.motor_iq_a.abs().max()),
        "max_sampled_norm_a": float(data.norm_a.max()),
        "timestamp_interval_ms": [float(intervals.min()), float(intervals.max())],
        "stopped_samples_after_first_minute": int((data.elapsed_min.ge(1) & data.motor_speed_mech_rpm.abs().lt(100)).sum()),
        "full_brake_first_s": float(data.loc[data.load_setpoint_a.ge(.249), "elapsed_min"].iloc[0] * 60),
        "first_10s_mean_id_ge_3_9_a_s": None,
        "times": times,
        "windows": {
            f"{left}_{right}_min": window_stats(data[data.elapsed_min.between(left, right)])
            for left, right in ((1, 20), (1, 40), (5, 15), (30, 40), (35, 40))
        },
        "last_5_min": window_stats(data[data.elapsed_min.ge(duration - 5)]),
        "last_10_min": window_stats(data[data.elapsed_min.ge(duration - 10)]),
        "first_threshold_min": {
            str(level): (float(data.loc[data.d6t_temp_c.ge(level), "elapsed_min"].iloc[0])
                         if data.d6t_temp_c.ge(level).any() else None)
            for level in (40, 45, 48, 50, 55, 60)
        },
        "first_temperature_rise_min": {
            str(level): (float(data.loc[data.rise_c.ge(level), "elapsed_min"].iloc[0])
                         if data.rise_c.ge(level).any() else None)
            for level in (10, 20, 25, 28)
        },
    }
    reached_id = data.motor_id_a.rolling(100, min_periods=100).mean().ge(3.9)
    if reached_id.any():
        row["first_10s_mean_id_ge_3_9_a_s"] = float(data.loc[reached_id, "elapsed_min"].iloc[0] * 60)

    # Independently reconcile headline numbers using the Python CSV reader.
    with source.open(encoding="utf-8-sig", newline="") as stream:
        raw = list(csv.DictReader(stream, delimiter=";"))
    assert len(raw) == len(data)
    independent_time = [(float(r["stm32_time_ms"]) - float(raw[0]["stm32_time_ms"])) / 60000 for r in raw]
    independent_start = statistics.median(float(r["d6t_temp_c"]) for r, t in zip(raw, independent_time) if t < 1 / 60)
    assert abs(independent_start - t_initial) < 1e-9
    common = [r for r, t in zip(raw, independent_time) if 30 <= t <= 40]
    for key in ("motor_id_a", "motor_iq_a"):
        assert abs(statistics.mean(float(r[key]) for r in common) - row["windows"]["30_40_min"][key]) < 1e-9
    independent_i2 = statistics.mean(float(r["motor_id_a"])**2 + float(r["motor_iq_a"])**2 for r in common)
    assert abs(independent_i2 - row["windows"]["30_40_min"]["i2_a2"]) < 1e-9
    frames.append((data, label, color))
    summary.append(row)

common_end = min(row["duration_min"] for row in summary)
for row, (data, _, _) in zip(summary, frames):
    row["matched_final_5_min"] = window_stats(data[data.elapsed_min.between(common_end - 5, common_end)])

grid = np.arange(1, 40 + 1 / 60, 1 / 60)
rise2 = np.interp(grid, frames[0][0].elapsed_min, frames[0][0].rise_c)
rise4 = np.interp(grid, frames[1][0].elapsed_min, frames[1][0].rise_c)
difference = rise4 - rise2
comparison = {
    "shared_duration_min": common_end,
    "rise_difference_4_minus_2_on_1s_grid_min1_40": {
        "mean_c": float(difference.mean()), "median_c": float(np.median(difference)),
        "mean_absolute_c": float(np.abs(difference).mean()),
    },
    "notes": [
        "Initial temperature: median of the first second of each CSV.",
        "Temperature at a given minute: median within plus/minus 5 seconds.",
        "Current comparison: identical elapsed-time windows, not different run endpoints.",
        "Squared-current metric uses logged 10 Hz samples and is not a PWM-bandwidth RMS measurement.",
        "DS18B20 is a measured external reference; its location and ambient representativeness are not established by the CSV.",
        "One run at each Id value: results do not isolate all environmental and load variations.",
    ],
}
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "mesures_id2_id4.json").write_text(json.dumps({"runs": summary, "comparison": comparison}, ensure_ascii=False, indent=2), encoding="utf-8")

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 10,
    "axes.titlesize": 11, "axes.spines.top": False, "axes.spines.right": False,
    "axes.edgecolor": "#acb5bf", "text.color": "#24313c",
    "axes.labelcolor": "#24313c", "grid.color": "#e1e6eb",
    "grid.linewidth": .7, "savefig.facecolor": "white",
})
fig, axes = plt.subplots(3, 2, figsize=(13.5, 11.8), sharex=True)
fig.subplots_adjust(left=.08, right=.97, bottom=.11, top=.85, hspace=.36, wspace=.23)
fig.suptitle("Essais Id = 2 A et Id = 4 A", x=.08, y=.975, ha="left", fontsize=18, fontweight="bold")
fig.text(.08, .943, "4500 tr/min, frein demandé 0,25 A, sorties d’air bouchées. Départs D6T : 20,3 °C et 22,25 °C.", fontsize=10.5)

id_plot_upper = 5.0
for data, label, color in frames:
    smooth = data[["d6t_temp_c", "rise_c", "motor_id_a", "motor_iq_a", "i2_a2", "ds18b20_temp_c"]].rolling(201, center=True, min_periods=1).mean()
    shown = data.elapsed_min.ge(1)
    id_plot_upper = max(id_plot_upper, float(smooth.loc[shown, "motor_id_a"].max()) + .35)
    for ax, key in zip(axes.flat, ("d6t_temp_c", "rise_c", "motor_iq_a", "motor_id_a", "i2_a2", "ds18b20_temp_c")):
        mask = shown if key in ("motor_iq_a", "motor_id_a", "i2_a2") else np.ones(len(data), dtype=bool)
        ax.plot(data.loc[mask, "elapsed_min"], smooth.loc[mask, key], color=color, lw=2, label=label)

axes[0, 0].axhline(60, color="#697580", ls="--", lw=1)
axes[0, 0].text(52, 60.5, "Objectif 60 °C", ha="right", fontsize=9, color="#697580")
axes[0, 0].set(title="Température D6T", ylabel="Température (°C)", ylim=(19, 63))
axes[0, 1].set(title="Élévation depuis le départ", ylabel="D6T − D6T initial (°C)", ylim=(0, 33))
axes[1, 0].set(title="Iq mesuré", ylabel="Iq (A)", ylim=(0, 12))
axes[1, 1].set(title="Id mesuré", ylabel="Id (A)", ylim=(0, id_plot_upper))
axes[1, 1].axhline(2, color="#b4bcc4", lw=.8, ls=":")
axes[1, 1].axhline(4, color="#b4bcc4", lw=.8, ls=":")
axes[2, 0].set(title="Somme des carrés des courants enregistrés", ylabel="Moyenne de Id² + Iq² (A²)", ylim=(0, 135))
axes[2, 1].set(title="Référence externe DS18B20", ylabel="Température (°C)", ylim=(21.5, 26))
for ax in axes.flat:
    ax.grid(True)
    ax.set_xlim(0, 54)
    ax.axvline(40, color="#b6bec6", lw=.8, ls=":")
for ax in axes[2]:
    ax.set_xlabel("Temps écoulé depuis le début du CSV (min)")
handles, labels = axes[0, 0].get_legend_handles_labels()
fig.legend(handles, labels, loc="upper left", bbox_to_anchor=(.072, .915), frameon=False, ncol=2)
fig.text(.08, .052, "Courbes lissées sur 20 s. Courants affichés après la première minute. Le trait vertical repère 40 min.", fontsize=9, color="#586570")
fig.text(.08, .030, "Sources : deux CSV du 22/10/2026, 11:00:36 et 15:05:59. La température initiale est la médiane de la première seconde.", fontsize=9, color="#586570")
for suffix in ("png", "svg"):
    fig.savefig(OUT / f"comparaison_id2_id4.{suffix}", dpi=170)
plt.close(fig)

for row in summary:
    print(json.dumps({
        "session": row["label"], "duration_min": row["duration_min"],
        "initial_d6t_c": row["initial_d6t_c"], "T20": row["times"]["20"], "T40": row["times"]["40"],
        "final_minute_d6t_c": row["final_minute_d6t_c"], "max_d6t_c": row["max_d6t_c"],
        "window_30_40": row["windows"]["30_40_min"], "matched_final_5_min": row["matched_final_5_min"],
        "last5_slope": row["last_5_min"]["d6t_slope_c_min"],
        "first_temperature_rise_min": row["first_temperature_rise_min"],
        "max_rpm": row["max_speed_rpm"], "max_sampled_norm_a": row["max_sampled_norm_a"],
        "stopped_samples_after_first_minute": row["stopped_samples_after_first_minute"],
    }, ensure_ascii=False, indent=2))
print(json.dumps(comparison, ensure_ascii=False, indent=2))
print("Graphique :", OUT / "comparaison_id2_id4.png")
