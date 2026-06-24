"""The generated .twb references the published datasource and renders the requested marks."""

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import twb_builder

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
