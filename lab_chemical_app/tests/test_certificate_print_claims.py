"""Accuracy and print-layout regressions for the three customer certificates."""

import unittest
from datetime import date

from lxml import html

from app import create_app, db
from app.models.certificate import Certificate
from app.models.chemical import ChemicalAnalysis
from app.models.permission import seed_default_permissions
from app.models.pipe import Pipe, PipeStage
from app.models.product import Customer, Product, ProductParameter
from app.models.production_order import ProductionOrder
from app.models.user import User
from app.services import certificate_service


class CertificatePrintClaimsTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()
        self.client = self.app.test_client()

        user = User(username="admin", full_name="Admin", role="admin", is_active=True)
        user.set_password("x")
        self.customer = Customer(code="C1", name_en="Cairo Water")
        db.session.add_all([user, self.customer])
        db.session.commit()
        self.user_id = user.id

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _parameter(self, kind, code, name):
        row = ProductParameter(
            param_type=kind, code=code, name_en=name, name_ar=name,
            is_active=True,
        )
        db.session.add(row)
        db.session.flush()
        return row

    def _order(self, number, standards, zinc=None, finish=None):
        product = Product(
            product_code=f"PRODUCT-{number}",
            description_en="Ductile Iron Pipes",
            is_active=True,
            zinc_type_param=(self._parameter("ZINC_TYPE", f"Z-{number}", zinc)
                             if zinc else None),
            external_finish_param=(self._parameter(
                "EXTERNAL_FINISH", f"E-{number}", finish) if finish else None),
            application_profile={"standards": standards},
        )
        db.session.add(product)
        db.session.flush()
        order = ProductionOrder(
            order_number=number, customer_id=self.customer.id,
            customer_name=self.customer.name_en, target_quantity=5,
            diameter=600, pipe_class="K9", product_id=product.id,
            product_description="Ductile Iron Pipes", product_length=6.0,
            order_date=date(2026, 9, 16),
            application_profile={"standards": standards},
        )
        db.session.add(order)
        db.session.commit()
        return order

    def _pipe(self, order, number, heat="H-1", batch="ANN-20260916-1"):
        pipe = Pipe(
            production_date=date(2026, 9, 16), ladle_id=heat,
            pipe_code=number, no_code=number, arrange_pipe=1,
            diameter=order.diameter, pipe_class=order.pipe_class,
            production_order_id=order.id, final_decision_value="ACCEPT",
        )
        db.session.add(pipe)
        db.session.flush()
        db.session.add(PipeStage(
            pipe_id=pipe.id, stage_name="Annealing", bundle_number=batch,
        ))
        db.session.commit()
        return pipe

    def _certificate(self, cert_type, order, pipes, number, **values):
        cert = Certificate(
            certificate_no=number, cert_type=cert_type,
            production_order_id=order.id, issue_date=date(2026, 9, 16),
            warranty_start_date=date(2026, 9, 16), warranty_years=3,
            customer_order_no="JOB-22", project_name="East Line",
            notes=values.pop("notes", "General certificate note"),
            **values,
        )
        cert.pipes = pipes
        cert.orders = [order]
        db.session.add(cert)
        db.session.commit()
        return cert

    def _login(self):
        with self.client.session_transaction() as session:
            session["_user_id"] = str(self.user_id)
            session["_fresh"] = True

    def _print(self, cert):
        self._login()
        return self.client.get(
            f"/certificates/{cert.id}/print?noauto=1"
        ).get_data(as_text=True)

    def test_standard_is_exact_and_never_expanded_to_a_pair(self):
        order = self._order("PO-STANDARDS", {
            "iso_8179": True,
            "en_598": False,
            "iso_2531": False,
            "en_545": False,
            "awwa": False,
        })

        self.assertEqual(
            "ISO 8179",
            certificate_service.work_test_standards(order),
        )
        self.assertIn(
            "as per ISO 8179",
            certificate_service.item_description(order),
        )
        self.assertNotIn(
            "EN 598", certificate_service.item_description(order),
        )

    def test_zinc_bitumen_claim_names_layer_and_iso_8179(self):
        order = self._order(
            "PO-ZINC", {"en_598": True}, zinc="Zinc 200 g/m²", finish="Bitumen",
        )

        text = certificate_service.external_coating_statement(order)

        self.assertIn("Zinc 200 g/m²", text)
        self.assertIn("Bitumen", text)
        self.assertIn("ISO 8179", text)

    def test_no_zinc_epoxy_claim_omits_zinc_layer_and_iso_8179(self):
        order = self._order(
            "PO-EPOXY", {"en_545": True}, zinc=None, finish="Epoxy",
        )

        text = certificate_service.external_coating_statement(order)

        self.assertIn("Epoxy", text)
        self.assertNotIn("zinc", text.lower())
        self.assertNotIn("ISO 8179", text)

    def test_work_test_print_uses_zinc_bitumen_product_claim(self):
        order = self._order(
            "PO-ZINC-PRINT", {"en_598": True},
            zinc="Zinc 200 g/m²", finish="Bitumen",
        )
        pipe = self._pipe(order, "PIPE-ZINC")
        cert = self._certificate(
            Certificate.WORK_TEST, order, [pipe], "TC-2026-811",
        )

        body = self._print(cert)

        self.assertIn("zinc layer (Zinc 200 g/m²)", body)
        self.assertIn("Bitumen finishing layer", body)
        self.assertIn("ISO 8179", body)

    def test_work_test_print_uses_epoxy_directly_when_zinc_is_absent(self):
        order = self._order(
            "PO-EPOXY-PRINT", {"en_545": True}, zinc=None, finish="Epoxy",
        )
        pipe = self._pipe(order, "PIPE-EPOXY")
        cert = self._certificate(
            Certificate.WORK_TEST, order, [pipe], "TC-2026-812",
        )

        body = self._print(cert)

        self.assertIn("externally coated directly with Epoxy", body)
        self.assertNotIn("zinc layer", body.lower())
        self.assertNotIn("ISO 8179", body)

    def test_mtc_heat_rows_list_only_selected_pipe_numbers_for_the_heat(self):
        order = self._order("PO-PIPES", {"en_545": True})
        chosen_a = self._pipe(order, "PIPE-101", heat="H-7")
        chosen_b = self._pipe(order, "PIPE-102", heat="H-7")
        self._pipe(order, "PIPE-NOT-SELECTED", heat="H-7")
        db.session.add(ChemicalAnalysis(
            test_date=date(2026, 9, 16), ladle_no=7, ladle_id="H-7",
            batch_no="CHEMICAL-BATCH", magnesium=0.05,
        ))
        db.session.commit()

        row = certificate_service.mtc_heats(
            [chosen_a, chosen_b], "standard"
        )[0]

        self.assertEqual(["PIPE-101", "PIPE-102"], row["pipe_numbers"])
        self.assertEqual("ANN-20260916-1", row["batch_no"])
        self.assertNotIn("CHEMICAL-BATCH", row["batch_no"])

    def test_warranty_and_work_test_have_frame_and_exact_signatories(self):
        order = self._order("PO-FRAME", {"iso_8179": True})
        pipe = self._pipe(order, "PIPE-FRAME")

        for cert_type, number in (
            (Certificate.WARRANTY, "WC-2026-801"),
            (Certificate.WORK_TEST, "TC-2026-801"),
        ):
            with self.subTest(cert_type=cert_type):
                body = self._print(self._certificate(
                    cert_type, order, [pipe], number,
                ))
                self.assertIn('class="certificate-frame"', body)
                self.assertIn(".certificate-frame {", body)
                self.assertIn("border: 1px solid #000", body)
                self.assertIn(
                    "Quality Assurance Manager / مدير ضمان الجودة", body,
                )
                self.assertIn("Quality Director / مدير الجودة", body)
                self.assertNotIn("Quality Assurance Section-Head", body)
                self.assertNotIn("EN 598", body)

    def test_mtc_print_uses_application_standard_notes_and_optional_pipe_column(self):
        order = self._order("PO-MTC", {"en_598": True})
        pipe = self._pipe(order, "PIPE-MTC", heat="H-8")
        db.session.add(ChemicalAnalysis(
            test_date=date(2026, 9, 16), ladle_no=8, ladle_id="H-8",
            batch_no="WRONG-CHEMICAL-BATCH", magnesium=0.05,
        ))
        db.session.commit()
        cert = self._certificate(
            Certificate.MTC, order, [pipe], "MTC-2026-801",
            notes="Handle with care",
        )
        cert.show_pipe_numbers = True

        body = self._print(cert)

        # Opening, mechanical Standard cell, and closing all repeat the exact
        # product-conformity standard (the description is the fourth use).
        self.assertGreaterEqual(body.count("EN 598"), 4)
        self.assertNotIn("ISO 2531 / EN 545", body)
        self.assertIn("ASTM A536", body)
        self.assertIn("Notes / ملاحظات", body)
        self.assertNotIn("Batch#", body)
        self.assertEqual(1, body.count("Handle with care"))
        self.assertIn("Pipe No.", body)
        self.assertIn("PIPE-MTC", body)
        self.assertIn("ANN-20260916-1", body)
        self.assertNotIn("WRONG-CHEMICAL-BATCH", body)
        self.assertIn(
            "Quality Assurance Manager / مدير ضمان الجودة", body,
        )
        self.assertIn("Quality Director / مدير الجودة", body)
        self.assertEqual(3, body.count('class="ref-value"'))

    def test_mtc_pipe_column_is_absent_when_option_is_off(self):
        order = self._order("PO-MTC-OFF", {"en_545": True})
        pipe = self._pipe(order, "PIPE-HIDDEN", heat="H-9")
        cert = self._certificate(
            Certificate.MTC, order, [pipe], "MTC-2026-802",
        )
        cert.show_pipe_numbers = False

        body = self._print(cert)

        self.assertNotIn("Pipe No.", body)
        self.assertNotIn("PIPE-HIDDEN", body)

    def test_mtc_heat_table_has_one_valid_header_and_body(self):
        order = self._order("PO-MTC-MARKUP", {"en_545": True})
        pipe = self._pipe(order, "PIPE-MARKUP", heat="H-10")
        cert = self._certificate(
            Certificate.MTC, order, [pipe], "MTC-2026-803",
        )

        document = html.fromstring(self._print(cert))
        heat_tables = document.xpath('//table[@data-mtc-heat-results]')

        self.assertEqual(1, len(heat_tables))
        heat_table = heat_tables[0]
        self.assertEqual(1, len(heat_table.xpath('./thead')))
        self.assertEqual(1, len(heat_table.xpath('./tbody')))
        self.assertEqual(
            ['thead', 'tbody'],
            [child.tag for child in heat_table if child.tag in ('thead', 'tbody')],
        )

    def test_print_preview_shows_legacy_application_fallback_warning(self):
        order = self._order("PO-LEGACY", {})
        order.application_profile = None
        order.product.application_profile = None
        pipe = self._pipe(order, "PIPE-LEGACY")
        db.session.commit()

        for cert_type, number in (
            (Certificate.WARRANTY, "WC-2026-821"),
            (Certificate.WORK_TEST, "TC-2026-821"),
            (Certificate.MTC, "MTC-2026-821"),
        ):
            with self.subTest(cert_type=cert_type):
                body = self._print(self._certificate(
                    cert_type, order, [pipe], number,
                ))
                self.assertIn('class="print-warning"', body)
                self.assertIn(
                    "Legacy Application fallback used for order(s): PO-LEGACY",
                    body,
                )


if __name__ == "__main__":
    unittest.main()
