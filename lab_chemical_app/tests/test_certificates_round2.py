"""Certificates, round 2 — the 2026-09-05 walkthrough.

The client opened the three forms on prod and asked for:

* C1  pick the customer, then the order the certificate is issued under, then
      add pipes from other orders (same customer or not);
* C3  the metres figure is computed but must be confirmable / correctable;
* C4  the work test names the standard the product is actually built to;
* C5  the work test's sample results are the average over the chosen ladles;
* C6  MTC mechanical columns follow the Standard/Actual toggle, actual being
      the per-ladle mean of every active test;
* C7  the MTC's Batch# is the Annealing batch (reversed on 2026-09-10: the
      walkthrough had pointed at the ladle's chemical-sheet number, the client
      then confirmed the batch is the one recorded at Annealing);
* C8  an MTC never mixes DN or class.
"""

import unittest
from datetime import date

from app import create_app, db
from app.models.certificate import Certificate
from app.models.chemical import ChemicalAnalysis
from app.models.mechanical import MechanicalTest
from app.models.permission import seed_default_permissions
from app.models.pipe import Pipe, PipeStage
from app.models.product import Customer
from app.models.production_order import ProductionOrder
from app.models.user import User
from app.services.certificate_service import (
    batch_numbers, group_pipes, mechanical_sample, mtc_heats, pipe_lengths,
    work_test_standards,
)


