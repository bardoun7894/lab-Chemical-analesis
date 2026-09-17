"""Andon board — /reports/andon.

The wall screen aggregates decisions the stage screens already record. What it
must never do is invent a number: an age with no timestamp behind it reads
"unknown", and a yield with nothing decided reads "—".
"""

import unittest
from datetime import date, datetime, time, timedelta

from flask import g

from app import create_app, db
from app.models.chemical import ChemicalAnalysis
from app.models.permission import seed_default_permissions
from app.models.pipe import Pipe, PipeStage
from app.models.user import User
from app.services import andon_service


class AndonBoardTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()
        self.client = self.app.test_client()

        self.admin = User(username="admin", full_name="Admin",
                          role="super_admin", is_active=True)
        self.admin.set_password("x")
        db.session.add(self.admin)
        db.session.commit()
        self.today = date.today()

        # Every pipe below is poured from ladle L1, and pipes.ladle_id is a
        # foreign key to chemical_analyses.ladle_id. SQLite never enforced it,
        # so these tests used to run against a pipe whose ladle did not exist —
        # a row production could not hold. Postgres rejects it outright.
        db.session.add(
            ChemicalAnalysis(test_date=self.today, ladle_no=1, ladle_id="L1")
        )
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self, user=None):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str((user or self.admin).id)
            sess["_fresh"] = True
        g.pop("_login_user", None)

    def _pipe(self, code, lab=None, final=None, day=None):
        p = Pipe(production_date=day or self.today, ladle_id="L1",
                 pipe_code=code, no_code=code, arrange_pipe=1, diameter=300,
                 pipe_class="K9", lab_decision=lab, final_decision_value=final)
        db.session.add(p)
        db.session.commit()
        return p

    def _stage(self, pipe, name, decision, when=None):
        st = PipeStage(pipe_id=pipe.id, stage_name=name, decision=decision)
        if when:
            st.stage_date = when.date()
            st.stage_time = when.time()
        db.session.add(st)
        db.session.commit()
        return st

    # --- WIP ------------------------------------------------------------

    def test_wip_counts_the_last_decided_stage(self):
        p = self._pipe("P1")
        self._stage(p, "CCM", "Accept")
        self._stage(p, "Annealing", "Accept")
        board = andon_service.board()
        at = {r["stage"]: r["count"] for r in board["wip"]}
        self.assertEqual(at["Annealing"], 1)
        self.assertEqual(at["CCM"], 0)

    def test_a_pipe_with_no_decision_queues_at_the_first_stage(self):
        self._pipe("P1")
        board = andon_service.board()
        first = board["wip"][0]
        self.assertEqual(first["count"], 1)

    def test_an_undecided_stage_row_does_not_advance_the_pipe(self):
        p = self._pipe("P1")
        self._stage(p, "CCM", "Accept")
        self._stage(p, "Annealing", "")  # opened, not decided
        at = {r["stage"]: r["count"] for r in andon_service.board()["wip"]}
        self.assertEqual(at["CCM"], 1)
        self.assertEqual(at["Annealing"], 0)

    def test_accepted_pipes_leave_the_board(self):
        self._pipe("P1", lab="ACCEPT", final="ACCEPT")
        self.assertEqual(andon_service.board()["open_count"], 0)

    # --- stopped --------------------------------------------------------

    def test_stopped_ranks_fail_before_hold(self):
        self._pipe("HOLDY", lab="HOLD")
        self._pipe("BAD", lab="REJECT")
        codes = [r["code"] for r in andon_service.board()["stopped"]]
        self.assertEqual(codes, ["BAD", "HOLDY"])

    def test_a_moving_pipe_is_not_listed(self):
        p = self._pipe("P1", lab="ACCEPT")
        self._stage(p, "CCM", "Accept")
        self.assertEqual(andon_service.board()["stopped"], [])

    def test_amber_stage_decisions_stop_the_pipe(self):
        # Rework/Retest are invisible to PipeStage.classify_decision; the
        # board must still show them as stopped work
        p = self._pipe("P1", lab="ACCEPT")
        self._stage(p, "Annealing", "Rework")
        stopped = andon_service.board()["stopped"]
        self.assertEqual(len(stopped), 1)
        self.assertEqual(stopped[0]["state"], "hold")
        self.assertEqual(stopped[0]["source"], "Annealing")

    def test_age_comes_from_the_stage_that_stopped_it(self):
        p = self._pipe("P1", lab="HOLD")
        self._stage(p, "Annealing", "Hold",
                    when=datetime.utcnow() - timedelta(hours=10))
        row = andon_service.board()["stopped"][0]
        self.assertEqual(row["source"], "Annealing")
        self.assertAlmostEqual(row["age_hours"], 10, delta=0.5)

    def test_age_is_unknown_rather_than_zero_without_a_timestamp(self):
        self._pipe("P1", lab="REJECT")  # lab decision carries no timestamp
        row = andon_service.board()["stopped"][0]
        self.assertIsNone(row["age_hours"])
        self.assertFalse(row["overdue"])

    def test_overdue_uses_the_configured_threshold(self):
        p = self._pipe("P1", lab="HOLD")
        self._stage(p, "Annealing", "Hold",
                    when=datetime.utcnow() - timedelta(hours=5))
        self.assertEqual(andon_service.board(alert_hours=8)["overdue"], [])
        self.assertEqual(len(andon_service.board(alert_hours=4)["overdue"]), 1)

    def test_the_worst_stage_wins_the_label(self):
        p = self._pipe("P1", lab="HOLD")
        self._stage(p, "Annealing", "Hold")
        self._stage(p, "Zinc", "Reject")
        row = andon_service.board()["stopped"][0]
        self.assertEqual(row["state"], "fail")
        self.assertEqual(row["source"], "Zinc")

    # --- today ----------------------------------------------------------

    def test_today_counts_and_yield(self):
        self._pipe("A", lab="ACCEPT")
        self._pipe("B", lab="ACCEPT")
        self._pipe("C", lab="REJECT")
        self._pipe("D", lab="HOLD")
        t = andon_service.yield_today()
        self.assertEqual((t["produced"], t["pass"], t["fail"], t["hold"]),
                         (4, 2, 1, 1))
        self.assertEqual(t["yield_pct"], 66.7)  # hold is not in the denominator

    def test_yield_is_none_when_nothing_is_decided(self):
        self._pipe("A")
        self.assertIsNone(andon_service.yield_today()["yield_pct"])

    def test_yesterday_is_not_counted_today(self):
        self._pipe("OLD", lab="ACCEPT", day=self.today - timedelta(days=1))
        self.assertEqual(andon_service.yield_today()["produced"], 0)

    # --- routes ---------------------------------------------------------

    def test_board_and_panel_render(self):
        self._login()
        p = self._pipe("P1", lab="HOLD")
        self._stage(p, "Annealing", "Hold",
                    when=datetime.utcnow() - timedelta(hours=20))
        html = self.client.get("/reports/andon").get_data(as_text=True)
        self.assertIn("andon-alarm", html)   # 20h > the 8h default
        self.assertIn("P1", html)
        self.assertEqual(self.client.get("/reports/andon/panel").status_code, 200)

    def test_bad_hours_falls_back_to_the_default(self):
        self._login()
        resp = self.client.get("/reports/andon?hours=bogus")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(str(andon_service.DEFAULT_ALERT_HOURS),
                      resp.get_data(as_text=True))

    def test_warehouse_role_is_denied(self):
        store = User(username="wh", full_name="Store", role="warehouse",
                     is_active=True)
        store.set_password("x")
        db.session.add(store)
        db.session.commit()
        self._login(store)
        self.assertIn(self.client.get("/reports/andon").status_code, (302, 403))


if __name__ == "__main__":
    unittest.main()
