"""Race-safety and no-JavaScript state regression tests for certificates."""

import unittest
from datetime import date
from unittest.mock import patch

from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from app import create_app, db
from app.models.certificate import Certificate, CertificatePipeClaim
from app.models.permission import seed_default_permissions
from app.models.pipe import Pipe
from app.models.production_order import ProductionOrder
from app.models.user import User


class CertificateAtomicityTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()

        self.user = User(
            username="atomic-quality", full_name="Atomic Quality",
            role="supervisor", is_active=True,
        )
        self.user.set_password("x")
        self.order = ProductionOrder(
            order_number="PO-ATOMIC-001", customer_name="Cairo Water",
            target_quantity=1, diameter=800, pipe_class="K9",
            product_description="Ductile Iron Pipes", product_length=6.0,
            order_date=date(2026, 9, 16),
            application_profile={"standards": {"iso_2531": True}},
        )
        db.session.add_all([self.user, self.order])
        db.session.flush()
        self.pipe = Pipe(
            production_date=date(2026, 9, 16), ladle_id="L1",
            pipe_code="PIPE-ATOMIC", no_code="PIPE-ATOMIC", arrange_pipe=1,
            diameter=800, pipe_class="K9", production_order_id=self.order.id,
            final_decision_value="ACCEPT",
        )
        db.session.add(self.pipe)
        db.session.commit()
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["_user_id"] = str(self.user.id)
            session["_fresh"] = True

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _certificate(self, number, cert_type=Certificate.WARRANTY, **kwargs):
        cert = Certificate(
            certificate_no=number, cert_type=cert_type,
            production_order_id=self.order.id, issue_date=date(2026, 9, 16),
            **kwargs,
        )
        db.session.add(cert)
        db.session.flush()
        return cert

    def _post(self, **extra):
        data = {
            "primary_order_id": str(self.order.id),
            "order_ids": [str(self.order.id)],
            "pipe_ids": [str(self.pipe.id)],
            **extra,
        }
        return self.client.post(
            "/certificates/new?type=warranty", data=data,
            follow_redirects=True,
        )

    def test_database_claim_is_unique_for_pipe_and_certificate_type(self):
        first = self._certificate("WC-2026-901")
        second = self._certificate("WC-2026-902")
        db.session.add(CertificatePipeClaim(
            certificate=first, pipe_id=self.pipe.id,
            cert_type=Certificate.WARRANTY,
        ))
        db.session.commit()

        db.session.add(CertificatePipeClaim(
            certificate=second, pipe_id=self.pipe.id,
            cert_type=Certificate.WARRANTY,
        ))
        with self.assertRaises(IntegrityError):
            db.session.commit()
        db.session.rollback()

        db.session.add(CertificatePipeClaim(
            certificate=second, pipe_id=self.pipe.id,
            cert_type=Certificate.WORK_TEST,
        ))
        db.session.commit()

    def test_claim_conflict_blocks_issue_even_without_a_legacy_pipe_link(self):
        existing = self._certificate("WC-2026-901")
        db.session.add(CertificatePipeClaim(
            certificate=existing, pipe_id=self.pipe.id,
            cert_type=Certificate.WARRANTY,
        ))
        db.session.commit()

        response = self._post()

        self.assertEqual(1, Certificate.query.count())
        self.assertIn("already", response.get_data(as_text=True).lower())

    def test_ordinary_issue_owns_claim_but_authorized_reissue_does_not(self):
        self._post()
        original = Certificate.query.one()
        claim = CertificatePipeClaim.query.one()
        self.assertEqual(original.id, claim.certificate_id)
        self.assertEqual(self.pipe.id, claim.pipe_id)
        self.assertEqual(Certificate.WARRANTY, claim.cert_type)

        response = self._post(
            reissue_of_id=str(original.id),
            reissue_reason="Customer copy was destroyed",
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual(2, Certificate.query.count())
        self.assertEqual(1, CertificatePipeClaim.query.count())

    def test_original_and_reissue_can_both_be_edited_after_reissue(self):
        self._post()
        original = Certificate.query.one()
        self._post(
            reissue_of_id=str(original.id),
            reissue_reason="Customer copy was destroyed",
        )
        reissue = Certificate.query.filter(
            Certificate.id != original.id).one()

        for certificate, note in (
            (original, "Corrected original"),
            (reissue, "Corrected reissue"),
        ):
            response = self.client.post(
                f"/certificates/{certificate.id}/edit",
                data={
                    "primary_order_id": str(self.order.id),
                    "order_ids": [str(self.order.id)],
                    "pipe_ids": [str(self.pipe.id)],
                    "notes": note,
                },
                follow_redirects=True,
            )
            self.assertEqual(200, response.status_code)
            db.session.refresh(certificate)
            self.assertEqual(note, certificate.notes)

        self.assertEqual(2, Certificate.query.count())
        self.assertEqual(1, CertificatePipeClaim.query.count())

    def test_multiple_authorized_reissues_share_the_original_claim(self):
        self._post()
        original = Certificate.query.one()

        for reason in ("First replacement", "Second replacement"):
            self._post(
                reissue_of_id=str(original.id),
                reissue_reason=reason,
            )

        self.assertEqual(3, Certificate.query.count())
        self.assertEqual(1, CertificatePipeClaim.query.count())
        self.assertEqual(
            {"First replacement", "Second replacement"},
            {certificate.reissue_reason for certificate in Certificate.query
             .filter(Certificate.reissue_of_id == original.id).all()},
        )

    def test_original_with_reissues_cannot_be_deleted(self):
        self._post()
        original = Certificate.query.one()
        self._post(
            reissue_of_id=str(original.id),
            reissue_reason="Customer copy was destroyed",
        )
        reissue = Certificate.query.filter(
            Certificate.id != original.id).one()
        self.user.role = "admin"
        db.session.commit()

        response = self.client.post(
            f"/certificates/{original.id}/delete", follow_redirects=True)

        self.assertEqual(200, response.status_code)
        self.assertEqual(2, Certificate.query.count())
        db.session.refresh(reissue)
        self.assertEqual(original.id, reissue.reissue_of_id)
        self.assertIn("reissue", response.get_data(as_text=True).lower())

    def test_create_locks_selected_pipes_and_uses_one_write_commit(self):
        query_type = type(Pipe.query)
        original_lock = query_type.with_for_update
        original_commit = db.session.commit
        with patch.object(
            query_type, "with_for_update", autospec=True,
            side_effect=lambda query, *args, **kwargs: original_lock(
                query, *args, **kwargs),
        ) as lock_rows, patch.object(
            db.session, "commit", wraps=original_commit,
        ) as commit:
            self._post()

        lock_rows.assert_called_once()
        commit.assert_called_once()

    def test_sqlite_write_lock_starts_before_issuance_reads(self):
        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _many):
            statements.append(statement.strip().upper())

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            self._post()
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        self.assertIn("BEGIN IMMEDIATE", statements)
        self.assertLess(
            statements.index("BEGIN IMMEDIATE"),
            next(i for i, sql in enumerate(statements)
                 if "FROM PIPES" in sql and "ORDER BY PIPES.ID" in sql),
        )

    def test_no_js_selection_actions_preserve_new_certificate_state(self):
        original = self._certificate("WC-2026-777")
        original.pipes = [self.pipe]
        db.session.commit()

        page = self.client.get(
            "/certificates/new",
            query_string={
                "type": "warranty",
                "primary_order_id": self.order.id,
                "order_ids": self.order.id,
                "reissue_of_id": original.id,
            },
        )
        expected = (
            f"/certificates/new/selection/warranty/{self.order.id}/"
            f"{original.id}/{self.order.id}"
        )
        self.assertGreaterEqual(
            page.get_data(as_text=True).count(f'action="{expected}"'), 2)

        redirected = self.client.get(
            expected, query_string={"pipe_lookup": self.pipe.no_code},
        )
        location = redirected.headers["Location"]
        self.assertIn("type=warranty", location)
        self.assertIn(f"primary_order_id={self.order.id}", location)
        self.assertIn(f"order_ids={self.order.id}", location)
        self.assertIn(f"reissue_of_id={original.id}", location)
        self.assertIn("pipe_lookup=PIPE-ATOMIC", location)

    def test_reissue_search_finds_an_original_older_than_one_hundred(self):
        for serial in range(1, 106):
            self._certificate(f"WC-2025-{serial:03d}")
        db.session.commit()

        page = self.client.get(
            "/certificates/new",
            query_string={
                "type": "warranty", "primary_order_id": self.order.id,
                "order_ids": self.order.id, "reissue_query": "WC-2025-001",
            },
        )

        self.assertIn("WC-2025-001", page.get_data(as_text=True))

    def test_no_js_selection_actions_preserve_current_edit_and_orders(self):
        cert = self._certificate("WC-2026-778")
        cert.pipes = [self.pipe]
        cert.orders = [self.order]
        db.session.commit()

        page = self.client.get(f"/certificates/{cert.id}/edit")
        expected = (
            f"/certificates/{cert.id}/edit/selection/"
            f"{self.order.id}/{self.order.id}"
        )
        self.assertGreaterEqual(
            page.get_data(as_text=True).count(f'action="{expected}"'), 2)

        redirected = self.client.get(
            expected, query_string={"pipe_lookup": self.pipe.no_code},
        )
        location = redirected.headers["Location"]
        self.assertIn(f"/certificates/{cert.id}/edit", location)
        self.assertIn(f"primary_order_id={self.order.id}", location)
        self.assertIn(f"order_ids={self.order.id}", location)
        self.assertIn("pipe_lookup=PIPE-ATOMIC", location)


if __name__ == "__main__":
    unittest.main()
