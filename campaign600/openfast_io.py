"""Minimal reader for OpenFAST binary time-series output.

The layout follows the official OpenFAST Toolbox reader. It is intentionally
small and read-only so baseline validation does not depend on an unpublished
conversion step.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np


FILE_WITH_TIME = 1
FILE_WITHOUT_TIME = 2
FILE_NO_COMPRESS_WITHOUT_TIME = 3
FILE_CHANNEL_LENGTH = 4


@dataclass(frozen=True)
class OpenFASTOutput:
    data: np.ndarray
    channel_names: tuple[str, ...]
    channel_units: tuple[str, ...]
    description: str
    file_id: int


def _read_scalar(handle, dtype: str):
    value = np.fromfile(handle, dtype=dtype, count=1)
    if value.size != 1:
        raise EOFError("Unexpected end of OpenFAST binary header")
    return value[0]


def _read_fixed_text(handle, length: int) -> str:
    raw = np.fromfile(handle, dtype=np.uint8, count=length)
    if raw.size != length:
        raise EOFError("Unexpected end of OpenFAST binary text field")
    return bytes(raw).decode("ascii", errors="replace").strip()


def read_openfast_binary(path: str | Path) -> OpenFASTOutput:
    path = Path(path)
    with path.open("rb") as handle:
        file_id = int(_read_scalar(handle, "<i2"))
        if file_id not in {
            FILE_WITH_TIME,
            FILE_WITHOUT_TIME,
            FILE_NO_COMPRESS_WITHOUT_TIME,
            FILE_CHANNEL_LENGTH,
        }:
            raise ValueError(f"Unsupported OpenFAST binary file ID {file_id}")
        name_length = int(_read_scalar(handle, "<i2")) if file_id == FILE_CHANNEL_LENGTH else 10
        output_channels = int(_read_scalar(handle, "<i4"))
        time_steps = int(_read_scalar(handle, "<i4"))
        if output_channels <= 0 or time_steps <= 0:
            raise ValueError("Invalid OpenFAST binary dimensions")
        if file_id == FILE_WITH_TIME:
            time_scale = float(_read_scalar(handle, "<f8"))
            time_offset = float(_read_scalar(handle, "<f8"))
        else:
            time_start = float(_read_scalar(handle, "<f8"))
            time_increment = float(_read_scalar(handle, "<f8"))
        if file_id == FILE_NO_COMPRESS_WITHOUT_TIME:
            column_scales = np.ones(output_channels)
            column_offsets = np.zeros(output_channels)
        else:
            column_scales = np.fromfile(handle, dtype="<f4", count=output_channels)
            column_offsets = np.fromfile(handle, dtype="<f4", count=output_channels)
            if column_scales.size != output_channels or column_offsets.size != output_channels:
                raise EOFError("Incomplete OpenFAST scale/offset vectors")
        description_length = int(_read_scalar(handle, "<i4"))
        description = _read_fixed_text(handle, description_length)
        channel_names = tuple(_read_fixed_text(handle, name_length) for _ in range(output_channels + 1))
        channel_units = tuple(
            _read_fixed_text(handle, name_length).strip("()[]") for _ in range(output_channels + 1)
        )
        if file_id == FILE_WITH_TIME:
            packed_time = np.fromfile(handle, dtype="<i4", count=time_steps)
            if packed_time.size != time_steps:
                raise EOFError("Incomplete OpenFAST time vector")
            time = (packed_time.astype(float) - time_offset) / time_scale
        else:
            time = time_start + time_increment * np.arange(time_steps)
        dtype = "<f8" if file_id == FILE_NO_COMPRESS_WITHOUT_TIME else "<i2"
        packed_data = np.fromfile(handle, dtype=dtype, count=time_steps * output_channels)
        if packed_data.size != time_steps * output_channels:
            raise EOFError("Incomplete OpenFAST channel data")
    values = packed_data.reshape(time_steps, output_channels).astype(float)
    invalid_constant = np.isnan(column_scales) & np.isnan(column_offsets)
    values[:, invalid_constant] = 0.0
    valid = ~invalid_constant
    values[:, valid] = (values[:, valid] - column_offsets[valid]) / column_scales[valid]
    data = np.column_stack([time, values])
    return OpenFASTOutput(
        data=data,
        channel_names=channel_names,
        channel_units=channel_units,
        description=description,
        file_id=file_id,
    )


def compare_openfast_outputs(
    reference: OpenFASTOutput,
    candidate: OpenFASTOutput,
    rtol_magnitude: float = 2.0,
    atol_magnitude: float = 1.9,
) -> list[dict[str, float | str]]:
    if reference.channel_names != candidate.channel_names:
        raise ValueError("Channel-name mismatch between reference and candidate")
    if reference.channel_units != candidate.channel_units:
        raise ValueError("Channel-unit mismatch between reference and candidate")
    if reference.data.shape != candidate.data.shape:
        raise ValueError(f"Shape mismatch: {reference.data.shape} versus {candidate.data.shape}")
    comparisons: list[dict[str, float | str]] = []
    for index, (name, unit) in enumerate(zip(reference.channel_names, reference.channel_units, strict=True)):
        expected = reference.data[:, index]
        observed = candidate.data[:, index]
        difference = observed - expected
        scale = max(float(np.ptp(expected)), float(np.max(np.abs(expected))), np.finfo(float).eps)
        baseline_offset = expected - np.min(expected)
        order_of_magnitude = np.floor(np.log10(baseline_offset + 1e-12))
        absolute_tolerance = max(float(10 ** (np.max(order_of_magnitude) - atol_magnitude)), 1e-6)
        relative_tolerance = float(10 ** (-rtol_magnitude))
        comparisons.append(
            {
                "channel": name,
                "unit": unit,
                "reference_min": float(np.min(expected)),
                "reference_max": float(np.max(expected)),
                "max_abs_error": float(np.max(np.abs(difference))),
                "relative_max_error": float(np.max(np.abs(difference)) / scale),
                "relative_l2_error": float(np.linalg.norm(difference) / max(np.linalg.norm(expected), np.finfo(float).eps)),
                "official_rtol": relative_tolerance,
                "official_atol": absolute_tolerance,
                "passes_official_pointwise": bool(
                    np.all(np.isclose(observed, expected, rtol=relative_tolerance, atol=absolute_tolerance))
                    and np.all(np.isfinite(observed))
                ),
            }
        )
    return comparisons
