"""Compare four measured motor runs without modifying their source CSV files."""
from pathlib import Path
import json

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
RUNS = [
    ("daq_log_20261019_111311.csv", "19/10, 4500 tr/min, ouvert", 4500, "#3568a0"),
    ("daq_log_20261020_104648.csv", "20/10 matin, 4500 tr/min, bouché", 4500, "#d07b16"),
    ("daq_log_20261020_150621.csv", "20/10 après-midi, 2500 tr/min, bouché", 2500, "#37845b"),
    ("daq_log_20261022_110036.csv", "22/10, 4500 tr/min, bouché, Id = 2 A", 4500, "#ac3c65"),
]


def stats(frame):
    return {
        "n": len(frame),
        **{column: float(frame[column].mean()) for column in (
            "d6t_temp_c", "ds18b20_temp_c", "motor_speed_mech_rpm",
            "motor_iq_a", "motor_id_a", "i2_a2", "norm_a",
        )},
        "speed_std_rpm": float(frame.motor_speed_mech_rpm.std()),
        "id_std_a": float(frame.motor_id_a.std()),
        "iq_std_a": float(frame.motor_iq_a.std()),
        "temperature_slope_c_min": float(np.polyfit(frame.elapsed_min, frame.d6t_temp_c, 1)[0]),
    }


frames = []
summary = []
for name, label, target, color in RUNS:
    path = ROOT / "datalogging" / "logs" / name
    data = pd.read_csv(path, sep=";").apply(pd.to_numeric, errors="raise")
    if not np.isfinite(data.to_numpy()).all():
        raise ValueError(f"Missing or invalid measurement in {name}")
    delta_ms = data.stm32_time_ms.diff().dropna()
    if not (delta_ms > 0).all():
        raise ValueError(f"Nonmonotonic time in {name}")
    data["elapsed_min"] = (data.stm32_time_ms - data.stm32_time_ms.iloc[0]) / 60000.0
    duration = float(data.elapsed_min.iloc[-1])
    start_temperature = float(data.loc[data.elapsed_min < 1 / 60, "d6t_temp_c"].median())
    data["temperature_rise_c"] = data.d6t_temp_c - start_temperature
    data["i2_a2"] = data.motor_id_a**2 + data.motor_iq_a**2
    data["norm_a"] = np.sqrt(data.i2_a2)
    steady = (
        data.elapsed_min.ge(1)
        & data.motor_speed_mech_rpm.between(target * 0.95, target * 1.05)
        & data.load_setpoint_a.ge(0.249)
    )
    tail5 = data[steady & data.elapsed_min.ge(duration - 5)]
    tail10 = data[steady & data.elapsed_min.ge(duration - 10)]
    first20 = data[steady & data.elapsed_min.between(1, 20)]
    end1 = data[steady & data.elapsed_min.ge(duration - 1)]
    at20 = data.loc[(data.elapsed_min - 20).abs() <= 5 / 60, "d6t_temp_c"]
    row = {
        "file": name, "label": label, "target_rpm": target,
        "rows": len(data), "duration_min": duration,
        "start_temperature_c": start_temperature,
        "temperature_at_20min_c": float(at20.median()),
        "final_temperature_last_1min_c": float(end1.d6t_temp_c.mean()),
        "max_temperature_c": float(data.d6t_temp_c.max()),
        "max_speed_rpm": float(data.motor_speed_mech_rpm.max()),
        "max_abs_iq_a": float(data.motor_iq_a.abs().max()),
        "max_current_norm_a": float(data.norm_a.max()),
        "last5": stats(tail5), "last10": stats(tail10), "common_1_20min": stats(first20),
        "first_full_load_s": float(data.loc[data.load_setpoint_a.ge(.249), "elapsed_min"].iloc[0] * 60),
        "last_full_load_a": float(data.load_setpoint_a.iloc[-1]),
        "on_fraction_after_first_min": float((data.loc[data.elapsed_min.ge(1), "motor_speed_mech_rpm"].abs() > 100).mean()),
        "time_to_temperature_min": {
            str(level): (float(data.loc[data.d6t_temp_c.ge(level), "elapsed_min"].iloc[0])
                         if data.d6t_temp_c.ge(level).any() else None)
            for level in (40, 45, 48, 50, 55, 60)
        },
    }
    summary.append(row)
    frames.append((data, steady, color, label))

