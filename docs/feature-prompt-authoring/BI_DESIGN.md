# BI_DESIGN.md — Deterministic Chart-Selection and Audience-Layout Rules

> **Status:** normative. Per `PLAN.md` §intro, this file wins over PLAN.md inline tables
> whenever the two diverge. The TypeScript planner (`src/planner/`) reads these tables as
> its rule source. Every rule is computable from static inputs; no LLM inference occurs
> inside the server.
>
> **Versioning:** increment `schemaVersion` (currently `1`) only when a rule change would
> break an existing unit test. Consumers MUST reject a `schemaVersion` they do not
> recognize.

---

## 0. Terminology and invariants

| Term | Definition |
|---|---|
| **Dimension** | A field that categorizes or slices data. Always placed on Cols or Rows as a grouping key. In `twb_builder.py` terms: `role="dimension"`, `type="nominal"`, encoded with `_dim_instance()`. |
| **Measure** | A numeric field aggregated with SUM. In `twb_builder.py` terms: `role="measure"`, `type="quantitative"`, encoded with `_measure_instance()`. |
| **Temporal** | A dimension whose values represent dates or times. |
| **Geographic** | A dimension whose values represent spatial entities (country, state, city, postal code, latitude/longitude). |
| **High-cardinality dimension** | A dimension with an expected distinct-value count > 20. Trigger top-N truncation. |
| **Low-cardinality dimension** | A dimension with an expected distinct-value count <= 20. Full enumeration is legible. |
| **KPI / BAN** | A "big-ass number": a single aggregate value with no grouping dimension, displayed as a large text mark. |
| **Shelf placement** | The assignment of field expressions to `cols` (x-axis), `rows` (y-axis / measure), and `measures` arrays in `SheetSpec`. Measures are always in the `measures` array regardless of visual orientation. |

