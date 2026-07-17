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


# ---------------------------------------------------------------------------
# Encoding/delimiter auto-sniff (UTF-16 / TSV) + explicit override
# ---------------------------------------------------------------------------


def _write_utf16_tsv(p: Path) -> None:
    rows = ["region\tcustomer\trevenue", "West\tAcme\t$1,234", "East\tBeta\t$5"]
    p.write_bytes(("\n".join(rows) + "\n").encode("utf-16"))


def test_file_to_dataframe_csv_autosniffs_utf16_tsv(tmp_path: Path) -> None:
    p = tmp_path / "migrated.csv"
    _write_utf16_tsv(p)
    df = hyper_builder.file_to_dataframe("csv", str(p))  # no encoding/sep passed
    assert list(df.columns) == ["region", "customer", "revenue"]
    assert len(df) == 2
    assert df["region"].tolist() == ["West", "East"]


def test_file_to_dataframe_csv_explicit_encoding_sep(tmp_path: Path) -> None:
    p = tmp_path / "migrated.csv"
    _write_utf16_tsv(p)
    df = hyper_builder.file_to_dataframe("csv", str(p), encoding="utf-16", sep="\t")
    assert list(df.columns) == ["region", "customer", "revenue"]
    assert len(df) == 2


def test_sniff_csv_dialect_detects_utf16_tab(tmp_path: Path) -> None:
    p = tmp_path / "m.csv"
    _write_utf16_tsv(p)
    enc, delim = hyper_builder._sniff_csv_dialect(p)
    assert enc == "utf-16"
    assert delim == "\t"


def test_sniff_csv_dialect_defaults_utf8_comma(tmp_path: Path) -> None:
    p = tmp_path / "plain.csv"
    p.write_text("a,b,c\n1,2,3\n")
    enc, delim = hyper_builder._sniff_csv_dialect(p)
    assert enc == "utf-8"
    assert delim == ","


# ---------------------------------------------------------------------------
# Numeric coercion of display-formatted measures ($, %, accounting negatives)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("$16", 16.0),
        ("-$5", -5.0),
        ("($5)", -5.0),
        ("20%", 0.20),
        ("$1,234.5", 1234.5),
        ("$0", 0.0),
        ("4.0", 4.0),
        ("", None),
        ("CA-2011-103800", None),
        ("Texas", None),
    ],
)
def test_parse_formatted_number(raw: str, expected: float | None) -> None:
    assert hyper_builder._parse_formatted_number(raw) == expected


def test_file_to_dataframe_coerces_formatted_measures(tmp_path: Path) -> None:
    """Currency/percent string columns become numeric; text dimensions stay text."""
    csv = tmp_path / "f.csv"
    csv.write_text(
        "Region,Sales,Discount,OrderID\n"
        "West,$1234,20%,CA-001\n"
        "East,$56,10%,CA-002\n"
        "Central,($7),0%,CA-003\n"
    )
    df = hyper_builder.file_to_dataframe("csv", str(csv))
    assert str(df["Sales"].dtype).startswith(("float", "int"))
    assert str(df["Discount"].dtype).startswith("float")
    assert df["Sales"].tolist() == [1234.0, 56.0, -7.0]
    assert df["Discount"].tolist() == [0.20, 0.10, 0.0]
    assert df["Region"].tolist() == ["West", "East", "Central"]
    assert df["OrderID"].dtype == object


def test_coercion_leaves_mostly_text_columns_alone(tmp_path: Path) -> None:
    """A column with <90% numeric-looking values is NOT coerced."""
    csv = tmp_path / "g.csv"
    csv.write_text("code\n$5\nABC\nDEF\nGHI\nJKL\n")  # 1/5 numeric -> below 90%
    df = hyper_builder.file_to_dataframe("csv", str(csv))
    assert df["code"].dtype == object
    assert df["code"].tolist() == ["$5", "ABC", "DEF", "GHI", "JKL"]


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


# ---------------------------------------------------------------------------
# Date coercion (E3 pre-req: Pulse needs a real time dimension, not strings)
# ---------------------------------------------------------------------------


def test_file_to_dataframe_coerces_date_strings(tmp_path: Path) -> None:
    """m/d/Y date strings become datetime; ID-like strings with hyphens stay text."""
    csv = tmp_path / "d.csv"
    csv.write_text(
        "Order Date,Ship Date,Order ID,Region\n"
        "1/3/2013,1/7/2013,CA-2011-103800,West\n"
        "2/5/2013,2/9/2013,CA-2011-112326,East\n"
        "11/22/2014,11/26/2014,US-2012-108966,South\n"
    )
    df = hyper_builder.file_to_dataframe("csv", str(csv))
    assert str(df["Order Date"].dtype).startswith("datetime64")
    assert str(df["Ship Date"].dtype).startswith("datetime64")
    # ID-like strings carry the hyphen hint but fail to parse -> stay text.
    assert df["Order ID"].dtype == object
    assert df["Region"].dtype == object
    assert df["Order Date"].iloc[0].year == 2013
    assert df["Order Date"].iloc[2].month == 11


def test_date_coercion_skips_integer_strings(tmp_path: Path) -> None:
    """Plain integers ('2013') lack the separator hint and are never dates."""
    csv = tmp_path / "y.csv"
    csv.write_text("Year,Label\n2013,a\n2014,b\n2015,c\n")
    df = hyper_builder.file_to_dataframe("csv", str(csv))
    assert not str(df["Year"].dtype).startswith("datetime64")


def test_date_coercion_requires_90pct(tmp_path: Path) -> None:
    """A column with <90% parseable dates is NOT coerced."""
    csv = tmp_path / "m.csv"
    csv.write_text("v\n1/3/2013\nnot-a-date\nalso-not\nnope-1\nnope-2\n")
    df = hyper_builder.file_to_dataframe("csv", str(csv))
    assert df["v"].dtype == object


def test_date_coercion_preserves_blanks_as_nat(tmp_path: Path) -> None:
    """Blank/NULL tokens in a date column become NaT, not strings.

    Two columns so the empty-date row is not a blank LINE (pandas'
    skip_blank_lines default would drop a fully blank row).
    """
    csv = tmp_path / "b.csv"
    csv.write_text("d,x\n1/3/2013,a\nNULL,b\n2/5/2013,c\n,d\n3/7/2013,e\n")
    df = hyper_builder.file_to_dataframe("csv", str(csv))
    assert str(df["d"].dtype).startswith("datetime64")
    assert df["d"].isna().sum() == 2
