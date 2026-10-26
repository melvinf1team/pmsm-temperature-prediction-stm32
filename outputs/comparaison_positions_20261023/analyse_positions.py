"""Read-only comparison of the recorded position 1 and position 2 motor tests."""
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
    ("daq_log_20261022_150559.csv", "22/10, position 1, frein 0,25 A", .25, "#3568a0"),
    ("daq_log_20261023_083421.csv", "23/10, position 2, frein 0,15 A", .15, "#bf5928"),
)
COLUMNS = ("d6t_temp_c", "ds18b20_temp_c", "motor_speed_mech_rpm", "motor_id_a", "motor_iq_a", "load_setpoint_a", "id2_a2", "iq2_a2", "i2_a2", "d6t_minus_ds18_c")


def stats(data):
    return {
        "n": len(data),
        **{column: float(data[column].mean()) for column in COLUMNS},
        "sampled_dq_norm_equivalent_a": float(np.sqrt(data.i2_a2.mean())),
        "speed_std_rpm": float(data.motor_speed_mech_rpm.std()),
        "id_std_a": float(data.motor_id_a.std()),
        "iq_std_a": float(data.motor_iq_a.std()),
        "d6t_slope_c_min": float(np.polyfit(data.elapsed_min, data.d6t_temp_c, 1)[0]),
    }


frames = []
summary = []
for name, label, load, color in RUNS:
    source = ROOT / "datalogging" / "logs" / name
    data = pd.read_csv(source, sep=";").apply(pd.to_numeric, errors="raise")
    if not np.isfinite(data.to_numpy()).all():
        raise ValueError(f"Nonfinite data in {name}")
    intervals = data.stm32_time_ms.diff().dropna()
    if not intervals.gt(0).all():
        raise ValueError(f"Nonmonotonic timestamps in {name}")
    data["elapsed_min"] = (data.stm32_time_ms - data.stm32_time_ms.iloc[0]) / 60000
    initial = data[data.elapsed_min.lt(1 / 60)]
    initial_c = float(initial.d6t_temp_c.median())
    duration = float(data.elapsed_min.iloc[-1])
    data["rise_c"] = data.d6t_temp_c - initial_c
    data["id2_a2"] = data.motor_id_a ** 2
    data["iq2_a2"] = data.motor_iq_a ** 2
    data["i2_a2"] = data.id2_a2 + data.iq2_a2
    data["norm_a"] = np.sqrt(data.i2_a2)
    data["d6t_minus_ds18_c"] = data.d6t_temp_c - data.ds18b20_temp_c
    after_start = data[data.elapsed_min.ge(1)]
    times = {}
    for minute in (5, 10, 15, 20, 25, 30):
        around = data[(data.elapsed_min - minute).abs().le(5 / 60)]
        times[str(minute)] = {column: float(around[column].median()) for column in ("d6t_temp_c", "ds18b20_temp_c", "rise_c", "d6t_minus_ds18_c")}
    row = {
        "source": str(source), "label": label, "requested_load_a": load,
        "rows": len(data), "duration_min": duration,
        "initial_d6t_c": initial_c, "initial_ds18_c": float(initial.ds18b20_temp_c.median()),
        "max_d6t_c": float(data.d6t_temp_c.max()),
        "final_d6t_c": float(data.d6t_temp_c.iloc[-1]),
        "final_minute_d6t_c": float(data.loc[data.elapsed_min.ge(duration - 1), "d6t_temp_c"].mean()),
        "max_speed_rpm": float(data.motor_speed_mech_rpm.max()),
        "max_abs_iq_a": float(data.motor_iq_a.abs().max()),
        "max_sampled_norm_a": float(data.norm_a.max()),
        "max_abs_id_a": float(data.motor_id_a.abs().max()),
        "timestamp_intervals_ms": [float(intervals.min()), float(intervals.max())],
        "stopped_samples_after_first_minute": int(after_start.motor_speed_mech_rpm.abs().lt(100).sum()),
        "speed_within_5pct_fraction_after_first_minute": float(after_start.motor_speed_mech_rpm.between(4275, 4725).mean()),
        "full_requested_load_first_s": float(data.loc[data.load_setpoint_a.ge(load - .001), "elapsed_min"].iloc[0] * 60),
        "times": times,
        "windows": {f"{a}_{b}_min": stats(data[data.elapsed_min.between(a, b)]) for a, b in ((1, 20), (1, 30), (20, 30), (25, 30))},
        "last5": stats(data[data.elapsed_min.ge(duration - 5)]),
        "first_temperature_min": {str(level): (float(data.loc[data.d6t_temp_c.ge(level), "elapsed_min"].iloc[0]) if data.d6t_temp_c.ge(level).any() else None) for level in (40, 45, 48, 50, 55, 60)},
    }
    # Independent reconciliation of the quantities used in the final comparison.
    with source.open(encoding="utf-8-sig", newline="") as stream:
        raw = list(csv.DictReader(stream, delimiter=";"))
    t0 = float(raw[0]["stm32_time_ms"])
    comparable = [r for r in raw if 1 <= (float(r["stm32_time_ms"]) - t0) / 60000 <= 30]
    assert len(raw) == len(data)
    for column in ("motor_id_a", "motor_iq_a", "motor_speed_mech_rpm"):
        assert abs(statistics.mean(float(r[column]) for r in comparable) - row["windows"]["1_30_min"][column]) < 1e-9
    independent_i2 = statistics.mean(float(r["motor_id_a"]) ** 2 + float(r["motor_iq_a"]) ** 2 for r in comparable)
    assert abs(independent_i2 - row["windows"]["1_30_min"]["i2_a2"]) < 1e-9
    frames.append((data, label, color))
    summary.append(row)