**Invariants that every generated `SheetSpec` must satisfy:**
1. `markType` must be one of `"bar" | "line" | "text" | "map"` (the four values in `_MARK_CLASS`).
2. A `text` mark with no `measures` entry is a label only — the `text` encoding in `twb_builder.py` requires at least one measure to render a value. If a KPI has no measure, the sheet is dropped.
3. A `bar` mark requires at least one dimension in `cols` and at least one entry in `measures`.
4. A `line` mark requires a temporal dimension in `cols` and at least one entry in `measures`.
5. A `map` mark is experimental (REQUIREMENTS Non-goal #7). It may appear in a plan only when geographic fields are present, and `build_from_plan` must emit a warning in `rationale`.
6. A `SheetSpec` with placeholder field tokens (`"<measure>"`, `"<dimension>"`) is valid for `design_dashboard` output but is rejected by `build_from_plan` (fail-loud).

---

## 1. Field-role inference

The planner classifies every `FieldHint` into exactly one primary role plus zero or more
secondary tags. Classification is a pure function of `(name, dataType)`. Rules are
evaluated top-to-bottom; **first match wins**.

### 1.1 Data-type primary classification

| `dataType` in `FieldHint` | Primary role | Notes |
|---|---|---|
| `"number"` | **measure** | Default aggregate: SUM. |
| `"date"` | **temporal** dimension | Also tagged `temporal`. Never treated as a measure. |
| `"boolean"` | **dimension** | Nominal. Cardinality = 2 (always low). |
| `"string"` | **dimension** | Nominal by default; name-pattern rules below may override. |
| absent / unknown | **dimension** | Conservative fallback; the builder accepts `datatype="string"`. |

### 1.2 Name-pattern overrides (applied after dtype classification, order-sensitive)

Patterns are matched against the lowercased field name using the regex listed. First
pattern that matches overrides the dtype result.

| Priority | Regex (case-insensitive, applied to field name) | Assigned role | Secondary tags |
|---|---|---|---|
| 1 | `\b(id|_id|uuid|guid|key|pk|fk|code|sku|serial|token|hash)\b` | **identifier** dimension | `high_cardinality`, `suppress` — never place on Rows/Cols/Color; suppress from shelf selection. |
| 2 | `\b(date|day|month|quarter|year|week|timestamp|time|period|fiscal|fy|cy)\b` | **temporal** dimension | `temporal` — place on Cols for line charts. |
| 3 | `\b(lat|latitude|lon|longitude|lng)\b` | **geographic** dimension | `geo_coordinate` — pair lat+lon for map marks. |
| 4 | `\b(country|nation|state|province|region|city|city_name|metro|zip|postal|postcode|geoid|territory)\b` | **geographic** dimension | `geo_named` — use for map marks; low-cardinality countries/states, high-cardinality cities/zip. |
| 5 | `\b(name|label|title|description|desc|category|cat|type|status|stage|segment|group|tier|bucket|channel|source|medium|campaign)\b` | **dimension** | `low_cardinality` hint — assume <= 20 distinct values unless `high_cardinality` evidence contradicts. |
| 6 | `\b(flag|is_|has_|can_|should_|active|enabled|deleted|archived|approved|published)\b` | **dimension** | `boolean_flag`, cardinality = 2. |
| 7 | `\b(revenue|sales|amount|total|sum|gross|net|profit|margin|cost|price|spend|budget|arr|mrr|gmv|ltv|aov|cac|volume|qty|quantity|count|units|orders|transactions|conversions|sessions|pageviews|clicks|impressions|rate|ratio|pct|percent|score|index|weight|value)\b` | **measure** | `numeric` — aggregate SUM by default. |
| 8 | `\b(rank|ranking|position|priority|order|index|sequence)\b` | **dimension** | `ordinal` — treat as an ordered categorical, not a summed numeric. |
| — (no match) | Fallback: dtype rule from §1.1 | — | — |

### 1.3 Cardinality heuristic

When actual distinct-value counts are not available (the server does no introspection —
Non-goal #8), classify cardinality from the field name alone:

| Cardinality tag | Evidence | Distinct-value threshold |
|---|---|---|
| `high_cardinality` | Secondary tag `identifier`, or name matches `\b(name|email|url|path|description)\b` | > 20 |
| `low_cardinality` | Secondary tag `boolean_flag`, or name matches `\b(status|stage|type|tier|channel|segment|region|country|state)\b` | <= 20 |
| unknown | No pattern match | Assume `low_cardinality` for initial chart selection; note in `rationale`. |

### 1.4 Tie-breakers

When a field matches more than one pattern at the same priority level (impossible by
design — patterns are mutually exclusive per priority), the earlier row wins.

When a field's dtype says `"number"` but its name matches an `identifier` pattern
(priority 1): **identifier wins** — a numeric ID is not a measure.

When a field's dtype says `"string"` but its name matches a `measure` pattern
(priority 7): treat as a **potential string-typed measure**; place it on the `measures`
array but annotate `rationale` with "string-typed measure — verify aggregation in
Tableau."

### 1.5 Field classification summary (output per field)

```
FieldClassification = {
  name:           string,          // original field name
  role:           "measure" | "dimension" | "identifier" | "temporal" | "geographic",
  tags:           Set<string>,     // zero or more secondary tags from §1.2
  cardinalityHint: "low" | "high" | "unknown",
  suppress:       boolean          // true for identifiers; exclude from shelf selection
}
```

The planner builds this set before chart selection. Suppressed fields are never placed
on shelves.

---

## 2. Chart and mark selection

### 2.1 Decision table

Evaluate the classified field set against the data-shape rules in order. **First matching
row wins.** After chart type is selected, §2.2 maps field → shelf.

| Rule ID | Data shape (from classified fields) | Chart type | `markType` | `twb_builder.py` mark class | Builder status |
|---|---|---|---|---|---|
| C-01 | 1 temporal dimension + 1 or more measures + 0 non-temporal dimensions | **Line** (single or multi-series line) | `"line"` | `Line` | **Supported** |
| C-02 | 1 temporal dimension + 1 or more measures + 1 low-card non-temporal dimension | **Multi-series line** (color by dimension) | `"line"` | `Line` | **Supported** — color encoding requires new builder support; see §2.3. |
| C-03 | 1 low-card dimension (non-temporal, non-geo) + 1 or more measures | **Bar** | `"bar"` | `Bar` | **Supported** |
| C-04 | 1 high-card dimension (non-temporal, non-geo) + 1 or more measures | **Top-N bar** (truncate to N=10 by default; note in `rationale`) | `"bar"` | `Bar` | **Supported** — top-N truncation is a planner annotation, not a builder feature; the field is placed on Cols normally. The agent must apply a top-N filter in Tableau after publish, or the planner annotates for the user. |
| C-05 | 2 measures + 0 or 1 low-card dimension | **Scatter** | — | — | **NOT supported** — `twb_builder.py` has no scatter mark class. Fall back to `"bar"` for `exec`/`operational`/`mixed`; allow for `analyst` with fallback applied and noted in `rationale`. See §2.3. |
| C-06 | 0 grouping dimensions + 1 measure (standalone KPI) | **BAN / KPI text** | `"text"` | `Text` | **Supported** — measure placed on `measures` array; `twb_builder.py` adds `<encodings><text>` automatically for `text` marks. |
| C-07 | 1 geographic dimension (`geo_named` or `geo_coordinate`) + 1 measure | **Filled map or symbol map** | `"map"` | `Map` | **Experimental** — emit a warning in `rationale`; do not use for `exec` audience. |
| C-08 | 1 low-card dimension + multiple measures (part-to-whole context detected) | **Stacked bar** | `"bar"` | `Bar` | **Supported** — stacking is a planner annotation; multiple measures go on `measures` array; note in `rationale` that stacking must be configured in Tableau. |
| C-09 | Multiple low-card dimensions + 1 measure | **Bar** (first dimension on Cols; additional on Color — see §2.3) | `"bar"` | `Bar` | **Supported** for single-dimension placement; Color encoding requires new builder support. |
| C-10 | 0 dimensions + 0 measures (degenerate) | **Drop sheet**; add note to `rationale` | — | — | N/A |

**Pie chart policy:** Pies are never generated. When a part-to-whole question is detected,
use stacked bar (C-08) or treemap (not currently supported — see §2.3). This is a firm
rule, not an audience preference. Rationale: pies fail perceptual accuracy tests at > 4
slices; stacked bars and treemaps are superior alternatives available in Tableau.

**Treemap policy:** Treemap (`Square` mark class) is not in `_MARK_CLASS`. It requires new
builder support. Until added, fall back to `"bar"` for part-to-whole shapes with more than
two measures. See §2.3.

### 2.2 Field-to-shelf mapping

For each chart type, the exact field placement. All field names are placed as-is; the
builder wraps them in `_dim_instance()` / `_measure_instance()`.

**C-01 / C-02 — Line**
```
cols:     [temporal_field]
rows:     []
measures: [measure_field_1, measure_field_2, ...]
color:    [low_card_dimension]   // C-02 only; see §2.3 for builder gap
```
The temporal field is the sole dimension; the builder places it on the x-axis.

**C-03 / C-04 — Bar**
```
cols:     [dimension_field]
rows:     []
measures: [measure_field_1, ...]
```
Horizontal bar: swap cols and rows (dimension on `rows`, empty `cols`). Use horizontal
orientation for high-card dimensions (C-04) so long labels are legible. The planner
chooses orientation based on cardinality: high-card → horizontal (dimension in `rows`);
low-card → vertical (dimension in `cols`).

**C-06 — KPI text (BAN)**
```
cols:     []
rows:     []
measures: [kpi_measure_field]
```
No dimension. `twb_builder.py` emits `<mark class="Text"><encodings><text column="..."/></encodings>`.

**C-07 — Map**
```
cols:     []
rows:     []
measures: [measure_field]
color:    [measure_field]   // heatmap intensity; see §2.3
rows_geo: [geo_dimension]   // placed via Rows in Tableau; builder handles "map" mark class
```
Note: the map mark's actual field placement in the generated `.twb` relies on the builder's
`Map` class. Geographic field must be placed on `rows` for the `Map` mark to resolve.

**C-08 — Stacked bar**
Same as C-03/C-04. Multiple measures in `measures` array. Add to `rationale`:
"Configure stacking in Tableau: Analysis > Stack Marks > On."

**C-09 — Bar with secondary dimension**
```
cols:     [primary_dimension]
rows:     []
measures: [measure_field]
color:    [secondary_dimension]   // requires new builder support; see §2.3
```
Fall back to dropping the secondary dimension if Color encoding is not yet supported.

### 2.3 Builder gap register — chart types requiring NEW `twb_builder.py` support

The following chart types or encodings are referenced in the decision table but are not
emitted by the current builder. Each entry: the gap, its impact, and the interim fallback
until the builder is extended.

| Gap ID | Feature | Affected rules | Impact | Interim fallback |
|---|---|---|---|---|
| G-01 | **Color encoding** on `<mark>` element. The builder has no `<encodings><color>` emission path for multi-series line (C-02) or segmented bar (C-09). | C-02, C-09 | Multi-series lines render as overlapping single-color lines. Segmented bars are unsegmented. | Drop color dimension from shelf. Planner annotates `rationale`: "Color encoding not yet supported by the builder; apply color manually in Tableau Desktop." |
| G-02 | **Scatter mark class** (`"scatter"` / `Circle` in Tableau XML). `_MARK_CLASS` has no `"scatter"` key. | C-05 | Scatter plots cannot be built. | Fall back to `"bar"` for all audiences. For `analyst`, annotate `rationale`: "Scatter plot requested; rendered as bar until builder adds Circle mark class." |
| G-03 | **Treemap mark class** (`Square`). Not in `_MARK_CLASS`. | Part-to-whole with > 2 measures | Treemaps cannot be built. | Fall back to `"bar"` (stacked annotation). |
| G-04 | **Reference lines / band annotations**. No `<reference-line>` emission. | Analyst annotation rule (§3) | Reference lines for averages or targets cannot be embedded. | Annotate `rationale`: "Add reference lines manually in Tableau." |
| G-05 | **Top-N set / filter**. Builder emits no `<filter>` element. | C-04 (high-card dim) | Top-N restriction must be applied after publish in Tableau Desktop. | Planner annotates `rationale`: "High-cardinality dimension — apply Top 10 filter in Tableau Desktop: right-click field > Filter > Top > By field." |

Any plan that relies on a gap feature must include the fallback and the `rationale`
annotation. `build_from_plan` does not block on gap features; it builds the fallback
silently and preserves the annotation.

---

## 3. Audience design heuristics

All values in this section are hard constraints enforced by the audience-clamp functions
in `src/planner/audience.ts`. Each rule maps to a unit-testable predicate.

### 3.1 Master audience table

| Property | `exec` | `analyst` | `operational` | `mixed` |
|---|---|---|---|---|
| **`maxSheets`** | **3** | **8** | **6** | **6** |
| **Minimum KPI sheet count** | **1** (text mark required) | 0 | **1** (text mark required) | 0 |
| **Allowed `markType` values** | `bar`, `line`, `text` | `bar`, `line`, `text` (scatter → bar fallback per G-02) | `text`, `bar`, `line` | `bar`, `line`, `text` |
| **Map mark allowed** | No (too experimental for exec) | Yes (with G-05 warning) | No | No |
| **Default `dashboardLayout`** | `tiled_vertical` | `tiled_horizontal` | `tiled_vertical` | `tiled_vertical` |
| **Layout override allowed** | Yes, by caller | Yes, by caller | No — always `tiled_vertical` (mobile-first) | Yes, by caller |
| **KPI zone position** | First sheet (index 0) | Any position | First sheet (index 0) | First sheet (index 0) if present |
| **Max measures per sheet** | 1 | 4 | 2 | 2 |
| **Max dimensions per sheet** | 1 | 3 | 1 | 2 |
| **Density** | Sparse | Dense | Medium | Standard |
| **Annotation / reference lines** | 1 key callout on the KPI (manual — G-04) | Full (average line, target line — G-04) | Status-driven callouts only | Optional |
| **Axis labels** | Suppressed (minimized) | Full | Abbreviated | Standard |
| **Default canvas width (px)** | 1000 | 1200 | 800 | 1000 |
| **Default canvas height (px)** | 800 | 900 | 1200 | 900 |

### 3.2 Audience clamp algorithm (pure function, order-sensitive)

Applied after the raw sheet list is generated by chart selection. Steps are applied in
the listed order; each step is independently unit-testable.

```
function applyAudienceClamps(sheets, audience):

  STEP 1 — TRUNCATE
    If sheets.length > maxSheets[audience]:
      Remove sheets from the tail until sheets.length == maxSheets[audience].
      Note removed sheets in rationale: "Truncated N sheets to fit audience limit."

  STEP 2 — DROP DISALLOWED MARK TYPES
    For each sheet where markType not in allowedMarkTypes[audience]:
      Replace markType with the audience default:
        exec        → "text" for 0-dimension sheets, "bar" otherwise
        analyst     → "bar"
        operational → "text" for 0-dimension sheets, "bar" otherwise
        mixed       → "bar"
      Append to sheet.rationale: "Mark type replaced to comply with audience constraints."

  STEP 3 — ENSURE KPI LEAD
    If minimumKpiCount[audience] > 0 AND no sheet with markType=="text" exists at index 0:
      Insert a KPI text sheet at index 0 (using the first measure from the field list).
      If insertion would exceed maxSheets[audience], drop the last sheet to make room.

  STEP 4 — ENFORCE LAYOUT
    If audience == "operational":
      Force dashboardLayout = "tiled_vertical" regardless of any other input.
    Else:
      Use defaultLayout[audience] unless the caller's directions explicitly set a layout.

  STEP 5 — CAP MEASURES AND DIMENSIONS PER SHEET
    For each sheet:
      If measures.length > maxMeasures[audience]:
        Truncate measures to maxMeasures[audience]; note in sheet.rationale.
      If (cols.length + rows.length) > maxDimensions[audience]:
        Truncate dimensions to maxDimensions[audience] (keep cols first); note in sheet.rationale.

  STEP 6 — MAP MARK GUARD
    If audience in {"exec", "operational", "mixed"}:
      For each sheet where markType == "map":
        Replace with "bar"; note in sheet.rationale: "Map mark not allowed for this audience."

  RETURN clamped sheets + dashboardLayout
```

### 3.3 Audience-specific design notes (non-algorithmic, for rationale generation)

These notes are appended to the `DashboardPlan.rationale` string verbatim so the agent
can relay them to the user. They are not enforced structurally.

**exec:** "Executive view: maximum 3 sheets, leading KPI, large text. Axis labels are
minimal. Annotations on the primary KPI should be added manually in Tableau."

**analyst:** "Analyst view: up to 8 sheets, dense layout, full axis labels. Reference
lines (average, target) should be added manually post-publish."

**operational:** "Operational view: single-column mobile-friendly layout, status marks
prominent, action-oriented KPIs. Dashboard is optimized for 800px width."

**mixed:** "General audience: balanced 4-6 sheet layout with one summary KPI and
standard density."

---

## 4. Dashboard layout templates

### 4.1 Canvas sizes (pixels)

| Audience | Width | Height | Rationale |
|---|---|---|---|
| `exec` | 1000 | 800 | Standard widescreen; fits a projector slide. |
| `analyst` | 1200 | 900 | Wide canvas for side-by-side sheets. |
| `operational` | 800 | 1200 | Tall single-column; mobile-friendly portrait. |
| `mixed` | 1000 | 900 | Balanced. |

These values map directly to the `<size>` element in the `<dashboard>` XML:
`<size maxheight="{height}" maxwidth="{width}" minheight="{height}" minwidth="{width}" />`.

### 4.2 Zone tiling geometry

Zone coordinates use the 0–100000 Tableau grid (as documented in `PLAN.md` §6.3).
The `_tile_zones(titles, layout)` helper in `twb_builder.py` implements this math.

**`tiled_vertical`** (default for exec, operational, mixed):
```
n = len(titles)
unit_h = floor(100000 / n)
For i in 0..n-1:
  x = 0
  w = 100000
  y = i * unit_h
  h = unit_h  (last zone: h = 100000 - (n-1)*unit_h  to absorb rounding remainder)
```

**`tiled_horizontal`** (default for analyst):
```
n = len(titles)
unit_w = floor(100000 / n)
For i in 0..n-1:
  y = 0
  h = 100000
  x = i * unit_w
  w = unit_w  (last zone: w = 100000 - (n-1)*unit_w  to absorb rounding remainder)
```

Invariants (tested by DB-2):
- `tiled_vertical`: all zones have equal `x=0` and `w=100000`; `y` values are strictly
  increasing; `Σh == 100000`.
- `tiled_horizontal`: all zones have equal `y=0` and `h=100000`; `x` values are strictly
  increasing; `Σw == 100000`.
- No two zones may have the same `(x, y)` origin.
- Zone IDs: container `id=1`; worksheet zones `id=2..n+1`.

### 4.3 Layout templates per audience

The templates describe zone arrangement abstractly. "KPI" = `text` mark sheet;
"Chart" = `bar` or `line` sheet.

**exec — vertical stack, KPI first**
```
Zone 1 (top,    ~30% h): KPI / BAN sheet
Zone 2 (middle, ~40% h): Primary trend or bar chart
Zone 3 (bottom, ~30% h): Secondary bar chart (if sheet count == 3)
```
Expressed as 3-sheet `tiled_vertical` tiling. If only 2 sheets: top ~30%, bottom ~70%.

**analyst — horizontal side-by-side**
```
Row 1 (top 50% h):
  Col 1 (left  50% w): Primary chart
  Col 2 (right 50% w): Secondary chart
Row 2 (bottom 50% h):
  Full width: Detail table or scatter → bar
```
The builder's current tiling is strictly 1D (all horizontal or all vertical). A true
2×2 grid requires nested zones not yet emitted by `_tile_zones`. **Interim:** for
`analyst` with up to 4 sheets, use `tiled_horizontal` (all sheets side-by-side); for
5-8 sheets, switch to `tiled_vertical`. Nested-zone support is a tracked gap (see §4.4).

**operational — single-column, KPI first**
```
Zone 1 (top,    ~20% h): KPI / status text mark
Zone 2 (middle, ~40% h): Bar chart (pipeline, volume)
Zone 3–6 (remaining h, equal): Detail text marks or small bar charts
```
Always `tiled_vertical`.

**mixed — vertical, KPI optional lead**
```
Zone 1 (top,    ~25% h): Summary KPI (if present)
Zone 2 (middle, ~50% h): Primary bar or line chart
Zone 3–6 (remaining h, equal): Supporting charts
```
`tiled_vertical` by default.

### 4.4 Layout gap register

| Gap ID | Feature | Impact | Interim |
|---|---|---|---|
| L-01 | **Nested / 2D zone layout.** `_tile_zones` only supports 1D tiling. A true 2×2 analyst grid (top row: 2 charts; bottom row: 1 wide chart) requires a container zone with two children, plus a second container zone. | Analyst dashboards with > 4 sheets are rendered as a tall single column when `tiled_horizontal` is chosen. | Use `tiled_horizontal` for <= 4 analyst sheets; `tiled_vertical` for 5-8. Note in `rationale`. |
| L-02 | **Floating zones.** Tableau supports floating layout in addition to tiled. No floating zone emission. | Annotations and KPI callouts cannot be layered over charts. | Not addressed; note in `rationale`. |

---

## 5. Worked examples

Each example shows: input fields + audience → field-role inference → chart selection →
audience clamps → final `DashboardPlan` sketch.

### 5.1 Example A — Exec: "How is revenue trending by region?"

**Input:**
```
businessQuestion: "How is revenue trending by region?"
audience:         "exec"
fieldHints:
  - { name: "order_date",  dataType: "date"   }
  - { name: "region",      dataType: "string"  }
  - { name: "revenue",     dataType: "number"  }
  - { name: "order_id",    dataType: "string"  }
  - { name: "customer_id", dataType: "string"  }
```

**Step 1 — Field-role inference:**

| Field | dtype | Name-pattern match | Role | Tags | Suppress |
|---|---|---|---|---|---|
| `order_date` | date | temporal pattern (`date`) | temporal dimension | `temporal` | No |
| `region` | string | low-card pattern (`region`) | dimension | `low_cardinality` | No |
| `revenue` | number | measure pattern (`revenue`) | measure | `numeric` | No |
| `order_id` | string | identifier pattern (`_id`) | identifier | `high_cardinality`, `suppress` | Yes |
| `customer_id` | string | identifier pattern (`_id`) | identifier | `high_cardinality`, `suppress` | Yes |

Usable fields after suppression: `order_date`, `region`, `revenue`.

**Step 2 — Keyword heuristic (from PLAN.md §3.2, cross-checked with §2.1):**

Question clauses:
- "trending" → matches `trend` keyword → line (Rule C-01 / C-02)
- "by region" → dimension modifier, low-card → consider multi-series (C-02) or separate bar (C-03)

Primary shape: 1 temporal + 1 non-temporal low-card dim + 1 measure → Rule **C-02** (multi-series line).

**Step 3 — Chart selection:**

Sheet 1: Multi-series line → markType `"line"`, cols: `["order_date"]`, rows: `[]`, measures: `["revenue"]`, color: `["region"]` (G-01 gap noted).

Supporting chart: "by region" also suggests a bar chart for regional totals.
Sheet 2: Rule C-03 → markType `"bar"`, cols: `["region"]`, rows: `[]`, measures: `["revenue"]`.

KPI: "revenue trending" implies a total KPI.
Sheet 3: Rule C-06 → markType `"text"`, cols: `[]`, rows: `[]`, measures: `["revenue"]`.

Raw sheet list (before clamps):
```
[Sheet3: KPI text, Sheet1: Line, Sheet2: Bar]
```

**Step 4 — Audience clamps (`exec`):**

- maxSheets = 3: 3 sheets — within limit, no truncation.
- KPI lead: Sheet3 (text) moved to index 0.
- Allowed marks: bar, line, text — all compliant.
- Layout: `tiled_vertical`.
- Max measures/sheet = 1, max dims/sheet = 1 — compliant.
- Map guard: no map marks.

**Final `DashboardPlan`:**
```
workbookName:    "Revenue Trend by Region"
audience:        "exec"
dashboardLayout: "tiled_vertical"
sheets:
  [0] { title: "Total Revenue",          markType: "text", cols: [],          rows: [],   measures: ["revenue"] }
  [1] { title: "Revenue Trend Over Time", markType: "line", cols: ["order_date"], rows: [], measures: ["revenue"],
         rationale: "Color by region not embedded (G-01); apply manually in Tableau." }
  [2] { title: "Revenue by Region",       markType: "bar",  cols: ["region"],    rows: [], measures: ["revenue"] }
rationale: "Executive view: KPI leads, trend line answers 'trending', bar answers 'by region'.
            Color encoding for multi-series not yet supported by builder (G-01).
            Executive view: maximum 3 sheets, leading KPI, large text."
```

---

### 5.2 Example B — Analyst: "What drives churn?"

**Input:**
```
businessQuestion: "What drives churn?"
audience:         "analyst"
fieldHints:
  - { name: "customer_id",      dataType: "string" }
  - { name: "churned",          dataType: "boolean" }
  - { name: "tenure_months",    dataType: "number"  }
  - { name: "plan_type",        dataType: "string"  }
  - { name: "monthly_spend",    dataType: "number"  }
  - { name: "support_tickets",  dataType: "number"  }
  - { name: "region",           dataType: "string"  }
  - { name: "signup_date",      dataType: "date"    }
```

**Step 1 — Field-role inference:**

| Field | Role | Tags | Suppress |
|---|---|---|---|
| `customer_id` | identifier | `high_cardinality`, `suppress` | Yes |
| `churned` | dimension | `boolean_flag`, `low_cardinality` | No |
| `tenure_months` | measure | `numeric` | No |
| `plan_type` | dimension | `low_cardinality` | No |
| `monthly_spend` | measure | `numeric` | No |
| `support_tickets` | measure | `numeric` | No |
| `region` | dimension | `low_cardinality` | No |
| `signup_date` | temporal dimension | `temporal` | No |

Usable fields: `churned`, `tenure_months`, `plan_type`, `monthly_spend`,
`support_tickets`, `region`, `signup_date`.

**Step 2 — Keyword heuristic:**

"What drives churn?" — "drives" → no exact keyword match; falls to default `"bar"`.
"churn" → `churned` is a boolean dimension (cardinality 2).
Interpretation: compare measures across churned vs. not-churned segments.

**Step 3 — Chart selection:**

Sheet 1 — Churn rate KPI: `churned` is boolean, 0-dim → Rule C-06 (text). Actually,
`churned` is a dimension not a measure. Build a KPI by counting churned customers:
markType `"text"`, measures: `["support_tickets"]` (proxy for churn volume — note in
rationale that agent should supply a churn-count computed field or use count of `churned`).

Sheet 2 — Tenure vs. spend (2 measures, 1 dim): 2 measures + 1 low-card dim → Rule C-05
(scatter). Scatter not supported (G-02) → fall back to bar. markType `"bar"`,
cols: `["plan_type"]`, measures: `["tenure_months"]`.

Sheet 3 — Spend by plan type and churn: C-03 → markType `"bar"`, cols: `["plan_type"]`,
measures: `["monthly_spend"]`.

Sheet 4 — Support tickets by churn status: C-03 → markType `"bar"`, cols: `["churned"]`,
measures: `["support_tickets"]`.

Sheet 5 — Churn over time: C-01 → markType `"line"`, cols: `["signup_date"]`,
measures: `["monthly_spend"]`.

Sheet 6 — Revenue by region and churn: C-03 → markType `"bar"`, cols: `["region"]`,
measures: `["monthly_spend"]`.

Raw sheet count: 6. Within analyst limit of 8.

**Step 4 — Audience clamps (`analyst`):**

- maxSheets = 8: 6 sheets — no truncation.
- No required KPI lead for analyst.
- All mark types compliant (bar, line, text).
- Layout: `tiled_horizontal` (default for analyst). 6 sheets → switch to `tiled_vertical`
  per L-01 interim rule (> 4 sheets with tiled_horizontal is too narrow).
- Scatter fallback noted (G-02).
- Max measures = 4, max dims = 3 — all compliant.

**Final `DashboardPlan`:**
```
workbookName:    "Churn Driver Analysis"
audience:        "analyst"
dashboardLayout: "tiled_vertical"
sheets:
  [0] { title: "Support Tickets (Churn Proxy)",  markType: "text", measures: ["support_tickets"],
         rationale: "KPI proxy — agent should supply a dedicated churn-count field." }
  [1] { title: "Tenure by Plan Type",             markType: "bar",  cols: ["plan_type"],  measures: ["tenure_months"],
         rationale: "Scatter plot (2-measure) not supported (G-02); rendered as bar." }
  [2] { title: "Monthly Spend by Plan Type",      markType: "bar",  cols: ["plan_type"],  measures: ["monthly_spend"] }
  [3] { title: "Support Tickets by Churn Status", markType: "bar",  cols: ["churned"],    measures: ["support_tickets"] }
  [4] { title: "Monthly Spend Over Time",         markType: "line", cols: ["signup_date"], measures: ["monthly_spend"] }
  [5] { title: "Spend by Region",                 markType: "bar",  cols: ["region"],     measures: ["monthly_spend"] }
rationale: "Analyst view: 6 sheets comparing measures across churn-related dimensions.
            Scatter plot for 2-measure shape replaced with bar (G-02).
            Switched to tiled_vertical because 6 sheets exceed the 4-sheet tiled_horizontal
            threshold (L-01). Add reference lines for averages manually post-publish."
```

---

### 5.3 Example C — Operational: "Current order pipeline status"

**Input:**
```
businessQuestion: "Current order pipeline status"
audience:         "operational"
fieldHints:
  - { name: "order_id",      dataType: "string" }
  - { name: "status",        dataType: "string" }
  - { name: "stage",         dataType: "string" }
  - { name: "amount",        dataType: "number" }
  - { name: "order_date",    dataType: "date"   }
  - { name: "region",        dataType: "string" }
  - { name: "days_open",     dataType: "number" }
```

**Step 1 — Field-role inference:**

| Field | Role | Tags | Suppress |
|---|---|---|---|
| `order_id` | identifier | `high_cardinality`, `suppress` | Yes |
| `status` | dimension | `low_cardinality` | No |
| `stage` | dimension | `low_cardinality` | No |
| `amount` | measure | `numeric` | No |
| `order_date` | temporal dimension | `temporal` | No |
| `region` | dimension | `low_cardinality` | No |
| `days_open` | measure | `numeric` | No |

Usable fields: `status`, `stage`, `amount`, `order_date`, `region`, `days_open`.

**Step 2 — Keyword heuristic:**

"Current order pipeline status" — "status" → no time keyword, "pipeline" → no exact match.
Default: `"bar"`. "current" hints at a snapshot KPI rather than a trend.

**Step 3 — Chart selection:**

Sheet 1 — Pipeline value KPI: 0 grouping dims + 1 measure → Rule C-06 (text KPI).
markType `"text"`, measures: `["amount"]`.

Sheet 2 — Orders by status: C-03 (low-card dim `status`) → markType `"bar"`,
cols: `["status"]`, measures: `["amount"]`.

Sheet 3 — Days open by stage: C-03 (`stage` is low-card) → markType `"bar"`,
cols: `["stage"]`, measures: `["days_open"]`.

Raw count: 3. Well within operational limit of 6.

**Step 4 — Audience clamps (`operational`):**

- maxSheets = 6: 3 sheets — no truncation.
- Minimum KPI count = 1: Sheet 1 is text at index 0 — satisfied (MA-3 equivalent).
- Allowed marks: text, bar, line — all compliant.
- Layout: force `tiled_vertical` (operational always vertical — layout override not allowed).
- Max measures = 2, max dims = 1 — compliant.
- Map guard: no map marks.

**Final `DashboardPlan`:**
```
workbookName:    "Order Pipeline Status"
audience:        "operational"
dashboardLayout: "tiled_vertical"
sheets:
  [0] { title: "Total Pipeline Value",  markType: "text", cols: [],         rows: [], measures: ["amount"] }
  [1] { title: "Orders by Status",      markType: "bar",  cols: ["status"], rows: [], measures: ["amount"] }
  [2] { title: "Days Open by Stage",    markType: "bar",  cols: ["stage"],  rows: [], measures: ["days_open"] }
rationale: "Operational view: status KPI leads, two bar charts show pipeline
            distribution by status and stage. Single-column layout for mobile-friendly
            display at 800×1200px."
```

---

## 6. Keyword → mark-type heuristic (full table)

This is the normative version of the inline table in `PLAN.md` §3.2. The planner in
`src/planner/marks.ts` must implement this table exactly. Rules are applied per clause
after splitting `businessQuestion` or `directions` on `\s+and\s+`, `,`, or `;`.
**First match wins per clause.** Multiple clauses may produce multiple sheets.

| Priority | Regex (case-insensitive, applied to clause text) | Implied chart type | `markType` | Typical shelf placement |
|---|---|---|---|---|
| 1 | `\b(top\s+\d+\|list\|table\|detail\|breakdown\|show me all\|all records)\b` | Detail table | `"text"` | First usable dimension on `rows`; first usable measure on `measures`. |
| 2 | `\b(trend\|over time\|by (month\|quarter\|year\|date\|day\|week\|period)\|growth\|historical\|time series\|trajectory)\b` | Line | `"line"` | First temporal dim on `cols`; first measure on `measures`. |
| 3 | `\b(kpi\|headline\|total\|grand total\|overall\|how many\|count of\|sum of\|single number\|big number)\b` | KPI text | `"text"` | No dim; first measure on `measures`. |
| 4 | `\b(compare\|comparison\|versus\|vs\.?\|by (region\|category\|segment\|channel\|product\|team\|country\|state\|city\|type\|status\|stage\|tier\|group)\|across\|per\|distribution\|breakdown by\|rank)\b` | Bar | `"bar"` | First usable non-temporal dim on `cols`; first measure on `measures`. |
| 5 | `\b(map\|geography\|geographic\|location\|where\|by country\|by state\|by city\|by region (on map)\|spatial)\b` | Map | `"map"` | First geo dim on `rows`; first measure on `measures`. |
| 6 | `\b(correlation\|scatter\|relationship between\|drives\|impact of\|x vs y\|plotted against)\b` | Scatter (→ bar fallback G-02) | `"bar"` | First usable dim on `cols`; first measure on `measures`. |
| 7 | `\b(share\|proportion\|composition\|part.?to.?whole\|breakdown of .+\%\|percentage of)\b` | Stacked bar (→ bar with annotation) | `"bar"` | First usable dim on `cols`; all usable measures on `measures`. |
| — (no match) | Default | Bar | `"bar"` | First usable non-temporal dim on `cols`; first usable measure on `measures`. |

**Conflict resolution when two priorities both match the same clause:** lower priority
number wins (highest priority = smallest integer).

**No-dimension case:** if the heuristic selects `"bar"` or `"line"` but no usable
dimension exists (all are suppressed or absent), downgrade to `"text"` (KPI) and note
in `rationale`.

---

## 7. Interview question bank (normative)

`design_dashboard(mode: "interview")` selects 3-7 questions from this ordered bank. A
question is included if and only if the corresponding input is unknown. Selection is
deterministic given the input state.

| ID | Included when | Question text | Hint |
|---|---|---|---|
| `q_audience` | `audience` is absent from input | "Who is the primary audience for this dashboard?" | "For example: executive leadership, data analyst team, operations/frontline staff, or a mixed group." |
| `q_goal` | `businessQuestion` and `directions` both absent | "What is the primary business question this dashboard should answer?" | "For example: 'How is revenue trending?' or 'Which regions are underperforming?'" |
| `q_key_metric` | `fieldHints` is absent or empty | "What is the one metric that matters most to the viewer?" | "For example: revenue, churn rate, order volume, customer count." |
| `q_time_frame` | `fieldHints` is absent or contains no temporal field | "Does this dashboard need to show data over time, or is a point-in-time snapshot sufficient?" | "Time-based views require a date or period field." |
| `q_dimensions` | `fieldHints` is absent or contains only measures (no usable dimensions) | "What categories or segments should the data be broken down by?" | "For example: by region, by product, by customer segment, by status." |
| `q_filters` | `context` does not mention filter or scope | "Are there any filters or scope limitations that should be applied by default?" | "For example: current fiscal year only, specific region, active customers only." |
| `q_action` | `audience` is `"operational"` or `context` mentions 'action' or 'decision' | "What action or decision does the viewer take after looking at this dashboard?" | "For example: escalate an order, contact a customer, reallocate budget." |

Questions are emitted in the order listed. If all 7 are applicable, all 7 are included
(maximum = 7, satisfying MB-1). If the minimum of 3 is not reached (all inputs known),
the planner always includes `q_goal`, `q_key_metric`, and `q_filters` as unconditional
minimums.

---

## 8. Rule versioning and test contract

### 8.1 Version

This file is `schemaVersion: 1`. The TS planner reads this value and throws if it does
not equal 1.

### 8.2 Unit-testable predicates (mapping to REQUIREMENTS criteria)

Each row below is a concrete assertion a test can make without an LLM call:

| Predicate | Maps to |
|---|---|
| `plan.sheets.length <= 3` when `audience == "exec"` | MA-1, C-8 |
| All `plan.sheets[*].markType` in `{"bar","line","text"}` when `audience == "exec"` | MA-1 |
| `plan.sheets.length <= 8` when `audience == "analyst"` | MA-2 |
| `plan.dashboardLayout == "tiled_vertical"` when `audience == "operational"` | MA-3 |
| At least one `plan.sheets[*].markType == "text"` when `audience in {"exec","operational"}` | MA-3, §3.2 step 3 |
| `plan.sheets[0].markType == "text"` when `audience == "exec"` (KPI lead) | §3.2 step 3 |
| `directed("show me a table of top 10 customers by revenue and a bar chart of revenue by region")` → `sheets.length == 2`, `sheets[0].markType == "text"`, `sheets[1].markType == "bar"` | MC-1 |
| `questions.length` in `[3,7]` for `mode == "interview"` | MB-1 |
| `"sheets"` key absent from `ClarifyingQuestions` response | MB-1 |
| `DashboardPlan.rationale` non-empty string | MB-2 |
| `plan.sheets[*].markType == "map"` only when geographic field present AND audience == "analyst"` | §3.1, §3.2 step 6 |
| `plan.sheets[*].measures.length <= maxMeasures[audience]` | §3.2 step 5 |
| `(cols.length + rows.length) <= maxDimensions[audience]` per sheet | §3.2 step 5 |
| Zone `Σh == 100000` for `tiled_vertical` with any `n` in `[1,8]` | DB-2 |
| Zone `Σw == 100000` for `tiled_horizontal` with any `n` in `[1,8]` | DB-2 |
| All `y` values distinct and `x == 0` for `tiled_vertical` | DB-2 |
| All `x` values distinct and `y == 0` for `tiled_horizontal` | DB-2 |

---

## 9. Design-excellence rules (Few/visionary layer)

Phase E1 (Pillar B, "Branding system + design-excellence layer") encodes a set of
deterministic rules inspired by the Stephen Few / Edward Tufte school of analytical
dashboard design — minimize non-data ink, favor direct comparison over decoration, size
KPIs with context rather than as bare numbers, and prefer perceptually accurate encodings
(position/length) over perceptually weak ones (angle/area). Each rule below states its
**source lineage** (the general principle it descends from, described rather than quoted —
no copyrighted text is reproduced), its **rationale**, and whether it is **ENFORCED**
(the planner or schema makes the violation structurally impossible or produces a
deterministic annotation, backed by a unit test) or **DOCUMENTED-only** (recorded in
`brand.yaml`'s free-text `rules:` list and/or this file, but not mechanically checked).
Honesty about which is which matters more than the count of "enforced" rules — several of
these are aspirational until a later phase adds the missing builder support (tracked via
the existing Gap Register in §2.3).

| ID | Rule | Source lineage | Status | Where |
|---|---|---|---|---|
| **FEW-1** | No-pie default: part-to-whole questions always resolve to a bar (or its text/KPI downgrade), never a pie. | Few/Tufte: pie charts rely on angle/area judgment, the weakest perceptual channels; position/length (bar) is read far more accurately at more than a handful of slices. | **ENFORCED** | `MarkTypeEnum` (`src/planner/schema.ts`) has no `"pie"` member at all — a pie is structurally unrepresentable, not merely discouraged. §6 priority-7 rule (`share\|proportion\|composition\|part-to-whole\|percentage of`) routes explicitly to `"bar"`. Regression-tested across all four audiences in `tests/planner-slice6.test.ts`. |
| **FEW-2** | Every KPI tile carries a comparison/delta when the underlying data offers one (CP/PP/Difference columns), instead of a bare number with no context. | Few: a number alone answers "what" but not "so what" — pairing it with a prior-period or target comparison turns a statistic into a judgment. | **ENFORCED** (pre-existing, formalized here) | `buildKpiStrip()` + `findPeriodPair()` (`src/planner/marks.ts`, `src/planner/fields.ts`) bind `comparisonMeasure`/`deltaMeasure` whenever a PP/CP/Difference counterpart exists; graceful degradation to a bare KPI otherwise (never blocks). Covered by the existing Slice 4/5 KPI-strip and direction tests. |
| **FEW-3** | No gauge marks are ever emitted; bullet graphs are the documented alternative. | Few: gauges (speedometer-style dials) waste area on decoration and are hard to compare side-by-side; the bullet graph he designed communicates the same target/actual/range semantics in a fraction of the space. | **ENFORCED** (negative rule, by omission) / **DOCUMENTED-only** (positive rule) | Negative: `MarkTypeEnum` has no `"gauge"` member — structurally impossible, same guarantee as FEW-1. Positive: bullet graphs are **not** implemented either (no `"bullet"` mark class in `twb_builder.py`'s `_MARK_CLASS`) — this is a real gap, not a silent substitution; `brand.yaml`'s default `rules:` list states the preference ("Don't use gauges — prefer bullet graphs") as guidance for a human/agent, and it is tracked as a future builder gap alongside G-03 (treemap) in §2.3. |
| **FEW-4** | Data-ink discipline: no gridline/border/shading options exist to emit; minimal axis labeling and a direct-labeling preference are documented for low-cardinality series. | Tufte: maximize the data-ink ratio — every mark that isn't data is a candidate for removal. | **ENFORCED** (structural, by omission) / **DOCUMENTED-only** (the labeling preference) | `SheetSpecSchema` (`src/planner/schema.ts`) has no gridline/border/shading/drop-shadow field anywhere in its shape, so the planner cannot emit one even accidentally. The exec audience note ("Axis labels are minimal", §3.3) and `brand.yaml`'s default rule ("use direct labeling on bars instead of a legend when there are 5 or fewer categories") are prose guidance, not mechanically checked — no builder-side legend/label toggle exists yet to check against. |
| **FEW-5** | Measure-colored marks (a bar or filled map colored by a quantitative field) always use continuous/sequential color semantics, never the categorical palette. | Few/Tufte: magnitude should map to a perceptually ordered ramp (light→dark), not to arbitrary hue cycling, which implies unordered categories. | **ENFORCED** (structural) / partial gap noted | `_color_column_instance()` (`sidecar/twb_builder.py`) resolves `color.kind === "measure"` to `_measure_instance()` (a continuous quantitative field reference) — it can never resolve to the categorical dimension path, so Tableau always renders it with its built-in continuous ramp. **Design Excellence Slice D6 (FINAL SHAPE, live-probe #4):** `brand.palette.sequential` is now bound to `map_filled` sheets' color-measure encoding — `_build_preferences_element()` registers `<preferences><color-palette name='<brandName> Sequential' type='ordered-sequential'>` (harmless, kept), and (when `design_theme` is present) `_map_filled_palette_style_rule()` adds `<style-rule element='mark'><encoding attr='color' type='custom-interpolated'><color-palette custom='true' name='' type='ordered-sequential'>` EMBEDDING the brand's sequential stops directly — mirrors the 8x-attested `WB-015`/`WB-062` shape. An earlier pass instead emitted a `palette='<brandName> Sequential' type='palette'` NAME reference back to `<preferences>`; live-probe #4 published that shape and found Tableau Cloud renders the workbook without error but silently ignores the name reference for a continuous measure (the ramp came back byte-identical to the unbranded render) — superseded by the embedded shape, which live-probe #4 confirmed actually renders. **Remaining gap (documented, deferred):** `brand.palette.diverging` is registered in `<preferences>` (same slice) but not yet bound to any encoding — no bar/other measure-colored mark reads it — and non-map measure-colored bars still render Tableau's built-in ramp. Tracked as a future builder gap. |
| **FEW-6** | High-cardinality dimensions carry a top-N discipline note (formerly ad-hoc "Top 10" prose, now a named constant). | Few: dense categorical axes with dozens of unlabeled or overlapping ticks are illegible; truncating to the top N plus an "Other" bucket keeps the comparison readable. | **ENFORCED** | `TOP_N_LIMIT` (`src/planner/marks.ts`, value `10`) is the single source of truth for the N in the `GAP_G05` rationale text ("apply Top 10 filter…"); `annotateHighCardinality()` still fires deterministically whenever a shelf field's `cardinalityHint === "high"` (BI_DESIGN §1.3). Verified end-to-end (via `generatePlan`) and as a named-constant assertion in `tests/planner-slice6.test.ts`. |
| **FEW-7** | Small-multiples hint: when a bar is colored by a second low-cardinality dimension (the C-09 "2 dims + 1 measure" pattern, §2.1) AND the question explicitly signals a cross-dimension comparison (`compare`/`comparison`/`versus`/`vs`/`across`/`breakdown by`), the rationale notes small multiples (one chart per category) as a legibility alternative to a single color-coded bar. | Tufte: small multiples let the eye compare shape-to-shape at a fixed scale, avoiding the color-decoding step a legend requires once more than a few categories are stacked into one chart. | **ENFORCED** | `appendSmallMultiplesHintIfApplicable()` (`src/planner/marks.ts`) is applied in both the keyword-heuristic path (`applyMarkHeuristic`) and the exec KPI-band path (`buildExecKpiBandPlan`, `src/planner/plan.ts`). Documentational only — no new mark type is emitted and no existing shelf assignment changes. Tested in `tests/planner-slice6.test.ts`. |

### 9.1 Persona-driven overrides (brand.yaml, consumed by the audience clamp)

Phase E1 Slice A scaffolded `personas.<name>.{chartDeny, kpiEmphasis, preferredArtifact,
tone}` in `brand.yaml` (validated by `PersonaOverridesSchema`,
`src/branding/schema.ts`) but did not yet wire them into the planner. Slice C consumes all
four:

| Override | Consumed by | Effect |
|---|---|---|
| `chartDeny: string[]` | `AudienceConstraintOverrides.chartDeny` → `applyAudienceClamps` STEP 1.5 (`src/planner/audience.ts`) | Any sheet whose `markType` (case-insensitive) appears in the list is replaced with `"bar"` (clearing `geo`/`scatter`), with a rationale note naming the `chartDeny` rule. Runs independently of, and before, the audience-level `allowedMarkTypes` drop (STEP 2) — this is a *persona* veto, not an *audience* capability check. |
| `kpiEmphasis: "high" \| "medium" \| "low"` | `AudienceConstraintOverrides.kpiEmphasis` → `applyAudienceClamps` STEP 3.5 | Caps the number of `kpi_tile` sheets in the KPI band: `high` = 4 (the historical default, unchanged), `medium` = 3, `low` = 2 (`KPI_EMPHASIS_MAX_TILES`). Extra tiles are dropped from the tail (lowest-ranked measures — `rankMeasures` already ordered the band by relevance) with a rationale note on the last surviving tile. |
| `preferredArtifact: "dashboard" \| "story" \| "pulse"` | `DashboardPlan.personaPreferredArtifact` → `buildProposal` (`src/planner/proposal.ts`) | `"dashboard"` (or unset) is a no-op — this tool already produces a dashboard. `"story"` is fully shipped (Phase E4, see §9.2 below): it drives `generatePlan()` to emit a deterministic `storyArc`/`storyOutline`, no stub note. `"pulse"` still appends an honest `openQuestions` entry naming the phase where that capability lands (Phase E3) — the server never silently ignores the preference or pretends to honor it with a dashboard substitute. |
| `tone: "concise" \| "detailed"` | `DashboardPlan.personaTone` → `buildProposal` | `"concise"` trims `DashboardProposal.summary` to its first sentence plus the layout-summary line (the persona-tailored provenance mention is folded into that same first sentence rather than dropped, so a concise CEO proposal still names the persona). `"detailed"` (or unset) keeps the full multi-sentence summary — unchanged, backward-compatible default. |

Both the planner (`src/planner/*`) and `sidecar/twb_builder.py` remain decoupled from
`brand.yaml` directly: `design_dashboard` (`src/tools/designDashboard.ts`) is the only
place that reads the brand file and resolves a persona's overrides; everything downstream
receives plain, already-resolved values — the "planner stays pure" invariant from Phase E1
Slice A holds through Slice C.

### 9.2 Story arcs — deterministic template floor + the client-edit quality path

Beauty-gate round 2 (PLAN.md's Top-100 Corpus plan) flagged the Phase E4 v1 story captions
as **"irrelevant — mechanical captions like 'Profit.' add no narrative value."** Slice T3
addressed this with two changes, both grounded in the T1 corpus-mining evidence rather than
invented rules:

**Mined finding — gating.** `design/corpus/stats/story_norms.yaml` (T1, 110 mined
workbooks: the 3 pre-existing exemplars + the top-100 VOTD corpus + 7 extra references)
found **zero storyboards in the entire corpus** — `usage_rate: { count: 0, n: 0, confidence:
"low" }`. Per the plan's own n<15 confidence guard (`design/corpus/GAPS.md` §1), a
low-confidence bucket is never auto-applied to a default. There is therefore no mined
evidence to support a usage-rate-based "propose a story N% of the time" default — the only
defensible gating is **explicit ask only**: `wantsStoryArc()` (`src/planner/plan.ts`) emits
a `storyArc` if and only if (a) the business question itself uses story/narrative/
presentation language, or (b) the resolved persona's `preferredArtifact === "story"`. No
audience, sheet-count, or other heuristic trigger exists. A future corpus refresh targeting
Tableau Public's dedicated Stories gallery (VOTD skews toward single-view vizzes) could
eventually clear the confidence floor and justify revisiting this — not attempted here
(GAPS.md).

**No mined finding — caption style, hence the template-floor + client-edit design.**
`story_norms.yaml`'s `caption_length`/`nav_type_distribution` buckets are equally
zero-evidence, so there is no mined caption-STYLE norm to encode either. Slice T3's caption
template v2 (`buildStoryArc()` in `src/planner/plan.ts`) is a **deterministic floor**, not a
data-driven one: takeaway-style, persona-toned, question-echoing sentences routed by the
captured sheet's own kind — a KPI tile gets a "watch this lever" caption, a bar gets a
"drives the mix" drivers caption, a map gets a "the geography" caption, and so on — instead
of v1's bare `"{measure}."` label. This closes the "adds no narrative value" defect
mechanically, but a template can never know the ACTUAL story behind a given dataset (why
sales dipped in Q3, which category is the real driver). That's why the architecture treats
the **client-edit path as the primary, intended quality mechanism**, not the template:

1. `design_dashboard` returns a `DashboardProposal` whose `storyOutline` mirrors
   `plan.storyArc[].caption` (template-v2 captions, deterministic).
2. **The calling LLM — which has the full conversational and data context the deterministic
   planner never sees — rewrites `plan.storyArc[].caption`** with real, data-aware
   narrative before confirming. It may also reorder points or drop ones that don't earn
   their place; it must not invent a `capturedSheet` that isn't one of `plan.sheets[].title`.
3. `build_from_plan` re-validates every `storyArc[].capturedSheet` reference against the
   plan's own sheets (`assertStoryArcCapturedSheetsExist`, `src/tools/buildFromPlan.ts`) —
   fail-loud, before any sidecar/REST call — then builds the `<dashboard type='storyboard'>`
   from whatever captions survive the rewrite. This validation is the ONLY story-quality
   mechanism that was already shipped pre-T3 (do not rebuild it); T3 only changed what the
   template emits by default and documented this rewrite step as the intended path.

**Worked example.** A `design_dashboard` call for an exec audience with
`businessQuestion: "Tell the story of how sales and profit are performing across
categories and states"` returns (abbreviated):

```json
{
  "kind": "proposal",
  "storyOutline": [
    "The headline: sales and profit are performing across categories and states — start with Sales at a glance.",
    "Profit: watch this lever alongside Sales.",
    "Category drives the mix — where Sales concentrates.",
    "The geography: where Sales shows up on the map."
  ],
  "plan": {
    "kind": "plan",
    "storyName": "Sales & Profit Performance",
    "storyArc": [
      { "caption": "The headline: …", "capturedSheet": "Sales" },
      { "caption": "Profit: watch this lever …", "capturedSheet": "Profit" },
      { "caption": "Category drives the mix …", "capturedSheet": "Sales by Category" },
      { "caption": "The geography: …", "capturedSheet": "Sales by State" }
    ]
  }
}
```

The agent presents this to the user, then — before calling `build_from_plan` — rewrites the
captions with the real context it has (e.g. from the conversation or the underlying data):

```json
"storyArc": [
  { "caption": "Q4 finished strong: Sales are up 14% YoY, led by the West.", "capturedSheet": "Sales" },
  { "caption": "Profit held pace with Sales — margin discipline is intact this quarter.", "capturedSheet": "Profit" },
  { "caption": "Technology is the standout category — nearly a third of total Sales.", "capturedSheet": "Sales by Category" },
  { "caption": "California and Texas anchor the map; the Midwest is the clearest whitespace.", "capturedSheet": "Sales by State" }
]
```

Every `capturedSheet` value is untouched — only `caption` text changed — so
`assertStoryArcCapturedSheetsExist` still passes and the story builds against the same
underlying sheets. See `docs/tool_reference.md`'s `build_from_plan` entry for the same
worked example framed as a tool-call sequence.

---

*End of BI_DESIGN.md — schemaVersion 1*
