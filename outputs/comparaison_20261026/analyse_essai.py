"""Compare three motor experiments without changing their source CSV files."""
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
    dict(file="daq_log_20261022_150559.csv", label="22/10 : position 1, 4500 tr/min, frein 0,25 A, Id 4 A",
         rpm=4500, gear=1, brake_a=.25, id_target_a=4, color="#5279a5"),
    dict(file="daq_log_20261023_083421.csv", label="23/10 : position 2, 4500 tr/min, frein 0,15 A, Id 4 A",
         rpm=4500, gear=2, brake_a=.15, id_target_a=4, color="#c27426"),
    dict(file="daq_log_20261026_103931.csv", label="26/10 : position 2, 4000 tr/min, frein 0,20 A, Id 0 A",
         rpm=4000, gear=2, brake_a=.20, id_target_a=0, color="#0f766e"),
)


def first_crossing(data, threshold):
    match = data.loc[data.d6t_temp_c.ge(threshold), "elapsed_min"]
    return float(match.iloc[0]) if len(match) else None


def first_sustained_crossing(data, threshold, hold_min=1):
    start = None
    for elapsed, temperature in zip(data.elapsed_min, data.d6t_temp_c):
        if temperature < threshold:
            start = None
        elif start is None:
            start = float(elapsed)
        elif elapsed - start >= hold_min:
            return start
    return None


frames, summaries = [], []
for run in RUNS:
    source = ROOT / "datalogging" / "logs" / run["file"]
    data = pd.read_csv(source, sep=";").apply(pd.to_numeric, errors="raise")
    if data.empty or not np.isfinite(data.to_numpy()).all():
        raise ValueError(f"Empty or nonfinite input: {source}")
    intervals = data.stm32_time_ms.diff().dropna()
    if not intervals.gt(0).all():
        raise ValueError(f"Nonmonotonic timestamps: {source}")
    data["elapsed_min"] = (data.stm32_time_ms - data.stm32_time_ms.iloc[0]) / 60000
    initial = float(data.loc[data.elapsed_min.lt(1 / 60), "d6t_temp_c"].median())
    data["rise_c"] = data.d6t_temp_c - initial
    data["sampled_i2_a2"] = data.motor_id_a ** 2 + data.motor_iq_a ** 2
    data["sampled_norm_a"] = np.sqrt(data.sampled_i2_a2)
    duration = float(data.elapsed_min.iloc[-1])
    steady = data[data.elapsed_min.between(1, 30)]
    tail = data[data.elapsed_min.ge(duration - 5)]
    times = {}
    for minute in (5, 10, 15, 20, 25, 30):
        around = data[(data.elapsed_min - minute).abs().le(5 / 60)]
        times[str(minute)] = {
            key: float(around[key].median())
            for key in ("d6t_temp_c", "ds18b20_temp_c", "rise_c")
        }
    row = {
        "source": str(source), "protocol": run, "rows": len(data),
        "duration_min": duration, "initial_first_sample_c": float(data.d6t_temp_c.iloc[0]),
        "initial_median_first_second_c": initial,
        "temperature_at_times": times,
        "first_threshold_min": {str(value): first_crossing(data, value) for value in (40, 50, 55, 60, 65, 70)},
        "first_60c_held_for_60s_min": first_sustained_crossing(data, 60),
        "maximum_d6t_c": float(data.d6t_temp_c.max()),
        "final_sample_d6t_c": float(data.d6t_temp_c.iloc[-1]),
        "final_minute_mean_d6t_c": float(data.loc[data.elapsed_min.ge(duration - 1), "d6t_temp_c"].mean()),
        "last5_mean_d6t_c": float(tail.d6t_temp_c.mean()),
        "last5_range_d6t_c": [float(tail.d6t_temp_c.min()), float(tail.d6t_temp_c.max())],
        "last5_slope_c_min": float(np.polyfit(tail.elapsed_min, tail.d6t_temp_c, 1)[0]),
        "sample_intervals_ms": [float(intervals.min()), float(intervals.max())],
        "full_brake_first_s": float(data.loc[data.load_setpoint_a.ge(run["brake_a"] - .001), "elapsed_min"].iloc[0] * 60),
        "steady_min1_30": {
            "rows": len(steady), "rpm_mean": float(steady.motor_speed_mech_rpm.mean()),
            "rpm_std": float(steady.motor_speed_mech_rpm.std(ddof=0)),
            "rpm_range": [float(steady.motor_speed_mech_rpm.min()), float(steady.motor_speed_mech_rpm.max())],
            "iq_mean_a": float(steady.motor_iq_a.mean()),
            "id_mean_a": float(steady.motor_id_a.mean()),
            "id_std_a": float(steady.motor_id_a.std(ddof=0)),
            "id_range_a": [float(steady.motor_id_a.min()), float(steady.motor_id_a.max())],
            "mean_sampled_i2_a2": float(steady.sampled_i2_a2.mean()),
            "sampled_norm_equivalent_a": float(np.sqrt(steady.sampled_i2_a2.mean())),
            "brake_command_range_a": [float(steady.load_setpoint_a.min()), float(steady.load_setpoint_a.max())],
        },
        "maximum_rpm": float(data.motor_speed_mech_rpm.max()),
        "maximum_abs_iq_a": float(data.motor_iq_a.abs().max()),
        "maximum_sampled_norm_a": float(data.sampled_norm_a.max()),
        "stopped_samples_after_minute1": int(data.loc[data.elapsed_min.ge(1), "motor_speed_mech_rpm"].abs().lt(100).sum()),
        "fraction_speed_within_5percent_after_minute1": float(data.loc[data.elapsed_min.ge(1), "motor_speed_mech_rpm"].between(.95 * run["rpm"], 1.05 * run["rpm"]).mean()),
    }
    # Independent checks against csv/statistics, separate from pandas processing.
    with source.open(encoding="utf-8-sig", newline="") as stream:
        raw = list(csv.DictReader(stream, delimiter=";"))
    origin = float(raw[0]["stm32_time_ms"])
    common = [r for r in raw if 60000 <= float(r["stm32_time_ms"]) - origin <= 1800000]
    assert len(raw) == len(data) and len(common) == len(steady)
    expected_iq = statistics.mean(float(r["motor_iq_a"]) for r in common)
    expected_i2 = statistics.mean(float(r["motor_id_a"]) ** 2 + float(r["motor_iq_a"]) ** 2 for r in common)
    assert abs(expected_iq - row["steady_min1_30"]["iq_mean_a"]) < 1e-9
    assert abs(expected_i2 - row["steady_min1_30"]["mean_sampled_i2_a2"]) < 1e-9
    frames.append(data)
    summaries.append(row)

