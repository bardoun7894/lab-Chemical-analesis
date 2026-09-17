# Certificate Print Acceptance - 2026-09-15

These PDFs were rendered from the real authenticated Flask certificate print
routes with headless Chrome using print media and A4 output.

## Acceptance set

- `warranty-a4.pdf` - EN 545 warranty with continuous content frame.
- `work-test-zinc-bitumen-a4.pdf` - EN 598, Zinc 200 g/m2, Bitumen, and the
  zinc-branch ISO 8179 claim.
- `work-test-no-zinc-epoxy-a4.pdf` - EN 545 and direct Epoxy coating, without
  a zinc-layer or ISO 8179 claim.
- `mtc-pipe-numbers-on-a4.pdf` - ISO 2531 MTC with two selected pipe numbers
  grouped under one heat.
- `mtc-pipe-numbers-off-a4.pdf` - EN 598 MTC with the optional Pipe No. column
  absent.

## Verification result

Poppler reported every file as one A4 portrait page (`594.96 x 841.92 pt`).
Every page was rasterized at 120 DPI and visually inspected. The Warranty and
both Work Test pages have continuous left, right, and bottom frame borders.
No table, signature block, or footer crosses a page boundary. Text and logos
are legible, the MTC heat table is not clipped, and the pipe-number column
changes cleanly between the on/off variants.
