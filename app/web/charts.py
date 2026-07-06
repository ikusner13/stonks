"""Pure server-side SVG chart geometry and color helpers."""

from __future__ import annotations

from math import cos, isfinite, radians, sin
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel

if TYPE_CHECKING:
    from ..portfolio.optimize import FrontierPoint, PortfolioMetrics
    from ..portfolio.snapshots import NavSnapshot


OTHER_GRAY = "#9ca3af"
PALETTE: list[str] = [
    "#E69F00",
    "#56B4E9",
    "#009E73",
    "#F0E442",
    "#0072B2",
    "#D55E00",
    "#CC79A7",
    "#A6D854",
    "#8DD3C7",
    "#BEBADA",
    "#FB8072",
    OTHER_GRAY,
]

_DONUT_SIZE = 220.0
_DONUT_CENTER = _DONUT_SIZE / 2
_DONUT_OUTER_R = 100.0
_DONUT_INNER_R = _DONUT_OUTER_R * 0.62


class DonutSlice(BaseModel):
    label: str
    value: float
    pct: float
    color: str
    path_d: str


class ScatterChart(BaseModel):
    width: int = 600
    height: int = 260
    frontier_polyline: str
    optimal_xy: tuple[float, float] | None
    current_xy: tuple[float, float] | None
    x_ticks: list[tuple[float, str]]
    y_ticks: list[tuple[float, str]]


class NavChart(BaseModel):
    width: int = 600
    height: int = 120
    polyline: str
    fill_path: str
    baseline_y: float
    first_label: str
    last_label: str
    min_label: str
    max_label: str


class _MetricPoint(Protocol):
    expected_return: float
    volatility: float


def donut(slices: list[tuple[str, float]]) -> list[DonutSlice]:
    """Build 220x220 annular SVG paths from positive values.

    Values are sorted descending; the top 11 are kept and any remaining
    positive values are combined into a gray ``Other`` slice.
    """
    positive = [(label, value) for label, value in slices if value > 0 and isfinite(value)]
    if not positive:
        return []

    positive.sort(key=lambda item: item[1], reverse=True)
    kept = positive[:11]
    rest = positive[11:]
    if rest:
        kept.append(("Other", sum(value for _, value in rest)))

    total = sum(value for _, value in kept)
    if total <= 0:
        return []

    result: list[DonutSlice] = []
    start = 0.0
    for index, (label, value) in enumerate(kept):
        angle = value / total * 360.0
        end = 360.0 if index == len(kept) - 1 else start + angle
        color = OTHER_GRAY if label == "Other" else PALETTE[index]
        result.append(
            DonutSlice(
                label=label,
                value=value,
                pct=value / total * 100.0,
                color=color,
                path_d=_donut_path(start, end),
            )
        )
        start = end
    return result


def corr_color(rho: float) -> tuple[str, str]:
    """Return redundant correlation color encoding as ``(bg_hex, text_hex)``.

    Diverging blue/red ramp anchored on the dark app background: cells
    brighten with |rho|, so intensity still reads as magnitude on a dark page.
    """
    if not isfinite(rho):
        rho = 0.0
    rho = max(-1.0, min(1.0, rho))
    if rho < 0:
        bg = _mix_hex("#131c1a", "#6ba3d6", abs(rho))
    elif rho > 0:
        bg = _mix_hex("#131c1a", "#cf6a60", rho)
    else:
        bg = "#131c1a"
    text = "#0f1a17" if abs(rho) > 0.55 else "#dfeeea"
    return bg, text