OUT.mkdir(parents=True, exist_ok=True)
(OUT / "mesures_comparatives.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
)

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 10,
    "axes.labelsize": 10, "axes.titlesize": 11,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.edgecolor": "#b3bbc3", "axes.labelcolor": "#26313b",
    "text.color": "#26313b", "xtick.color": "#4b5661", "ytick.color": "#4b5661",
    "grid.color": "#e4e8ec", "grid.linewidth": 0.7,
    "savefig.facecolor": "white",
})
fig, axes = plt.subplots(2, 2, figsize=(14, 9.5), sharex=True)
fig.subplots_adjust(left=.075, right=.975, bottom=.12, top=.80, hspace=.30, wspace=.22)
fig.suptitle("Comparaison des quatre essais moteur", x=.075, y=.97, ha="left", fontsize=17, fontweight="bold")
fig.text(.075, .925, "Frein demandé : 0,25 A. Le nouvel essai dure 53,2 min ; l'ancien essai à 4500 tr/min bouché dure 20,4 min.", fontsize=10.5)

for data, steady, color, label in frames:
    smooth = data[["d6t_temp_c", "temperature_rise_c", "motor_iq_a", "motor_id_a"]].rolling(201, center=True, min_periods=1).mean()
    axes[0, 0].plot(data.elapsed_min, smooth.d6t_temp_c, color=color, lw=2.1, label=label)
    axes[0, 1].plot(data.elapsed_min, smooth.temperature_rise_c, color=color, lw=2.1)
    # Exclude the launch transient in current plots; retain measured currents.
    shown = data.elapsed_min.ge(1)
    axes[1, 0].plot(data.loc[shown, "elapsed_min"], smooth.loc[shown, "motor_iq_a"], color=color, lw=1.7)
    axes[1, 1].plot(data.loc[shown, "elapsed_min"], smooth.loc[shown, "motor_id_a"], color=color, lw=1.7)

axes[0, 0].axhline(60, color="#68727d", ls="--", lw=1.1)
axes[0, 0].text(53.5, 60.4, "Objectif 60 °C", ha="right", fontsize=9, color="#68727d")
axes[0, 0].set(title="Température mesurée par le D6T", ylabel="Température (°C)", ylim=(19, 63))
axes[0, 1].set(title="Élévation depuis la température initiale", ylabel="D6T − D6T initial (°C)", ylim=(0, 34))
axes[1, 0].set(title="Iq mesuré", ylabel="Iq (A)", ylim=(0, 12))
axes[1, 1].set(title="Id mesuré", ylabel="Id (A)", ylim=(-1.8, 3.8))
axes[1, 1].axhline(0, color="#9aa3ad", lw=.8)
axes[1, 1].axhline(2, color="#9aa3ad", lw=.8, ls=":")

for ax in axes.flat:
    ax.grid(True)
    ax.axvline(20, color="#b7bec6", lw=.9, ls=":")
    ax.set_xlim(0, 54)
for ax in axes[1]:
    ax.set_xlabel("Temps écoulé depuis le début du CSV (min)")

handles, labels = axes[0, 0].get_legend_handles_labels()
fig.legend(handles, labels, loc="upper left", bbox_to_anchor=(.065, .905), ncol=2, frameon=False, fontsize=9.8, columnspacing=2)

data_open = frames[0][0]
peak = data_open.d6t_temp_c.idxmax()
axes[0, 0].annotate(
    "Obstructions manuelles le 19/10",
    xy=(data_open.elapsed_min.iloc[peak], data_open.d6t_temp_c.iloc[peak]),
    xytext=(25, 54), fontsize=8.5, color=RUNS[0][3],
    arrowprops={"arrowstyle": "-", "lw": .8, "color": RUNS[0][3]},
)
fig.text(.075, .055, "Courbes lissées sur 20 s. Courants représentés après la première minute. Le trait vertical repère 20 min.", fontsize=9, color="#596673")
fig.text(.075, .030, "Les CSV mesurent Id et Iq, pas la limite configurée ni la puissance mécanique. Sources : les quatre enregistrements fournis.", fontsize=9, color="#596673")
for suffix in ("png", "svg"):
    fig.savefig(OUT / f"comparaison_4_sessions.{suffix}", dpi=180)
plt.close(fig)

for row in summary:
    print(json.dumps({
        "session": row["label"], "duration_min": row["duration_min"],
        "T20": row["temperature_at_20min_c"], "Tfin_1min": row["final_temperature_last_1min_c"],
        "Tmax": row["max_temperature_c"], "Iq_1_20": row["common_1_20min"]["motor_iq_a"],
        "Id_1_20": row["common_1_20min"]["motor_id_a"],
        "Iq_tail5": row["last5"]["motor_iq_a"], "Id_tail5": row["last5"]["motor_id_a"],
        "slope_tail5_C_min": row["last5"]["temperature_slope_c_min"],
        "norm_max_A": row["max_current_norm_a"],
        "max_rpm": row["max_speed_rpm"],
    }, ensure_ascii=False))
print("Graphique :", OUT / "comparaison_4_sessions.png")