previous = summaries[1]["steady_min1_30"]
current = summaries[2]["steady_min1_30"]
comparison = {
    "iq_mean_ratio_vs_previous_position2": current["iq_mean_a"] / previous["iq_mean_a"],
    "sampled_i2_ratio_vs_previous_position2": current["mean_sampled_i2_a2"] / previous["mean_sampled_i2_a2"],
    "d6t_at30_difference_vs_previous_position2_c": summaries[2]["temperature_at_times"]["30"]["d6t_temp_c"] - summaries[1]["temperature_at_times"]["30"]["d6t_temp_c"],
    "notes": [
        "User confirmed blocked air outlets and unchanged D6T view of motor windings.",
        "Elapsed time starts at the first CSV row. Matched electrical statistics use minutes 1 through 30.",
        "Temperature at specified minutes is the median within plus/minus five seconds.",
        "The current target Id=0 is supported by the firmware change and the measured mean; instantaneous Id is not identically zero.",
        "10 Hz current samples do not capture complete PWM phase-current RMS or instantaneous protection peaks.",
        "Current-squared ratios concern a copper-loss proxy, not measured total motor heat or input power.",
        "DS18B20 is an external reference, not a validated ambient-air measurement.",
        "Speed, brake command and Id target changed together, so the experiment does not isolate the causal effect of disabling Id.",
        "CSV does not contain fault codes, DC bus voltage, actual brake current, shaft torque or power-board temperature.",
    ],
}
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "mesures_20261026.json").write_text(json.dumps({"runs": summaries, "comparison": comparison}, indent=2, ensure_ascii=False), encoding="utf-8")

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
    "axes.titlesize": 11, "axes.spines.top": False, "axes.spines.right": False,
    "axes.edgecolor": "#b0bac3", "text.color": "#24313c", "axes.labelcolor": "#24313c",
    "grid.color": "#e1e6eb", "savefig.facecolor": "white"})