common_end = min(row["duration_min"] for row in summary)
for row, (data, _, _) in zip(summary, frames):
    row["matched_last5"] = stats(data[data.elapsed_min.between(common_end - 5, common_end)])
baseline = summary[0]["windows"]["1_30_min"]
new = summary[1]["windows"]["1_30_min"]
comparison = {
    "common_end_min": common_end,
    "iq_mean_change_percent_min1_30": (new["motor_iq_a"] / baseline["motor_iq_a"] - 1) * 100,
    "sampled_i2_change_percent_min1_30": (new["i2_a2"] / baseline["i2_a2"] - 1) * 100,
    "copper_loss_increment_if_phase_Rs_0_01_ohm_and_peak_dq_convention_w": 1.5 * .01 * (new["i2_a2"] - baseline["i2_a2"]),
    "new_temperature_shortfall_at30min_c": 60 - summary[1]["times"]["30"]["d6t_temp_c"],
    "notes": [
        "Protocol supplied by user: 4500 motor rpm, Id setpoint 4 A, Iq ceiling 28 A, total ceiling 30 A.",
        "User confirmed blocked air outlets and unchanged D6T view of winding copper.",
        "TB-200S values are commands; actual brake current and shaft torque are not logged.",
        "Temperature at each minute is the median within plus/minus five seconds; initial temperature is median of first second.",
        "DS18B20 is an external measured reference, not a validated ambient-air measurement.",
        "10 Hz samples do not capture the full PWM current RMS; no input DC power or mechanical shaft power is recorded.",
        "The optional copper-loss estimate assumes the configured resistance is the actual phase resistance; it is not measured total motor heating.",
    ],
}
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "mesures_positions.json").write_text(json.dumps({"runs": summary, "comparison": comparison}, ensure_ascii=False, indent=2), encoding="utf-8")

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.titlesize": 11, "axes.spines.top": False, "axes.spines.right": False, "axes.edgecolor": "#aeb7c0", "text.color": "#24313c", "axes.labelcolor": "#24313c", "grid.color": "#e1e6eb", "grid.linewidth": .7, "savefig.facecolor": "white"})
fig, axes = plt.subplots(3, 2, figsize=(13.5, 11.8), sharex=True)
fig.subplots_adjust(left=.08, right=.97, bottom=.11, top=.85, hspace=.36, wspace=.23)
fig.suptitle("Comparaison des positions 1 et 2 de la perceuse", x=.08, y=.975, ha="left", fontsize=17, fontweight="bold")
fig.text(.08, .942, "4500 tr/min moteur, Id demandé 4 A, sorties d’air bouchées, visée D6T identique.", fontsize=10.5)
fig.text(.08, .919, "Position 1 : frein 0,25 A ; position 2 : frein 0,15 A. Les conditions thermiques de départ diffèrent.", fontsize=10)
bounds = {"motor_iq_a": [0, 12], "motor_id_a": [0, 5], "i2_a2": [0, 150]}
keys = ("d6t_temp_c", "rise_c", "motor_iq_a", "motor_id_a", "i2_a2", "ds18b20_temp_c")
for data, label, color in frames:
    smooth = data[list(keys)].rolling(201, center=True, min_periods=1).mean()
    for ax, key in zip(axes.flat, keys):
        mask = data.elapsed_min.ge(1) if key in bounds else np.ones(len(data), dtype=bool)
        ax.plot(data.loc[mask, "elapsed_min"], smooth.loc[mask, key], lw=2, color=color, label=label)
        if key in bounds:
            bounds[key][0] = min(bounds[key][0], float(smooth.loc[mask, key].min()) - .3)
            bounds[key][1] = max(bounds[key][1], float(smooth.loc[mask, key].max()) + .4)
