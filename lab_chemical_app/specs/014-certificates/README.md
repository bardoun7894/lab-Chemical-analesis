# Customer certificates

The client's GCP paper forms, brought into the app. All three built.

| Form | Code | Status |
|---|---|---|
| Warranty Certificate | QC-01-F-28 | built 2026-09-02 |
| Work Test Certificate | QC-01-F-26 | built 2026-09-02 |
| Material Test Certificate (MTC) | LAB-DOC-8.1.1 | built 2026-09-02 |

Sources: `~/Downloads/28-Warranty Certificate.xlsx`,
`~/Downloads/26-Work Test Certificate.xlsx`, `~/Downloads/MTC1 Final.xlsx`.

## Flow

Order detail → **Warranty Certificate** or **Work Test Certificate** → pick the pipes →
fill in the references that live on the customer's paperwork → **Save & print**.
Quantity, DN, class and lengths are computed from what was ticked, never typed.
`/certificates` lists both forms for reprinting.

## Split between the system and the user

**From the system:** certificate number (a serial), issue date, customer name, DN, class,
pipe lengths, quantity, item description — and on the work test, the chemical limits and
the mechanical sample result.

**Typed in:** Order No. (the *customer's*, not our `order_number`), Project Name, warranty
start date, warranty period, notes. Confirmed with the client 2026-09-02.

## Shared behaviour

- One `certificates` table with a `cert_type`; the two forms differ only in what prints
  below the material table and in who signs.
- Certificate number is `WC-YYYY-NNN` / `WT-YYYY-NNN` — year-scoped, counted per form,
  and issued **once at save** so a reprint keeps the same number.
- Pipe length comes from the Finish stage's `measurement_value`, falling back to the
  order's product length. Rejected pipes are never offered.
- One material row per DN / class / length group, so an order cut to two lengths prints
  two lines.

## Warranty certificate (QC-01-F-28)

Adds the warranty start date and the period. The period is selectable 1 / 2 / 3 years
(default 3) and fills the blank in the Arabic clause **and** replaces the hardcoded
"three years" in the English one, so the two never disagree. Cover runs from the warranty
start date, not the issue date. Fixed on the form: the three out-of-scope defect clauses;
signed by the Quality Assurance Manager and the Quality Director.

## Work test certificate (QC-01-F-26)

Adds, under the material table:

- **Chemical analysis** — the limits each element was accepted against, read from
  `ElementSpecification` so the printed band cannot drift from the one decisions are made
  against. An element with no configured row falls back to what the paper form said. The
  decimals follow the paper form (`3.00: 4.20`, not `3: 4.2`), because a float cannot
  remember its own trailing zeros.
- **Mechanical properties** — the most recent ACTIVE `MechanicalTest` on the selected
  pipes or their ladles, quoted as the random sample: elongation, tensile strength, DN.
  The sheet still prints with the standards alone if no test exists.
- The seven inspection clauses (a–g), fixed text.

Signed by the Quality Assurance Section-Head and the Quality Director.

## MTC (LAB-DOC-8.1.1)

Bilingual throughout, its own letterhead, and one row per **heat** (ladle) — so it runs to
more than one page on a large delivery. Certified under EN 10204 Type 3.1.

- **Material details** state one delivery, not a line per length: total metres, total
  quantity, every class, and one Batch# line per annealing batch the pipes went through.
- **Section 1 chemistry** — seven elements (Mg, Mn, S, Cr, Cu, Si, C; no CE), standard
  source ASTM A536.
- **Section 2 mechanics** — the ISO 2531 limits, in this form's units: tensile in **MPa**
  (not Kg/mm² like the work test), plus hardness HB.
- **Heat table** — per ladle: chemistry, then tensile / elongation / hardness.

### The standard-vs-actual toggle

`values_mode` on the certificate, chosen on the builder form:

- `standard` — the chemical columns print the accepted limits, the way the sample sheet
  the client sent does.
- `actual` — each heat row prints that ladle's own measured analysis, and the section-1
  row becomes the **average** across the selected ladles.

The client issues the same delivery both ways depending on what the customer asked for,
and both are approved — hence a per-certificate choice, not a setting. The mechanical
columns are always the ladle's own results in either mode; that is the point of listing
the heats at all.

Heats and batches sort naturally (2 before 10), since the heat numbers are slash-separated
digits that a plain string sort files wrongly.

## Column order gotcha

The two QC workbooks are `rightToLeft="1"`, so their column letters run backwards: the
merge in `L:M` is the **leftmost** thing on the printed page and `A:E` the rightmost. The
work test's chemical strip and mechanical table both had to be mirrored against the raw
cell dump to match what the client actually sees. **MTC1 is not RTL** and reads normally —
check the flag before assuming either way.

## Implementation

- `app/models/certificate.py` — `Certificate` + `certificate_pipes`
- `app/services/certificate_service.py` — pipe grouping, chemical limits, mechanical sample
- `app/routes/certificates.py` — blueprint at `/certificates`
- `app/templates/certificates/` — `_print_base.html` (shared letterhead, references,
  material table, signatures) with `warranty_print.html` and `work_test_print.html`
  extending it; `mtc_print.html` is standalone because its letterhead and layout differ;
  plus `list.html` and `form.html`
- `app/models/permission.py` — `certificates` module (admin + supervisor by default)
- `migrations/versions/e7c95d21ab40_add_certificates.py`
- `tests/test_certificates.py`

The Bureau Veritas badge was extracted from the workbook to
`app/static/images/bv_iso9001.png`; the GCP logo already existed.

## Round 2 — client walkthrough of 2026-09-05

The client reviewed all three forms on prod (which still ran the single-order build) and
asked for the following. Branch `feat/certificates-round-2`, migration `a9d2e7f14c58`.

| Code | Ask | Done as |
|---|---|---|
| C1 | Pick the customer, then the order the certificate is issued under, then add pipes from other orders (same customer or not) | Customer filter + **Primary order** select + **Additional orders** multi-select on the builder. `primary_order_id` is stored as `production_order_id`; coverage still derives from the ticked pipes. A primary with no ticked pipe falls back to the first ticked order and warns. |
| C2 | Count + sum of *actual* lengths | Already on main (`group_pipes` sums Finish `measurement_value`, order `product_length` fallback). Picker now shows each pipe's length and a live sum. |
| C3 | The metres figure is confirmable / correctable | `Certificate.total_length_m`. The form mirrors the computed sum until the user types; NULL prints the computed sum, a value prints as-is on all three forms. |
| C4 | Work test names the standard the product is built to | `work_test_standards(order)` reads the order's Application block: sewage → "ISO 8179 & EN 598", water → "ISO 2531 & EN 545" (the ISO/EN pair prints together), nothing ticked → the paper form's "ISO 2531 & EN 545". The numeric limits stay — every one of these asks 420 MPa / 10 %. |
| C5 | Work-test tensile / elongation are the average over the chosen ladles | `mechanical_sample` averages every ACTIVE test on the selected pipes or their ladles; Notes cell reads "Average of N heats". |
| C6 | MTC mechanical columns follow Standard/Actual | Standard mode prints the ISO limits; actual mode prints the per-ladle **mean** of every ACTIVE test (a missing hardness drops out of the mean, not into it). |
| C7 | Batch No. per ladle | New `ChemicalAnalysis.batch_no`, typed on the chemical form; the MTC heat table gains a Batch No. column and the Batch# band lists the ladles' batches. Closes **Q3**: the batch is *not* the annealing bundle. |
| C8 | An MTC never mixes DN or class | Server refuses with a message naming the combos; the picker greys out mismatched pipes once one is ticked. Warranty and work test still print one row per DN/class. |
| C9 | "12 m for 2 pipes — standard or actual?" | Header reads **Total Length (m)**; with several DN/class rows a Total line closes the table. |

Out of scope, unchanged: header/title, the seven inspection clauses, signatories.

## Open questions

- **Q2** Order No. is typed in per the client, but the system does hold `sales_number` on
  the order. Confirm they are different things before prefilling it.
- ~~**Q3**~~ Closed 2026-09-05: the batch is the ladle's own number off the chemical sheet
  (see C7 above).
- **Q4** The MTC's sample sheet names its two signatories. They print as blank Name lines
  here, since a person's name baked into a template goes stale. Confirm that is wanted.
