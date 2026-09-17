"""Customer certificates: warranty (QC-01-F-28) and work test (QC-01-F-26).

What the client asked for, on both forms: pick the order, tick the pipes, and
the sheet fills its own quantity, DN, class and lengths. The references that
live on the customer's paperwork — their order number, the project, and for the
warranty its start date — are typed in. The certificate number is a serial the
system issues once, so a reprint carries the same number.
"""

import unittest
from datetime import date

from app import create_app, db
from app.models.certificate import Certificate
from app.models.chemical import ElementSpecification
from app.models.mechanical import MechanicalTest
from app.models.permission import seed_default_permissions
from app.models.pipe import Pipe, PipeStage
from app.models.production_order import ProductionOrder
from app.models.user import User
from app.models.chemical import ChemicalAnalysis
from app.services.certificate_service import (
    batch_numbers, chemical_limits, dn_range_label, group_pipes,
    mechanical_sample, mtc_chemical_summary, mtc_heats)


class CertificateTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()
        self.client = self.app.test_client()

        u = User(username="admin", full_name="Admin", role="admin", is_active=True)
        u.set_password("x")
        db.session.add(u)
        db.session.commit()
        self.user_id = u.id

        self.order = ProductionOrder(
            order_number="PO-20260902-001",
            customer_name="Cairo Water",
            target_quantity=10,
            diameter=800,
            pipe_class="K9",
            product_description="Ductile Iron Pipes",
            product_length=6.0,
            order_date=date(2026, 9, 1),
        )
        db.session.add(self.order)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_id)
            sess["_fresh"] = True

    def _pipe(self, no_code, decision="ACCEPT", length=None, ladle="L1"):
        p = Pipe(
            production_date=date(2026, 9, 1), ladle_id=ladle,
            pipe_code=no_code, no_code=no_code, arrange_pipe=1,
            diameter=800, pipe_class="K9",
            production_order_id=self.order.id,
            final_decision_value=decision,
        )
        db.session.add(p)
        db.session.commit()
        if length is not None:
            db.session.add(PipeStage(
                pipe_id=p.id, stage_name="Finish",
                measurement_value=length, decision="Accept",
            ))
            db.session.commit()
        return p

    # ---- serial -----------------------------------------------------------

    def test_serial_is_year_scoped_and_per_form(self):
        first = Certificate.generate_certificate_no(
            Certificate.WARRANTY, date(2026, 9, 2))
        self.assertEqual("WC-2026-001", first)

        db.session.add(Certificate(
            certificate_no=first, cert_type=Certificate.WARRANTY,
            production_order_id=self.order.id,
            issue_date=date(2026, 9, 2), warranty_years=3))
        db.session.commit()

        self.assertEqual(
            "WC-2026-002",
            Certificate.generate_certificate_no(
                Certificate.WARRANTY, date(2026, 12, 31)))
        # The work test counts on its own, so the two never collide.
        self.assertEqual(
            "WT-2026-001",
            Certificate.generate_certificate_no(
                Certificate.WORK_TEST, date(2026, 9, 2)))
        # A new year restarts the count.
        self.assertEqual(
            "WC-2027-001",
            Certificate.generate_certificate_no(
                Certificate.WARRANTY, date(2027, 1, 2)))

    def test_a_taken_serial_is_retried_not_a_500(self):
        """Four gunicorn workers can read the same 'last' number at once.

        Simulated by letting the first generate_certificate_no hand back a
        number that already exists, the way a racing worker's commit would.
        """
        p = self._pipe("N0090", length=6.0)
        db.session.add(Certificate(
            certificate_no="WC-2026-001", cert_type=Certificate.WARRANTY,
            production_order_id=self.order.id,
            issue_date=date(2026, 9, 2), warranty_years=3))
        db.session.commit()

        real = Certificate.generate_certificate_no
        calls = {"n": 0}

        def stale_then_real(cert_type=Certificate.WARRANTY, when=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return "WC-2026-001"          # already taken
            return real(cert_type, when)

        Certificate.generate_certificate_no = staticmethod(stale_then_real)
        try:
            self._login()
            resp = self.client.post(
                f"/certificates/new?order_id={self.order.id}",
                data={"pipe_ids": [str(p.id)], "warranty_years": "3"},
                follow_redirects=True)
        finally:
            Certificate.generate_certificate_no = real

        self.assertEqual(200, resp.status_code)
        self.assertEqual(2, Certificate.query.count())
        issued = Certificate.query.filter(
            Certificate.certificate_no != "WC-2026-001").one()
        self.assertEqual("WC-2026-002", issued.certificate_no)
        # The retry must not lose the pipe selection.
        self.assertEqual(1, issued.quantity)

    # ---- the table fills itself from the chosen pipes ----------------------

    def test_selected_pipes_drive_quantity_and_length(self):
        # Same DN and class, so all three land on one row — its Pipe Length
        # is the sum of what each pipe actually measured, not one of theirs
        # copied onto the row (the bug: a two-pipe certificate printed the
        # length of a single pipe).
        a = self._pipe("N0001", length=6.0)
        b = self._pipe("N0002", length=6.0)
        c = self._pipe("N0003", length=5.5)

        rows = group_pipes([a, b, c], self.order)
        self.assertEqual(1, len(rows))
        self.assertEqual(3, rows[0]["quantity"])
        self.assertEqual(17.5, rows[0]["length"])
        self.assertEqual(800, rows[0]["dn"])
        self.assertEqual("K9", rows[0]["pipe_class"])

    def test_unmeasured_pipe_falls_back_to_the_order_length(self):
        p = self._pipe("N0004")
        rows = group_pipes([p], self.order)
        self.assertEqual(1, len(rows))
        self.assertEqual(6.0, rows[0]["length"])

    def test_rejected_pipes_are_visible_but_unavailable(self):
        self._pipe("N0010")
        self._pipe("N0011", decision="REJECT")
        self._login()

        resp = self.client.get(f"/certificates/new?order_id={self.order.id}")
        self.assertEqual(200, resp.status_code)
        body = resp.get_data(as_text=True)
        self.assertIn("N0010", body)
        self.assertIn("N0011", body)
        self.assertIn("REJECT", body)
        self.assertRegex(body, r'value="\d+"[^>]*disabled')

    # ---- warranty form -----------------------------------------------------

    def test_warranty_create_then_print_carries_every_field(self):
        a = self._pipe("N0020", length=6.0)
        b = self._pipe("N0021", length=6.0)
        self._login()

        resp = self.client.post(
            f"/certificates/new?type=warranty&order_id={self.order.id}",
            data={
                "pipe_ids": [str(a.id), str(b.id)],
                "warranty_start_date": "2026-10-01",
                "customer_order_no": "CW-778",
                "project_name": "New Cairo Trunk Main",
                "warranty_years": "2",
                "notes": "Coated batch",
            },
            follow_redirects=True)
        self.assertEqual(200, resp.status_code)

        cert = Certificate.query.one()
        self.assertEqual("WC-2026-001", cert.certificate_no)
        self.assertEqual(Certificate.WARRANTY, cert.cert_type)
        self.assertEqual(2, cert.quantity)
        self.assertEqual(date(2026, 10, 1), cert.warranty_start_date)
        self.assertEqual(2, cert.warranty_years)

        body = resp.get_data(as_text=True)
        self.assertIn("WC-2026-001", body)
        self.assertIn("CW-778", body)
        self.assertIn("New Cairo Trunk Main", body)
        self.assertIn("Cairo Water", body)
        self.assertIn("DN800", body)
        self.assertIn("K9", body)
        # The period appears in both clauses, in both languages.
        self.assertIn("two years", body)
        self.assertIn("سنتين", body)
        self.assertIn("QC-01-F-28", body)

    def test_certificate_with_no_pipes_is_refused(self):
        self._pipe("N0030")
        self._login()
        resp = self.client.post(
            f"/certificates/new?order_id={self.order.id}",
            data={"warranty_years": "3"})
        self.assertEqual(200, resp.status_code)
        self.assertEqual(0, Certificate.query.count())

    def test_reprint_keeps_the_same_serial(self):
        p = self._pipe("N0040", length=6.0)
        self._login()
        self.client.post(
            f"/certificates/new?order_id={self.order.id}",
            data={"pipe_ids": [str(p.id)], "warranty_years": "3"},
            follow_redirects=True)
        cert = Certificate.query.one()

        first = self.client.get(f"/certificates/{cert.id}/print?noauto=1")
        second = self.client.get(f"/certificates/{cert.id}/print?noauto=1")
        self.assertIn(cert.certificate_no, first.get_data(as_text=True))
        self.assertIn(cert.certificate_no, second.get_data(as_text=True))
        self.assertEqual(1, Certificate.query.count())

    # ---- work test form ----------------------------------------------------

    def test_work_test_prints_its_own_form_and_serial(self):
        p = self._pipe("N0050", length=6.0)
        self._login()

        resp = self.client.post(
            f"/certificates/new?type=work_test&order_id={self.order.id}",
            data={
                "pipe_ids": [str(p.id)],
                "customer_order_no": "CW-901",
                "project_name": "Sixth of October Main",
            },
            follow_redirects=True)
        self.assertEqual(200, resp.status_code)

        cert = Certificate.query.one()
        self.assertEqual("WT-2026-001", cert.certificate_no)
        self.assertEqual(Certificate.WORK_TEST, cert.cert_type)

        body = resp.get_data(as_text=True)
        self.assertIn("QC-01-F-26", body)
        self.assertIn("TEST CERTIFICATE NO.", body)
        self.assertIn("CW-901", body)
        self.assertIn("Sixth of October Main", body)
        self.assertIn("Chemical Analysis", body)
        self.assertIn("Hydro-static Test", body)
        self.assertIn(
            "Quality Assurance Manager / مدير ضمان الجودة", body)
        self.assertIn("Quality Director / مدير الجودة", body)
        # The warranty clause belongs to the other form only.
        self.assertNotIn("out of warranty scope", body)

    def test_chemical_limits_follow_the_configured_specifications(self):
        db.session.add(ElementSpecification(
            element_code="Mg", element_name="Magnesium",
            min_value=0.035, max_value=0.065))
        db.session.add(ElementSpecification(
            element_code="S", element_name="Sulphur",
            min_value=None, max_value=0.012))
        db.session.commit()

        limits = dict(chemical_limits())
        # Written to the same decimals as the paper form, so 0.035 keeps three
        # places rather than collapsing to 0.035 / 0.06.
        self.assertEqual("0.035: 0.065", limits["Mg"])
        self.assertEqual("Max. 0.012", limits["S"])
        # An element with no row keeps what the paper form used to say.
        self.assertEqual("1.8: 2.8", limits["Si"])

    def test_configured_limits_keep_the_paper_form_s_trailing_zeros(self):
        db.session.add(ElementSpecification(
            element_code="C", min_value=3.0, max_value=4.2))
        db.session.commit()
        self.assertEqual("3.00: 4.20", dict(chemical_limits())["C"])

    def test_mechanical_sample_comes_from_the_selected_pipes(self):
        p = self._pipe("N0060", length=6.0, ladle="L7")
        db.session.add(MechanicalTest(
            test_date=date(2026, 9, 1), diameter=800, ladle_id="L7",
            tensile_strength=49.4, elongation=12.6, status="ACTIVE"))
        # Every active test on the ladle counts: the sheet quotes the average
        # of the delivery (2026-09-05), not whichever test was newest.
        db.session.add(MechanicalTest(
            test_date=date(2026, 8, 1), diameter=800, ladle_id="L7",
            tensile_strength=44.0, elongation=10.0, status="ACTIVE"))
        # A superseded test is out of it.
        db.session.add(MechanicalTest(
            test_date=date(2026, 7, 1), diameter=800, ladle_id="L7",
            tensile_strength=1.0, elongation=1.0, status="SUPERSEDED"))
        db.session.commit()

        sample = mechanical_sample([p])
        self.assertIsNotNone(sample)
        self.assertEqual(46.7, sample["tensile"])
        self.assertEqual(11.3, sample["elongation"])
        self.assertEqual("800", sample["dn"])

    def test_work_test_prints_without_any_mechanical_test(self):
        p = self._pipe("N0070", length=6.0)
        self._login()
        resp = self.client.post(
            f"/certificates/new?type=work_test&order_id={self.order.id}",
            data={"pipe_ids": [str(p.id)]},
            follow_redirects=True)
        self.assertEqual(200, resp.status_code)
        self.assertIn("Min. : 42.8", resp.get_data(as_text=True))

    # ---- MTC -------------------------------------------------------------

    def _heat(self, ladle, ladle_no, **elements):
        values = {"magnesium": 0.050, "manganese": 0.30, "sulfur": 0.010,
                  "chromium": 0.03, "copper": 0.05, "silicon": 2.3,
                  "carbon": 3.60}
        values.update(elements)
        db.session.add(ChemicalAnalysis(
            test_date=date(2026, 9, 1), ladle_no=ladle_no, ladle_id=ladle,
            **values))
        db.session.commit()

    def test_mtc_heat_rows_follow_the_chosen_mode(self):
        a = self._pipe("N0100", ladle="L1")
        b = self._pipe("N0101", ladle="L2")
        self._heat("L1", 1, magnesium=0.052, carbon=3.61)
        self._heat("L2", 2, magnesium=0.048, carbon=3.55)
        db.session.add(MechanicalTest(
            test_date=date(2026, 9, 1), diameter=800, ladle_id="L1",
            tensile_strength=51.6, elongation=15.84, hardness=151,
            status="ACTIVE"))
        db.session.commit()

        standard = mtc_heats([a, b], "standard")
        self.assertEqual(["L1", "L2"], [h["heat_no"] for h in standard])
        # Standard mode prints the same accepted band on every row.
        self.assertEqual("0.030: 0.070", standard[0]["chemistry"]["Mg"])
        self.assertEqual("0.030: 0.070", standard[1]["chemistry"]["Mg"])

        actual = mtc_heats([a, b], "actual")
        self.assertEqual("0.052", actual[0]["chemistry"]["Mg"])
        self.assertEqual("0.048", actual[1]["chemistry"]["Mg"])
        self.assertEqual("3.61", actual[0]["chemistry"]["C"])

    def test_mtc_mechanical_columns_follow_the_mode(self):
        p = self._pipe("N0110", ladle="L3")
        self._heat("L3", 3)
        db.session.add(MechanicalTest(
            test_date=date(2026, 9, 1), diameter=800, ladle_id="L3",
            tensile_strength=51.6, elongation=15.84, hardness=151,
            status="ACTIVE"))
        db.session.commit()

        actual = mtc_heats([p], "actual")[0]
        # Tensile is quoted in MPa on this form, not Kg/mm².
        self.assertEqual(round(51.6 * 9.8, 1), actual["tensile"])
        self.assertEqual(15.84, actual["elongation"])
        self.assertEqual(151, actual["hardness"])

        standard = mtc_heats([p], "standard")[0]
        self.assertEqual("Min. : 420 (MPa )", standard["tensile"])
        self.assertEqual("Min. :10%", standard["elongation"])
        self.assertEqual("Max. 230", standard["hardness"])

    def test_heats_are_listed_the_way_a_person_reads_them(self):
        pipes = []
        for n in (2, 10, 1):
            ladle = f"{n}/11/2/2026"
            pipes.append(self._pipe(f"N02{n:02d}", ladle=ladle))
            self._heat(ladle, n)

        self.assertEqual(
            ["1/11/2/2026", "2/11/2/2026", "10/11/2/2026"],
            [h["heat_no"] for h in mtc_heats(pipes, "standard")])

    def test_mtc_summary_averages_the_actual_analyses(self):
        a = self._pipe("N0120", ladle="L4")
        b = self._pipe("N0121", ladle="L5")
        self._heat("L4", 4, magnesium=0.040)
        self._heat("L5", 5, magnesium=0.060)

        self.assertEqual(
            "0.030: 0.070", mtc_chemical_summary([a, b], "standard")["Mg"])
        self.assertEqual(
            "0.050", mtc_chemical_summary([a, b], "actual")["Mg"])

    def test_batch_numbers_come_from_the_annealing_stage(self):
        p = self._pipe("N0130", ladle="L6")
        q = self._pipe("N0131", ladle="L6")
        db.session.add(ChemicalAnalysis(
            test_date=date(2026, 9, 1), ladle_no=6, ladle_id="L6",
            batch_no="211022026"))
        db.session.add_all([
            PipeStage(pipe_id=p.id, stage_name="Annealing",
                      bundle_number="ANN-20260901-1"),
            PipeStage(pipe_id=q.id, stage_name="Annealing",
                      bundle_number="ANN-20260901-1"),
        ])
        db.session.commit()
        # Two pipes annealed together share a batch: listed once, not twice.
        self.assertEqual(["ANN-20260901-1"], batch_numbers([p, q]))

    def test_mtc_prints_both_modes(self):
        p = self._pipe("N0140", ladle="L8")
        self._heat("L8", 8, magnesium=0.055)
        db.session.add(MechanicalTest(
            test_date=date(2026, 9, 1), diameter=800, ladle_id="L8",
            tensile_strength=51.6, elongation=15.84, hardness=151,
            status="ACTIVE"))
        db.session.commit()
        self._login()

        resp = self.client.post(
            f"/certificates/new?type=mtc&order_id={self.order.id}",
            data={
                "pipe_ids": [str(p.id)],
                "customer_order_no": "Deraya/2026/3",
                "project_name": "USAID Non-Revenue Water",
                "values_mode": "actual",
            },
            follow_redirects=True)
        self.assertEqual(200, resp.status_code)

        cert = Certificate.query.one()
        self.assertEqual("MTC-2026-001", cert.certificate_no)
        self.assertTrue(cert.shows_actuals)

        body = resp.get_data(as_text=True)
        self.assertIn("شهادة فحص المواد", body)
        self.assertIn("Deraya/2026/3", body)
        self.assertIn("L8", body)
        self.assertIn("0.055", body)
        self.assertIn("EN 10204 Type 3.1", body)
        self.assertIn("505.7", body)  # 51.6 Kg/mm² in MPa

        # Switching to standard mode swaps the chemistry for the limits.
        self.client.post(
            f"/certificates/{cert.id}/edit",
            data={"pipe_ids": [str(p.id)], "values_mode": "standard"})
        body = self.client.get(
            f"/certificates/{cert.id}/print?noauto=1").get_data(as_text=True)
        self.assertIn("0.030: 0.070", body)
        self.assertNotIn("0.055", body)
        # Since 2026-09-05 the mechanical columns follow the toggle too: the
        # standard sheet prints the limits, not the ladle's own figure.
        self.assertNotIn("505.7", body)
        self.assertIn("Min. : 420 (MPa )", body)

    def test_dn_range_collapses_to_one_value_or_a_span(self):
        a = self._pipe("N0080")
        self.assertEqual("800", dn_range_label([a], self.order))

        b = self._pipe("N0081")
        b.diameter = 300
        db.session.commit()
        self.assertEqual("300-800", dn_range_label([a, b], self.order))


if __name__ == "__main__":
    unittest.main()
