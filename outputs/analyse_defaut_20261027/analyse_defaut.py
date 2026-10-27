"""Read-only diagnosis of the 27 October motor test; no source CSV is changed."""
from pathlib import Path
import csv
import hashlib
import json
import math

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
SOURCE = ROOT / "datalogging/logs/daq_log_20261027_092837.csv"
BASELINE = ROOT / "datalogging/old_test_logs/daq_log_20261026_103931.csv"
FAULT_MS = 406684  # Supplied serial #FAULT record, MCU clock.


def load(path):
    data = pd.read_csv(path, sep=";")
    assert len(data) and np.isfinite(data.to_numpy()).all()
    assert data.stm32_time_ms.diff().dropna().eq(100).all()
    data["norm_a"] = np.hypot(data.motor_id_a, data.motor_iq_a)
    return data


before_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
data = load(SOURCE)
baseline = load(BASELINE)
powered = data[(data.stm32_time_ms < FAULT_MS) & data.motor_speed_mech_rpm.abs().gt(100)]
pre = data[data.stm32_time_ms < FAULT_MS].iloc[-1]
post = data[data.stm32_time_ms > FAULT_MS].iloc[0]
last_window = powered[powered.stm32_time_ms.ge(399957)]
changes = powered[powered.load_setpoint_a.diff().abs().gt(.0001)]
steady_baseline = baseline[(baseline.stm32_time_ms-baseline.stm32_time_ms.iloc[0]).between(60000, 1800000)]

report = {
    "source": str(SOURCE), "baseline": str(BASELINE),
    "source_sha256": before_hash, "rows": len(data), "period_ms": 100,
    "fault_from_console": {"t_ms": FAULT_MS, "mc_now": "0x0040", "speed_rpm": 3183.701,
                           "iq_a": 6.997, "id_a": .840, "brake_command_a": .196},
    "first_powered_sample_ms": int(powered.stm32_time_ms.iloc[0]),
    "seconds_first_powered_sample_to_fault": float((FAULT_MS-powered.stm32_time_ms.iloc[0])/1000),
    "last_pre_fault_sample": pre.to_dict(), "first_post_fault_sample": post.to_dict(),
    "last_pre_fault_sample_age_ms": int(FAULT_MS-pre.stm32_time_ms),
    "max_iq_sampled_a": float(powered.motor_iq_a.max()),
    "max_norm_sampled_a": float(powered.norm_a.max()),
    "max_speed_sampled_rpm": float(powered.motor_speed_mech_rpm.max()),
    "max_temperature_while_powered_c": float(powered.d6t_temp_c.max()),
    "last_window_ms": [399957, FAULT_MS],
    "last_window_rpm_range": [float(last_window.motor_speed_mech_rpm.min()), float(last_window.motor_speed_mech_rpm.max())],
    "last_window_id_range_a": [float(last_window.motor_id_a.min()), float(last_window.motor_id_a.max())],
    "last_window_iq_range_a": [float(last_window.motor_iq_a.min()), float(last_window.motor_iq_a.max())],
    "last_load_changes": changes.tail(4).to_dict("records"),
    "baseline_duration_s": float((baseline.stm32_time_ms.iloc[-1]-baseline.stm32_time_ms.iloc[0])/1000),
    "baseline_max_iq_sampled_a": float(baseline.motor_iq_a.max()),
    "baseline_mean_iq_min1_30_a": float(steady_baseline.motor_iq_a.mean()),
    "baseline_max_norm_sampled_a": float(baseline.norm_a.max()),
    "notes": [
        "Motor fields are explicitly zeroed by firmware outside RUN; zero does not establish physical stop time.",
        "10 Hz samples are not a high-speed peak-current measurement and cannot exclude short overcurrent events.",
        "Brake values are commands, not measurements of actual excitation current or torque.",
        "The CSV does not contain speed references, bus voltage, board temperature or a latched hardware fault source.",
        "The final 0.195 to 0.196 A brake step is near the fault, but its proximity does not prove causation.",
        "User confirmed unchanged gear 2, blocked outlets and D6T view relative to 26 October.",
    ],
}

# Cross-check headline values using csv/math, independently of pandas/numpy operations.
with SOURCE.open(encoding="utf-8-sig", newline="") as stream:
    raw = [{k: float(v) for k, v in row.items()} for row in csv.DictReader(stream, delimiter=";")]
