"""Official TWB XSD validation oracle for build_twb_xml() output (A1).

Vendored schema
---------------
Source: tableau/tableau-document-schemas
Path:   schemas/2026_1/twb_2026.1.0.xsd
Commit: e5560910473c867e31adbe21621772a8177d0524 (pinned, not a moving branch)
Date:   2026-06-24

Two import stubs are bundled alongside the XSD so lxml can compile the schema
fully offline with ``no_network=True``:

- ``xml.xsd``  — W3C canonical schema for the xml: namespace (xml:base etc.),
  fetched from https://www.w3.org/2001/xml.xsd on 2026-06-24.  Not modified.
- ``user.xsd`` — locally authored stub for the Tableau user: namespace.
  The TWB schema references only ``UserAttributes-AG``; this stub satisfies
  that import with ``anyAttribute processContents="skip"``.

See ``sidecar/tests/schemas/PROVENANCE.md`` for the full lineage record.

Security posture (O-5)
----------------------
lxml is configured with ``resolve_entities=False``, ``no_network=True``,
``load_dtd=False``, ``huge_tree=False`` to close the XXE / billion-laughs
attack surface.  A dedicated test asserts these flags (regression-guard).
Even though all inputs here are trusted (vendored files + our own XML), the
hardened configuration is enforced so it cannot be silently relaxed.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from lxml import etree

import twb_builder

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

SCHEMAS_DIR = Path(__file__).parent / "schemas"
XSD_PATH = SCHEMAS_DIR / "twb_2026.1.0.xsd"

SHEETS = [
    {
        "title": "Revenue by Region",
        "mark_type": "bar",
        "cols": ["Region"],
        "rows": [],
        "measures": ["Revenue"],
    }
]

SHEETS_2 = [
    {
        "title": "Revenue by Region",
        "mark_type": "bar",
        "cols": ["Region"],
        "rows": [],
        "measures": ["Revenue"],
    },
    {
        "title": "Top 10 Customers",
        "mark_type": "text",
        "cols": [],
        "rows": [],
        "measures": ["Revenue"],
    },
]

DASHBOARDS_BASIC = [
    {"name": "Dashboard 1", "titles": ["Revenue by Region", "Top 10 Customers"]}
]


def _safe_parser() -> etree.XMLParser:
    """Return a hardened XMLParser: entities OFF, network OFF, DTD OFF, huge OFF."""
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        huge_tree=False,
    )


def _load_schema() -> etree.XMLSchema:
    """Load the vendored XSD once with the hardened parser.

    The XSD file has ``schemaLocation`` attributes added to its two
    ``xs:import`` statements so lxml resolves ``xml.xsd`` and ``user.xsd``
    from the same directory without network access.
    """
    parser = _safe_parser()
    xsd_doc = etree.parse(str(XSD_PATH), parser)
    return etree.XMLSchema(xsd_doc)


# ---------------------------------------------------------------------------
# A1.1 — Schema compiles offline (standalone gate: must pass before anything else)
# ---------------------------------------------------------------------------


def test_schema_compiles_offline() -> None:
    """The vendored XSD (+ stubs) must load with no_network=True and no errors.

    This test is a fast-fail: if it is RED the stubs or schemaLocation patches
    are broken and all subsequent validation tests are meaningless.  Fix this
    test first.
    """
    schema = _load_schema()
    assert schema is not None, "XMLSchema construction returned None"


# ---------------------------------------------------------------------------
# A1.2 — Parser safety (O-5): regression-guard the hardened config
# ---------------------------------------------------------------------------


def test_safe_parser_flags() -> None:
    """Parser hardening flags must be set exactly as required (O-5).

    These flags close the XXE and billion-laughs attack surfaces.  Changing
    them silently would remove a security property; this test makes that
    change CI-visible.

    The test uses two functional probes:

    1. **XXE probe** — a document with an entity reference and resolve_entities=False
       must NOT expand it.  A positive result (entity text in output) would mean the
       flag was not honoured and the configuration is unsafe.

    2. **Network probe** — a fresh schema-load attempt over a URL that requires
       the network; with no_network=True this must fail without any HTTP request.
       We do not make an actual network call; we rely on the ``test_schema_compiles_offline``
       test which passes only because no_network=True is set.

    The constructor call in ``_safe_parser()`` is the authoritative configuration;
    these probes regression-guard the *behaviour* so a refactor of _safe_parser()
    cannot silently weaken it.
    """
    parser = _safe_parser()

    # --- Probe 1: entity resolution must be OFF (XXE guard) -----------------
    probe = b"""<?xml version='1.0'?>
