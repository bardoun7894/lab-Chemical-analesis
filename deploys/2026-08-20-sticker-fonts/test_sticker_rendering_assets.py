"""Guards for the sticker's rendering assets — fonts and the barcode library.

Both are loaded through fallbacks that degrade instead of raising, so when
they go missing the label simply renders in the old sparse style and nothing
anywhere reports a problem. That is exactly what happened on production:
python-barcode was imported but never declared in requirements.txt, and
python:3.12-slim ships no TrueType fonts, so for a month the pipe label drew
with PIL's bitmap default and no Code128 strip.

These tests fail loudly in any environment that cannot render a correct label.
"""
import unittest

from app import create_app
from app.routes.stickers import (
    FONT_PATHS_BOLD,
    FONT_PATHS_REGULAR,
    _load_font,
)


class StickerFontAssetsTestCase(unittest.TestCase):
    """The renderer must find a real scalable font, not the bitmap fallback."""

    def setUp(self):
        self.app = create_app("testing")
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        self.ctx.pop()

    # NOTE: do not assert isinstance(font, ImageFont.FreeTypeFont) — Pillow >= 10
    # returns a FreeTypeFont from load_default() too (its bundled Aileron), so
    # the fallback passes that check. Assert the resolved path and the scaling
    # behaviour instead; those actually distinguish the two.

    def test_font_resolves_to_a_configured_path(self):
        font = _load_font(40, bold=False)
        self.assertIn(
            getattr(font, "path", None), FONT_PATHS_REGULAR,
            "no configured TrueType font on this system — the sticker renders "
            "with PIL's fixed-size default and looks like the pre-dab2e39 "
            "sparse layout. Install fonts-dejavu-core.",
        )

    def test_bold_font_resolves_to_a_configured_path(self):
        font = _load_font(40, bold=True)
        self.assertIn(getattr(font, "path", None), FONT_PATHS_BOLD)

    def test_font_scales_with_requested_size(self):
        """The default fallback is fixed-size; a real scalable font is not.

        This is the assertion that caught the broken production image.
        """
        small = _load_font(12)
        large = _load_font(48)
        self.assertLess(
            small.getbbox("PIP1")[3], large.getbbox("PIP1")[3],
            "font size had no effect — this is the fallback signature",
        )


class StickerBarcodeDependencyTestCase(unittest.TestCase):
    """python-barcode must be installed, not merely present in a dev venv."""

    def test_barcode_module_importable(self):
        try:
            import barcode  # noqa: F401
        except ImportError as exc:  # pragma: no cover - the failure we guard
            self.fail(
                f"python-barcode is not installed ({exc}). stickers.py imports "
                "it to draw the Code128 strip and swallows the failure, so the "
                "label silently loses its barcode. It must stay declared in "
                "requirements.txt."
            )

    def test_code128_renders(self):
        from io import BytesIO

        import barcode
        from barcode.writer import ImageWriter

        buf = BytesIO()
        barcode.get("code128", "PIP1-1-112082026", writer=ImageWriter()).write(
            buf,
            options={"module_height": 9.0, "font_size": 7,
                     "text_distance": 3.0, "quiet_zone": 1.0},
        )
        self.assertGreater(buf.tell(), 0, "Code128 write produced no image")


if __name__ == "__main__":
    unittest.main()