raw_powered = [r for r in raw if r["stm32_time_ms"] < FAULT_MS and abs(r["motor_speed_mech_rpm"]) > 100]
assert len(raw) == len(data)
assert max(r["motor_iq_a"] for r in raw_powered) == report["max_iq_sampled_a"]
assert math.isclose(max(math.hypot(r["motor_id_a"], r["motor_iq_a"]) for r in raw_powered), report["max_norm_sampled_a"])
assert max(r["d6t_temp_c"] for r in raw_powered) == report["max_temperature_while_powered_c"]

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.spines.top": False,
                     "axes.spines.right": False, "axes.titleweight": "bold"})
fig, axs = plt.subplots(2, 2, figsize=(13, 8.8))
fig.subplots_adjust(left=.075, right=.98, bottom=.13, top=.87, hspace=.52, wspace=.27)
fig.suptitle("Essai du 27/10/2026 : arr\u00eat mat\u00e9riel 0x0040", fontsize=18, fontweight="bold")
elapsed = (data.stm32_time_ms-data.stm32_time_ms.iloc[0])/60000
fault_min = (FAULT_MS-data.stm32_time_ms.iloc[0])/60000
first_min = (powered.stm32_time_ms.iloc[0]-data.stm32_time_ms.iloc[0])/60000
axs[0, 0].plot(elapsed, data.d6t_temp_c, color="#0f766e", lw=1.6)
axs[0, 0].axvspan(first_min, fault_min, color="#0f766e", alpha=.08, label="Moteur en RUN (mesures)")
axs[0, 0].axvline(fault_min, color="#bc3434", ls="--", label="D\u00e9faut")
axs[0, 0].set(title="Temp\u00e9rature du bobinage mesur\u00e9e par D6T", ylabel="Temp\u00e9rature (\u00b0C)", xlabel="Minutes depuis la premi\u00e8re ligne CSV")
axs[0, 0].legend(loc="lower right", fontsize=9)

zoom = powered[powered.stm32_time_ms.ge(FAULT_MS-20000)].copy()
x = (zoom.stm32_time_ms-FAULT_MS)/1000
axs[0, 1].plot(x, zoom.motor_speed_mech_rpm, color="#315caa", lw=1, marker=".", ms=2)
axs[0, 1].set(title="Vitesse estim\u00e9e : oscillations avant l'arr\u00eat", ylabel="Vitesse (tr/min)")
axs[1, 0].step(x, zoom.load_setpoint_a, where="post", color="#b77922", lw=1.7)
axs[1, 0].set(title="Frein : commande envoy\u00e9e au TB-200S", ylabel="Consigne de frein (A)", ylim=(.08, .21))
axs[1, 1].plot(x, zoom.motor_iq_a, label="Iq mesur\u00e9", color="#0f766e", lw=1.1)
axs[1, 1].plot(x, zoom.motor_id_a, label="Id mesur\u00e9 (consigne 0 A)", color="#9755a5", lw=1)
axs[1, 1].set(title="Courants enregistr\u00e9s \u00e0 10 Hz", ylabel="Courant (A)", ylim=(-10, 15))
axs[1, 1].legend(loc="upper left", fontsize=9)
for ax in (axs[0, 1], axs[1, 0], axs[1, 1]):
    ax.axvline(0, color="#bc3434", ls="--", lw=1.2)
    ax.set_xlim(-20, .6)
    ax.set_xlabel("Secondes avant le d\u00e9faut (t_ms = 406684)")
for ax in axs.flat:
    ax.grid(alpha=.2)
fig.text(.5, .015, "Dernier \u00e9chantillon : 27 ms avant le d\u00e9faut. Les pointes rapides et la source exacte du break ne sont pas enregistr\u00e9es.",
         ha="center", fontsize=10, color="#454545")
for ext in ("png", "svg"):
    fig.savefig(OUT / f"analyse_defaut_20261027.{ext}", dpi=170, facecolor="white")
plt.close(fig)
assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == before_hash
(OUT / "mesures_defaut.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
print(json.dumps({k: v for k, v in report.items() if k not in ("notes", "last_load_changes")}, indent=2))