class CertificateRound2TestCase(unittest.TestCase):
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

        self.cairo = Customer(code="CAI", name_en="Cairo Water")
        self.alex = Customer(code="ALX", name_en="Alexandria Utilities")
        db.session.add_all([self.cairo, self.alex])
        db.session.commit()

        self.order1 = self._order("PO-1", self.cairo)
        self.order2 = self._order("PO-2", self.alex)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    # ---- helpers -----------------------------------------------------------

    def _order(self, number, customer, dn=800, pipe_class="K9", profile=None):
        o = ProductionOrder(
            order_number=number, customer_id=customer.id,
            customer_name=customer.name_en, target_quantity=10,
            diameter=dn, pipe_class=pipe_class,
            product_description="Ductile Iron Pipes", product_length=6.0,
            order_date=date(2026, 9, 1), application_profile=profile,
        )
        db.session.add(o)
        db.session.commit()
        return o

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_id)
            sess["_fresh"] = True

    def _pipe(self, order, no_code, ladle="L1", dn=None, pipe_class=None,
              length=None):
        p = Pipe(
            production_date=date(2026, 9, 1), ladle_id=ladle,
            pipe_code=no_code, no_code=no_code, arrange_pipe=1,
            diameter=dn or order.diameter, pipe_class=pipe_class or order.pipe_class,
            production_order_id=order.id, final_decision_value="ACCEPT",
        )
        db.session.add(p)
        db.session.commit()
        if length is not None:
            db.session.add(PipeStage(pipe_id=p.id, stage_name="Finish",
                                     decision="Accept", measurement_value=length))
            db.session.commit()
        return p

    def _heat(self, ladle, ladle_no, batch_no=None):
        db.session.add(ChemicalAnalysis(
            test_date=date(2026, 9, 1), ladle_no=ladle_no, ladle_id=ladle,
            batch_no=batch_no, magnesium=0.05, manganese=0.3, sulfur=0.01,
            chromium=0.03, copper=0.05, silicon=2.3, carbon=3.6))
        db.session.commit()

    def _test(self, ladle, tensile, elongation, hardness=None, pipe=None,
              when=date(2026, 9, 1)):
        db.session.add(MechanicalTest(
            test_date=when, diameter=800, ladle_id=ladle,
            pipe_id=pipe.id if pipe else None,
            tensile_strength=tensile, elongation=elongation, hardness=hardness,
            status="ACTIVE"))
        db.session.commit()

    def _post(self, cert_type, orders, data, primary=None, follow=True):
        query = "&".join(f"order_ids={o.id}" for o in orders)
        if primary is not None:
            query += f"&primary_order_id={primary.id}"
        return self.client.post(
            f"/certificates/new?type={cert_type}&{query}",
            data=data, follow_redirects=follow)

    # ---- C1: explicit primary order ---------------------------------------

    def test_primary_order_is_the_one_chosen_not_the_first_ticked(self):
        a = self._pipe(self.order1, "A1")
        b = self._pipe(self.order2, "B1")
        self._login()
        resp = self._post("warranty", [self.order1, self.order2],
                          {"pipe_ids": [str(a.id), str(b.id)], "warranty_years": "3"},
                          primary=self.order2)
        self.assertEqual(200, resp.status_code)

        cert = Certificate.query.one()
        self.assertEqual(self.order2.id, cert.production_order_id)
        self.assertEqual({self.order1.id, self.order2.id}, {o.id for o in cert.orders})
        # The certificate is issued in the primary order's customer's name.
        self.assertIn("Alexandria Utilities", resp.get_data(as_text=True))

    def test_primary_order_with_no_ticked_pipe_falls_back_and_warns(self):
        a = self._pipe(self.order1, "A2")
        self._login()
        resp = self._post("warranty", [self.order1, self.order2],
                          {"pipe_ids": [str(a.id)], "warranty_years": "3"},
                          primary=self.order2, follow=False)
        self.assertEqual(302, resp.status_code)
        cert = Certificate.query.one()
        self.assertEqual(self.order1.id, cert.production_order_id)
        body = self.client.get("/certificates/").get_data(as_text=True)
        self.assertIn("no ticked pipe", body.lower())

    def test_customer_filter_only_narrows_the_picker_not_what_can_be_posted(self):
        a = self._pipe(self.order1, "A3")
        b = self._pipe(self.order2, "B3")
        self._login()
        # The filter is a UI convenience on the order list; pipes from a
        # loaded order of another customer are still accepted.
        resp = self.client.post(
            f"/certificates/new?type=warranty&customer_id={self.cairo.id}"
            f"&order_ids={self.order1.id}&order_ids={self.order2.id}",
            data={"pipe_ids": [str(a.id), str(b.id)], "warranty_years": "3"},
            follow_redirects=True)
        self.assertEqual(200, resp.status_code)
        self.assertEqual(2, Certificate.query.one().quantity)

    def test_form_offers_customers_and_a_primary_order_select(self):
        self._login()
        body = self.client.get("/certificates/new?type=warranty").get_data(as_text=True)
        self.assertIn('name="primary_order_id"', body)
        self.assertIn('id="customerFilter"', body)
        self.assertIn("Cairo Water", body)
        self.assertIn("Alexandria Utilities", body)

    # ---- C2/C3: per-pipe lengths and the confirmed total -------------------

    def test_pipe_lengths_reads_finish_then_falls_back_to_the_order(self):
        a = self._pipe(self.order1, "A4", length=5.85)
        b = self._pipe(self.order1, "B4")
        self.assertEqual({a.id: 5.85, b.id: 6.0}, pipe_lengths([a, b]))

    def test_picker_shows_each_pipe_s_length(self):
        self._pipe(self.order1, "A5", length=5.85)
        self._login()
        body = self.client.get(
            f"/certificates/new?type=warranty&order_ids={self.order1.id}"
        ).get_data(as_text=True)
        # The shared selector owns the checkboxes; certificate-only length
        # metadata is kept in its companion map for the live total.
        self.assertRegex(body, r'var lengths = \{[^}]*5\.85')
        self.assertIn('name="total_length_m"', body)

    def test_blank_total_prints_the_computed_sum(self):
        a = self._pipe(self.order1, "A6", length=5.9)
        b = self._pipe(self.order1, "B6", length=6.1)
        self._login()
        resp = self._post("warranty", [self.order1],
                          {"pipe_ids": [str(a.id), str(b.id)],
                           "warranty_years": "3", "total_length_m": ""})
        self.assertIn("12.0", resp.get_data(as_text=True))
        self.assertIsNone(Certificate.query.one().total_length_m)

    def test_confirmed_total_prints_on_all_three_forms(self):
        for cert_type in ("warranty", "work_test", "mtc"):
            # ORM delete so the pipe links go with it — a bulk delete leaves
            # them behind on SQLite and the next id collides in the link table.
            for old in Certificate.query.all():
                db.session.delete(old)
            db.session.commit()
            a = self._pipe(self.order1, f"A7{cert_type}", length=6.0, ladle="L7")
            self._login()
            resp = self._post(cert_type, [self.order1],
                              {"pipe_ids": [str(a.id)], "warranty_years": "3",
                               "total_length_m": "5.75"})
            self.assertEqual(200, resp.status_code, cert_type)
            body = resp.get_data(as_text=True)
            self.assertIn("5.75", body, cert_type)
            self.assertEqual(5.75, Certificate.query.one().total_length_m)

    def test_material_table_header_says_total(self):
        a = self._pipe(self.order1, "A8", length=6.0)
        self._login()
        resp = self._post("warranty", [self.order1],
                          {"pipe_ids": [str(a.id)], "warranty_years": "3"})
        self.assertIn("Total Length (m)", resp.get_data(as_text=True))

    # ---- C4: the work test names the order's standard ----------------------

    def test_work_test_standard_follows_the_order_s_application(self):
        sewage = self._order("PO-S", self.cairo, profile={
            "standards": {"iso_8179": True, "en_598": True}})
        self.assertEqual("ISO 8179 & EN 598", work_test_standards(sewage))

    def test_work_test_standard_does_not_expand_one_selection_into_a_pair(self):
        sewage = self._order("PO-ONE", self.cairo, profile={
            "standards": {"iso_8179": True, "en_598": False}})
        self.assertEqual("ISO 8179", work_test_standards(sewage))

    def test_work_test_standard_falls_back_for_an_untagged_order(self):
        self.assertEqual("ISO 2531 & EN 545", work_test_standards(self.order1))

    def test_work_test_print_names_the_sewage_standard(self):
        sewage = self._order("PO-S2", self.cairo, profile={
            "standards": {"iso_8179": True, "en_598": True}})
        a = self._pipe(sewage, "S1", length=6.0)
        self._login()
        resp = self._post("work_test", [sewage], {"pipe_ids": [str(a.id)]})
        body = resp.get_data(as_text=True)
        self.assertIn("ISO 8179 &amp; EN 598", body)
        self.assertNotIn("ISO2531 &amp; EN545", body)

    # ---- C5: the sample is an average ------------------------------------

    def test_mechanical_sample_is_the_average_of_the_chosen_ladles(self):
        a = self._pipe(self.order1, "A9", ladle="L1")
        b = self._pipe(self.order1, "B9", ladle="L2")
        self._test("L1", 49.4, 12.6)
        self._test("L1", 44.2, 10.0)   # L1 averages to 46.8 / 11.3
        self._test("L2", 50.0, 14.1)
        self._test("L9", 99.0, 99.0)  # another ladle entirely — ignored

        sample = mechanical_sample([a, b])
        # Each heat is averaged first; a twice-tested ladle weighs the same as
        # a once-tested one.
        self.assertEqual(48.4, sample["tensile"])
        self.assertEqual(12.7, sample["elongation"])
        self.assertEqual(3, sample["tests"])
        self.assertEqual(2, sample["heats"])

    def test_mtc_guard_uses_the_order_s_dn_when_the_pipe_left_it_blank(self):
        a = self._pipe(self.order1, "A21")
        b = self._pipe(self.order1, "B21")
        b.diameter = None
        db.session.commit()
        self._login()
        self._post("mtc", [self.order1], {"pipe_ids": [str(a.id), str(b.id)]})
        self.assertEqual(1, Certificate.query.count())

    def test_multi_row_material_table_closes_with_a_total_line(self):
        a = self._pipe(self.order1, "A22", dn=800, length=6.0)
        b = self._pipe(self.order1, "B22", dn=600, length=5.5)
        self._login()
        resp = self._post("warranty", [self.order1],
                          {"pipe_ids": [str(a.id), str(b.id)], "warranty_years": "3"})
        body = resp.get_data(as_text=True)
        self.assertIn("<strong>Total</strong>", body)
        self.assertIn("<strong>11.5</strong>", body)

    def test_a_typed_total_survives_a_validation_bounce(self):
        a = self._pipe(self.order1, "A23", dn=800)
        b = self._pipe(self.order1, "B23", dn=600)
        self._login()
        resp = self._post("mtc", [self.order1],
                          {"pipe_ids": [str(a.id), str(b.id)], "total_length_m": "9.5"})
        self.assertEqual(0, Certificate.query.count())
        self.assertIn('value="9.5"', resp.get_data(as_text=True))

    def test_mechanical_sample_counts_a_pipe_test_without_a_ladle(self):
        a = self._pipe(self.order1, "A10", ladle=None)
        self._test(None, 47.0, 11.0, pipe=a)
        sample = mechanical_sample([a])
        self.assertEqual(47.0, sample["tensile"])

    def test_work_test_print_says_it_is_an_average(self):
        a = self._pipe(self.order1, "A11", ladle="L1", length=6.0)
        self._test("L1", 49.4, 12.6)
        self._login()
        resp = self._post("work_test", [self.order1], {"pipe_ids": [str(a.id)]})
        body = resp.get_data(as_text=True)
        self.assertIn("Average of 1 heat", body)
        self.assertNotIn("Random sample", body)

    # ---- C6: MTC mechanics follow the toggle -------------------------------

    def test_mtc_standard_mode_prints_the_mechanical_limits(self):
        a = self._pipe(self.order1, "A12", ladle="L3")
        self._heat("L3", 3)
        self._test("L3", 51.6, 15.84, hardness=151)
        row = mtc_heats([a], "standard")[0]
        self.assertEqual("Min. : 420 (MPa )", row["tensile"])
        self.assertEqual("Min. :10%", row["elongation"])
        self.assertEqual("Max. 230", row["hardness"])

    def test_mtc_actual_mode_is_the_mean_of_every_active_test_on_the_ladle(self):
        a = self._pipe(self.order1, "A13", ladle="L4")
        self._heat("L4", 4)
        self._test("L4", 50.0, 14.0, hardness=150)
        self._test("L4", 52.0, 16.0, hardness=None)
        db.session.add(MechanicalTest(
            test_date=date(2026, 9, 1), diameter=800, ladle_id="L4",
            tensile_strength=10.0, elongation=1.0, status="SUPERSEDED"))
        db.session.commit()

        row = mtc_heats([a], "actual")[0]
        self.assertEqual(round(51.0 * 9.8, 1), row["tensile"])
        self.assertEqual(15.0, row["elongation"])
        # One missing hardness does not drag the mean to half.
        self.assertEqual(150, row["hardness"])

    def test_mtc_actual_mode_leaves_untested_ladles_blank(self):
        a = self._pipe(self.order1, "A14", ladle="L5")
        self._heat("L5", 5)
        row = mtc_heats([a], "actual")[0]
        self.assertIsNone(row["tensile"])
        self.assertIsNone(row["hardness"])

    # ---- C7: the batch is the Annealing batch -----------------------------

    def test_batch_numbers_come_from_the_annealing_stage(self):
        a = self._pipe(self.order1, "A15", ladle="L6")
        b = self._pipe(self.order1, "B15", ladle="L6")
        self._heat("L6", 6, batch_no="211022026")
        db.session.add(PipeStage(pipe_id=a.id, stage_name="Annealing",
                                 bundle_number="ANN-20260901-1"))
        db.session.commit()
        self.assertEqual(["ANN-20260901-1"], batch_numbers([a, b]))

    def test_batch_numbers_ignore_other_stages_bundle_numbers(self):
        a = self._pipe(self.order1, "A15b", ladle="L6b")
        db.session.add_all([
            PipeStage(pipe_id=a.id, stage_name="Annealing",
                      bundle_number="ANN-20260901-2"),
            PipeStage(pipe_id=a.id, stage_name="Delivery",
                      bundle_number="DN-777"),
        ])
        db.session.commit()
        self.assertEqual(["ANN-20260901-2"], batch_numbers([a]))

    def test_mtc_heat_row_carries_its_annealing_batch(self):
        a = self._pipe(self.order1, "A16", ladle="L8")
        self._heat("L8", 8, batch_no="1/312022026")
        db.session.add(PipeStage(pipe_id=a.id, stage_name="Annealing",
                                 bundle_number="ANN-20260902-1"))
        db.session.commit()
        row = mtc_heats([a], "standard")[0]
        self.assertEqual("L8", row["heat_no"])
        self.assertEqual("ANN-20260902-1", row["batch_no"])

    def test_mtc_heat_row_lists_every_batch_the_heat_went_through(self):
        a = self._pipe(self.order1, "A16a", ladle="L8b")
        b = self._pipe(self.order1, "A16b", ladle="L8b")
        db.session.add_all([
            PipeStage(pipe_id=a.id, stage_name="Annealing",
                      bundle_number="ANN-20260902-1"),
            PipeStage(pipe_id=b.id, stage_name="Annealing",
                      bundle_number="ANN-20260902-2"),
        ])
        db.session.commit()
        row = mtc_heats([a, b], "standard")[0]
        self.assertEqual("ANN-20260902-1 / ANN-20260902-2", row["batch_no"])

    def test_mtc_print_shows_the_batch_beside_the_heat(self):
        a = self._pipe(self.order1, "A17", ladle="L9", length=6.0)
        self._heat("L9", 9, batch_no="211022026")
        db.session.add(PipeStage(pipe_id=a.id, stage_name="Annealing",
                                 bundle_number="ANN-20260903-1"))
        db.session.commit()
        self._login()
        resp = self._post("mtc", [self.order1], {"pipe_ids": [str(a.id)]})
        body = resp.get_data(as_text=True)
        self.assertIn("ANN-20260903-1", body)
        self.assertIn("Batch No", body)

    # ---- C8: an MTC never mixes DN or class --------------------------------

    def test_mtc_refuses_pipes_of_two_diameters(self):
        a = self._pipe(self.order1, "A18", dn=800)
        b = self._pipe(self.order1, "B18", dn=600)
        self._login()
        resp = self._post("mtc", [self.order1], {"pipe_ids": [str(a.id), str(b.id)]})
        self.assertEqual(200, resp.status_code)
        self.assertEqual(0, Certificate.query.count())
        body = resp.get_data(as_text=True)
        self.assertIn("DN600", body)
        self.assertIn("DN800", body)
        self.assertIn("one DN and one class", body)

    def test_mtc_refuses_pipes_of_two_classes(self):
        a = self._pipe(self.order1, "A19", pipe_class="K9")
        b = self._pipe(self.order1, "B19", pipe_class="C40")
        self._login()
        self._post("mtc", [self.order1], {"pipe_ids": [str(a.id), str(b.id)]})
        self.assertEqual(0, Certificate.query.count())

    def test_warranty_still_accepts_mixed_diameters(self):
        a = self._pipe(self.order1, "A20", dn=800)
        b = self._pipe(self.order1, "B20", dn=600)
        self._login()
        self._post("warranty", [self.order1],
                   {"pipe_ids": [str(a.id), str(b.id)], "warranty_years": "3"})
        self.assertEqual(1, Certificate.query.count())
        self.assertEqual(2, len(group_pipes(Certificate.query.one().pipes)))


if __name__ == "__main__":
    unittest.main()
