# Schema Provenance

## twb_2026.1.0.xsd

| Field | Value |
|-------|-------|
| Upstream repo | `tableau/tableau-document-schemas` |
| Upstream path | `schemas/2026_1/twb_2026.1.0.xsd` |
| Pinned commit SHA | `e5560910473c867e31adbe21621772a8177d0524` |
| Retrieval date | 2026-06-24 |
| Local modifications | Two `xs:import` statements have `schemaLocation` attributes added (`user.xsd` and `xml.xsd`) so lxml can resolve them offline with `no_network=True`. No other changes. |

Fetch command used:
```
gh api 'repos/tableau/tableau-document-schemas/contents/schemas/2026_1/twb_2026.1.0.xsd?ref=e5560910473c867e31adbe21621772a8177d0524' \
  --jq '.content' | base64 --decode
```

To re-vendor: fetch at a new SHA, apply the same two `schemaLocation` additions to the
`xs:import` lines, verify the schema compiles offline (the test suite does this automatically),
and update this file's commit SHA.

## xml.xsd

| Field | Value |
|-------|-------|
| Source | W3C canonical — https://www.w3.org/2001/xml.xsd |
| Retrieval date | 2026-06-24 |
| Local modifications | None |

This is the official W3C schema for the `http://www.w3.org/XML/1998/namespace` namespace
(defines `xml:base`, `xml:lang`, `xml:space`, `xml:id` attributes). Required because
`twb_2026.1.0.xsd` uses `xml:base` in four places and lxml cannot fetch it from the network
when `no_network=True`.

## user.xsd

**AUTHORED LOCALLY** — not vendored from any upstream source.

The `tableau/tableau-document-schemas` repo does not publish a schema for the
`http://www.tableausoftware.com/xml/user` namespace. This stub provides the single
construct referenced by `twb_2026.1.0.xsd`: the `UserAttributes-AG` attributeGroup.
The stub uses `anyAttribute processContents="skip"` to accept any user: attributes
without validation, matching the permissive posture used throughout the rest of the
TWB schema. See `user.xsd` for the inline documentation.
