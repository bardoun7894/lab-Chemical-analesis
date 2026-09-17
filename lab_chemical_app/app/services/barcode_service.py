"""One rule for every scannable code in the app.

Product barcodes and the per-pipe warehouse barcode are both read by the same
handheld scanners, so they get the same normalisation: a stray letter or a
trailing space from a mis-configured reader has to fail loudly here rather
than quietly store a code that will never scan back.
"""


MAX_BARCODE_LEN = 32


def clean_barcode(raw):
    """Normalize a submitted barcode. Returns (value, error_message).

    Digits only. Empty is allowed and comes back as None — a barcode is
    optional everywhere it appears, and blanking one is a legitimate edit.
    """
    value = (raw or "").strip()
    if not value:
        return None, None
    if not value.isdigit():
        return None, "Barcode must contain digits only (e.g. 2100000172400)"
    if len(value) > MAX_BARCODE_LEN:
        return None, f"Barcode is too long (max {MAX_BARCODE_LEN} digits)"
    return value, None
