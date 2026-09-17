# Certificate Accuracy and Consistent Pipe Selection Design

**Date:** 2026-09-15
**Project:** Chemical Lab
**Status:** Proposed for user review

## Objective

Make warehouse receiving, sticker printing, and certificate creation use the
same pipe-selection logic without merging the users or their screens. Correct
the three customer certificates so that their frames, standards, coating text,
material data, and signatories reflect the selected production orders and
pipes.

## Scope and role separation

The warehouse, sticker, and certificate screens remain separate:

- Warehouse staff continue to work only from the warehouse receiving screen
  and its existing permissions.
- Quality staff continue to work from the sticker and certificate screens and
  their existing permissions.
- There will be no central pipe-selection screen and no new shared navigation
  entry.
- The application will reuse a server-side selection service, a small UI
  partial, and browser-side selection behavior so that the three screens do not
  drift apart again.

Reports, mechanical-decision behavior, and the later report feedback mentioned
in WhatsApp are outside this change.

## Common selection behavior

Each of the three screens will offer both entry paths at the same time:

1. Select one or more production orders and load their pipes.
2. Scan a barcode or type a pipe number and add the matching pipe directly.

Both paths feed one on-screen selection basket. The user can select all visible
pipes, select individual pipes, remove a pipe, clear the basket, and see the
order, pipe number, DN, class, status, and any reason the pipe is unavailable.
A refresh must not silently lose a long warehouse selection; the warehouse
basket will retain its current session-storage behavior.

The shared layer is responsible only for finding orders and pipes, preserving
selection, avoiding duplicate IDs, and returning status metadata. Each module
continues to enforce its own business eligibility and perform its own action.
All eligibility rules are checked again on the server at save/print time; the
browser is not trusted as the final gate.

### Warehouse receiving

- Keep scan/manual pipe-number lookup.
- Add one-or-more-order selection and a table of pipes that can be added to the
  same delivery basket.
- Apply one set of delivery fields to the selected pipes, as the current batch
  receiving flow does.
- Rejected, held, or not-yet-accepted pipes remain visible with the blocking
  reason but cannot be submitted.
- A pipe that already has delivery data is not eligible for a new delivery.
  Scanning it or loading it through an order shows its previous delivery date,
  receipt, sales order, and customer instead of adding it to the basket.
- Correcting an existing delivery remains a separate edit of the existing
  Delivery stage, protected by the appropriate warehouse permission and
  recorded by the existing audit mechanism. Normal receiving must never
  overwrite a previous delivery.

### Sticker printing

- Keep scan/manual pipe lookup and the existing single-sticker preview.
- Upgrade bulk printing from one production-order filter to a one-or-more-order
  selector, feeding the same selectable pipe table and basket pattern.
- Printing a sticker again is allowed: a damaged label may need replacement.
  This does not change pipe or delivery state.
- Existing sticker-specific filters and size/internal-label choices remain on
  the sticker screen.

### Certificate creation

- Keep one primary order plus optional additional orders.
- Add scan/manual pipe lookup to the same selection used by the order-loaded
  table.
- A rejected pipe remains unavailable.
- A pipe may appear once in each certificate type: Warranty, Work Test, and
  MTC are independent documents and may legitimately cover the same pipe.
- A pipe already linked to a saved certificate of the same type is unavailable
  for a new ordinary certificate. The UI shows the existing certificate number
  and links to its print/edit page. Editing that original certificate may keep
  or remove its own pipes; it is not blocked by its own duplicate check.
- Reprinting uses the original saved certificate and number. Correcting it uses
  the existing edit route.
- An exceptional reissue requires a dedicated quality permission, selection of
  the original certificate, and a non-empty reason. The new record stores its
  relationship to the original and the reason so the audit trail is explicit.

## Certificate specification consistency

Certificate claims are derived from the selected pipes' production orders,
not from fixed template text. The exact standards checked in each order's
Application profile print in their configured order; the system must not
silently expand one selected standard into a pair. Before saving a certificate,
the server builds a normalized specification signature from each selected
order: application standards, zinc presence/type, internal finish, and
external finish.

Multiple orders may be used on one certificate only when their signatures are
compatible for the statements printed by that certificate. If they differ,
creation stops with a message listing the conflicting orders and fields. The
user must split them into accurate certificates; the application must not
merge unlike specifications into a misleading sentence.

Legacy orders without an Application profile will use the same documented
fallbacks as the current paper forms, but the print preview will show a warning
that the fallback was used.

### Warranty certificate

- Draw a continuous page frame around the certificate content beneath the
  existing letterhead, including both side borders and the bottom border.
- Continue deriving the item description and displayed standards from the
  primary/compatible selected order Application profile.
- Remove any remaining fixed standard phrase that can contradict that profile.

### Work Test certificate

- Draw the same continuous page frame used by the Warranty certificate.
- Use the compatible selected orders' Application standards in the
  introduction and mechanical-properties clause.
- Build the external-coating sentence from the selected product parameters:
  zinc type/presence and external finish.
- When zinc exists, name the zinc layer and the Bitumen/Epoxy finishing layer,
  and print the zinc standard (ISO 8179) only in this zinc branch.
