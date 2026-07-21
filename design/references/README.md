# `design/references/` — downloaded exemplar workbooks (provenance)

This directory holds the raw `.twbx` files the user identified as "beautiful" Tableau Public
Superstore dashboards, downloaded with the user's explicit authorization (see PLAN.md context and
`ASSUMPTIONS.md`). **The `.twbx` files themselves are not committed** — they are covered by the
repo-root `.gitignore` (`*.twbx`, `*.twb`; verified with `git check-ignore` against each downloaded file).
This README is the durable, committed record of what was downloaded and when, plus their sha256
for reproducibility — `sidecar/design_miner.py` was run directly against these three files (see
`design/corpus/SCHEMA.md`).

**Source-identity redaction (policy):** these are real people's public Tableau Public workbooks.
Author handles and source URLs are intentionally not published here — each file is cited only by
its opaque, stable `WB-NNN` identifier (see `design/corpus/SCHEMA.md`'s provenance section); the
sha256 below remains the integrity anchor and is independently verifiable against the exact mined
bytes.

## Downloaded exemplars (3 of 4)

| WB-id | Direction | Downloaded | Size (bytes) | sha256 |
|---|---|---|---|---|
| `WB-117` | dark — navy KPI band/header, white BANs | 2026-07-17 | 491446 | `79fb59f394e61002db52e3a4b79eeb5e1b4e145d40218d959d9bc03f43fd60f1` |
| `WB-118` | light — white cards, hairline borders | 2026-07-17 | 1969910 | `f52da3f982cd8c4b9c5f5d48b68f01051496318dcf605c90919e9e4110790c92` |
| `WB-114` | light — white cards, hairline borders | 2026-07-17 | 375946 | `e22672587b11869fe717d6fee292e33ca50c188ba3ded9bf836f5e4bc3962f57` |

sha256 above is of the `.twbx` zip archive itself (as downloaded). `sidecar/design_miner.py`
additionally records the sha256 of the *inner* `.twb` XML member (see
`design/corpus/recipes/*.yaml`'s `sha256` field) — the two are different hashes for the same
logical file; both are reproducible from the `.twbx`.

## Not downloadable: fourth exemplar (documented design-around)

The plan's fourth exemplar could not be downloaded: **the author disabled downloads** on Tableau
Public for that workbook (no `.twbx` download endpoint is exposed; the workbook is view-only).
This was verified by attempting the same fixed-URL-template download pattern used successfully
for the other three exemplars, which returned no downloadable archive for this one.

**Design-around:** the *dark* visual direction the fourth exemplar was chosen to represent
(navy/dark executive KPI band) is already fully covered by `WB-117`, which is the sole source for
`design/corpus/themes/executive_dark.yaml`. No dark-direction vocabulary is missing from the D0
corpus as a result of this gap — see `ASSUMPTIONS.md` for the full note.

## The other 7 mining inputs

`sidecar/design_miner.py` was also run against 7 pre-existing on-disk reference workbooks
(`WB-015`, `WB-093`, `WB-095`, `WB-133`, `WB-058`, `WB-062`, `WB-063`) used since earlier phases of
this project (see `docs/adr/0006-brand-kit-yaml-persona-resolution.md` and the "7 on-disk
reference workbooks" mentioned in `PLAN.md`). Like the `.twbx` exemplars above, these `.twb` files
are not committed (covered by the same `.gitignore` rule) — only the mined, deterministic YAML
output under `design/corpus/recipes/` is committed. `WB-062` and `WB-063` are byte-identical (same
sha256); the miner's cross-file dedup collapses their mined entries into one set attributed to
`WB-062` (see `design/corpus/SCHEMA.md`'s dedup policy).

## Reproducing the corpus

```bash
cd sidecar
uv run python design_miner.py \
  ../design/references/<WB-117-file>.twbx \
  ../design/references/<WB-118-file>.twbx \
  ../design/references/<WB-114-file>.twbx \
  <path-to>/<WB-015-file> <path-to>/<WB-093-file> <path-to>/<WB-095-file> \
  <path-to>/<WB-133-file> <path-to>/<WB-058-file> <path-to>/<WB-062-file> <path-to>/<WB-063-file> \
  --out ../design/corpus
```

Output is deterministic (sorted keys, sorted entries, stable dump) — re-running with the same
input set, in any order, produces byte-identical `design/corpus/recipes/*.yaml`.
