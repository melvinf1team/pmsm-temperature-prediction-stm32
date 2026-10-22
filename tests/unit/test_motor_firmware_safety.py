"""Compile the real firmware C and execute it in an ARM emulator, without a board.

Optional dependencies: unicorn, pyelftools, and arm-none-eabi-gcc.
Only HAL/MCSDK boundaries are mocked; the ramp, state machine and D6T cache
under test are included directly from each project's production sources.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "firmware"
PROJECTS = (ROOT / "firmware_acquisition" / "tets_motor_dewalt", ROOT / "firmware_validation")


def find_gcc() -> str | None:
    compiler = os.environ.get("ARM_NONE_EABI_GCC") or shutil.which("arm-none-eabi-gcc")
    if compiler:
        return compiler
    installations = Path("C:/ST")
    if installations.is_dir():
        for p in installations.glob("STM32CubeIDE*/STM32CubeIDE/plugins/*gnu-tools*/tools/bin/arm-none-eabi-gcc.exe"):
            return str(p)
    return None


class MotorFirmwareSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import unicorn
            from elftools.elf.elffile import ELFFile
        except ImportError as exc:
            raise unittest.SkipTest("Install optional firmware-test dependencies: unicorn pyelftools") from exc
        cls.gcc = find_gcc()
        if not cls.gcc:
            raise unittest.SkipTest("arm-none-eabi-gcc unavailable; set ARM_NONE_EABI_GCC")

    def execute_c(self, project: Path, fixture: str, source: str):
        from elftools.elf.elffile import ELFFile
        from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB
        from unicorn.arm_const import UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_R0, UC_ARM_REG_PC

        with tempfile.TemporaryDirectory(prefix="pmsm-safety-") as directory:
            work = Path(directory)
            for name in ("main", "motorcontrol", "mc_api", "mc_interface", "mc_type",
                         "mc_tasks", "pidreg_speed", "fixpmath"):
                (work / f"{name}.h").write_text('#include "test_platform.h"\n', encoding="utf-8")
            elf_path = work / "test.elf"
            command = [self.gcc, "-mcpu=cortex-m4", "-mthumb", "-mfloat-abi=soft",
                       "-std=gnu11", "-Ofast", "-Wall", "-Wextra", "-Werror",
                       "-ffunction-sections", "-fdata-sections", "-nostartfiles",
                       "--specs=nosys.specs", "-Wl,--gc-sections", "-Wl,-e,main",
                       "-Wl,-Ttext=0x10000", "-Wl,-Tdata=0x40000",
                       "-I", str(work), "-I", str(FIXTURES), "-I", str(project / "Inc"),
                       f'-DFIRMWARE_SOURCE="{(project / "STM32CubeIDE/Application/User" / source).as_posix()}"',
                       str(FIXTURES / fixture), "-lm", "-lc", "-lgcc", "-o", str(elf_path)]
            built = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)

            emulator = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
            emulator.mem_map(0, 0x200000)
            with elf_path.open("rb") as stream:
                elf = ELFFile(stream)
                for segment in elf.iter_segments():
                    if segment["p_type"] == "PT_LOAD":
                        emulator.mem_write(segment["p_vaddr"], segment.data())
                entry = elf.header["e_entry"]
                debug_symbols = {
                    symbol.name: symbol["st_value"]
                    for symbol in elf.get_section_by_name(".symtab").iter_symbols()
                    if symbol.name in ("app_mc_state", "app_custom_phase", "app_mc_fault_reason")
                }
            sentinel = 0x1F0000
            emulator.reg_write(UC_ARM_REG_SP, 0x1E0000)
            emulator.reg_write(UC_ARM_REG_LR, sentinel | 1)
            emulator.emu_start(entry | 1, sentinel, count=20_000_000)
            self.assertEqual(emulator.reg_read(UC_ARM_REG_PC), sentinel,
                             "C test did not return before the instruction limit")
            result = emulator.reg_read(UC_ARM_REG_R0)
            details = {}
            if result:
                for name, address in debug_symbols.items():
                    value = int.from_bytes(emulator.mem_read(address, 4), "little")
                    if name == "app_mc_fault_reason":
                        value = bytes(emulator.mem_read(value, 80)).split(b"\0", 1)[0].decode()
                    details[name] = value
            self.assertEqual(result, 0,
                             f"{project.name}: C assertion failed at {fixture}:{result}; {details}")

    def test_motor_state_machine_and_id_ramp(self):
        for project in PROJECTS:
            with self.subTest(project=project.name):
                self.execute_c(project, "motor_safety_harness.c", "app_motor_control.c")

    def test_d6t_missing_stale_and_tick_wrap(self):
        for project in PROJECTS:
            with self.subTest(project=project.name):
                self.execute_c(project, "d6t_freshness_harness.c", "d6t_ir.c")


if __name__ == "__main__":
    unittest.main(verbosity=2)
