"""Issuance integrity: reissues, duplicates, and specification compatibility."""

import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from app import create_app, db
from app.models.certificate import Certificate
from app.models.permission import (
    Permission,
    RolePermission,
    ROLE_DEFAULTS,
    seed_default_permissions,
)
from app.models.pipe import Pipe
from app.models.product import Product, ProductParameter
from app.models.production_order import ProductionOrder
from app.models.user import User


class CertificateIssuanceModelTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()

        self.order = ProductionOrder(
            order_number="PO-CERT-001",
            customer_name="Cairo Water",
            target_quantity=1,
            diameter=800,
            pipe_class="K9",
            product_description="Ductile Iron Pipes",
            product_length=6.0,
            order_date=date(2026, 9, 16),
        )
        db.session.add(self.order)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_certificate_stores_pipe_number_choice_and_reissue_audit_link(self):
        original = Certificate(
            certificate_no="MTC-2026-001",
            cert_type=Certificate.MTC,
            production_order_id=self.order.id,
            issue_date=date(2026, 9, 16),
        )
        db.session.add(original)
        db.session.flush()

        reissue = Certificate(
            certificate_no="MTC-2026-002",
            cert_type=Certificate.MTC,
            production_order_id=self.order.id,
            issue_date=date(2026, 9, 16),
            show_pipe_numbers=True,
            reissue_of=original,
            reissue_reason="Customer copy was damaged",
        )
        db.session.add(reissue)
        db.session.commit()

        self.assertTrue(reissue.show_pipe_numbers)
        self.assertEqual(original.id, reissue.reissue_of_id)
        self.assertEqual(original, reissue.reissue_of)
        self.assertIn(reissue, original.reissues)

    def test_reissue_permission_is_quality_scoped_with_safe_defaults(self):
        permission = Permission.query.filter_by(
            module="certificates", screen="reissue"
        ).one()

        self.assertEqual("execute", permission.action)
        self.assertIn("reissue", ROLE_DEFAULTS["supervisor"]["certificates"])
        self.assertNotIn("certificates", ROLE_DEFAULTS["warehouse"])
        self.assertNotIn("warehouse", ROLE_DEFAULTS["supervisor"])


class CertificateSpecificationTestCase(unittest.TestCase):
    @staticmethod
    def _order(number, standards=None, zinc=None,
               internal="Cement lining", external="Bitumen"):
        profile = None if standards is None else {
            "standards": standards,
        }

        def parameter(name):
            return None if name is None else SimpleNamespace(
                code=name.upper().replace(" ", "_"), name_en=name)

        product = SimpleNamespace(
            zinc_type_param=parameter(zinc),
            internal_finish_param=parameter(internal),
            external_finish_param=parameter(external),
        )
        return SimpleNamespace(
            id=number, order_number=f"PO-{number}",
            application_profile=profile, product=product,
        )

    def test_signature_keeps_only_checked_standards_in_configured_order(self):
        from app.services.certificate_issuance_service import specification_for

        order = self._order(
            1,
            standards={"en_545": True, "iso_8179": False,
                       "iso_2531": True, "awwa": False},
            zinc="Zinc 200 g/m2",
            internal="Cement Lining",
            external="Blue Epoxy",
        )

        spec = specification_for(order)

        self.assertEqual(("ISO 2531", "EN 545"), spec.application_standards)
        self.assertTrue(spec.zinc_present)
        self.assertEqual("Zinc 200 g/m2", spec.zinc_type)
        self.assertEqual("Cement Lining", spec.internal_finish)
        self.assertEqual("Blue Epoxy", spec.external_finish)
        self.assertFalse(spec.used_application_fallback)

    def test_compatibility_reports_orders_and_each_conflicting_field(self):
        from app.services.certificate_issuance_service import compatibility_conflicts

        first = self._order(
            1, {"iso_2531": True}, zinc="Zinc 200 g/m2",
            external="Bitumen")
        second = self._order(
            2, {"en_598": True}, zinc=None, external="Epoxy")

        conflicts = compatibility_conflicts([first, second])
        message = " ".join(conflicts)

        self.assertIn("PO-1", message)
        self.assertIn("PO-2", message)
        self.assertIn("Application standards", message)
        self.assertIn("zinc presence/type", message)
        self.assertIn("external finish", message)

    def test_legacy_order_uses_documented_fallback_and_is_flagged(self):
        from app.services.certificate_issuance_service import specification_for

        spec = specification_for(self._order(9, standards=None))

        self.assertEqual(("ISO 2531", "EN 545"), spec.application_standards)
        self.assertTrue(spec.used_application_fallback)

    def test_legacy_print_labels_use_the_same_documented_fallback(self):
        from app.services.certificate_service import application_standard_labels

        order = self._order(14, standards=None)
        order.standard_labels = lambda: ["EN 598"]

        self.assertEqual(
            ["ISO 2531", "EN 545"], application_standard_labels(order))

    def test_signature_uses_the_orders_stored_application_snapshot(self):
        from app.services.certificate_issuance_service import specification_for

        order = self._order(10, {"iso_2531": True})
        order.effective_application = lambda: {
            "standards": {"en_598": True, "iso_2531": False},
        }

        spec = specification_for(order)

        self.assertEqual(("ISO 2531",), spec.application_standards)

    def test_numeric_application_layers_do_not_replace_named_finish_parameters(self):
        """Layer tables store numbers; Product parameters own printed names."""
        from app.services.certificate_issuance_service import compatibility_conflicts

        first = self._order(12, {"iso_2531": True}, zinc="Zinc 200 g/m2")
        second = self._order(13, {"iso_2531": True}, zinc="Zinc 200 g/m2")
        first.effective_application = lambda: {
            "standards": {"iso_2531": True},
            "layers": {"coating": {"nominal": 70}},
        }
        second.effective_application = lambda: {
            "standards": {"iso_2531": True},
            "layers": {"coating": {"nominal": 100}},
        }

        self.assertEqual([], compatibility_conflicts([first, second]))

    def test_explicit_no_zinc_parameter_normalizes_to_absent(self):
        from app.services.certificate_issuance_service import specification_for

        spec = specification_for(self._order(
            11, {"iso_2531": True}, zinc="No Zinc"))

        self.assertFalse(spec.zinc_present)
        self.assertEqual("", spec.zinc_type)


class CertificateIssuanceRouteTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()
        self.client = self.app.test_client()

        self.user = User(
            username="quality", full_name="Quality Supervisor",
            role="supervisor", is_active=True,
        )
        self.user.set_password("x")
        db.session.add(self.user)
        db.session.commit()

        self.order1 = self._order("PO-ISSUE-001", {"iso_2531": True})
        self.order2 = self._order("PO-ISSUE-002", {"iso_2531": True})
        self.pipe1 = self._pipe(self.order1, "PIPE-001")
        self.pipe2 = self._pipe(self.order2, "PIPE-002")
        self._login()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self):
        with self.client.session_transaction() as session:
            session["_user_id"] = str(self.user.id)
            session["_fresh"] = True

    @staticmethod
    def _order(number, standards):
        order = ProductionOrder(
            order_number=number,
            customer_name="Cairo Water",
            target_quantity=2,
            diameter=800,
            pipe_class="K9",
            product_description="Ductile Iron Pipes",
            product_length=6.0,
            order_date=date(2026, 9, 16),
            application_profile=(
                None if standards is None else {"standards": standards}),
        )
        db.session.add(order)
        db.session.commit()
        return order

    @staticmethod
    def _pipe(order, code, decision="ACCEPT", reason=None):
        pipe = Pipe(
            production_date=date(2026, 9, 16), ladle_id="L1",
            pipe_code=code, no_code=code, arrange_pipe=1,
            diameter=800, pipe_class="K9",
            production_order_id=order.id,
            final_decision_value=decision,
            final_decision_reason=reason,
        )
        db.session.add(pipe)
        db.session.commit()
        return pipe

    def _post(self, cert_type, pipe_ids, orders=None, **extra):
        orders = orders or [self.order1]
        query = "&".join(f"order_ids={order.id}" for order in orders)
        data = {"pipe_ids": [str(value) for value in pipe_ids], **extra}
        return self.client.post(
            f"/certificates/new?type={cert_type}&{query}",
            data=data, follow_redirects=True,
        )

    def test_same_type_duplicate_is_blocked_but_other_type_is_allowed(self):
        self._post(Certificate.WARRANTY, [self.pipe1.id])

        duplicate = self._post(Certificate.WARRANTY, [self.pipe1.id])

        self.assertEqual(1, Certificate.query.count())
        body = duplicate.get_data(as_text=True)
        self.assertIn("WC-2026-001", body)
        self.assertIn(f"/certificates/{Certificate.query.one().id}/print", body)
        self.assertIn(f"/certificates/{Certificate.query.one().id}/edit", body)

        self._post(Certificate.WORK_TEST, [self.pipe1.id])
        self.assertEqual(2, Certificate.query.count())

    def test_issuance_locks_selected_pipe_rows_before_validation(self):
        query_type = type(Pipe.query)
        original = query_type.with_for_update

        with patch.object(
            query_type,
            "with_for_update",
            autospec=True,
            side_effect=lambda query, *args, **kwargs: original(
                query, *args, **kwargs),
        ) as lock_rows:
            self._post(Certificate.WARRANTY, [self.pipe1.id])

        lock_rows.assert_called_once()

    def test_show_pipe_numbers_survives_create_edit_and_reprint(self):
        self._post(
            Certificate.MTC, [self.pipe1.id], show_pipe_numbers="1")
        cert = Certificate.query.one()
        self.assertTrue(cert.show_pipe_numbers)

        self.client.post(
            f"/certificates/{cert.id}/edit",
            data={
                "order_ids": [str(self.order1.id)],
                "primary_order_id": str(self.order1.id),
                "pipe_ids": [str(self.pipe1.id)],
                "show_pipe_numbers": "1",
            },
            follow_redirects=True,
        )
        db.session.refresh(cert)
        self.assertTrue(cert.show_pipe_numbers)

        self.client.get(f"/certificates/{cert.id}/print?noauto=1")
        db.session.refresh(cert)
        self.assertTrue(cert.show_pipe_numbers)

    def test_edit_may_retain_its_own_pipe(self):
        self._post(Certificate.WARRANTY, [self.pipe1.id])
        cert = Certificate.query.one()

        response = self.client.post(
            f"/certificates/{cert.id}/edit",
            data={
                "order_ids": [str(self.order1.id)],
                "primary_order_id": str(self.order1.id),
                "pipe_ids": [str(self.pipe1.id)],
                "notes": "corrected",
            },
            follow_redirects=True,
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual(1, Certificate.query.count())
        self.assertEqual("corrected", Certificate.query.one().notes)

    def test_missing_pipe_rejects_whole_issue_before_number_allocation(self):
        missing_id = self.pipe1.id + 9999

        response = self._post(
            Certificate.WARRANTY, [self.pipe1.id, missing_id])

        self.assertEqual(0, Certificate.query.count())
        self.assertIn(str(missing_id), response.get_data(as_text=True))

        self._post(Certificate.WARRANTY, [self.pipe1.id])
        self.assertEqual("WC-2026-001", Certificate.query.one().certificate_no)

    def test_rejected_pipe_is_visible_with_reason_and_cannot_be_issued(self):
        rejected = self._pipe(
            self.order1, "PIPE-REJECT", decision="REJECT",
            reason="Hydrostatic test failed",
        )

        page = self.client.get(
            f"/certificates/new?type=warranty&order_ids={self.order1.id}")
        page_body = page.get_data(as_text=True)
        self.assertIn("PIPE-REJECT", page_body)
        self.assertIn("Hydrostatic test failed", page_body)

        response = self._post(Certificate.WARRANTY, [rejected.id])
        self.assertEqual(0, Certificate.query.count())
        self.assertIn("Hydrostatic test failed", response.get_data(as_text=True))

    def test_nonaccepted_status_change_is_visible_and_rejected_at_submit(self):
        self.pipe1.final_decision_value = "HOLD"
        self.pipe1.final_decision_reason = "Awaiting laboratory retest"
        db.session.commit()

        page = self.client.get(
            f"/certificates/new?type=warranty&order_ids={self.order1.id}")
        row = page.get_data(as_text=True).split(
            f'data-visible-pipe="{self.pipe1.id}"', 1)[1].split("</tr>", 1)[0]
        self.assertIn("disabled", row)
        self.assertIn("Awaiting laboratory retest", row)

        response = self._post(Certificate.WARRANTY, [self.pipe1.id])
        self.assertEqual(0, Certificate.query.count())
        self.assertIn("HOLD", response.get_data(as_text=True))

    def test_incompatible_orders_are_named_and_rejected_before_numbering(self):
        self.order2.application_profile = {"standards": {"en_598": True}}
        db.session.commit()

        response = self._post(
            Certificate.WARRANTY,
            [self.pipe1.id, self.pipe2.id],
            orders=[self.order1, self.order2],
        )

        body = response.get_data(as_text=True)
        self.assertEqual(0, Certificate.query.count())
        self.assertIn(self.order1.order_number, body)
        self.assertIn(self.order2.order_number, body)
        self.assertIn("Application standards", body)

        self._post(Certificate.WARRANTY, [self.pipe1.id])
        self.assertEqual("WC-2026-001", Certificate.query.one().certificate_no)

    def test_direct_scan_adds_compatible_pipe_order_to_certificate_coverage(self):
        # Only order1 was loaded; pipe2 represents a direct AJAX scan from a
        # different compatible order. The server derives coverage from the
        # reloaded pipes rather than trusting posted order IDs.
        response = self._post(
            Certificate.WARRANTY, [self.pipe1.id, self.pipe2.id],
            orders=[self.order1])

        self.assertEqual(200, response.status_code)
        cert = Certificate.query.one()
        self.assertEqual(
            {self.order1.id, self.order2.id}, {order.id for order in cert.orders})

    def test_manual_lookup_adds_pipe_and_its_order_to_same_form(self):
        response = self.client.get(
            "/certificates/new?type=warranty&pipe_lookup=PIPE-002")
        body = response.get_data(as_text=True)

        self.assertEqual(200, response.status_code)
        self.assertIn("PIPE-002", body)
        self.assertIn(f'name="order_ids" value="{self.order2.id}"', body)

    def test_form_uses_shared_pipe_selection_component(self):
        response = self.client.get(
            f"/certificates/new?type=warranty&order_ids={self.order1.id}")
        body = response.get_data(as_text=True)

        self.assertIn("data-pipe-selection", body)
        self.assertIn("/static/js/pipe_selection.js", body)
        self.assertNotIn('class="form-check-input pipe-check"', body)

    def test_legacy_application_fallback_warns_in_form_and_server_log(self):
        legacy = self._order("PO-LEGACY", None)
        self._pipe(legacy, "PIPE-LEGACY")

        with self.assertLogs(self.app.logger, level="WARNING") as logs:
            response = self.client.get(
                f"/certificates/new?type=warranty&order_ids={legacy.id}")

        body = response.get_data(as_text=True)
        self.assertIn("legacy Application fallback", body)
        self.assertTrue(any("PO-LEGACY" in line for line in logs.output))

    def test_authorized_reissue_stores_original_and_nonempty_reason(self):
        self._post(Certificate.WARRANTY, [self.pipe1.id])
        original = Certificate.query.one()

        response = self._post(
            Certificate.WARRANTY,
            [self.pipe1.id],
            reissue_of_id=str(original.id),
            reissue_reason="Signed original was damaged in transit",
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual(2, Certificate.query.count())
        reissue = Certificate.query.filter(
            Certificate.id != original.id).one()
        self.assertEqual(original.id, reissue.reissue_of_id)
        self.assertEqual(
            "Signed original was damaged in transit", reissue.reissue_reason)

    def test_authorized_reissue_mode_allows_original_pipe_in_shared_basket(self):
        self._post(Certificate.WARRANTY, [self.pipe1.id])
        original = Certificate.query.one()

        response = self.client.get(
            "/certificates/new",
            query_string={
                "type": Certificate.WARRANTY,
                "order_ids": self.order1.id,
                "reissue_of_id": original.id,
            },
        )
        body = response.get_data(as_text=True)
        row = body.split(f'data-visible-pipe="{self.pipe1.id}"', 1)[1].split(
            "</tr>", 1)[0]

        self.assertNotIn("disabled", row)
        self.assertIn(
            f'name="reissue_of_id" value="{original.id}"', body)
        self.assertIn(original.certificate_no, body)

    def test_reissue_rejects_empty_reason_wrong_type_and_missing_original(self):
        self._post(Certificate.WARRANTY, [self.pipe1.id])
        original = Certificate.query.one()

        empty = self._post(
            Certificate.WARRANTY, [self.pipe1.id],
            reissue_of_id=str(original.id), reissue_reason="   ")
        self.assertEqual(1, Certificate.query.count())
        self.assertIn("reason", empty.get_data(as_text=True).lower())

        wrong_type = self._post(
            Certificate.WORK_TEST, [self.pipe1.id],
            reissue_of_id=str(original.id), reissue_reason="Needed")
        self.assertEqual(1, Certificate.query.count())
        self.assertIn("same type", wrong_type.get_data(as_text=True).lower())

        missing = self._post(
            Certificate.WARRANTY, [self.pipe1.id],
            reissue_of_id="99999", reissue_reason="Needed")
        self.assertEqual(1, Certificate.query.count())
        self.assertIn("original", missing.get_data(as_text=True).lower())

    def test_user_without_dedicated_permission_cannot_reissue(self):
        self._post(Certificate.WARRANTY, [self.pipe1.id])
        original = Certificate.query.one()
        permission = Permission.query.filter_by(
            module="certificates", screen="reissue").one()
        grant = RolePermission.query.filter_by(
            role=self.user.role, permission_id=permission.id).one()
        db.session.delete(grant)
        db.session.commit()

        response = self._post(
            Certificate.WARRANTY, [self.pipe1.id],
            reissue_of_id=str(original.id), reissue_reason="Needed")

        self.assertEqual(1, Certificate.query.count())
        self.assertIn("permission", response.get_data(as_text=True).lower())

    def test_edit_revalidates_status_and_leaves_saved_certificate_unchanged(self):
        self._post(
            Certificate.WARRANTY, [self.pipe1.id], notes="original note")
        cert = Certificate.query.one()
        self.pipe1.final_decision_value = "REJECT"
        self.pipe1.final_decision_reason = "Decision changed after form load"
        db.session.commit()

        response = self.client.post(
            f"/certificates/{cert.id}/edit",
            data={
                "order_ids": [str(self.order1.id)],
                "primary_order_id": str(self.order1.id),
                "pipe_ids": [str(self.pipe1.id)],
                "notes": "must not persist",
            },
            follow_redirects=True,
        )

        db.session.refresh(cert)
        self.assertEqual("original note", cert.notes)
        self.assertEqual([self.pipe1.id], [pipe.id for pipe in cert.pipes])
        self.assertIn(
            "Decision changed after form load", response.get_data(as_text=True))

    def test_edit_rejects_incompatible_order_and_same_type_duplicate(self):
        self._post(Certificate.WARRANTY, [self.pipe1.id])
        original = Certificate.query.one()
        other = Certificate(
            certificate_no="WC-2026-900",
            cert_type=Certificate.WARRANTY,
            production_order_id=self.order2.id,
            issue_date=date(2026, 9, 16),
            pipes=[self.pipe2],
        )
        self.order2.application_profile = {"standards": {"en_598": True}}
        db.session.add(other)
        db.session.commit()

        response = self.client.post(
            f"/certificates/{original.id}/edit",
            data={
                "order_ids": [str(self.order1.id), str(self.order2.id)],
                "primary_order_id": str(self.order1.id),
                "pipe_ids": [str(self.pipe1.id), str(self.pipe2.id)],
            },
            follow_redirects=True,
        )

        db.session.refresh(original)
        self.assertEqual([self.pipe1.id], [pipe.id for pipe in original.pipes])
        body = response.get_data(as_text=True)
        self.assertIn("Application standards", body)
        self.assertIn("WC-2026-900", body)

    def test_print_context_does_not_expand_one_checked_application_standard(self):
        response = self._post(Certificate.WORK_TEST, [self.pipe1.id])
        body = response.get_data(as_text=True)

        self.assertIn("ISO 2531", body)
        self.assertNotIn("EN 545", body)

    def test_work_test_print_receives_accurate_external_coating_context(self):
        external = ProductParameter(
            param_type="EXTERNAL_FINISH", code="EP",
            name_en="Blue Epoxy")
        product = Product(
            product_code="CERT-EP",
            external_finish_param=external,
            application_profile={"standards": {"iso_2531": True}},
        )
        db.session.add(product)
        db.session.flush()
        self.order1.product_id = product.id
        db.session.commit()

        response = self._post(Certificate.WORK_TEST, [self.pipe1.id])

        self.assertIn(
            "externally coated directly with Epoxy",
            response.get_data(as_text=True),
        )


if __name__ == "__main__":
    unittest.main()
