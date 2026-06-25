"""DataFrame / SQL -> .hyper extract authoring.

Uses pantab (which infers Hyper column types from pandas dtypes, so no manual
dtype mapping is needed). All extracts are written to the conventional
``Extract.Extract`` table so the packaged ``.tds`` can reference them reliably.

Multi-format ingest:
  - csv    : pd.read_csv
  - json   : pd.read_json  (top-level array or {"key": [...]} with jsonPath="$.key")
  - jsonl  : pd.read_json(lines=True)
  - xlsx   : pd.read_excel  (openpyxl engine; excelSheet selects sheet by name or index)
  - xls    : pd.read_excel  (same)
  - parquet: pd.read_parquet (pyarrow engine; already a dep via pantab)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import pantab
from tableauhyperapi import Connection, HyperProcess, TableName, Telemetry

DEFAULT_MAX_ROWS = 1_000_000

#: Hard byte-size cap on files accepted by :func:`file_to_dataframe`.
#: Rejects files before any parsing begins (PA-1).
MAX_FILE_BYTES = 500 * 1024 * 1024  # 500 MB


def dtype_to_category(dtype: Any) -> str:
    """Map a pandas dtype to a coarse, planner-friendly category string.

    Returns one of: ``"number"``, ``"date"``, ``"boolean"``, ``"string"``.

    The mapping is intentionally coarse so the planner can build worksheets
    using only the information it needs (mark type, shelf placement) without
    knowing Tableau's exact internal type names.

    Args:
        dtype: A pandas dtype object (e.g. ``dtype('int64')``).  The mapping
            is performed on the string representation, so any object with a
            meaningful ``str()`` works.

    Returns:
        A planner-friendly category string: one of ``"number"``, ``"date"``,
        ``"boolean"``, or ``"string"``.
    """
    dtype_str = str(dtype).lower()
    if dtype_str.startswith(("int", "uint", "float")):
        return "number"
    if dtype_str.startswith(("datetime", "timedelta")):
        return "date"
    if dtype_str == "bool":
        return "boolean"
    return "string"


def dataframe_columns(df: pd.DataFrame) -> list[dict[str, str]]:
    """Return the column schema of *df* as a list of ``{name, dataType}`` dicts.

    ``dataType`` is derived from :func:`dtype_to_category` — one of
    ``"number"``, ``"date"``, ``"boolean"``, ``"string"``.

    Args:
        df: Any pandas DataFrame.

    Returns:
        A list of ``{"name": <column name>, "dataType": <category>}`` dicts,
        one per column, preserving the original column order.
    """
    return [{"name": str(col), "dataType": dtype_to_category(df[col].dtype)} for col in df.columns]


# Tableau's convention for a single-table extract.
EXTRACT_SCHEMA = "Extract"
EXTRACT_TABLE = "Extract"
EXTRACT_TABLE_NAME = TableName(EXTRACT_SCHEMA, EXTRACT_TABLE)


@dataclass(frozen=True)
class ColumnSpec:
    """A column as Tableau describes it in a .tds/.twb."""

    name: str
    datatype: str  # integer | real | string | boolean | date | datetime
    role: str  # dimension | measure
    type: str  # nominal | ordinal | quantitative


def _tableau_types_from_hyper(type_tag_name: str) -> tuple[str, str, str]:
    """Map a Hyper SqlType tag name -> (tableau datatype, role, type)."""
    tag = type_tag_name.upper()
    if tag in {"SMALL_INT", "INT", "BIG_INT"}:
        return "integer", "measure", "quantitative"
    if tag in {"DOUBLE", "NUMERIC", "FLOAT"}:
        return "real", "measure", "quantitative"
    if tag == "BOOL":
        return "boolean", "dimension", "nominal"
    if tag == "DATE":
        return "date", "dimension", "ordinal"
    if tag in {"TIMESTAMP", "TIMESTAMP_TZ"}:
        return "datetime", "dimension", "ordinal"
    return "string", "dimension", "nominal"


def dataframe_to_hyper(df: pd.DataFrame, out_path: Path) -> Path:
    """Write ``df`` to a .hyper extract at ``out_path`` (table = Extract.Extract)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pantab.frame_to_hyper(df, str(out_path), table=EXTRACT_TABLE_NAME)
    return out_path


