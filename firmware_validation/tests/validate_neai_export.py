"""Valide la compatibilite structurelle d'un export NanoEdge avec le firmware."""

from __future__ import annotations

import json
import re
from pathlib import Path


FIRMWARE_ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = FIRMWARE_ROOT / "AI_Model"
EXPECTED_FEATURE_COUNT = 55


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def header_define(header: str, name: str) -> str:
    match = re.search(rf"^#define\s+{re.escape(name)}\s+(.+?)\s*$", header, re.MULTILINE)
    if match is None:
        raise AssertionError(f"Macro {name} absente de NanoEdgeAI.h")
    return match.group(1).strip().strip('"')


def main() -> None:
    library = MODEL_DIR / "libneai.a"
    header_path = MODEL_DIR / "NanoEdgeAI.h"
    metadata_path = MODEL_DIR / "metadata.json"
    feature_order_path = MODEL_DIR / "feature_order.txt"

    required = [library, header_path, metadata_path, feature_order_path]
    missing = [str(path.relative_to(FIRMWARE_ROOT)) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("Fichiers modele manquants: " + ", ".join(missing))

    header = header_path.read_text(encoding="utf-8")
    metadata = load_json(metadata_path)

    neai_id = header_define(header, "NEAI_ID")
    signal_length = int(header_define(header, "NEAI_INPUT_SIGNAL_LENGTH"), 0)
    axis_count = int(header_define(header, "NEAI_INPUT_AXIS_NUMBER"), 0)

    assert metadata["algorithm_type"] == "regression"
    assert metadata["mcu_type"] == "cortex-m4"
    assert metadata["compilation_flags"]["float_abi"] == "hard"
    assert metadata["library_id"] == neai_id
    assert metadata["data"]["nb_cols"] == EXPECTED_FEATURE_COUNT
    assert signal_length == 1
    assert axis_count == EXPECTED_FEATURE_COUNT
    assert len(feature_order_path.read_text(encoding="utf-8").splitlines()) == axis_count

    library_bytes = library.read_bytes()
    for symbol in (b"neai_extrapolation_init", b"neai_extrapolation"):
        assert symbol in library_bytes, f"Symbole {symbol.decode()} absent de libneai.a"

    validate_ridge_artifacts(metadata, axis_count)

    print(
        f"Export NanoEdge compatible: id={neai_id}, "
        f"input={signal_length}x{axis_count}, model={metadata['mcu_type']}/hard-float."
    )


def validate_ridge_artifacts(metadata: dict, axis_count: int) -> None:
    if re.search(r"'name'\s*:\s*'RIDGE'", metadata.get("compilation_str", "")) is None:
        return

    model_path = MODEL_DIR / "artifacts" / "ridge_model_params.json"
    preprocessing_path = MODEL_DIR / "artifacts" / "ridge_preprocessing_params.json"

    if not model_path.is_file() or not preprocessing_path.is_file():
        raise FileNotFoundError("Artefacts Ridge manquants dans AI_Model/artifacts")

    model = load_json(model_path)
    preprocessing = load_json(preprocessing_path)

    assert model["signal_len"] == 1
    assert model["dimension"] == axis_count
    assert model["input_size"] == 2 * EXPECTED_FEATURE_COUNT
    assert len(model["weights"]) == model["input_size"]
    assert len(model["intercepts"]) == model["num_targets"] == 1

    normalize = preprocessing[2]["normalize"]
    stats = normalize["context"]["stats"]
    assert normalize["method"] == "robust"
    assert len(stats["q_low_val"][0]) == EXPECTED_FEATURE_COUNT
    assert len(stats["q_high_val"][0]) == EXPECTED_FEATURE_COUNT


if __name__ == "__main__":
    main()
