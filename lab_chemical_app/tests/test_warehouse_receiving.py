"""The storekeeper can record a delivery and reach nothing else.

This is the test the whole feature exists for. The warehouse screen was built
as a separate blueprint precisely because `stages.edit` is all-or-nothing over
every quality decision, defect and measurement on a pipe — so the boundary has
to be asserted, not assumed from the fact that the template renders no other
inputs.
"""

import unittest
from datetime import date

from app import create_app, db
from app.models.audit import AuditLog
from app.models.permission import seed_default_permissions
from app.models.pipe import Pipe, PipeStage
from app.models.production_order import ProductionOrder
from app.models.stage import ProductionStage
from app.models.user import User


class WarehouseReceivingTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()
        self.client = self.app.test_client()

        keeper = User(username="keeper", full_name="Storekeeper",
                      role=User.ROLE_WAREHOUSE, is_active=True)
        keeper.set_password("x")
        db.session.add(keeper)
        db.session.commit()
        self.keeper_id = keeper.id

        self.delivery_name = ProductionStage.name_for_code("delivery")

        pipe = Pipe(
            production_date=date(2026, 8, 20),
            ladle_id="L1",
            pipe_code="L1-P1",
            no_code="N1",
            arrange_pipe=1,
            diameter=800,
            pipe_class="K9",
            warehouse_barcode="9001",
            final_decision_value="ACCEPT",
        )
        db.session.add(pipe)
        db.session.commit()
        self.pipe_id = pipe.id

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self, user_id=None):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(user_id or self.keeper_id)
            sess["_fresh"] = True

    def _delivery_row(self):
        return PipeStage.query.filter_by(
            pipe_id=self.pipe_id, stage_name=self.delivery_name
        ).first()

    # --- the happy path -----------------------------------------------------

    def test_scan_page_renders(self):
        self._login()
        r = self.client.get("/warehouse/")
        self.assertEqual(r.status_code, 200)

    def test_no_js_scan_adds_a_known_barcode_to_the_html_basket(self):
        self._login()
        r = self.client.post("/warehouse/", data={"barcode": "9001"})
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        self.assertIn("data-pipe-selection", html)
        self.assertIn("data-server-selected", html)
        self.assertIn('value="1"', html)
        self.assertIn('form="batchForm"', html)
        self.assertIn("L1-P1", html)

    def test_unknown_barcode_says_so_rather_than_404(self):
        self._login()
        r = self.client.post("/warehouse/", data={"barcode": "404404"},
                             follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn("404404", r.get_data(as_text=True))

    # --- finding a pipe by code, not just by barcode ------------------------

    def test_an_exact_pipe_code_adds_that_pipe_to_the_basket(self):
        """Not every pipe carries a barcode; the code on the label always works."""
        self._login()
        r = self.client.post("/warehouse/", data={"barcode": "L1-P1"})
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        self.assertIn("data-server-selected", html)
        self.assertIn("L1-P1", html)

    def test_pipe_code_is_case_insensitive(self):
        self._login()
        r = self.client.post("/warehouse/", data={"barcode": "l1-p1"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("data-server-selected", r.get_data(as_text=True))

    def test_an_exact_no_code_opens_that_pipe(self):
        self._login()
        r = self.client.post("/warehouse/", data={"barcode": "N1"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("data-server-selected", r.get_data(as_text=True))

    def test_a_partial_matching_one_pipe_adds_it_to_the_basket(self):
        """One candidate is not ambiguous — select it in the no-JS basket."""
        self._login()
        r = self.client.post("/warehouse/", data={"barcode": "L1-P"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("data-server-selected", r.get_data(as_text=True))

    def test_a_partial_matching_several_lists_them(self):
        for n in range(2, 5):
            db.session.add(Pipe(
                production_date=date(2026, 8, 20), ladle_id="L1",
                pipe_code=f"PIP1-{n}", no_code=f"N{n}", arrange_pipe=n,
            ))
        db.session.commit()

        self._login()
        r = self.client.post("/warehouse/", data={"barcode": "PIP1"})
        self.assertEqual(r.status_code, 200, "a list, not a redirect")
        html = r.get_data(as_text=True)
        for n in range(2, 5):
            self.assertIn(f"PIP1-{n}", html)
        # It offered a list, so it must not also claim nothing was found.
        self.assertNotIn("No pipe matches", html)

    def test_a_shared_no_code_lists_the_batch(self):
        """no_code is a batch identifier, not an id — on prod "PIP1" is eleven
        pipes. An exact match on it must not hand back an arbitrary one."""
        for n in range(2, 5):
            db.session.add(Pipe(
                production_date=date(2026, 8, 20), ladle_id="L1",
                pipe_code=f"L1-P{n}", no_code="N1", arrange_pipe=n,
            ))
        db.session.commit()

        self._login()
        r = self.client.post("/warehouse/", data={"barcode": "N1"})
        self.assertEqual(r.status_code, 200, "a batch is a list, not a redirect")
        html = r.get_data(as_text=True)
        for n in range(1, 5):
            self.assertIn(f"L1-P{n}", html)

    def test_the_barcode_wins_over_a_pipe_code(self):
        """A scanned barcode is the unambiguous key; it must not lose to a
        pipe code that happens to hold the same string."""
        other = Pipe(
            production_date=date(2026, 8, 20), ladle_id="L1",
            pipe_code="9001", no_code="N-OTHER", arrange_pipe=7,
        )
        db.session.add(other)
        db.session.commit()
        other_id = other.id

        self._login()
        r = self.client.post("/warehouse/", data={"barcode": "9001"})
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        self.assertIn("data-server-selected", html)
        self.assertIn("L1-P1", html)
        self.assertNotIn('data-server-selected="%s"' % other_id, html)

    def test_the_four_fields_save(self):
        self._login()
        r = self.client.post(
            f"/warehouse/pipe/{self.pipe_id}",
            data={
                "delivery_date": "2026-08-29",
                "sales_order": "SO-77",
                "delivery_customer": "ACME",
                "delivery_receipt": "R-12",
            },
        )
        self.assertEqual(r.status_code, 302)
        row = self._delivery_row()
        self.assertIsNotNone(row)
        self.assertEqual(row.delivery_date, date(2026, 8, 29))
        self.assertEqual(row.sales_order, "SO-77")
        self.assertEqual(row.delivery_customer, "ACME")
        self.assertEqual(row.delivery_receipt, "R-12")
        self.assertEqual(row.updated_by_id, self.keeper_id)

    # --- the boundary -------------------------------------------------------

    def test_posted_extras_are_ignored(self):
        """A crafted POST carrying quality fields must change none of them."""
        stage = PipeStage(
            pipe_id=self.pipe_id,
            stage_name=self.delivery_name,
            decision="Pending",
            notes="original note",
            defect_type="none",
        )
        db.session.add(stage)
        db.session.commit()

        self._login()
        self.client.post(
            f"/warehouse/pipe/{self.pipe_id}",
            data={
                "delivery_customer": "ACME",
                # None of these are in the handler's allowlist.
                "decision": "Accept",
                "notes": "tampered",
                "defect_type": "cracked",
                "has_defect": "1",
                "measurement_value": "99",
                "machine_id": "1",
            },
        )
        row = self._delivery_row()
        self.assertEqual(row.delivery_customer, "ACME")
        self.assertEqual(row.decision, "Pending")
        self.assertEqual(row.notes, "original note")
        self.assertEqual(row.defect_type, "none")
        self.assertIsNone(row.measurement_value)
        self.assertIsNone(row.machine_id)

    def test_storekeeper_cannot_reach_the_stage_screens(self):
        self._login()
        for path in (
            f"/stages/{self.pipe_id}/edit",
            "/stages/add",
            "/stages/console",
            "/stages/",
            f"/stages/console/pipe/{self.pipe_id}",
            "/admin/users",
        ):
            r = self.client.get(path)
            self.assertIn(
                r.status_code, (302, 403),
                f"{path} returned {r.status_code} for a warehouse user",
            )

    def test_storekeeper_cannot_post_a_stage_update(self):
        self._login()
        r = self.client.post(
            f"/stages/{self.pipe_id}/stage/{self.delivery_name}",
            json={"decision": "Accept"},
        )
        self.assertIn(r.status_code, (302, 403))
        self.assertIsNone(self._delivery_row())

    # --- the quality gate ---------------------------------------------------

    def test_receiving_is_refused_until_quality_accepts(self):
        pipe = db.session.get(Pipe, self.pipe_id)
        pipe.final_decision_value = None
        db.session.commit()

        self._login()
        page = self.client.get(f"/warehouse/pipe/{self.pipe_id}")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn('name="delivery_customer"', page.get_data(as_text=True))

        self.client.post(
            f"/warehouse/pipe/{self.pipe_id}",
            data={"delivery_customer": "ACME"},
        )
        self.assertIsNone(self._delivery_row())

    def test_a_held_pipe_cannot_be_received(self):
        pipe = db.session.get(Pipe, self.pipe_id)
        pipe.lab_decision = "HOLD"
        db.session.commit()

        self._login()
        self.client.post(
            f"/warehouse/pipe/{self.pipe_id}",
            data={"delivery_customer": "ACME"},
        )
        self.assertIsNone(self._delivery_row())

    # --- a whole truck at once ----------------------------------------------

    def _extra_pipes(self, count, accepted=True):
        """More pipes on the same load."""
        ids = []
        for n in range(2, 2 + count):
            p = Pipe(
                production_date=date(2026, 8, 20), ladle_id="L1",
                pipe_code=f"L1-P{n}", no_code="N1", arrange_pipe=n,
                warehouse_barcode=f"900{n}",
                final_decision_value="ACCEPT" if accepted else None,
            )
            db.session.add(p)
            db.session.commit()
            ids.append(p.id)
        return ids

    def _rows(self, pipe_ids):
        return PipeStage.query.filter(
            PipeStage.pipe_id.in_(pipe_ids),
            PipeStage.stage_name == self.delivery_name,
        ).all()

    def test_lookup_returns_the_pipe_without_leaving_the_page(self):
        self._login()
        r = self.client.get("/warehouse/lookup?barcode=9001")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["pipe"]["id"], self.pipe_id)
        self.assertIsNone(body["pipe"]["blocked"])

    def test_lookup_reports_a_held_pipe_as_blocked_rather_than_hiding_it(self):
        """The storekeeper has it on the truck — say why it cannot go."""
        pipe = db.session.get(Pipe, self.pipe_id)
        pipe.lab_decision = "HOLD"
        db.session.commit()

        self._login()
        body = self.client.get("/warehouse/lookup?barcode=9001").get_json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["pipe"]["blocked"])
        self.assertEqual(body["pipe"]["status"], "HOLD")

    def test_lookup_reports_a_lab_reject_as_rejected_even_before_final_rollup(self):
        pipe = db.session.get(Pipe, self.pipe_id)
        pipe.lab_decision = "REJECT"
        pipe.final_decision_value = None
        db.session.commit()

        self._login()
        body = self.client.get("/warehouse/lookup?barcode=9001").get_json()["pipe"]
        self.assertFalse(body["selectable"])
        self.assertEqual(body["status"], "REJECT")
        self.assertIn("rejected", body["reason"].lower())

    def test_lookup_lists_candidates_for_an_ambiguous_code(self):
        self._extra_pipes(3)
        self._login()
        body = self.client.get("/warehouse/lookup?barcode=N1").get_json()
        self.assertFalse(body["ok"])
        self.assertEqual(len(body["matches"]), 4, "the whole batch")

    def test_lookup_says_nothing_found_rather_than_404(self):
        self._login()
        body = self.client.get("/warehouse/lookup?barcode=404404").get_json()
        self.assertFalse(body["ok"])
        self.assertTrue(body["not_found"])
        self.assertEqual(body["matches"], [])

    def test_one_entry_writes_the_whole_load(self):
        """The point of the feature: type the four fields once for 4 pipes."""
        others = self._extra_pipes(3)
        every = [self.pipe_id] + others

        self._login()
        r = self.client.post("/warehouse/batch", data={
            "pipe_ids": [str(i) for i in every],
            "delivery_date": "2026-08-29",
            "sales_order": "SO-77",
            "delivery_customer": "ACME",
            "delivery_receipt": "R-12",
        })
        self.assertEqual(r.status_code, 302)

        rows = self._rows(every)
        self.assertEqual(len(rows), 4)
        for row in rows:
            self.assertEqual(row.delivery_date, date(2026, 8, 29))
            self.assertEqual(row.stage_date, date(2026, 8, 29))
            self.assertEqual(row.sales_order, "SO-77")
            self.assertEqual(row.delivery_customer, "ACME")
            self.assertEqual(row.delivery_receipt, "R-12")
            self.assertEqual(row.updated_by_id, self.keeper_id)

    def test_a_pipe_scanned_twice_is_written_once(self):
        self._login()
        self.client.post("/warehouse/batch", data={
            "pipe_ids": [str(self.pipe_id), str(self.pipe_id)],
            "delivery_customer": "ACME",
        })
        self.assertEqual(len(self._rows([self.pipe_id])), 1)

    def test_a_blocked_pipe_rejects_the_whole_load_and_is_named(self):
        """It can go on hold between the scan and the save, so the gate is
        re-checked per pipe — and the storekeeper is told which to take off."""
        others = self._extra_pipes(2)
        held = db.session.get(Pipe, others[0])
        held.lab_decision = "HOLD"
        db.session.commit()

        self._login()
        r = self.client.post("/warehouse/batch", data={
            "pipe_ids": [str(self.pipe_id)] + [str(i) for i in others],
            "delivery_customer": "ACME",
        }, follow_redirects=True)

        self.assertIsNone(
            PipeStage.query.filter_by(pipe_id=others[0],
                                      stage_name=self.delivery_name).first(),
            "a held pipe must not be booked out",
        )
        self.assertEqual(len(self._rows([self.pipe_id, others[1]])), 0)
        self.assertIn("L1-P2", r.get_data(as_text=True), "named, not silently dropped")

    def test_an_unaccepted_pipe_rejects_the_whole_load(self):
        others = self._extra_pipes(1, accepted=False)
        self._login()
        self.client.post("/warehouse/batch", data={
            "pipe_ids": [str(self.pipe_id), str(others[0])],
            "delivery_customer": "ACME",
        })
        self.assertEqual(len(self._rows([others[0]])), 0)
        self.assertEqual(len(self._rows([self.pipe_id])), 0)

    def test_a_missing_or_invalid_id_rejects_the_whole_load(self):
        others = self._extra_pipes(1)
        self._login()
        for posted in (
            [str(self.pipe_id), "999999"],
            [str(self.pipe_id), "not-an-id"],
        ):
            with self.subTest(posted=posted):
                r = self.client.post(
                    "/warehouse/batch",
                    data={"pipe_ids": posted, "delivery_customer": "ACME"},
                    follow_redirects=True,
                )
                self.assertEqual(self._rows([self.pipe_id] + others), [])
                self.assertIn("not saved", r.get_data(as_text=True).lower())

    def test_a_delivered_pipe_rejects_the_whole_load_without_overwriting_it(self):
        other_id = self._extra_pipes(1)[0]
        existing = PipeStage(
            pipe_id=self.pipe_id,
            stage_name=self.delivery_name,
            delivery_date=date(2026, 9, 1),
            sales_order="ORIGINAL-SO",
            delivery_customer="Original customer",
            delivery_receipt="ORIGINAL-R",
        )
        db.session.add(existing)
        db.session.commit()

        self._login()
        self.client.post("/warehouse/batch", data={
            "pipe_ids": [str(self.pipe_id), str(other_id)],
            "delivery_date": "2026-09-15",
            "sales_order": "NEW-SO",
            "delivery_customer": "New customer",
            "delivery_receipt": "NEW-R",
        })

        db.session.refresh(existing)
        self.assertEqual(existing.delivery_date, date(2026, 9, 1))
        self.assertEqual(existing.sales_order, "ORIGINAL-SO")
        self.assertEqual(existing.delivery_customer, "Original customer")
        self.assertEqual(existing.delivery_receipt, "ORIGINAL-R")
        self.assertEqual(self._rows([other_id]), [])

    def test_lookup_exposes_prior_delivery_and_does_not_offer_it_for_the_basket(self):
        db.session.add(PipeStage(
            pipe_id=self.pipe_id,
            stage_name=self.delivery_name,
            delivery_date=date(2026, 9, 1),
            sales_order="SO-1",
            delivery_customer="ACME",
            delivery_receipt="R-1",
        ))
        db.session.commit()

        self._login()
        body = self.client.get("/warehouse/lookup?barcode=9001").get_json()["pipe"]
        self.assertFalse(body["selectable"])
        self.assertEqual(body["status"], "DELIVERED")
        self.assertEqual(body["delivery"], {
            "date": "2026-09-01",
            "sales_order": "SO-1",
            "customer": "ACME",
            "receipt": "R-1",
        })

    def test_multiple_orders_load_into_the_shared_selection_table(self):
        first = ProductionOrder(
            order_number="PO-1", target_quantity=1, order_date=date(2026, 9, 1)
        )
        second = ProductionOrder(
            order_number="PO-2", target_quantity=1, order_date=date(2026, 9, 1)
        )
        db.session.add_all([first, second])
        db.session.flush()
        pipe = db.session.get(Pipe, self.pipe_id)
        pipe.production_order_id = first.id
        other_id = self._extra_pipes(1)[0]
        db.session.get(Pipe, other_id).production_order_id = second.id
        db.session.commit()

        self._login()
        html = self.client.get(
            f"/warehouse/?order_ids={first.id}&order_ids={second.id}"
        ).get_data(as_text=True)
        self.assertIn("PO-1", html)
        self.assertIn("PO-2", html)
        self.assertIn("L1-P1", html)
        self.assertIn("L1-P2", html)
        self.assertIn("pipe_selection.js", html)

    def test_no_js_scan_preserves_selected_orders(self):
        order = ProductionOrder(
            order_number="PO-KEEP", target_quantity=1, order_date=date(2026, 9, 1)
        )
        db.session.add(order)
        db.session.flush()
        db.session.get(Pipe, self.pipe_id).production_order_id = order.id
        db.session.commit()

        self._login()
        html = self.client.post(
            "/warehouse/",
            data={"barcode": "9001", "order_ids": [str(order.id)]},
        ).get_data(as_text=True)
        self.assertIn("PO-KEEP", html)
        self.assertIn(f'name="order_ids" value="{order.id}"', html)

    def test_persisted_id_reconciliation_returns_current_eligibility(self):
        other_id = self._extra_pipes(1)[0]
        other = db.session.get(Pipe, other_id)
        other.lab_decision = "HOLD"
        db.session.commit()

        self._login()
        body = self.client.get(
            f"/warehouse/selection?pipe_ids={self.pipe_id}&pipe_ids={other_id}&pipe_ids=99999"
        ).get_json()
        self.assertEqual([row["id"] for row in body["pipes"]], [self.pipe_id, other_id])
        self.assertTrue(body["pipes"][0]["selectable"])
        self.assertFalse(body["pipes"][1]["selectable"])
        self.assertEqual(body["missing_ids"], [99999])

    def test_normal_single_pipe_save_cannot_overwrite_an_existing_delivery(self):
        existing = PipeStage(
            pipe_id=self.pipe_id,
            stage_name=self.delivery_name,
            delivery_date=date(2026, 9, 1),
            delivery_customer="Before",
            sales_order="ORIGINAL-SO",
        )
        db.session.add(existing)
        db.session.commit()

        self._login()
        response = self.client.post(
            f"/warehouse/pipe/{self.pipe_id}",
            data={"delivery_customer": "After", "sales_order": "NEW-SO"},
            follow_redirects=True,
        )
        db.session.refresh(existing)
        self.assertEqual(existing.delivery_customer, "Before")
        self.assertEqual(existing.sales_order, "ORIGINAL-SO")
        self.assertIn("already delivered", response.get_data(as_text=True).lower())

    def test_prior_delivery_page_has_an_explicit_correction_action_and_reason(self):
        db.session.add(PipeStage(
            pipe_id=self.pipe_id,
            stage_name=self.delivery_name,
            delivery_date=date(2026, 9, 1),
            delivery_customer="Before",
        ))
        db.session.commit()

        self._login()
        html = self.client.get(f"/warehouse/pipe/{self.pipe_id}").get_data(as_text=True)
        self.assertIn(f"/warehouse/pipe/{self.pipe_id}/correct", html)
        self.assertIn('name="audit_reason"', html)
        self.assertIn("2026-09-01", html)
        self.assertIn("Before", html)

    def test_explicit_authorized_correction_requires_reason_and_is_audited(self):
        existing = PipeStage(
            pipe_id=self.pipe_id,
            stage_name=self.delivery_name,
            delivery_date=date(2026, 9, 1),
            delivery_customer="Before",
        )
        db.session.add(existing)
        db.session.commit()

        self._login()
        without_reason = self.client.post(
            f"/warehouse/pipe/{self.pipe_id}/correct",
            data={"delivery_customer": "Must not save"},
        )
        self.assertEqual(without_reason.status_code, 302)
        db.session.refresh(existing)
        self.assertEqual(existing.delivery_customer, "Before")

        response = self.client.post(
            f"/warehouse/pipe/{self.pipe_id}/correct",
            data={
                "delivery_date": "2026-09-02",
                "delivery_customer": "After",
                "audit_reason": "Correct customer from signed receipt",
            },
        )
        self.assertEqual(response.status_code, 302)
        db.session.refresh(existing)
        self.assertEqual(existing.delivery_date, date(2026, 9, 2))
        self.assertEqual(existing.delivery_customer, "After")
        audits = AuditLog.query.filter_by(
            table_name="pipe_stages", record_id=existing.id, action="UPDATE"
        ).all()
        self.assertTrue(audits)
        self.assertTrue(all(log.user_id == self.keeper_id for log in audits))
        self.assertTrue(all(log.reason == "Correct customer from signed receipt"
                            for log in audits))

    def test_batch_ignores_posted_extras_too(self):
        """The allowlist is the boundary — it must not have a second door."""
        stage = PipeStage(
            pipe_id=self.pipe_id, stage_name=self.delivery_name,
            decision="Pending", notes="original note", defect_type="none",
        )
        db.session.add(stage)
        db.session.commit()

        self._login()
        self.client.post("/warehouse/batch", data={
            "pipe_ids": [str(self.pipe_id)],
            "delivery_customer": "ACME",
            "decision": "Accept",
            "notes": "tampered",
            "defect_type": "cracked",
            "measurement_value": "99",
            "machine_id": "1",
        })
        row = self._delivery_row()
        self.assertEqual(row.delivery_customer, "ACME")
        self.assertEqual(row.decision, "Pending")
        self.assertEqual(row.notes, "original note")
        self.assertEqual(row.defect_type, "none")
        self.assertIsNone(row.measurement_value)

    def test_an_invalid_date_saves_nothing_at_all(self):
        """Parsed once, before the loop — no half-written truck."""
        others = self._extra_pipes(2)
        self._login()
        self.client.post("/warehouse/batch", data={
            "pipe_ids": [str(self.pipe_id)] + [str(i) for i in others],
            "delivery_date": "29-08-2026",
            "delivery_customer": "ACME",
        })
        self.assertEqual(self._rows([self.pipe_id] + others), [])

    def test_a_viewer_cannot_save_a_load(self):
        viewer = User(username="v2", role=User.ROLE_VIEWER, is_active=True)
        viewer.set_password("x")
        db.session.add(viewer)
        db.session.commit()

        self._login(viewer.id)
        r = self.client.post("/warehouse/batch", data={
            "pipe_ids": [str(self.pipe_id)], "delivery_customer": "ACME",
        })
        self.assertIn(r.status_code, (302, 403))
        self.assertIsNone(self._delivery_row())

    # --- the role itself ----------------------------------------------------

    def test_warehouse_role_is_not_granted_blanket_edit(self):
        keeper = db.session.get(User, self.keeper_id)
        self.assertFalse(keeper.can_edit)
        self.assertFalse(keeper.can_approve)
        self.assertFalse(keeper.is_admin)

    def test_a_viewer_cannot_receive(self):
        viewer = User(username="v", role=User.ROLE_VIEWER, is_active=True)
        viewer.set_password("x")
        db.session.add(viewer)
        db.session.commit()

        self._login(viewer.id)
        r = self.client.post(
            f"/warehouse/pipe/{self.pipe_id}", data={"delivery_customer": "ACME"}
        )
        self.assertIn(r.status_code, (302, 403))
        self.assertIsNone(self._delivery_row())

    def test_a_viewer_cannot_correct_an_existing_delivery(self):
        existing = PipeStage(
            pipe_id=self.pipe_id,
            stage_name=self.delivery_name,
            delivery_customer="Original",
        )
        viewer = User(username="vc", role=User.ROLE_VIEWER, is_active=True)
        viewer.set_password("x")
        db.session.add_all([existing, viewer])
        db.session.commit()

        self._login(viewer.id)
        response = self.client.post(
            f"/warehouse/pipe/{self.pipe_id}/correct",
            data={"delivery_customer": "Tampered", "audit_reason": "No authority"},
        )
        self.assertIn(response.status_code, (302, 403))
        db.session.refresh(existing)
        self.assertEqual(existing.delivery_customer, "Original")


if __name__ == "__main__":
    unittest.main()