def frontier_chart(
    frontier: list["FrontierPoint"],
    optimal: "PortfolioMetrics",
    current: "PortfolioMetrics | None",
) -> ScatterChart | None:
    if len(frontier) < 2:
        return None

    points: list[_MetricPoint] = [*frontier, optimal]
    if current is not None:
        points.append(current)

    vols = [p.volatility for p in points]
    rets = [p.expected_return for p in points]
    x_min, x_max = _padded_range(vols)
    y_min, y_max = _padded_range(rets)

    def xy(point: _MetricPoint) -> tuple[float, float]:
        x = (point.volatility - x_min) / (x_max - x_min) * 600
        y = 260 - ((point.expected_return - y_min) / (y_max - y_min) * 260)
        return (round(x, 2), round(y, 2))

    frontier_polyline = " ".join(f"{x:.2f},{y:.2f}" for x, y in (xy(p) for p in frontier))
    x_mid = (x_min + x_max) / 2
    y_mid = (y_min + y_max) / 2
    return ScatterChart(
        frontier_polyline=frontier_polyline,
        optimal_xy=xy(optimal),
        current_xy=xy(current) if current is not None else None,
        x_ticks=[(0.0, _pct_label(x_min)), (300.0, _pct_label(x_mid)), (600.0, _pct_label(x_max))],
        y_ticks=[(260.0, _pct_label(y_min)), (130.0, _pct_label(y_mid)), (0.0, _pct_label(y_max))],
    )


def nav_area(points: list["NavSnapshot"]) -> NavChart | None:
    if len(points) < 2:
        return None

    width = 600.0
    height = 120.0
    values = [p.total_with_cash for p in points]
    low = min(values)
    high = max(values)
    spread = high - low
    x_step = width / (len(points) - 1)

    coords: list[tuple[float, float]] = []
    for idx, value in enumerate(values):
        x = idx * x_step
        y = height / 2 if spread == 0 else height - ((value - low) / spread * height)
        coords.append((x, y))

    polyline = " ".join(f"{x:.2f},{y:.2f}" for x, y in coords)
    fill_path = (
        f"M {coords[0][0]:.2f},{height:.2f} "
        f"L {polyline} "
        f"L {coords[-1][0]:.2f},{height:.2f} Z"
    )
    first_value = values[0]
    baseline_y = height / 2 if spread == 0 else height - ((first_value - low) / spread * height)
    return NavChart(
        polyline=polyline,
        fill_path=fill_path,
        baseline_y=round(baseline_y, 2),
        first_label=points[0].day,
        last_label=points[-1].day,
        min_label=f"${low:,.0f}",
        max_label=f"${high:,.0f}",
    )


def _donut_path(start_deg: float, end_deg: float) -> str:
    total = end_deg - start_deg
    if total >= 359.999:
        mid = start_deg + 180.0
        return f"{_donut_segment(start_deg, mid)} {_donut_segment(mid, end_deg)}"
    return _donut_segment(start_deg, end_deg)


def _donut_segment(start_deg: float, end_deg: float) -> str:
    large_arc = 1 if (end_deg - start_deg) > 180.0 else 0
    sweep = 1
    outer_start = _polar(_DONUT_OUTER_R, start_deg)
    outer_end = _polar(_DONUT_OUTER_R, end_deg)
    inner_end = _polar(_DONUT_INNER_R, end_deg)
    inner_start = _polar(_DONUT_INNER_R, start_deg)
    return (
        f"M {outer_start[0]:.2f},{outer_start[1]:.2f} "
        f"A {_DONUT_OUTER_R:.2f},{_DONUT_OUTER_R:.2f} 0 {large_arc} {sweep} "
        f"{outer_end[0]:.2f},{outer_end[1]:.2f} "
        f"L {inner_end[0]:.2f},{inner_end[1]:.2f} "
        f"A {_DONUT_INNER_R:.2f},{_DONUT_INNER_R:.2f} 0 {large_arc} 0 "
        f"{inner_start[0]:.2f},{inner_start[1]:.2f} Z"
    )


def _polar(radius: float, angle_deg: float) -> tuple[float, float]:
    angle = radians(angle_deg - 90.0)
    return (_DONUT_CENTER + radius * cos(angle), _DONUT_CENTER + radius * sin(angle))


def _mix_hex(start: str, end: str, t: float) -> str:
    start_rgb = _hex_to_rgb(start)
    end_rgb = _hex_to_rgb(end)
    rgb = tuple(round(a + (b - a) * t) for a, b in zip(start_rgb, end_rgb))
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def _padded_range(values: list[float]) -> tuple[float, float]:
    low = min(values)
    high = max(values)
    span = high - low
    if span == 0:
        pad = abs(low) * 0.05 or 0.01
    else:
        pad = span * 0.05
    return low - pad, high + pad


def _pct_label(value: float) -> str:
    return f"{value * 100:.0f}%"
