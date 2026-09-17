"""Per-meter readings compared against the standard the run is built to.

The dimension grid still takes its symbols and nominals from TA 1012 keyed by
DN (Settings > Dimension Standards). The Min/Nominal/Max band the CCM wall
thickness, cement and coating readings are judged against does not: since
2026-08-27 it comes from the product's Application table for the standard the
order picked, and the grids render it as three editable boxes.

Either way the rule that matters is unchanged. An empty band must say it is
empty, and a nominal with no tolerance must withhold the verdict, rather than
let the grid look like it is judging anything.
"""

import json
import shutil
import unittest
from datetime import date

from app import create_app, db
from app.models.permission import seed_default_permissions
from app.models.pipe import Pipe, PipeStage
from app.models.stage import ProductionStage
from app.models.user import User
from app.services import dimension_standard_service as std


class _Base(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()
        self.client = self.app.test_client()

        u = User(username="admin", full_name="Admin", role="super_admin",
                 is_active=True)
        u.set_password("x")
        db.session.add(u)
        db.session.commit()
        self.user_id = u.id

        self.backup = std.STANDARDS_FILE + ".cmpbak"
        shutil.copy2(std.STANDARDS_FILE, self.backup)

        self.coating = ProductionStage.name_for_code("coating")

    def tearDown(self):
        shutil.move(self.backup, std.STANDARDS_FILE)
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _pipe(self, diameter=800):
        pipe = Pipe(production_date=date(2026, 8, 20), ladle_id="L",
                    pipe_code="SC1", no_code="S0001", arrange_pipe=1,
                    diameter=diameter, pipe_class="K9")
        db.session.add(pipe)
        db.session.commit()
        return pipe

    def _set_standard(self, dn, symbol, nominal, tol_plus=None, tol_minus=None):
        doc = std.load_standards()
        doc["by_dn"][str(dn)][symbol] = {
            "nominal": nominal, "tol_plus": tol_plus, "tol_minus": tol_minus}
        std.save_standards(doc)

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_id)
            sess["_fresh"] = True

    def _form(self, pipe):
        self._login()
        return self.client.get(
            "/stages/%s/edit" % pipe.id).get_data(as_text=True)


class ConfigTest(_Base):
    def test_the_lining_is_not_a_dimension_standard_symbol(self):
        # TA 1012 names neither, and they shipped with no nominal on any DN.
        # The lining spec belongs to the product's Application table.
        self.assertNotIn("cement", std.symbol_keys())
        self.assertNotIn("coating", std.symbol_keys())
        for dn in std.available_dns():
            entries = std.for_dn(dn)
            self.assertNotIn("cement", entries)
            self.assertNotIn("coating", entries)

    def test_a_lining_entry_left_in_the_config_is_ignored(self):
        # An install that filled one in while it was still offered must not get
        # a second, DN-keyed answer competing with the product's figures.
        self._set_standard(800, "cement", 3.5, tol_plus=0.5, tol_minus=0.5)
        self.assertNotIn("cement", std.for_dn(800))
        self.assertNotIn("cement", std.ovality_points_for(800))

    def test_the_sheets_own_symbols_are_untouched(self):
        entries = std.for_dn(800)
        self.assertIn("S1", entries)
        self.assertIn("d1", entries)
        self.assertEqual(entries["S1"]["nominal"], 11.7)


class FormTest(_Base):
    def test_the_dimension_grid_carries_the_dn_standard(self):
        # The TA 1012 grid measures S1 as one of the sheet's symbols, and that
        # nominal is still DN-keyed. Distinct from the CCM 7-position wall
        # thickness grid, whose band comes from the order.
        pipe = self._pipe(800)
        html = self._form(pipe)
        self.assertIn('data-cmp="S1"', html)
        self.assertIn('data-nominal="11.7"', html)   # S1 for DN800

    def test_settings_no_longer_feeds_the_lining_grid(self):
        # Configuring cement in Settings > Dimension Standards used to reach
        # the lining grid. There is one source for the lining now.
        self._set_standard(800, "cement", 3.5, tol_plus=0.5, tol_minus=0.5)
        pipe = self._pipe(800)
        html = self._form(pipe)
        # the cement grid is still rendered — X/Y at the socket and the spigot
        self.assertIn('name="stage_%s_thick_cement_pt_socket_1_x"'
                      % self.coating, html)
        # but Settings does not judge it
        self.assertNotIn('data-lsl="3.0"', html)

    def test_the_lining_grid_carries_the_saved_band(self):
        pipe = self._pipe(800)
        db.session.add(PipeStage(
            pipe_id=pipe.id, stage_name=self.coating,
            thickness_profile={"cement_std": 3.5, "cement_std_min": 3.0,
                               "cement_std_max": 4.0},
        ))
        db.session.commit()
        html = self._form(pipe)
        band = html.split('name="stage_%s_thick_cement_std_min"'
                          % self.coating, 1)[1].split(">", 1)[0]
        self.assertIn('value="3.0"', band)
        # and every X/Y cell is judged against it
        self.assertIn('data-lsl="3.0"', html)
        self.assertIn('data-usl="4.0"', html)

    def test_missing_lining_standard_says_so(self):
        # No order, so no Application band: the grid has nothing to judge
        # against and must say so instead of looking like everything passed.
        pipe = self._pipe(800)
        html = self._form(pipe)
        self.assertIn("No standard on file for", html)
        self.assertIn('spec-note spec-note-missing" data-spec-for=', html)

    def test_a_standard_without_tolerance_withholds_the_verdict(self):
        # A nominal with no Min and no Max: the deviation is still worth
        # showing, but no pass/fail can be given, so neither is claimed.
        pipe = self._pipe(800)
        db.session.add(PipeStage(
            pipe_id=pipe.id, stage_name=self.coating,
            thickness_profile={"cement_std": 3.5},
        ))
        db.session.commit()
        html = self._form(pipe)
        self.assertIn("No tolerance on file for", html)
        self.assertIn('spec-note spec-note-notol" data-spec-for=', html)

    def test_unknown_dn_does_not_pretend_to_have_a_standard(self):
        pipe = self._pipe(9999)
        html = self._form(pipe)
        self.assertNotIn('data-nominal="11.7"', html)

    def test_deviation_cells_are_present_for_every_column(self):
        pipe = self._pipe(800)
        html = self._form(pipe)
        self.assertIn('class="fw-bold std-cmp-diff" data-cmp="S1" data-col="0"', html)
        self.assertIn('data-cmp="S1" data-col="6"', html)


if __name__ == "__main__":
    unittest.main()
