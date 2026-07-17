"""Package a .hyper extract into a .tdsx published-datasource file.

There is no maintained Document-API library that *creates* a .tdsx, so we build
the .tds XML directly (connection class='hyper' pointing at the embedded extract)
and zip it together with the .hyper under Data/. The .tds is derived from the
extract's actual columns (read back from the .hyper) so it always matches the data.

Phase E2 slice C adds a second, unrelated builder in this module —
``build_live_tds`` — for live Cloud connections (Snowflake, Presto). See its
own docstring below; it emits a plain ``.tds`` (no zip, no extract, no
credentials) rather than a ``.tdsx``.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

from hyper_builder import (
    EXTRACT_SCHEMA,
    EXTRACT_TABLE,
    ColumnSpec,
    read_hyper_columns,
)

TDS_VERSION = "18.1"

# Tableau remote-type (ODBC) codes used in metadata-records.
_REMOTE_TYPE = {
    "integer": "20",
    "real": "5",
    "string": "129",
    "boolean": "11",
    "date": "133",
    "datetime": "135",
}

_RELATION_TABLE = f"[{EXTRACT_SCHEMA}].[{EXTRACT_TABLE}]"


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_")
    return cleaned or "datasource"


def build_tds_xml(datasource_name: str, hyper_filename: str, columns: list[ColumnSpec]) -> str:
    """Build the .tds XML for a single-table Hyper extract datasource."""
    conn_id = f"hyper.{_slug(datasource_name)}"
    dbname = f"Data/{hyper_filename}"

    datasource = ET.Element(
        "datasource",
        {
            "formatted-name": f"federated.{_slug(datasource_name)}",
            "inline": "true",
            "version": TDS_VERSION,
        },
    )

    federated = ET.SubElement(datasource, "connection", {"class": "federated"})
    named_conns = ET.SubElement(federated, "named-connections")
    named_conn = ET.SubElement(
        named_conns, "named-connection", {"caption": datasource_name, "name": conn_id}
    )
    ET.SubElement(
        named_conn,
        "connection",
        {
            "class": "hyper",
            "dbname": dbname,
            "schema": EXTRACT_SCHEMA,
            "tablename": EXTRACT_TABLE,
            "default-settings": "yes",
        },
    )
    ET.SubElement(
        federated,
        "relation",
        {
            "connection": conn_id,
            "name": EXTRACT_TABLE,
            "table": _RELATION_TABLE,
            "type": "table",
        },
    )

    metadata = ET.SubElement(federated, "metadata-records")
    for ordinal, col in enumerate(columns):
        record = ET.SubElement(metadata, "metadata-record", {"class": "column"})
        ET.SubElement(record, "remote-name").text = col.name
        ET.SubElement(record, "remote-type").text = _REMOTE_TYPE.get(col.datatype, "129")
        ET.SubElement(record, "local-name").text = f"[{col.name}]"
        ET.SubElement(record, "parent-name").text = f"[{EXTRACT_TABLE}]"
        ET.SubElement(record, "remote-alias").text = col.name
        ET.SubElement(record, "ordinal").text = str(ordinal)
        ET.SubElement(record, "local-type").text = col.datatype

    # Top-level field definitions.
    for col in columns:
        ET.SubElement(
            datasource,
            "column",
            {
                "datatype": col.datatype,
                "name": f"[{col.name}]",
                "role": col.role,
                "type": col.type,
            },
        )

    # Mark the datasource as an extract pointing at the embedded .hyper.
    extract = ET.SubElement(
        datasource, "extract", {"count": "-1", "enabled": "true", "units": "records"}
    )
    extract_conn = ET.SubElement(
        extract,
        "connection",
        {
            "class": "hyper",
            "dbname": dbname,
            "schema": EXTRACT_SCHEMA,
            "tablename": EXTRACT_TABLE,
        },
    )
    ET.SubElement(
        extract_conn,
        "relation",
        {"name": EXTRACT_TABLE, "table": _RELATION_TABLE, "type": "table"},
    )

    xml_body = ET.tostring(datasource, encoding="unicode")
    return f"<?xml version='1.0' encoding='utf-8' ?>\n{xml_body}"


def hyper_to_tdsx(hyper_path: Path, datasource_name: str, out_path: Path) -> Path:
    """Build a .tdsx (zip of the .tds + the .hyper under Data/) from a .hyper extract."""
    columns = read_hyper_columns(hyper_path)
    tds_xml = build_tds_xml(datasource_name, hyper_path.name, columns)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{_slug(datasource_name)}.tds", tds_xml)
        archive.write(hyper_path, arcname=f"Data/{hyper_path.name}")
    return out_path


# ---------------------------------------------------------------------------
# Live-connection .tds builder — Phase E2 slice C (data connectivity).
#
# Unlike `hyper_to_tdsx`/`build_tds_xml` above (which describe an *extract*
# backed by an embedded `.hyper` file), `build_live_tds` emits a `.tds` whose
# `<connection>` points directly at a live, Cloud-reachable database —
# Snowflake or Presto/Trino today. There is no `<extract>` element and no
# data is materialized locally: Tableau queries the source live, on every
# render and on every `schedule_refresh` run (see `src/rest/schedules.ts`).
#
# CREDENTIALS ARE NEVER WRITTEN INTO THIS FILE. `username`/`password` are
# supplied separately, at publish time, via the REST `<connectionCredentials>`
# element on the Publish Datasource multipart request (see
# `src/rest/credentials.ts` + `TableauRestClient.publishDatasource`). This
# module only ever receives and emits connection *topology*
# (server/warehouse/schema/database/table/...), never a secret — the
# structural tests in `test_tds_builder_live.py` assert this directly (no
# `password`/`username` substring anywhere in the emitted XML).
#
# VERIFY-LIVE — attribute spelling (per the Phase E2 plan; NOT yet confirmed
# against a Desktop-exported `.tds` for either connector):
#
#   Snowflake (`class="snowflake"`) — documented reference: `server` is
#   `<account>.snowflakecomputing.com`, plus `warehouse`, `dbname`, `schema`,
#   `authentication` (`"username-password"` | `"oauth"`), optional `role`.
#   Key-pair authentication is a KNOWN IMPOSSIBLE case for this path — it is
#   not REST-publishable (Tableau Desktop-only) — `server.py`'s
#   `/datasource/live` route rejects it with a clean, actionable 400 rather
#   than silently emitting bogus XML.
#
#   Presto/Trino (`class="presto"`) — documented reference: `server`, `port`,
#   `catalog`, `schema`, an SSL flag, plus username/password or LDAP at the
#   credential layer (never here, see above). Presto is generally
#   Tableau-Bridge-dependent on Cloud (see the `create_live_datasource` tool
#   description) unless the endpoint is internet-reachable and allowlisted.
#
# `SNOWFLAKE_ATTRS`/`PRESTO_ATTRS` are each a single lookup table (our-key ->
# literal Tableau XML attribute name) so a correction, once a real
# Desktop-exported `.tds` is available for comparison, touches only this
# module.
# ---------------------------------------------------------------------------

#: our-key (snake_case, matches `LiveConnectionSpec.model_dump()` in server.py)
#: -> the XML attribute name Tableau expects on ``<connection class="snowflake">``.
#: VERIFY-LIVE: see the module-section docstring above.
SNOWFLAKE_ATTRS: dict[str, str] = {
    "server": "server",
    "warehouse": "warehouse",
    "dbname": "dbname",
    "db_schema": "schema",
    "authentication": "authentication",
    "role": "role",
}

#: our-key -> the XML attribute name Tableau expects on ``<connection class="presto">``.
#: VERIFY-LIVE: see the module-section docstring above.
PRESTO_ATTRS: dict[str, str] = {
    "server": "server",
    "port": "port",
    "catalog": "catalog",
    "db_schema": "schema",
    "ssl": "ssl",
}

_LIVE_ATTR_MAPS: dict[str, dict[str, str]] = {
    "snowflake": SNOWFLAKE_ATTRS,
    "presto": PRESTO_ATTRS,
}


def _live_connection_attrs(spec: dict[str, Any], attr_map: dict[str, str]) -> dict[str, str]:
    """Project `spec` through `attr_map`.

    Drops unset (``None``) fields and stringifies booleans as ``"yes"``/``"no"``
    (Tableau's XML attribute convention, matching `build_tds_xml` elsewhere).
    """
    attrs: dict[str, str] = {}
    for our_key, xml_attr in attr_map.items():
        value = spec.get(our_key)
        if value is None:
            continue
        if isinstance(value, bool):
            attrs[xml_attr] = "yes" if value else "no"
        else:
            attrs[xml_attr] = str(value)
    return attrs


def build_live_tds(datasource_name: str, connection_spec: dict[str, Any]) -> str:
    """Build a live-connection .tds XML string — no extract, no credentials.

    ``connection_spec`` is the snake_case dict produced by the sidecar's
    ``LiveConnectionSpec.model_dump()`` (THE MODEL_DUMP LESSON: field names,
    never aliases — see that model's docstring in server.py). Required keys:
    ``type`` (``"snowflake"`` | ``"presto"``), ``server``, ``db_schema``,
    ``table``, plus the type-specific keys documented in
    ``SNOWFLAKE_ATTRS``/``PRESTO_ATTRS`` above.

    The caller (``server.py``'s ``/datasource/live`` route) is responsible
    for validating type-specific required fields and rejecting the
    known-impossible Snowflake key-pair case *before* calling this function;
    this function only raises on an unsupported ``type``.
    """
    conn_type = connection_spec["type"]
    attr_map = _LIVE_ATTR_MAPS.get(conn_type)
    if attr_map is None:
        raise ValueError(
            f"Unsupported live connection type {conn_type!r}. Supported: {sorted(_LIVE_ATTR_MAPS)}."
        )

    attrs = _live_connection_attrs(connection_spec, attr_map)
    db_schema = connection_spec["db_schema"]
    table = connection_spec["table"]
    relation_table = f"[{db_schema}].[{table}]"

    conn_id = f"{conn_type}.{_slug(datasource_name)}"
    datasource = ET.Element(
        "datasource",
        {
            "formatted-name": f"federated.{_slug(datasource_name)}",
            "inline": "true",
            "version": TDS_VERSION,
        },
    )
    federated = ET.SubElement(datasource, "connection", {"class": "federated"})
    named_conns = ET.SubElement(federated, "named-connections")
    named_conn = ET.SubElement(
        named_conns, "named-connection", {"caption": datasource_name, "name": conn_id}
    )
    ET.SubElement(named_conn, "connection", {"class": conn_type, **attrs})
    ET.SubElement(
        federated,
        "relation",
        {"connection": conn_id, "name": table, "table": relation_table, "type": "table"},
    )

    xml_body = ET.tostring(datasource, encoding="unicode")
    return f"<?xml version='1.0' encoding='utf-8' ?>\n{xml_body}"
