"""DataFrame / SQL -> .hyper extract authoring.

Uses pantab (which infers Hyper column types from pandas dtypes, so no manual
dtype mapping is needed). All extracts are written to the conventional
``Extract.Extract`` table so the packaged ``.tds`` can reference them reliably.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import pantab
from tableauhyperapi import Connection, HyperProcess, TableName, Telemetry

DEFAULT_MAX_ROWS = 1_000_000

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


def query_to_dataframe(
    connection: dict[str, Any], sql: str, max_rows: int = DEFAULT_MAX_ROWS
) -> pd.DataFrame:
    """Run SQL (or read a CSV) and return a DataFrame, capped at ``max_rows`` rows.

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
