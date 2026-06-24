"""The generated .twb references the published datasource and renders the requested marks."""

import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import twb_builder

# Regex pattern that a QUUID-ST must match: {XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX}
_QUUID_PATTERN = re.compile(
    r"^\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}"
    r"-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}$"
)

SHEETS = [
    {
        "title": "Revenue by Region",
        "mark_type": "bar",
        "cols": ["Region"],
        "rows": [],
        "measures": ["Revenue"],
    }
]


def test_twb_references_published_datasource() -> None:
    xml = twb_builder.build_twb_xml(
        "Top Customers",
        "TopCustomers",
        "mysite",
        SHEETS,
        server_url="https://x.online.tableau.com",
    )
    root = ET.fromstring(xml)
    assert root.tag == "workbook"

    datasource = root.find(".//datasources/datasource")
    assert datasource is not None
    assert datasource.get("caption") == "Top Customers"
    assert datasource.get("inline") == "true"
    assert datasource.get("name") == "sqlproxy.TopCustomers"
    assert root.find(".//connection[@class='sqlproxy']") is not None

    repo = root.find(".//repository-location")
    assert repo is not None
    assert repo.get("id") == "TopCustomers"
    assert repo.get("path") == "/t/mysite/datasources"
    assert repo.get("site") == "mysite"

    inner = root.find("./datasources/datasource/connection[@class='sqlproxy']")
    assert inner is not None
    assert inner.get("dbname") == "TopCustomers"
    assert inner.get("server") == "x.online.tableau.com"


def test_twb_one_worksheet_per_sheet_with_dependencies() -> None:
    xml = twb_builder.build_twb_xml("DS", "ds", "site", SHEETS)
    root = ET.fromstring(xml)
    worksheets = root.findall(".//worksheets/worksheet")
    assert len(worksheets) == 1

    deps = root.find(".//datasource-dependencies")
    assert deps is not None
    assert deps.get("datasource") == "sqlproxy.ds"
    dep_cols = {c.get("name") for c in deps.findall("column")}
    assert "[Region]" in dep_cols
    assert "[Revenue]" in dep_cols


def test_twb_mark_class_per_type() -> None:
    for mark_type, expected in [("bar", "Bar"), ("line", "Line"), ("text", "Text")]:
        sheets = [
            {"title": "S", "mark_type": mark_type, "cols": ["D"], "rows": [], "measures": ["M"]}
        ]
        root = ET.fromstring(twb_builder.build_twb_xml("DS", "ds", "site", sheets))
        mark = root.find(".//pane/mark")
        assert mark is not None
        assert mark.get("class") == expected


def test_twb_default_site_path() -> None:
    root = ET.fromstring(twb_builder.build_twb_xml("DS", "ds", "", SHEETS))
    repo = root.find(".//repository-location")
    assert repo is not None
    assert repo.get("path") == "/datasources"


def test_build_starter_twbx_is_valid_zip(tmp_path: Path) -> None:
    out = twb_builder.build_starter_twbx("DS", "ds", "site", SHEETS, tmp_path / "wb.twbx")
    assert zipfile.is_zipfile(out)
    with zipfile.ZipFile(out) as archive:
        twb_files = [n for n in archive.namelist() if n.endswith(".twb")]
        assert len(twb_files) == 1
        root = ET.fromstring(archive.read(twb_files[0]))
        assert root.tag == "workbook"


# ---------------------------------------------------------------------------
# A2 structural pins — regression-guard the schema-valid output shape
# re-baselined for schema-valid output (XSD A2); see ADAPTATION_PLAN §A2
# ---------------------------------------------------------------------------


def test_worksheet_has_simple_id() -> None:
    """Each <worksheet> must have a <simple-id> child with a valid QUUID uuid attr."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", SHEETS)
    root = ET.fromstring(xml)
    for ws in root.findall(".//worksheets/worksheet"):
        sid = ws.find("simple-id")
        assert sid is not None, f"worksheet '{ws.get('name')}' missing <simple-id>"
        uuid_val = sid.get("uuid", "")
        assert _QUUID_PATTERN.match(uuid_val), (
            f"<simple-id> uuid '{uuid_val}' does not match QUUID pattern"
        )


def test_window_has_cards() -> None:
    """Each worksheet <window> must have a <cards/> child (XSD required)."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", SHEETS)
    root = ET.fromstring(xml)
    for win in root.findall(".//windows/window[@class='worksheet']"):
        assert win.find("cards") is not None, (
            f"window '{win.get('name')}' missing <cards/>"
        )


def test_view_has_aggregation() -> None:
    """Each <view> inside a worksheet must have <aggregation> (required by XSD)."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", SHEETS)
    root = ET.fromstring(xml)
    for view in root.findall(".//worksheets/worksheet/table/view"):
        agg = view.find("aggregation")
        assert agg is not None, "<view> missing required <aggregation> element"
        assert agg.get("value") == "true"


def test_table_has_style_before_rows() -> None:
    """<table> must contain <style> between <view> and <rows>/<cols> (XSD required)."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", SHEETS)
    root = ET.fromstring(xml)
    for table in root.findall(".//worksheets/worksheet/table"):
        children = [child.tag for child in table]
        assert "style" in children, "<table> missing <style> element"
        style_idx = children.index("style")
        rows_idx = children.index("rows") if "rows" in children else len(children)
        cols_idx = children.index("cols") if "cols" in children else len(children)
        assert style_idx < rows_idx, "<style> must come before <rows>"
        assert style_idx < cols_idx, "<style> must come before <cols>"


def test_workbook_has_explain_data() -> None:
    """Workbook must contain <explain-data> after <windows> (required by XSD)."""
    xml = twb_builder.build_twb_xml("DS", "ds", "site", SHEETS)
    root = ET.fromstring(xml)
    explain = root.find("explain-data")
    assert explain is not None, "<workbook> missing required <explain-data> element"
    assert explain.get("enabled-for-viewer") == "false"
    assert explain.get("extreme-values-enabled-for-all") == "false"
