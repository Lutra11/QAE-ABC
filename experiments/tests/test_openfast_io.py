from pathlib import Path

import numpy as np

from qae_abc.data.openfast_io import compare_openfast_outputs, read_openfast_binary


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CASE_NAME = "5MW_OC4Jckt_DLL_WTurb_WavesIrr_MGrowth"


def test_reference_openfast_binary_parses():
    path = PROJECT_ROOT / "external" / "r-test" / "glue-codes" / "openfast" / CASE_NAME / f"{CASE_NAME}.outb"
    if not path.exists():
        return
    output = read_openfast_binary(path)
    assert output.data.ndim == 2
    assert output.data.shape[0] > 0
    assert output.data.shape[1] == len(output.channel_names)
    assert output.channel_names[0].lower() == "time"


def test_self_comparison_is_exact():
    path = PROJECT_ROOT / "external" / "r-test" / "glue-codes" / "openfast" / CASE_NAME / f"{CASE_NAME}.outb"
    if not path.exists():
        return
    output = read_openfast_binary(path)
    comparison = compare_openfast_outputs(output, output)
    assert np.allclose([row["relative_l2_error"] for row in comparison], 0.0)

