"""Round-trip a DataFrame through a .hyper extract and verify rows + column types."""

from pathlib import Path

import pandas as pd

import hyper_builder


def test_dataframe_to_hyper_roundtrip(tmp_path: Path) -> None:
    df = pd.DataFrame(
        {
            "region": ["West", "East", "West"],
            "revenue": [100.5, 200.0, 50.25],
            "units": [3, 5, 1],
            "active": [True, False, True],
        }
    )
    out = tmp_path / "extract.hyper"
    hyper_builder.dataframe_to_hyper(df, out)

    info = hyper_builder.hyper_table_info(out)
    assert info["row_count"] == 3
    columns = info["columns"]
    assert set(columns) == {"region", "revenue", "units", "active"}
    assert columns["units"] in {"INT", "BIG_INT", "SMALL_INT"}
    assert columns["revenue"] == "DOUBLE"
    assert columns["region"] == "TEXT"
    assert columns["active"] == "BOOL"


def test_read_hyper_columns_roles(tmp_path: Path) -> None:
    df = pd.DataFrame({"category": ["a", "b"], "amount": [1.0, 2.0]})
    out = tmp_path / "extract.hyper"
    hyper_builder.dataframe_to_hyper(df, out)

    specs = {c.name: c for c in hyper_builder.read_hyper_columns(out)}
    assert specs["amount"].role == "measure"
    assert specs["amount"].datatype == "real"
    assert specs["category"].role == "dimension"
    assert specs["category"].datatype == "string"


def test_query_to_dataframe_csv(tmp_path: Path) -> None:
    csv = tmp_path / "data.csv"
    csv.write_text("a,b\n1,x\n2,y\n")
    df = hyper_builder.query_to_dataframe({"type": "csv", "path": str(csv)}, "")
    assert list(df.columns) == ["a", "b"]
    assert len(df) == 2


def test_query_to_dataframe_respects_max_rows(tmp_path: Path) -> None:
    csv = tmp_path / "data.csv"
    csv.write_text("a\n1\n2\n3\n4\n")
    df = hyper_builder.query_to_dataframe({"type": "csv", "path": str(csv)}, "", max_rows=2)
    assert len(df) == 2


def test_records_to_dataframe() -> None:
    df = hyper_builder.records_to_dataframe([{"a": 1, "b": "x"}, {"a": 2, "b": "y"}])
    assert list(df.columns) == ["a", "b"]
    assert len(df) == 2
