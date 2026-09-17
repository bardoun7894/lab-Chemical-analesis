"""The internal label says where the pipe is standing; the external one does not.

A "PENDING" label tells the shop floor nothing it can act on — the pipe is
somewhere in the line, but the operator has to go and look up which stage is
holding it. The stage was already known at the moment the verdict was decided
and simply thrown away, so the internal print keeps it.

The mode is per-print, not a stored setting: the same pipe wears an internal
label on the floor and an external one when it ships. External is the default
everywhere, so a print that forgets to say cannot put the line's internals on
a customer's pipe.
"""

import unittest
from datetime import date

from app import create_app, db
from app.models.chemical import Machine
from app.models.certificate import Certificate
from app.models.permission import seed_default_permissions
from app.models.pipe import Pipe, PipeStage
from app.models.production_order import ProductionOrder
from app.models.stage import ProductionStage
from app.models.user import User
from app.routes.stickers import (
    get_sticker_sizes,
    sticker_decision,
    sticker_position,
    sticker_status,
)


class StickerInternalStageTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()
        self.client = self.app.test_client()

        admin = User(username="a", role=User.ROLE_ADMIN, is_active=True)
        admin.set_password("x")
        db.session.add(admin)

        pipe = Pipe(
            production_date=date(2026, 8, 20),
            ladle_id="L1", pipe_code="L1-P1", no_code="N1", arrange_pipe=1,
            diameter=800, pipe_class="K9",
            lab_decision="ACCEPT",
        )
        db.session.add(pipe)
        db.session.commit()
        self.admin_id = admin.id
        self.pipe_id = pipe.id
        self.stages = ProductionStage.active_names()
        self.delivery = ProductionStage.name_for_code("delivery")

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _pipe(self):
        return db.session.get(Pipe, self.pipe_id)

    def _accept_all_through(self, upto_index):
        """Record an accept on every stage up to (not including) upto_index."""
        for name in self.stages[:upto_index]:
            db.session.add(PipeStage(pipe_id=self.pipe_id, stage_name=name,
                                     decision="Accept"))
        db.session.commit()

    def _finish_every_stage(self):
        """Accept every stage the pipe answers to. Delivery is decided after
        the label is printed, so it stays blank."""
        for name in self.stages:
            if name == self.delivery:
                continue
            db.session.add(PipeStage(pipe_id=self.pipe_id, stage_name=name,
                                     decision="Accept"))
        db.session.commit()

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin_id)
            sess["_fresh"] = True

    # --- the verdict is untouched -------------------------------------------

    def test_the_verdict_is_the_same_in_both_modes(self):
        """The floor's label and the customer's label never disagree on PASS."""
        self._accept_all_through(2)
        verdict = sticker_decision(self._pipe())
        self.assertEqual(verdict, "PENDING")
        # sticker_decision takes no mode at all — the position is a separate row.
        self.assertEqual(sticker_decision(self._pipe()), verdict)

    def test_a_finished_pipe_still_reads_pass(self):
        self._finish_every_stage()
        self.assertEqual(sticker_decision(self._pipe()), "PASS")

    # --- where it is standing ------------------------------------------------

    def test_pending_names_the_first_stage_without_a_decision(self):
        """Not just any incomplete stage — the earliest one in production order."""
        self._accept_all_through(2)
        verdict, stage, _ = sticker_status(self._pipe())
        self.assertEqual(verdict, "PENDING")
        self.assertEqual(stage, self.stages[2])

    def test_the_first_stage_is_named_even_with_later_gaps(self):
        """Accepting stage 4 while 2 is still blank must not move the answer."""
        self._accept_all_through(2)
        db.session.add(PipeStage(pipe_id=self.pipe_id,
                                 stage_name=self.stages[4], decision="Accept"))
        db.session.commit()
        self.assertEqual(sticker_status(self._pipe())[1], self.stages[2])

    def test_a_hold_names_the_stage_holding_it(self):
        self._accept_all_through(2)
        db.session.add(PipeStage(pipe_id=self.pipe_id,
                                 stage_name=self.stages[2], decision="Hold"))
        db.session.commit()
        verdict, stage, _ = sticker_status(self._pipe())
        self.assertEqual(verdict, "NOT PASS (HOLD)")
        self.assertEqual(stage, self.stages[2])

    def test_a_reject_names_the_stage_that_rejected_it(self):
        self._accept_all_through(2)
        db.session.add(PipeStage(pipe_id=self.pipe_id,
                                 stage_name=self.stages[2], decision="Reject"))
        db.session.commit()
        verdict, stage, _ = sticker_status(self._pipe())
        self.assertEqual(verdict, "NOT PASS")
        self.assertEqual(stage, self.stages[2])

    def test_a_reject_outranks_a_hold_wherever_each_sits(self):
        """This used to walk an unordered set and return whichever it met
        first, so the same pipe could label itself either way between prints."""
        self._accept_all_through(1)
        db.session.add(PipeStage(pipe_id=self.pipe_id,
                                 stage_name=self.stages[1], decision="Hold"))
        db.session.add(PipeStage(pipe_id=self.pipe_id,
                                 stage_name=self.stages[3], decision="Reject"))
        db.session.commit()
        for _ in range(5):
            verdict, stage, _m = sticker_status(self._pipe())
            self.assertEqual(verdict, "NOT PASS")
            self.assertEqual(stage, self.stages[3])

    def test_a_lab_verdict_has_no_stage(self):
        """A lab reject is about the metal, not a position on the line."""
        pipe = self._pipe()
        pipe.lab_decision = "REJECT"
        db.session.commit()
        verdict, stage, _ = sticker_status(self._pipe())
        self.assertEqual(verdict, "NOT PASS")
        self.assertIsNone(stage)

    def test_a_pass_explains_no_verdict_with_a_stage(self):
        """Nothing is holding it, so no stage explains the PASS."""
        self._finish_every_stage()
        self.assertIsNone(sticker_status(self._pipe())[1])

    def test_a_finished_pipe_still_says_where_it_is_standing(self):
        """The internal print of a passed pipe used to be byte-identical to
        the external one — the whole mode picker looked broken, because the
        common case on a print run is a pipe that passed. A pipe that passed
        everything is still standing at the last stage it cleared."""
        self._finish_every_stage()
        cleared = [n for n in self.stages if n != self.delivery]
        self.assertEqual(
            sticker_position(self._pipe(), internal=True), cleared[-1]
        )

    def test_the_two_modes_differ_on_a_finished_pipe(self):
        self._finish_every_stage()
        self.assertNotEqual(
            sticker_position(self._pipe(), internal=True),
            sticker_position(self._pipe(), internal=False),
        )

    def test_the_machine_is_named_on_a_finished_pipe_too(self):
        machine = Machine(machine_code="ZN-1", machine_name="Zinc 1")
        db.session.add(machine)
        db.session.commit()
        self._finish_every_stage()
        last = [n for n in self.stages if n != self.delivery][-1]
        self._pipe().get_stage(last).machine_id = machine.id
        db.session.commit()
        self.assertEqual(
            sticker_position(self._pipe(), internal=True), f"{last} (ZN-1)"
        )

    def test_a_pipe_the_line_never_touched_is_queued_at_the_first_stage(self):
        """It has to be somewhere, and it is: at the head of the line."""
        pipe = self._pipe()
        pipe.lab_decision = "REJECT"
        db.session.commit()
        self.assertEqual(
            sticker_position(self._pipe(), internal=True), self.stages[0]
        )

    def test_a_pipe_still_waiting_on_the_lab_says_where_it_is(self):
        """The label that started this row: PENDING on its own tells the
        floor nothing it can act on. A WAITING lab result is not a position,
        so the verdict has no stage to offer — the internal print has to
        walk the line itself."""
        self._accept_all_through(2)
        pipe = self._pipe()
        pipe.lab_decision = "WAITING"
        db.session.commit()

        self.assertEqual(sticker_decision(self._pipe()), "PENDING")
        self.assertIsNone(sticker_status(self._pipe())[1])
        self.assertEqual(
            sticker_position(self._pipe(), internal=True), self.stages[2]
        )

    def test_a_lab_reject_still_says_where_the_pipe_is_standing(self):
        """The metal failed, but the pipe is a physical object sitting at a
        stage, and somebody has to go and pull it."""
        self._accept_all_through(2)
        pipe = self._pipe()
        pipe.lab_decision = "REJECT"
        db.session.commit()
        self.assertEqual(sticker_decision(self._pipe()), "NOT PASS")
        self.assertEqual(
            sticker_position(self._pipe(), internal=True), self.stages[2]
        )

    def test_a_finished_pipe_is_not_standing_at_delivery(self):
        """Delivery is decided after the label is printed, so an undecided
        Delivery is not a queue the pipe is waiting in."""
        self._finish_every_stage()
        self.assertNotEqual(
            sticker_position(self._pipe(), internal=True), self.delivery
        )

    def test_every_internal_print_answers_the_question(self):
        """Whatever state the pipe is in, an internal label that has seen the
        line at all names a stage."""
        self._accept_all_through(2)
        for lab in ("ACCEPT", "WAITING", "HOLD", "REJECT", "BLOCKED", None):
            pipe = self._pipe()
            pipe.lab_decision = lab
            db.session.commit()
            self.assertIsNotNone(
                sticker_position(self._pipe(), internal=True),
                f"no position for lab_decision={lab!r}",
            )

    # --- the machine ---------------------------------------------------------

    def test_the_machine_is_named_when_the_stage_records_one(self):
        machine = Machine(machine_code="CCM-2", machine_name="Caster 2")
        db.session.add(machine)
        db.session.commit()
        self._accept_all_through(2)
        db.session.add(PipeStage(pipe_id=self.pipe_id,
                                 stage_name=self.stages[2], decision="Hold",
                                 machine_id=machine.id))
        db.session.commit()
        self.assertEqual(
            sticker_position(self._pipe(), internal=True),
            f"{self.stages[2]} (CCM-2)",
        )

    def test_a_stage_with_no_machine_names_only_the_stage(self):
        """Most stages record none — Pipe.machine_id is unused."""
        self._accept_all_through(2)
        self.assertEqual(
            sticker_position(self._pipe(), internal=True), self.stages[2]
        )

    # --- external must never leak it ----------------------------------------

    def test_external_prints_carry_no_position(self):
        self._accept_all_through(2)
        self.assertIsNone(sticker_position(self._pipe(), internal=False))

    def test_internal_is_the_default_when_no_mode_is_given(self):
        """The floor prints far more labels than ship, so the label answers
        "where is it" by default; the customer print is the explicit choice."""
        self._accept_all_through(2)
        self._login()
        r = self.client.get(f"/stickers/preview/{self.pipe_id}")
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        self.assertIn("Standing at:", html)

    def test_an_unknown_mode_is_treated_as_internal(self):
        self._accept_all_through(2)
        self._login()
        html = self.client.get(
            f"/stickers/preview/{self.pipe_id}?mode=whatever"
        ).get_data(as_text=True)
        self.assertIn("Standing at:", html)

    def test_explicit_external_mode_carries_no_position(self):
        """External is now the explicit opt-out — the only value that must
        not put the line's internals on a customer's pipe."""
        self._accept_all_through(2)
        self._login()
        html = self.client.get(
            f"/stickers/preview/{self.pipe_id}?mode=external"
        ).get_data(as_text=True)
        self.assertNotIn("Standing at:", html)

    def test_wants_internal_true_when_mode_missing(self):
        from app.routes.stickers import wants_internal

        with self.app.test_request_context("/stickers/preview/1"):
            self.assertTrue(wants_internal())

    def test_wants_internal_false_for_external(self):
        from app.routes.stickers import wants_internal

        with self.app.test_request_context("/stickers/preview/1?mode=external"):
            self.assertFalse(wants_internal())

    def test_wants_internal_external_is_case_insensitive_and_stripped(self):
        from app.routes.stickers import wants_internal

        with self.app.test_request_context("/stickers/preview/1?mode=%20EXTERNAL%20"):
            self.assertFalse(wants_internal())

    def test_wants_internal_true_for_unknown_mode(self):
        from app.routes.stickers import wants_internal

        with self.app.test_request_context("/stickers/preview/1?mode=whatever"):
            self.assertTrue(wants_internal())

    def test_internal_mode_reports_the_position_on_the_preview(self):
        self._accept_all_through(2)
        self._login()
        html = self.client.get(
            f"/stickers/preview/{self.pipe_id}?mode=internal"
        ).get_data(as_text=True)
        self.assertIn("Standing at:", html)
        self.assertIn(self.stages[2], html)

    # --- the card the labels are actually printed on ------------------------

    def test_the_card_size_matches_the_stock(self):
        """The stock is a 100x100mm card. 100x60 left the bottom 40% blank."""
        from app.routes.stickers import get_sticker_sizes

        self.assertEqual(get_sticker_sizes()["card"], (100, 90))

    def test_the_card_stops_short_of_the_paper_edge(self):
        """The cards are hand-cut; a border drawn at the edge gets trimmed off."""
        from app.routes.stickers import get_sticker_sizes

        _w, h = get_sticker_sizes()["card"]
        self.assertLessEqual(h, 95, "no margin left for the trim")
        self.assertGreaterEqual(h, 85, "back to wasting the card")

    def test_the_card_is_what_a_print_gets_by_default(self):
        """Nobody should have to know to pick the size the floor actually uses."""
        from app.routes.stickers import DEFAULT_STICKER_SIZE, get_sticker_sizes

        self.assertEqual(DEFAULT_STICKER_SIZE, "card")
        self._login()
        r = self.client.get(f"/stickers/generate/{self.pipe_id}")
        self.assertEqual(r.status_code, 200)

        from PIL import Image
        from io import BytesIO
        w_mm, h_mm = get_sticker_sizes()["card"]
        img = Image.open(BytesIO(r.get_data()))
        self.assertAlmostEqual(img.width / img.height, w_mm / h_mm, places=2)

    def test_the_tall_card_gets_a_third_description_line(self):
        """A product description is one long sentence; on the short labels it
        is cut at two lines, and the card has the room for a third."""
        from app.routes.stickers import _load_font, _wrap
        from PIL import Image, ImageDraw

        text = (
            "Ductile Iron - Centrifugally Casted Socket Spigot DN700 K9 Zn "
            "130gm/m2 length 6 m x L internal finish Cement SRC External finish"
        )
        draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        font = _load_font(30)
        self.assertEqual(len(_wrap(draw, text, font, 900, max_lines=3)), 3)

    def test_no_print_path_carries_its_own_default_size(self):
        """Every button that prints a label must reach for the same default.

        The order screen's batch print kept a literal 'medium' of its own, so
        the same pipe came out 100x60 from one button and 100x90 from another
        — one card in the pile not matching the rest.
        """
        import re
        from pathlib import Path

        import app as app_pkg

        root = Path(app_pkg.__file__).parent
        offenders = []
        for path in (root / "routes").glob("*.py"):
            for n, line in enumerate(path.read_text().splitlines(), 1):
                if re.search(r"""\.get\(\s*['"]size['"]\s*,\s*['"]\w+['"]""", line):
                    offenders.append(f"{path.name}:{n}: {line.strip()}")

        template_paths = [
            root / "templates" / "production_orders" / "print_stickers.html"
        ]
        template_paths += sorted((root / "templates" / "stickers").glob("*.html"))
        for path in template_paths:
            for n, line in enumerate(path.read_text().splitlines(), 1):
                if re.search(r"size=(small|medium|large|card|gcp)\b", line):
                    offenders.append(f"{path.name}:{n}: {line.strip()}")

        self.assertEqual(
            offenders, [],
            "a print path hardcodes its own default size instead of "
            "DEFAULT_STICKER_SIZE:\n" + "\n".join(offenders),
        )

    def test_print_stickers_screen_offers_a_mode_and_the_verdict_legend(self):
        """The order's own print screen used to have no mode picker at all
        and a hardcoded '?size=medium' href — this is the one screen the
        floor actually prints an order's stickers from."""
        from app.models.production_order import ProductionOrder

        order = ProductionOrder(
            order_number="PO-STICKER-1", customer_name="ACME", target_quantity=1,
        )
        db.session.add(order)
        db.session.commit()

        self._login()
        html = self.client.get(
            f"/orders/{order.id}/print-stickers"
        ).get_data(as_text=True)
        self.assertIn('name="mode"', html)
        # the verdict legend
        self.assertIn("PASS", html)
        self.assertIn("PENDING", html)
        self.assertIn("HOLD", html)

    # --- bulk printing without a detour through a saved file ----------------

    def test_bulk_offers_a_print_and_not_only_a_pdf(self):
        """Bulk printing only ever handed back a PDF download: save a file,
        find it, open it, then print. The floor's normal case is a stack of
        cards now."""
        self._login()
        html = self.client.get("/stickers/bulk").get_data(as_text=True)
        self.assertIn("/stickers/bulk/print", html)
        self.assertIn("bi-printer", html)
        # The PDF is still there — it is the copy that gets kept and sent.
        self.assertIn("bi-file-pdf", html)

    def test_bulk_accepts_multiple_orders_in_the_shared_selection(self):
        first = ProductionOrder(
            order_number="PO-1", target_quantity=1, order_date=date(2026, 9, 1)
        )
        second = ProductionOrder(
            order_number="PO-2", target_quantity=1, order_date=date(2026, 9, 1)
        )
        db.session.add_all([first, second])
        db.session.flush()
        self._pipe().production_order_id = first.id
        other = Pipe(
            production_order_id=second.id,
            production_date=date(2026, 9, 2),
            pipe_code="L2-P1",
            no_code="N2",
            arrange_pipe=1,
            diameter=600,
            pipe_class="K9",
            lab_decision="ACCEPT",
        )
        db.session.add(other)
        db.session.commit()

        self._login()
        html = self.client.get(
            f"/stickers/bulk?order_ids={first.id}&order_ids={second.id}"
        ).get_data(as_text=True)
        self.assertIn("PO-1", html)
        self.assertIn("PO-2", html)
        self.assertIn("L1-P1", html)
        self.assertIn("L2-P1", html)
        self.assertIn("pipe_selection.js", html)

    def test_bulk_direct_lookup_accepts_an_exact_warehouse_barcode(self):
        pipe = self._pipe()
        pipe.warehouse_barcode = "998877"
        db.session.commit()

        self._login()
        body = self.client.get("/stickers/search?q=998877").get_json()
        self.assertEqual(len(body["pipes"]), 1)
        self.assertEqual(body["pipes"][0]["id"], self.pipe_id)
        self.assertEqual(body["pipes"][0]["status"], "PENDING")
        self.assertTrue(body["pipes"][0]["selectable"])

    def test_no_js_bulk_lookup_returns_an_html_basket_and_preserves_filters(self):
        pipe = self._pipe()
        pipe.warehouse_barcode = "998877"
        db.session.commit()

        self._login()
        response = self.client.get(
            "/stickers/bulk?pipe_lookup=998877&customer=KeepMe&mode=external"
        )
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("data-pipe-selection", html)
        self.assertIn("data-server-selected", html)
        self.assertIn("L1-P1", html)
        self.assertIn('name="customer" value="KeepMe"', html)
        self.assertIn('name="mode" value="external"', html)
        self.assertNotEqual(response.mimetype, "application/json")

    def test_existing_sticker_search_by_ladle_is_preserved(self):
        pipe = self._pipe()
        pipe.ladle_id = "HEAT-ONLY-77"
        db.session.commit()

        self._login()
        body = self.client.get("/stickers/search?q=HEAT-ONLY-77").get_json()
        self.assertEqual([item["id"] for item in body["pipes"]], [self.pipe_id])

    def test_the_print_sheet_carries_the_selected_labels(self):
        self._accept_all_through(2)
        self._login()
        r = self.client.post("/stickers/bulk/print",
                             data={"pipe_ids": [str(self.pipe_id)]})
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        self.assertIn("data:image/png;base64,", html)
        self.assertIn("window.print()", html)

    def test_repeated_bulk_printing_does_not_mutate_pipe_delivery_or_certificates(self):
        self._accept_all_through(2)
        self._login()
        before = (
            PipeStage.query.count(),
            Certificate.query.count(),
            self._pipe().final_decision_value,
            self._pipe().updated_at,
        )

        for _ in range(2):
            response = self.client.post(
                "/stickers/bulk/print",
                data={"pipe_ids": [str(self.pipe_id)], "size": "card", "mode": "internal"},
            )
            self.assertEqual(response.status_code, 200)

        db.session.expire_all()
        self.assertEqual(
            (
                PipeStage.query.count(),
                Certificate.query.count(),
                self._pipe().final_decision_value,
                self._pipe().updated_at,
            ),
            before,
        )

    def test_the_print_sheet_honours_the_mode(self):
        """Same route, both modes, and internal is the one that says where."""
        self._accept_all_through(2)
        self._login()
        sheets = {}
        for mode in ("external", "internal"):
            sheets[mode] = self.client.post(
                "/stickers/bulk/print",
                data={"pipe_ids": [str(self.pipe_id)], "mode": mode},
            ).get_data(as_text=True)
        self.assertNotEqual(sheets["external"], sheets["internal"])

    def test_the_print_sheet_is_one_label_per_page(self):
        """The paper in a label printer IS the label: it feeds one, prints
        it, feeds the next. A sheet that tiles them onto A4 is the PDF's
        job, not the printer's."""
        self._accept_all_through(2)
        self._login()
        html = self.client.post(
            "/stickers/bulk/print",
            data={"pipe_ids": [str(self.pipe_id)], "size": "card"},
        ).get_data(as_text=True)
        w, h = get_sticker_sizes()["card"]
        self.assertIn(f"size: {w}mm {h}mm", html)
        self.assertIn("page-break-after: always", html)
        self.assertNotIn("size: A4", html)

    def test_the_bulk_list_shows_what_the_label_will_say(self):
        """The Decision column is the lab's verdict on the metal; the label
        only reads PASS once every stage has accepted. A row reading ACCEPT
        that prints "PENDING - Annealing" looked like a broken label."""
        self._accept_all_through(2)
        pipe = self._pipe()
        pipe.final_decision_value = "ACCEPT"
        db.session.commit()
        self._login()
        html = self.client.get("/stickers/bulk").get_data(as_text=True)
        self.assertIn("ACCEPT", html)          # the lab's word
        self.assertIn("PENDING", html)         # the label's word
        self.assertIn(self.stages[2], html)    # and where it is standing

    def test_the_print_sheet_needs_a_selection(self):
        self._login()
        r = self.client.post("/stickers/bulk/print", data={})
        self.assertEqual(r.status_code, 302)

    def test_pdf_generation_rejects_invalid_and_missing_pipe_ids(self):
        self._login()
        before = (PipeStage.query.count(), Certificate.query.count())
        for bad_id in ("bad-id", str(self.pipe_id + 9999)):
            with self.subTest(bad_id=bad_id):
                response = self.client.post(
                    "/stickers/bulk/generate",
                    data={"pipe_ids": [str(self.pipe_id), bad_id]},
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn(bad_id, response.get_json()["error"])
                self.assertNotEqual(response.mimetype, "application/pdf")
                self.assertEqual(
                    (PipeStage.query.count(), Certificate.query.count()), before
                )

    def test_print_sheet_rejects_invalid_and_missing_pipe_ids(self):
        self._login()
        for bad_id in ("bad-id", str(self.pipe_id + 9999)):
            with self.subTest(bad_id=bad_id):
                response = self.client.post(
                    "/stickers/bulk/print",
                    data={"pipe_ids": [str(self.pipe_id), bad_id]},
                    follow_redirects=True,
                )
                body = response.get_data(as_text=True)
                self.assertEqual(response.status_code, 200)
                self.assertIn(bad_id, body)
                self.assertNotIn("data:image/png;base64,", body)

    def test_json_batch_rejects_invalid_and_missing_pipe_ids(self):
        self._login()
        for bad_id in ("bad-id", self.pipe_id + 9999):
            with self.subTest(bad_id=bad_id):
                response = self.client.post(
                    "/stickers/batch",
                    json={"pipe_ids": [self.pipe_id, bad_id]},
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn(str(bad_id), response.get_json()["error"])

    def test_json_batch_deduplicates_and_preserves_posted_order(self):
        other = Pipe(
            production_date=date(2026, 8, 21),
            ladle_id="L2",
            pipe_code="L2-P1",
            no_code="N2",
            arrange_pipe=1,
            diameter=600,
            pipe_class="K9",
            lab_decision="ACCEPT",
        )
        db.session.add(other)
        db.session.commit()

        self._login()
        response = self.client.post(
            "/stickers/batch",
            json={"pipe_ids": [other.id, self.pipe_id, other.id]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["no_code"] for row in response.get_json()["stickers"]],
            ["N2", "N1"],
        )

    def test_both_modes_render_a_real_png(self):
        """The extra row must not break the renderer at any size."""
        self._accept_all_through(2)
        self._login()
        for mode in ("external", "internal"):
            for size in ("small", "medium", "large"):
                r = self.client.get(
                    f"/stickers/generate/{self.pipe_id}?size={size}&mode={mode}"
                )
                self.assertEqual(r.status_code, 200, f"{mode}/{size}")
                self.assertEqual(r.mimetype, "image/png")
                self.assertTrue(r.get_data().startswith(b"\x89PNG"),
                                f"{mode}/{size} is not a PNG")


if __name__ == "__main__":
    unittest.main()