fig, axes = plt.subplots(3, 2, figsize=(13.5, 11.8), sharex=True)
fig.subplots_adjust(left=.08, right=.97, bottom=.10, top=.81, hspace=.35, wspace=.23)
fig.suptitle("Essai du 26 octobre : 60 °C atteints en environ 14 min 20 s", x=.08, y=.975, ha="left", fontsize=16, fontweight="bold")
fig.text(.08, .944, "Position 2, 4000 tr/min, frein 0,20 A, consigne Id 0 A. Sorties d’air bouchées, visée D6T identique.", fontsize=10.5)
keys = ("d6t_temp_c", "rise_c", "motor_iq_a", "motor_id_a", "sampled_i2_a2", "ds18b20_temp_c")
for run, data in zip(RUNS, frames):
    smooth = data[list(keys)].rolling(201, center=True, min_periods=1).mean()
    for ax, key in zip(axes.flat, keys):
        mask = data.elapsed_min.ge(1) if key in ("motor_iq_a", "motor_id_a", "sampled_i2_a2") else np.ones(len(data), dtype=bool)
        ax.plot(data.loc[mask, "elapsed_min"], smooth.loc[mask, key], color=run["color"], lw=2, label=run["label"])
axes[0, 0].set(title="Température du bobinage mesurée par le D6T", ylabel="Température (°C)", ylim=(17, 72))
axes[0, 0].axhline(60, color="#657381", ls="--", lw=1)
axes[0, 0].axhline(70, color="#b85454", ls=":", lw=1)
axes[0, 0].text(33.3, 70.6, "Coupure D6T >70 °C", ha="right", fontsize=8.5, color="#a34c4c")
hit = summaries[2]["first_threshold_min"]["60"]
axes[0, 0].scatter([hit], [60], color=RUNS[2]["color"], s=28, zorder=4)
axes[0, 0].annotate("60 °C : 14 min 19 s", xy=(hit, 60), xytext=(4, 65), fontsize=9,
    arrowprops=dict(arrowstyle="->", color="#4a6365"))
axes[0, 1].set(title="Élévation depuis la température initiale", ylabel="D6T − D6T initial (°C)", ylim=(0, 50))
axes[1, 0].set(title="Iq mesuré, moyenne lissée", ylabel="Iq (A)", ylim=(0, 23))
axes[1, 1].set(title="Id mesuré, moyenne lissée", ylabel="Id (A)", ylim=(-1, 8))
axes[1, 1].axhline(0, color="#aeb7c0", ls=":", lw=.8)
axes[2, 0].set(title="Somme des carrés des courants enregistrés", ylabel="Moyenne de Id² + Iq² (A²)", ylim=(0, 450))
axes[2, 1].set(title="Référence externe DS18B20", ylabel="Température (°C)", ylim=(20, 26))
for ax in axes.flat:
    ax.set_xlim(0, 34)
    ax.axvline(30, color="#b4bdc5", ls=":", lw=.9)
    ax.grid(True)
for ax in axes[2]:
    ax.set_xlabel("Temps écoulé depuis le début du CSV (min)")
handles, labels = axes[0, 0].get_legend_handles_labels()
fig.legend(handles, labels, loc="upper left", bbox_to_anchor=(.072, .918), frameon=False, fontsize=9.5)
fig.text(.08, .052, "Courbes lissées sur 20 s. Courants comparés après la première minute. Le trait vertical repère 30 min.", fontsize=9, color="#586570")
fig.text(.08, .029, "Les trois paramètres vitesse, frein et Id ont changé ensemble. Id² + Iq² à 10 Hz ne mesure pas la puissance thermique totale.", fontsize=9, color="#586570")
for extension in ("png", "svg"):
    fig.savefig(OUT / f"comparaison_20261026.{extension}", dpi=170)
plt.close(fig)

for row in summaries:
    print(json.dumps({"label": row["protocol"]["label"], "temperature_at_times": row["temperature_at_times"],
        "first60_min": row["first_threshold_min"]["60"], "first60_held60s_min": row["first_60c_held_for_60s_min"],
        "steady_min1_30": row["steady_min1_30"], "maximum_d6t_c": row["maximum_d6t_c"]}, ensure_ascii=False))
print(json.dumps(comparison, ensure_ascii=False))
