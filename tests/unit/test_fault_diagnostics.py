from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

DATALOGGING_DIR = Path(__file__).resolve().parents[2] / "datalogging"
if str(DATALOGGING_DIR) not in sys.path:
    sys.path.insert(0, str(DATALOGGING_DIR))

from fault_diagnostics import (
    FAULT_VERDICT_FOC_LATE,
    FAULT_VERDICT_FOC_OVERRUN,
    FAULT_VERDICT_FOC_SLOW,
    FAULT_VERDICT_GLITCH,
    FAULT_VERDICT_NO_BREAK,
    FAULT_VERDICT_NO_DIAG,
    FaultReportCollector,
    diagnose_fault_source,
    fault_masks,
    format_fault_report,
)

# Lines of the 27 October 2026 random-profile session (firmware without FOC timing).
SESSION_FAULT = (
    "#FAULT,MCSDK_FAULT,t_ms=450777,speed_mrpm=3485858,speed_limit_rpm=5000,iq_ma=-39,"
    "id_ma=-27,hard_ma=30000,load_ma=177,mc_now=0x0000,mc_occurred=0x0001"
)
SESSION_DIAG = (
    "#FAULT_DIAG,break=0,t_break_ms=0,t_now_ms=450777,irq_count=0,tim_sr=0x0000,bkin_low=0,"
    "comp=0x0,bkin_now_low=0,comp_now=0x0,peak_run_da=271,peak_recent_da=167,ocp_da=1100,"
    "vbus_dv=180,trace_n=0,trace_period_us=62"
)
OVERSPEED_FAULT = "#FAULT,OVERSPEED,t_ms=1,mc_now=0x0000,mc_occurred=0x0000"


def timed_diag(**overrides) -> str:
    """``SESSION_DIAG`` with the FOC timing fields of the current firmware."""
    fields = {
        "foc_period_ns": "62494", "foc_max_ns": "31000", "foc_late_max_ns": "13200",
        "foc_overruns": "1", "foc_overrun_ns": "30500", "foc_overrun_late_ns": "13200",
        "quiet_timeouts": "0",
    }
    fields.update(overrides)
    return SESSION_DIAG + "".join(f",{key}={value}" for key, value in fields.items())


class FocOverrunTests(unittest.TestCase):
    def test_masks_are_read_from_the_fault_line(self) -> None:
        self.assertEqual(fault_masks(SESSION_FAULT), (0, 1))
        self.assertEqual(fault_masks("#FAULT,LOAD_DAC"), (0, 0))

    def test_session_without_timing_is_an_foc_overrun(self) -> None:
        verdict, details = diagnose_fault_source(SESSION_DIAG, (), SESSION_FAULT)
        self.assertEqual(verdict, FAULT_VERDICT_FOC_OVERRUN)
        self.assertTrue(any("flash the current firmware" in line for line in details))

    def test_late_start_is_identified(self) -> None:
        verdict, details = diagnose_fault_source(timed_diag(), (), SESSION_FAULT)
        self.assertEqual(verdict, FAULT_VERDICT_FOC_LATE)
        self.assertTrue(any("started 13.2 us late" in line for line in details))
        self.assertTrue(any("start delayed up to 13.2 us (period 62.5 us)" in line for line in details))

    def test_on_time_start_means_a_slow_computation(self) -> None:
        verdict, _ = diagnose_fault_source(
            timed_diag(foc_overrun_late_ns="300", foc_overrun_ns="61000"), (), SESSION_FAULT)
        self.assertEqual(verdict, FAULT_VERDICT_FOC_SLOW)

    def test_overrun_without_capture_stays_generic(self) -> None:
        verdict, _ = diagnose_fault_source(timed_diag(foc_overruns="0"), (), SESSION_FAULT)
        self.assertEqual(verdict, FAULT_VERDICT_FOC_OVERRUN)

    def test_dropped_one_wire_slots_are_reported(self) -> None:
        _, details = diagnose_fault_source(timed_diag(quiet_timeouts="3"), (), SESSION_FAULT)
        self.assertTrue(any("3 DS18B20 1-Wire slot(s)" in line for line in details))

    def test_other_application_stops_keep_the_no_break_verdict(self) -> None:
        verdict, _ = diagnose_fault_source(timed_diag(foc_overruns="0"), (), OVERSPEED_FAULT)
        self.assertEqual(verdict, FAULT_VERDICT_NO_BREAK)
        verdict, _ = diagnose_fault_source(SESSION_DIAG)
        self.assertEqual(verdict, FAULT_VERDICT_NO_BREAK)


class CollectorTests(unittest.TestCase):
    def test_report_without_trace_completes_on_the_diag_line(self) -> None:
        collector = FaultReportCollector()
        self.assertFalse(collector.feed(SESSION_FAULT))
        self.assertTrue(collector.active)
        self.assertTrue(collector.feed(timed_diag()))
        self.assertEqual(collector.diagnose()[0], FAULT_VERDICT_FOC_LATE)
        self.assertEqual(collector.raw_lines(), [SESSION_FAULT, timed_diag()])

    def test_break_report_waits_for_its_trace(self) -> None:
        collector = FaultReportCollector()
        collector.feed("#FAULT,MCSDK_FAULT,mc_now=0x0040,mc_occurred=0x0040")
        diag = SESSION_DIAG.replace("break=0", "break=1").replace("trace_n=0", "trace_n=16")
        self.assertFalse(collector.feed(diag))
        self.assertFalse(collector.feed("#FAULT_TRACE,-16," + ",".join(["120:-60:-60:180"] * 8)))
        with self.assertRaises(ValueError):
            collector.feed("#FAULT_TRACE,-8,120:-60")
        self.assertTrue(collector.feed("#FAULT_TRACE,-8," + ",".join(["120:-60:-60:180"] * 8)))
        self.assertEqual(collector.diagnose()[0], FAULT_VERDICT_GLITCH)

    def test_incomplete_trace_is_reported(self) -> None:
        collector = FaultReportCollector()
        collector.feed(SESSION_DIAG.replace("break=0", "break=1").replace("trace_n=0", "trace_n=16"))
        _, details = collector.diagnose()
        self.assertIn("Trace incomplete: 0/16 samples received.", details)

    def test_firmware_without_diagnostic(self) -> None:
        collector = FaultReportCollector()
        collector.feed(SESSION_FAULT)
        self.assertEqual(collector.diagnose()[0], FAULT_VERDICT_NO_DIAG)

    def test_trace_without_diag_is_ignored(self) -> None:
        collector = FaultReportCollector()
        self.assertFalse(collector.feed("#FAULT_TRACE,-8,1:2:3:180"))
        self.assertFalse(collector.active)

    def test_report_text(self) -> None:
        text = format_fault_report("Verdict", ["one"], [SESSION_FAULT, "", SESSION_DIAG],
                                   datetime(2026, 10, 27, 11, 44, 43))
        self.assertEqual(text.splitlines(), [
            "Fault report 2026-10-27T11:44:43", "Verdict: Verdict", "- one", "",
            "Raw firmware lines:", SESSION_FAULT, SESSION_DIAG,
        ])


if __name__ == "__main__":
    unittest.main()
