"""Multi-format ingest: round-trip tests for file_to_dataframe (PA-1, PA-3)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

import hyper_builder  # noqa: E402

# ---------------------------------------------------------------------------
# PA-1  round-trip each supported format
# ---------------------------------------------------------------------------


def test_file_to_dataframe_csv(tmp_path: Path) -> None:
    csv = tmp_path / "data.csv"
    csv.write_text("a,b\n1,x\n2,y\n")
    df = hyper_builder.file_to_dataframe("csv", str(csv))
    assert list(df.columns) == ["a", "b"]
    assert len(df) == 2


def test_file_to_dataframe_json(tmp_path: Path) -> None:
    data = [{"id": 1, "val": "a"}, {"id": 2, "val": "b"}]
    p = tmp_path / "data.json"
    p.write_text(json.dumps(data))
    df = hyper_builder.file_to_dataframe("json", str(p))
    assert len(df) == 2
    assert set(df.columns) == {"id", "val"}


def test_file_to_dataframe_jsonl(tmp_path: Path) -> None:
    p = tmp_path / "data.jsonl"
    p.write_text('{"a": 1}\n{"a": 2}\n')
    df = hyper_builder.file_to_dataframe("jsonl", str(p))
    assert len(df) == 2
    assert "a" in df.columns


def test_file_to_dataframe_xlsx(tmp_path: Path) -> None:
    p = tmp_path / "data.xlsx"
    df_src = pd.DataFrame({"x": [10, 20], "y": ["p", "q"]})
    df_src.to_excel(str(p), index=False)
    df = hyper_builder.file_to_dataframe("xlsx", str(p))
    assert len(df) == 2
    assert set(df.columns) == {"x", "y"}


def test_file_to_dataframe_parquet(tmp_path: Path) -> None:
    p = tmp_path / "data.parquet"
    df_src = pd.DataFrame({"col1": [1, 2, 3], "col2": ["a", "b", "c"]})
    df_src.to_parquet(str(p))
    df = hyper_builder.file_to_dataframe("parquet", str(p))
    assert len(df) == 3
    assert set(df.columns) == {"col1", "col2"}


# ---------------------------------------------------------------------------
# PA-3  Excel sheet selection by index and by name
# ---------------------------------------------------------------------------


def test_file_to_dataframe_xlsx_sheet_by_index(tmp_path: Path) -> None:
    p = tmp_path / "multi.xlsx"
    with pd.ExcelWriter(str(p), engine="openpyxl") as writer:
        pd.DataFrame({"sheet1_col": [1, 2]}).to_excel(writer, sheet_name="Sheet1", index=False)
        pd.DataFrame({"sheet2_col": [3, 4, 5]}).to_excel(writer, sheet_name="Sheet2", index=False)

    df = hyper_builder.file_to_dataframe("xlsx", str(p), excel_sheet=1)
    assert len(df) == 3
    assert "sheet2_col" in df.columns


def test_file_to_dataframe_xlsx_sheet_by_name(tmp_path: Path) -> None:
    p = tmp_path / "multi.xlsx"
    with pd.ExcelWriter(str(p), engine="openpyxl") as writer:
        pd.DataFrame({"sheet1_col": [1, 2]}).to_excel(writer, sheet_name="Sheet1", index=False)
        pd.DataFrame({"sheet2_col": [3, 4, 5]}).to_excel(writer, sheet_name="Sheet2", index=False)

    df = hyper_builder.file_to_dataframe("xlsx", str(p), excel_sheet="Sheet2")
    assert len(df) == 3
    assert "sheet2_col" in df.columns


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


def test_file_to_dataframe_unsupported_type(tmp_path: Path) -> None:
    p = tmp_path / "data.xml"
    p.write_text("<root/>")
    with pytest.raises(ValueError, match="Unsupported file_type"):
        hyper_builder.file_to_dataframe("xml", str(p))


def test_file_to_dataframe_missing_file() -> None:
    with pytest.raises(ValueError, match="not a regular file"):
        hyper_builder.file_to_dataframe("csv", "/no/such/file.csv")


def test_file_to_dataframe_json_path_depth_error(tmp_path: Path) -> None:
    p = tmp_path / "data.json"
    p.write_text('{"a": {"b": [1, 2]}}')
    with pytest.raises(ValueError, match="not supported"):
        hyper_builder.file_to_dataframe("json", str(p), json_path="$.a.b")


# ---------------------------------------------------------------------------
# PA-1  size cap and row cap
# ---------------------------------------------------------------------------


def test_file_to_dataframe_rejects_oversized_file(tmp_path: Path) -> None:
    """A file that exceeds max_bytes must raise ValueError before parsing begins (PA-1)."""
    csv = tmp_path / "big.csv"
    csv.write_text("a,b\n1,x\n2,y\n")
    # Pass a tiny max_bytes so the real file (just a few bytes) looks oversized.
    with pytest.raises(ValueError, match="size limit"):
        hyper_builder.file_to_dataframe("csv", str(csv), max_bytes=1)


def test_file_to_dataframe_clamps_rows_csv(tmp_path: Path) -> None:
    """CSV rows beyond max_rows must be dropped, not returned (PA-1)."""
    csv = tmp_path / "data.csv"
    rows = "\n".join(f"{i},{i * 2}" for i in range(1, 201))
    csv.write_text(f"a,b\n{rows}\n")
    df = hyper_builder.file_to_dataframe("csv", str(csv), max_rows=50)
    assert len(df) == 50


def test_file_to_dataframe_clamps_rows_xlsx(tmp_path: Path) -> None:
    """Excel rows beyond max_rows must be dropped after reading (PA-1)."""
    p = tmp_path / "data.xlsx"
    df_src = pd.DataFrame({"x": range(200)})
    df_src.to_excel(str(p), index=False)
    df = hyper_builder.file_to_dataframe("xlsx", str(p), max_rows=30)
    assert len(df) == 30


def test_file_to_dataframe_clamps_rows_parquet(tmp_path: Path) -> None:
    """Parquet rows beyond max_rows must be dropped after reading (PA-1)."""
    p = tmp_path / "data.parquet"
    pd.DataFrame({"col": range(500)}).to_parquet(str(p))
    df = hyper_builder.file_to_dataframe("parquet", str(p), max_rows=100)
    assert len(df) == 100


def test_file_to_dataframe_clamps_rows_json(tmp_path: Path) -> None:
    """JSON array rows beyond max_rows must be dropped (PA-1)."""
    p = tmp_path / "data.json"
    data = [{"id": i} for i in range(300)]
    p.write_text(json.dumps(data))
    df = hyper_builder.file_to_dataframe("json", str(p), max_rows=75)
    assert len(df) == 75


def test_file_to_dataframe_clamps_rows_jsonl(tmp_path: Path) -> None:
    """JSONL rows beyond max_rows must be dropped via chunked reading (PA-1)."""
    p = tmp_path / "data.jsonl"
    lines = "\n".join(json.dumps({"n": i}) for i in range(400))
    p.write_text(lines + "\n")
    df = hyper_builder.file_to_dataframe("jsonl", str(p), max_rows=60)
    assert len(df) == 60