def records_to_dataframe(records: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame.from_records(records)


def read_hyper_columns(hyper_path: Path) -> list[ColumnSpec]:
    """Introspect the extract's columns directly from the .hyper (source of truth)."""
    specs: list[ColumnSpec] = []
    with (
        HyperProcess(telemetry=Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU) as hyper,
        Connection(endpoint=hyper.endpoint, database=str(hyper_path)) as conn,
    ):
        table_def = conn.catalog.get_table_definition(EXTRACT_TABLE_NAME)
        for column in table_def.columns:
            datatype, role, ttype = _tableau_types_from_hyper(column.type.tag.name)
            specs.append(
                ColumnSpec(name=column.name.unescaped, datatype=datatype, role=role, type=ttype)
            )
    return specs


def hyper_table_info(hyper_path: Path) -> dict[str, Any]:
    """Return {'row_count': int, 'columns': {name: hyper_type_tag}} for tests."""
    with (
        HyperProcess(telemetry=Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU) as hyper,
        Connection(endpoint=hyper.endpoint, database=str(hyper_path)) as conn,
    ):
        table_def = conn.catalog.get_table_definition(EXTRACT_TABLE_NAME)
        columns = {c.name.unescaped: c.type.tag.name for c in table_def.columns}
        row_count = conn.execute_scalar_query(f"SELECT COUNT(*) FROM {EXTRACT_TABLE_NAME}")
    return {"row_count": int(row_count), "columns": columns}


_FORMATTED_NUM_RE = re.compile(r"^-?\$?-?[\d,]+(?:\.\d+)?%?$")
_ACCT_NEG_RE = re.compile(r"^\(\s*\$?[\d,]+(?:\.\d+)?%?\s*\)$")
_NULL_TOKENS = {"", "null", "nan", "none", "-", "n/a", "na"}


def _parse_formatted_number(value: object) -> float | None:
    """Parse a display-formatted number ('$1,234', '20%', '($5)') to float, else None.

    Currency ($) and thousands (,) separators are stripped; a trailing percent is
    converted to a ratio (20% -> 0.20); accounting negatives '($5)' -> -5.0.
    """
    s = str(value).strip()
    if s.lower() in _NULL_TOKENS:
        return None
    negative = False
    if _ACCT_NEG_RE.match(s):
        negative = True
        s = s[1:-1].strip()
    compact = s.replace(" ", "")
    if not _FORMATTED_NUM_RE.match(compact):
        return None
    is_pct = compact.endswith("%")
    cleaned = compact.replace("$", "").replace(",", "").replace("%", "")
    try:
        num = float(cleaned)
    except ValueError:
        return None
    if is_pct:
        num /= 100.0
    return -num if negative else num


def _coerce_formatted_numerics(df: pd.DataFrame) -> pd.DataFrame:
    """Convert display-formatted string columns ('$16', '20%') to numeric in place.

    A column is converted only when >= 90% of its non-blank values parse as a
    formatted number, so genuine text dimensions (names, IDs, categories) are left
    untouched. Real-world exports (e.g. Tableau "migrated data") carry measures as
    display strings that would otherwise import as un-aggregatable text.
    """
    for col in df.columns:
        if df[col].dtype != object:
            continue
        series = df[col]
        text = series.astype(str).str.strip()
        nonblank = text[~text.str.lower().isin(_NULL_TOKENS)]
        if len(nonblank) == 0:
            continue
        parsed_sample = nonblank.map(_parse_formatted_number)
        if parsed_sample.notna().mean() >= 0.9:
            df[col] = series.map(_parse_formatted_number)
    return df


def _sniff_csv_dialect(p: Path) -> tuple[str, str]:
    """Sniff ``(encoding, delimiter)`` for a delimited text file from its first bytes.

    Detects a UTF-16/UTF-8 byte-order mark and whether the header row is tab- or
    comma-separated. Used only when the caller does not pass ``encoding``/``sep``.
    Many real-world exports (e.g. Tableau's "migrated data" CSVs) are UTF-16 LE and
    TAB-separated, which the default ``pd.read_csv`` (UTF-8 + comma) cannot parse.

    Returns a best-effort guess; falls back to ``("utf-8", ",")``.
    """
    with p.open("rb") as fh:
        head = fh.read(65536)
    if head[:2] in (b"\xff\xfe", b"\xfe\xff"):
        encoding = "utf-16"
    elif head[:3] == b"\xef\xbb\xbf":
        encoding = "utf-8-sig"
    else:
        encoding = "utf-8"
    try:
        text = head.decode(encoding, errors="replace")
    except LookupError:
        text = head.decode("utf-8", errors="replace")
    lines = text.splitlines()
    first_line = lines[0] if lines else ""
    delimiter = "\t" if first_line.count("\t") > first_line.count(",") else ","
    return encoding, delimiter


def file_to_dataframe(
    file_type: str,
    path: str,
    excel_sheet: str | int | None = None,
    json_path: str | None = None,
    max_rows: int = DEFAULT_MAX_ROWS,
    max_bytes: int = MAX_FILE_BYTES,
    encoding: str | None = None,
    sep: str | None = None,
) -> pd.DataFrame:
    """Read a local file into a DataFrame, with pre-read size and post-read row caps.

    Supported file_type values: csv, json, jsonl, xlsx, xls, parquet.

    ``excel_sheet`` selects the sheet by name (str) or 0-based index (int); defaults to 0.
    ``json_path`` supports a single-level selector of the form ``$.<key>`` (e.g. ``$.data``).
    Anything deeper is rejected with a clear error rather than silently mis-parsing.

    ``encoding`` / ``sep`` (csv only): explicit overrides for the text encoding and the
    column delimiter. When either is omitted, the dialect is auto-sniffed from the file's
    first bytes (BOM → encoding; tab-vs-comma in the header → delimiter), so UTF-16 / TSV
    exports load without the caller having to know the encoding up front.

    Safety caps (PA-1):
    - ``max_bytes``: rejects files larger than this limit before any parsing begins.
    - ``max_rows``: clamps the returned DataFrame to at most this many rows.  For csv/json/jsonl
      the cap is applied during reading (``nrows`` / chunked); for xlsx/parquet it is a
      post-read ``.head()`` call (those readers do not support early row truncation in a
      safe/uniform way).  Either way, the result never exceeds ``max_rows`` rows.
    """
    ftype = file_type.lower()
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"file path is not a regular file: {path!r}")

    file_size = p.stat().st_size
    if file_size > max_bytes:
        limit_mb = max_bytes // (1024 * 1024)
        raise ValueError(
            f"File exceeds the {limit_mb} MB size limit "
            f"({file_size // (1024 * 1024)} MB). Reduce the file size before ingesting."
        )

    if ftype == "csv":
        enc, delim = encoding, sep
        if enc is None or delim is None:
            sniffed_enc, sniffed_delim = _sniff_csv_dialect(p)
            enc = enc or sniffed_enc
            delim = delim or sniffed_delim
        return _coerce_formatted_numerics(
            pd.read_csv(p, nrows=max_rows, encoding=enc, sep=delim)
        )

    if ftype in {"xlsx", "xls"}:
        sheet: str | int = excel_sheet if excel_sheet is not None else 0
        df_excel = pd.read_excel(p, sheet_name=sheet, engine="openpyxl")
        if len(df_excel) > max_rows:
            df_excel = df_excel.head(max_rows)
        return _coerce_formatted_numerics(df_excel)

    if ftype == "parquet":
        df_parquet = pd.read_parquet(p)
        if len(df_parquet) > max_rows:
            df_parquet = df_parquet.head(max_rows)
        return _coerce_formatted_numerics(df_parquet)

    if ftype in {"json", "jsonl"}:
        if json_path is not None:
            # json_path requires full parse first; apply row cap afterward.
            kwargs_full: dict[str, Any] = {"lines": True} if ftype == "jsonl" else {}
            raw: Any = pd.read_json(p, **kwargs_full)

            # Only support single-level $.<key> selectors.
            match = re.fullmatch(r"\$\.([A-Za-z_][A-Za-z0-9_]*)", json_path.strip())
            if not match:
                raise ValueError(
                    f"jsonPath {json_path!r} is not supported. Only single-level selectors "
                    f"of the form '$.key' are accepted."
                )
            key = match.group(1)
            if ftype == "json" and isinstance(raw, pd.DataFrame):
                # raw may be a dict-of-arrays frame; try orient="index" fallback
                # by re-reading and extracting the key from the first row.
                try:
                    raw_dict: Any = pd.read_json(p, orient="index")
                    raw = pd.DataFrame(list(raw_dict[key]))
                except Exception:
                    raw = pd.DataFrame(list(raw[key]))
            else:
                raw = pd.DataFrame(list(raw[key]))

            df_json = pd.DataFrame(raw)
        elif ftype == "jsonl":
            # jsonl: use chunked reading to cap rows without loading the full file.
            chunks = []
            remaining = max_rows
            reader = pd.read_json(p, lines=True, chunksize=10_000)
            for chunk in reader:
                if remaining <= 0:
                    break
                chunks.append(chunk.head(remaining))
                remaining -= len(chunk)
            df_json = pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame()
        else:
            # Plain JSON: nrows is not supported by pd.read_json; load then head().
            df_json = pd.read_json(p)

        if len(df_json) > max_rows:
            df_json = df_json.head(max_rows)
        return _coerce_formatted_numerics(df_json)

    raise ValueError(
        f"Unsupported file_type: {file_type!r}. "
        "Supported values: csv, json, jsonl, xlsx, xls, parquet."
    )