axes[0, 0].set(title="Température mesurée par le D6T", ylabel="Température (°C)", ylim=(20, 63))
axes[0, 0].axhline(60, color="#697580", ls="--", lw=1)
axes[0, 0].text(42, 60.5, "Objectif 60 °C", ha="right", fontsize=9, color="#697580")
axes[0, 1].set(title="Élévation depuis le départ", ylabel="D6T − D6T initial (°C)", ylim=(0, 32))
axes[1, 0].set(title="Iq mesuré", ylabel="Iq (A)", ylim=bounds["motor_iq_a"])
axes[1, 1].set(title="Id mesuré", ylabel="Id (A)", ylim=bounds["motor_id_a"])
axes[1, 1].axhline(4, color="#aeb7c0", ls=":", lw=.8)
axes[2, 0].set(title="Somme des carrés des courants enregistrés", ylabel="Moyenne de Id² + Iq² (A²)", ylim=bounds["i2_a2"])
axes[2, 1].set(title="Référence externe DS18B20", ylabel="Température (°C)", ylim=(21, 26))
for ax in axes.flat:
    ax.set_xlim(0, 44)
    ax.axvline(30, color="#b4bdc5", ls=":", lw=.9)
    ax.grid(True)
for ax in axes[2]:
    ax.set_xlabel("Temps écoulé depuis le début du CSV (min)")
handles, labels = axes[0, 0].get_legend_handles_labels()
fig.legend(handles, labels, loc="upper left", bbox_to_anchor=(.072, .897), ncol=2, frameon=False, fontsize=9.5)
fig.text(.08, .052, "Courbes lissées sur 20 s. Courants affichés après la première minute. Le trait vertical repère l’objectif de 30 min.", fontsize=9, color="#586570")
fig.text(.08, .030, "Sources : CSV du 22/10/2026 à 15:05:59 et du 23/10/2026 à 08:34:21. Température initiale : médiane de la première seconde.", fontsize=9, color="#586570")
for extension in ("png", "svg"):
    fig.savefig(OUT / f"comparaison_positions.{extension}", dpi=170)
plt.close(fig)
for row in summary:
    print(json.dumps({"label": row["label"], "duration_min": row["duration_min"], "initial_d6t_c": row["initial_d6t_c"], "at30": row["times"]["30"], "max_d6t_c": row["max_d6t_c"], "final_minute_d6t_c": row["final_minute_d6t_c"], "current_window_1_30": row["windows"]["1_30_min"], "matched_last5": row["matched_last5"], "max_rpm": row["max_speed_rpm"], "max_sampled_norm_a": row["max_sampled_norm_a"], "full_requested_load_first_s": row["full_requested_load_first_s"], "stopped_samples_after_first_minute": row["stopped_samples_after_first_minute"]}, ensure_ascii=False, indent=2))
print(json.dumps(comparison, ensure_ascii=False, indent=2))
