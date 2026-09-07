"""Project figure style following the installed scientific-figure skill."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


PALETTE = {
    "blue_main": "#0F4D92",
    "blue_secondary": "#3775BA",
    "green_1": "#DDF3DE",
    "green_2": "#AADCA9",
    "green_3": "#8BCF8B",
    "red_1": "#F6CFCB",
    "red_2": "#E9A6A1",
    "red_strong": "#B64342",
    "neutral": "#CFCECE",
    "highlight": "#FFD700",
    "teal": "#42949E",
    "violet": "#9A4D8E",
}


@dataclass(frozen=True)
class FigureStyle:
    font_size: int = 15
    axes_linewidth: float = 2.0
    # DejaVu Sans ships with Matplotlib and avoids silent font substitution on
    # clean reproduction environments where Arial/Helvetica are unavailable.
    font_family: tuple[str, ...] = ("DejaVu Sans",)


def apply_publication_style(style: FigureStyle | None = None) -> None:
    style = style or FigureStyle()
    plt.rcParams.update(
        {
            "font.family": list(style.font_family),
            "font.size": style.font_size,
            "axes.linewidth": style.axes_linewidth,
            "axes.spines.right": False,
            "axes.spines.top": False,
            "legend.frameon": False,
            "svg.fonttype": "none",
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "savefig.facecolor": "white",
        }
    )


def finalize_figure(
    fig: plt.Figure,
    out_path: str | Path,
    formats: Iterable[str] = ("png", "pdf"),
    dpi: int = 300,
) -> list[Path]:
    base = Path(out_path)
    base.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout(pad=1.5)
    paths: list[Path] = []
    for fmt in formats:
        path = base.with_suffix(f".{fmt}")
        fig.savefig(path, dpi=dpi, bbox_inches="tight", pad_inches=0.06)
        paths.append(path)
    plt.close(fig)
    return paths
