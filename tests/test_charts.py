import math
import re

import pytest

from app.portfolio.optimize import FrontierPoint, PortfolioMetrics
from app.portfolio.snapshots import NavSnapshot
from app.web.charts import OTHER_GRAY, corr_color, donut, frontier_chart, nav_area


def _outer_arc_degrees(path_d: str) -> list[float]:
    pattern = re.compile(
        r"M ([\d.]+),([\d.]+) A 100\.00,100\.00 0 ([01]) 1 ([\d.]+),([\d.]+)"
    )
    degrees: list[float] = []
    for match in pattern.finditer(path_d):
        start_x, start_y, large_arc, end_x, end_y = match.groups()
        start = _clockwise_angle(float(start_x), float(start_y))
        end = _clockwise_angle(float(end_x), float(end_y))
        delta = (end - start) % 360
        if large_arc == "1" and delta < 180:
            delta += 360
        degrees.append(delta)
    return degrees


def _clockwise_angle(x: float, y: float) -> float:
    raw = math.degrees(math.atan2(y - 110.0, x - 110.0)) + 90.0
    return raw % 360


def test_donut_sums_to_full_circle_and_100_pct():
    slices = donut([("AAA", 60), ("BBB", 30), ("CCC", 10)])

    assert sum(s.pct for s in slices) == pytest.approx(100)
    assert sum(sum(_outer_arc_degrees(s.path_d)) for s in slices) == pytest.approx(360, abs=0.05)
    assert [s.label for s in slices] == ["AAA", "BBB", "CCC"]


def test_donut_aggregates_after_top_11_and_skips_non_positive_values():
    slices = donut([(f"S{i:02d}", 20 - i) for i in range(13)] + [("ZERO", 0), ("NEG", -1)])

    assert len(slices) == 12
    assert slices[-1].label == "Other"
    assert slices[-1].color == OTHER_GRAY
    assert slices[-1].value == 17


def test_donut_single_holding_uses_two_half_arcs_and_empty_returns_empty():
    slices = donut([("ONLY", 100)])

    assert len(slices) == 1
    assert slices[0].pct == pytest.approx(100)
    assert slices[0].path_d.count("A 100.00,100.00") == 2
    assert donut([("ZERO", 0), ("NEG", -3)]) == []


def test_corr_color_endpoints_and_text_flip():
    assert corr_color(0) == ("#131c1a", "#dfeeea")
    assert corr_color(-1)[0] == "#6ba3d6"
    assert corr_color(1)[0] == "#cf6a60"
    assert corr_color(0.55)[1] == "#dfeeea"
    assert corr_color(0.56)[1] == "#0f1a17"
    assert corr_color(-0.56)[1] == "#0f1a17"


def test_frontier_chart_coordinates_include_padding_and_extreme_marker():
    frontier = [
        FrontierPoint(expected_return=0.05, volatility=0.10, sharpe=0.5),
        FrontierPoint(expected_return=0.10, volatility=0.20, sharpe=0.5),
        FrontierPoint(expected_return=0.15, volatility=0.30, sharpe=0.5),
    ]
    optimal = PortfolioMetrics(
        weights={"AAA": 1.0},
        expected_return=0.10,
        volatility=0.20,
        sharpe=0.5,
    )
    current = PortfolioMetrics(
        weights={"AAA": 1.0},
        expected_return=0.20,
        volatility=0.40,
        sharpe=0.5,
    )

    chart = frontier_chart(frontier, optimal, current)

    assert chart is not None
    assert chart.frontier_polyline == "27.27,248.18 209.09,169.39 390.91,90.61"
    assert chart.optimal_xy == (209.09, 169.39)
    assert chart.current_xy == (572.73, 11.82)
    assert 0 < chart.current_xy[0] < chart.width
    assert 0 < chart.current_xy[1] < chart.height


def test_frontier_chart_returns_none_with_fewer_than_two_frontier_points():
    metric = PortfolioMetrics(
        weights={"AAA": 1.0},
        expected_return=0.10,
        volatility=0.20,
        sharpe=0.5,
    )

    assert frontier_chart([], metric, None) is None
    assert (
        frontier_chart(
            [FrontierPoint(expected_return=0.05, volatility=0.10, sharpe=0.5)],
            metric,
            None,
        )
        is None
    )


def test_nav_area_flat_series_has_no_divide_by_zero_and_fill_closes():
    points = [
        NavSnapshot(
            day="2026-07-01",
            total_value=100,
            cash=0,
            total_with_cash=100,
            total_cost=90,
            unrealized_pl=10,
        ),
        NavSnapshot(
            day="2026-07-02",
            total_value=100,
            cash=0,
            total_with_cash=100,
            total_cost=90,
            unrealized_pl=10,
        ),
    ]

    chart = nav_area(points)

    assert chart is not None
    assert chart.polyline == "0.00,60.00 600.00,60.00"
    assert chart.baseline_y == 60
    assert chart.fill_path == "M 0.00,120.00 L 0.00,60.00 600.00,60.00 L 600.00,120.00 Z"
    assert nav_area(points[:1]) is None