- When zinc does not exist, omit the word and zinc layer entirely and describe
  the external finish directly as Bitumen or Epoxy. The template must not claim
  that a non-zinc pipe is zinc coated.

### Material Test Certificate (MTC)

- Right-align the customer, order, and project values so their presentation
  follows the intended bilingual layout.
- Use the compatible selected orders' Application standards in the opening,
  mechanical Standard cell, and closing statement; remove fixed
  `ISO 2531 / EN 545` claims when they do not match the order. `ASTM A536`
  remains the chemical-composition limit source unless the data model later
  gains an explicit chemical-standard field; it is not substituted for the
  order's product-conformity standard.
- Replace `Batch#` in the Material Details table with `Notes / ملاحظات` and
  print the manually entered certificate notes there, matching the other
  certificate forms.
- Keep each heat row's Batch No. sourced from the selected pipes' Annealing
  stage. The chemical-analysis batch number is not used.
- Add an MTC form option, off by default, named `Show pipe numbers`. When on,
  the per-heat results table gains a Pipe No. column listing the selected pipe
  numbers belonging to that heat. The choice is stored on the certificate so a
  reprint is identical.
- Keep the per-heat Notes column reserved for row-specific information; do not
  duplicate the general certificate note into every heat row.

### Signatories

Use the same pair on all three certificates:

- Quality Assurance Manager / مدير ضمان الجودة
- Quality Director / مدير الجودة

## Data changes

Add the following certificate fields through a database migration:

- `show_pipe_numbers` — boolean, non-null, default false.
- `reissue_of_id` — nullable self-reference to the original certificate.
- `reissue_reason` — nullable text, required by application validation for a
  reissue.

Add a quality permission for exceptional certificate reissue. Do not add a
warehouse user to quality permissions or a quality user to warehouse
permissions as part of this feature.

No new warehouse-delivery table is required. Existing delivery data remains on
the Delivery stage; the normal receiving routes gain a delivered-state guard,
while the explicit edit path continues to update that same audited record.

## Components and data flow

1. A shared pipe-selection service parses order IDs, performs exact and partial
   pipe lookup, loads ordered results, and de-duplicates selections.
2. Warehouse, sticker, and certificate routes supply their eligibility policy
   and serialize module-specific status information.
3. A reusable template partial renders the order selector, scan/search input,
   pipe table, and selected basket inside each existing module screen.
4. Each form posts selected pipe IDs to its existing module endpoint.
5. The endpoint reloads the pipes, verifies permissions and eligibility inside
   the transaction, then performs receiving, sticker rendering, or certificate
   creation.
6. Certificate creation additionally checks same-type duplication and the
   specification signature before issuing a number.

The shared service must not contain warehouse delivery writes, sticker image
generation, certificate numbering, or cross-module permission decisions.

## Error handling

- Missing/deleted pipe between selection and submit: do not skip it silently;
  reject the operation with the affected pipe identifier.
- Pipe status changed after selection: reject that pipe with the current
  reason and leave unrelated database state unchanged.
- Previously delivered pipe: return prior delivery details and do not
  overwrite them through normal receiving.
- Same-type certificate duplicate: show existing certificate number(s) and
  offer reprint/edit; show the reissue controls only to authorized users.
- Mixed specification orders: identify each mismatch and stop before a
  certificate number is issued.
- Invalid or empty reissue reason: do not create the reissue.
- Missing Application profile: use the existing fallback with a visible
  preview warning and a server log entry.

## Testing and acceptance criteria

### Shared selection

- Every module accepts one or multiple orders and can add a pipe by exact
  barcode or typed pipe number.
- Duplicate scans/order loads produce one selected row.
- Module permissions still deny users outside their own area.
- Server-side eligibility catches status changes made after the page loaded.

### Warehouse

- A first delivery succeeds for accepted pipes.
- A normal second delivery is blocked and the original values remain intact.
- An authorized correction edits the existing delivery and produces audit
  entries.
- Rejected, held, and pending pipes cannot be delivered from either entry path.

### Stickers

- Multiple orders can feed one bulk print selection.
- Single and repeated sticker printing remain allowed and do not alter delivery
  or certificate state.

### Certificates

- The same pipe can receive one Warranty, one Work Test, and one MTC.
- A second ordinary certificate of the same type is blocked, while editing the
  original certificate can retain its existing pipes.
- Reprint/edit uses the existing certificate; an authorized reissue stores the
  original link and reason.
- Compatible multi-order certificates save; incompatible Application/coating
  combinations are rejected before numbering.
- Warranty and Work Test frames visibly close on all sides when printed to A4.
- Warranty, Work Test, and MTC standards match the selected order Application.
- Zinc, no-zinc, Bitumen, and Epoxy branches render accurate Work Test text.
- MTC Material Details shows manual Notes, heat Batch No. comes from Annealing,
  and the optional pipe-number column survives save/edit/reprint.
- All three forms show the same bilingual signatory titles.

Print verification must include rendered A4 screenshots or PDFs for all three
certificate types, including zinc/no-zinc and MTC pipe-number on/off cases.