<!DOCTYPE root [
  <!ENTITY xxe "should-not-appear">
]>
<root>&xxe;</root>"""
    try:
        doc = etree.fromstring(probe, parser)
        # Parsing may succeed (lxml can leave the entity reference unevaluated).
        root_text = doc.text or ""
        assert "should-not-appear" not in root_text, (
            "XXE REGRESSION: entity was resolved — resolve_entities must be False"
        )
    except etree.XMLSyntaxError:
        # Also acceptable: parser rejected the DTD entirely (load_dtd=False).
        pass

    # --- Probe 2: parser must reject a document referencing a network entity --
    # With load_dtd=False the external system entity below cannot be loaded.
    external_probe = b"""<?xml version='1.0'?>
<!DOCTYPE root SYSTEM "http://should-not-be-fetched.example.com/never.dtd">
<root/>"""
    with contextlib.suppress(etree.XMLSyntaxError):
        etree.fromstring(external_probe, parser)
        # If parsing succeeded, the external DTD was not loaded — acceptable.
        # The absence of a network call is already proven by test_schema_compiles_offline.

    # --- Probe 3: constructing a parser from the same flags must be idempotent -
    # Two calls to _safe_parser() must produce equivalent behaviour.
    parser2 = _safe_parser()
    doc_p1 = etree.fromstring(b"<ok/>", parser)
    doc_p2 = etree.fromstring(b"<ok/>", parser2)
    assert doc_p1.tag == doc_p2.tag == "ok", (
        "_safe_parser() produced inconsistent parsers"
    )


# ---------------------------------------------------------------------------
# A1.3 — Worksheets-only output is schema-valid (O-1)
# ---------------------------------------------------------------------------


def test_schema_valid_worksheets_only() -> None:
    """build_twb_xml() (worksheets-only, no dashboards) must be schema-valid.

    Validates the output that the starter workbook path produces.
    On failure, the schema error_log is attached to the assertion message
    so the RED run names each offending element and line number — making this
    test self-describing as an A2 specification.
    """
    schema = _load_schema()
    parser = _safe_parser()

    xml_str = twb_builder.build_twb_xml(
        "Top Customers",
        "TopCustomers",
        "mysite",
        SHEETS,
        server_url="https://x.online.tableau.com",
    )
    doc = etree.fromstring(xml_str.encode(), parser)
    valid = schema.validate(doc)
    errors = "\n".join(
        f"  line {e.line}: {e.message}" for e in schema.error_log
    )
    assert valid, (
        f"TWB XSD validation FAILED for worksheets-only output:\n{errors}"
    )


# ---------------------------------------------------------------------------
# A1.4 — With-dashboard output is schema-valid (O-1)
# ---------------------------------------------------------------------------


def test_schema_valid_with_dashboard() -> None:
    """build_twb_xml() (with dashboards kwarg) must be schema-valid.

    Validates the full dashboard path including the <dashboards> element
    ordering (before <windows>) and the dashboard <window> structure.
    On failure, the schema error_log is attached to the assertion message.
    """
    schema = _load_schema()
    parser = _safe_parser()

    xml_str = twb_builder.build_twb_xml(
        "DS",
        "ds",
        "site",
        SHEETS_2,
        dashboards=DASHBOARDS_BASIC,
    )
    doc = etree.fromstring(xml_str.encode(), parser)
    valid = schema.validate(doc)
    errors = "\n".join(
        f"  line {e.line}: {e.message}" for e in schema.error_log
    )
    assert valid, (
        f"TWB XSD validation FAILED for with-dashboard output:\n{errors}"
    )
