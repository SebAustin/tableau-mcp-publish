"""Package a .hyper extract into a .tdsx published-datasource file.

There is no maintained Document-API library that *creates* a .tdsx, so we build
the .tds XML directly (connection class='hyper' pointing at the embedded extract)
and zip it together with the .hyper under Data/. The .tds is derived from the
extract's actual columns (read back from the .hyper) so it always matches the data.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

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
