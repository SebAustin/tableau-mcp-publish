"""A .hyper -> .tdsx is a valid zip (.tds + Data/*.hyper) whose .tds is structurally sound."""

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pandas as pd

import hyper_builder
import tds_builder


def _make_hyper(tmp_path: Path) -> Path:
    df = pd.DataFrame({"region": ["W", "E"], "revenue": [1.0, 2.0]})
    return hyper_builder.dataframe_to_hyper(df, tmp_path / "extract.hyper")


def test_tdsx_is_valid_zip_with_tds_and_hyper(tmp_path: Path) -> None:
    hyper = _make_hyper(tmp_path)
    tdsx = tds_builder.hyper_to_tdsx(hyper, "Top Customers", tmp_path / "ds.tdsx")

    assert zipfile.is_zipfile(tdsx)
    with zipfile.ZipFile(tdsx) as archive:
        names = archive.namelist()
        tds_files = [n for n in names if n.endswith(".tds")]
        data_hyper = [n for n in names if n.startswith("Data/") and n.endswith(".hyper")]
        assert len(tds_files) == 1
        assert len(data_hyper) == 1
        tds_xml = archive.read(tds_files[0]).decode()

    root = ET.fromstring(tds_xml)
    assert root.tag == "datasource"
    assert root.findall(".//connection[@class='hyper']"), "expected a hyper connection"
    assert root.findall(".//relation"), "expected a relation"
    columns = root.findall("column")
    assert {c.get("name") for c in columns} == {"[region]", "[revenue]"}


def test_tds_dbname_matches_zip_path(tmp_path: Path) -> None:
    hyper = _make_hyper(tmp_path)
    columns = hyper_builder.read_hyper_columns(hyper)
    xml = tds_builder.build_tds_xml("DS", "extract.hyper", columns)
    root = ET.fromstring(xml)
    conn = root.find(".//named-connection/connection[@class='hyper']")
    assert conn is not None
    assert conn.get("dbname") == "Data/extract.hyper"


def test_tds_marks_columns_with_roles(tmp_path: Path) -> None:
    hyper = _make_hyper(tmp_path)
    columns = hyper_builder.read_hyper_columns(hyper)
    xml = tds_builder.build_tds_xml("DS", "extract.hyper", columns)
    root = ET.fromstring(xml)
    by_name = {c.get("name"): c for c in root.findall("column")}
    assert by_name["[revenue]"].get("role") == "measure"
    assert by_name["[region]"].get("role") == "dimension"