def query_to_dataframe(
    connection: dict[str, Any], sql: str, max_rows: int = DEFAULT_MAX_ROWS
) -> pd.DataFrame:
    """Run SQL (or read a CSV/file) and return a DataFrame, capped at ``max_rows`` rows.

    Database drivers are imported lazily so the snowflake/postgres extras are only
    required when those connection types are actually used.
    """
    ctype = connection.get("type")
    if ctype == "csv":
        path = connection.get("path")
        if not path:
            raise ValueError("csv connection requires a 'path'")
        csv_file = Path(path)
        if not csv_file.is_file():
            raise ValueError(f"csv path is not a regular file: {path}")
        df = pd.read_csv(csv_file)
    elif ctype == "snowflake":
        df = _snowflake_query(connection, sql)
    elif ctype == "postgres":
        df = _postgres_query(connection, sql)
    else:
        raise ValueError(f"Unsupported connection type: {ctype!r}")

    if len(df) > max_rows:
        df = df.head(max_rows)
    return df


def _snowflake_query(connection: dict[str, Any], sql: str) -> pd.DataFrame:
    import snowflake.connector  # lazy import; requires the 'connectors' extra

    conn = snowflake.connector.connect(
        account=connection["account"],
        user=connection["user"],
        password=connection.get("password"),
        token=connection.get("token"),
        database=connection.get("database"),
        schema=connection.get("schema"),
        warehouse=connection.get("warehouse"),
        role=connection.get("role"),
    )
    try:
        cursor = conn.cursor()
        cursor.execute(sql)
        return cursor.fetch_pandas_all()
    finally:
        conn.close()


def _postgres_query(connection: dict[str, Any], sql: str) -> pd.DataFrame:
    import psycopg  # lazy import; requires the 'connectors' extra

    # Pass credentials as keyword args (not an interpolated conninfo string) so a
    # password containing spaces/quotes can't corrupt the connection or leak in traces.
    with psycopg.connect(
        host=connection.get("host"),
        port=connection.get("port", 5432),
        dbname=connection.get("database"),
        user=connection.get("user"),
        password=connection.get("password"),
    ) as conn:
        return pd.read_sql(sql, conn)
