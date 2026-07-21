"""N4 — sign-based KPI delta color (external-skill-suite Calc-Engine "KPI Status").

The delta arrow is colored by sign via Tableau's bracketed number-format
section colors ([good]▲;[bad]▼) when `delta_color_by_sign` is set. This is
VERIFY-LIVE (no reference workbook uses bracketed-color formats), gated behind
an additive flag that defaults off → byte-identical to prior output.
"""

from __future__ import annotations

import twb_builder


def test_nearest_tableau_format_color_maps_brand_semantic_colors() -> None:
    f = twb_builder._nearest_tableau_format_color
    # brand.yaml defaults
    assert f("#59a14f") == "Green"  # good
    assert f("#e15759") == "Red"  # bad
    # exact corners
    assert f("#000000") == "Black"
    assert f("#ffffff") == "White"
    assert f("#0000ff") == "Blue"
    assert f("#ffff00") == "Yellow"
    # shorthand is normalized first
    assert f("#0f0") == "Green"


def test_arrow_format_plain_when_color_by_sign_off() -> None:
    # use_semantic on but color_by_sign off → plain arrow (unchanged)
    fmt = twb_builder._kpi_delta_arrow_format({"use_semantic_delta_colors": True}, _brand())
    assert fmt == twb_builder._KPI_DELTA_ARROW_FORMAT


def test_delta_color_by_sign_falls_back_to_arrow_only_refuted_live() -> None:
    # VERIFY-LIVE → REFUTED: Cloud strips bracketed-color number formats in the
    # customized-label context (they kill the arrow too). The flag therefore
    # emits the corpus-proven arrow-only format — never the broken colored one.
    fmt = twb_builder._kpi_delta_arrow_format(
        {"use_semantic_delta_colors": True, "delta_color_by_sign": True}, _brand()
    )
    assert fmt == twb_builder._KPI_DELTA_ARROW_FORMAT
    assert "[Green]" not in fmt and "[Red]" not in fmt


def test_arrow_format_plain_when_flag_set_but_no_brand() -> None:
    fmt = twb_builder._kpi_delta_arrow_format({"delta_color_by_sign": True}, None)
    assert fmt == twb_builder._KPI_DELTA_ARROW_FORMAT


def test_arrow_format_plain_when_palette_missing_semantic() -> None:
    fmt = twb_builder._kpi_delta_arrow_format(
        {"delta_color_by_sign": True}, {"palette": {"categorical": ["#123456"]}}
    )
    assert fmt == twb_builder._KPI_DELTA_ARROW_FORMAT


def _brand() -> dict[str, object]:
    return {"palette": {"good": "#59a14f", "bad": "#e15759", "neutral": "#898989"}}
