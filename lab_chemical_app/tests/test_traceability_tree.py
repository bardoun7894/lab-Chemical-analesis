"""Traceability canvas — /reports/traceability-tree.

The graph must agree with the decision engine: the ladle decision picks the
sampling branch, mechanical results cascade per §4.2 (fail → HOLD, never
auto-scrap), amber stage decisions read as hold not "not started", and rework
loops are real backward wires.
"""

import unittest
from datetime import date

from flask import g

from app import create_app, db
from app.models.chemical import ChemicalAnalysis
from app.models.mechanical import MechanicalTest
from app.models.permission import seed_default_permissions
from app.models.pipe import Pipe, PipeStage
from app.models.production_order import ProductionOrder
from app.models.user import User
from app.services import nonconformance_service as nc
from app.services import traceability_service as ts


class StateOfTestCase(unittest.TestCase):
    """state_of is the single classifier every node colour comes from."""

    def test_reject_vocabulary_is_fail(self):
        for v in nc.REJECT_DECISIONS - {"BLOCKED", "Blocked"}:
            self.assertEqual(ts.state_of(v), "fail", v)

    def test_hold_vocabulary_is_hold(self):
        for v in nc.HOLD_DECISIONS:
            self.assertEqual(ts.state_of(v), "hold", v)

    def test_blocked_and_waiting_are_their_own_states(self):
        self.assertEqual(ts.state_of("BLOCKED"), "blocked")
        self.assertEqual(ts.state_of("Blocked"), "blocked")
        self.assertEqual(ts.state_of("WAITING"), "waiting")

    def test_ladle_inspect_levels(self):
        self.assertEqual(ts.state_of("فحص أخيرة فقط"), "pass")
        self.assertEqual(ts.state_of("فحص أولى وأخيرة"), "pass")
        self.assertEqual(ts.state_of("فحص الشحنة 100%"), "pass")
        self.assertEqual(ts.state_of("تالف"), "fail")

    def test_case_insensitive_like_the_tracker_fix(self):
        # lowercase 'accept' from a misconfigured option broke the tracker
        # colours once — the canvas must not repeat that
        self.assertEqual(ts.state_of("accept"), "pass")
        self.assertEqual(ts.state_of("hold"), "hold")
        self.assertEqual(ts.state_of("rework"), "hold")

    def test_amber_is_hold_not_pending(self):
        # the classify_decision regression this resolver exists to avoid
        for v in ("Rework", "Retest", "Resample", "Reheat treatment", "DownGrade"):
            self.assertEqual(ts.state_of(v), "hold", v)

    def test_empty_is_none(self):
        self.assertEqual(ts.state_of(None), "none")
        self.assertEqual(ts.state_of(""), "none")


class LayoutTestCase(unittest.TestCase):
    X0 = ts.PAD + ts.BAND_LABEL_W

    def test_longest_path_wins(self):
        nodes = [{"id": a} for a in ("a", "b", "c", "d")]
        edges = [
            {"from": "a", "to": "b"}, {"from": "b", "to": "c"},
            {"from": "a", "to": "d"}, {"from": "c", "to": "d"},
        ]
        pos, _bands = ts.layout(nodes, edges)
        # d has predecessors at depth 0 (a) and depth 2 (c) — deepest wins
        self.assertEqual(pos["d"][0], self.X0 + 3 * ts.COL_W)

    def test_same_depth_stacks_into_lanes(self):
        nodes = [{"id": a} for a in ("root", "x", "y")]
        edges = [{"from": "root", "to": "x"}, {"from": "root", "to": "y"}]
        pos, _bands = ts.layout(nodes, edges)
        self.assertEqual(pos["x"][0], pos["y"][0])
        self.assertNotEqual(pos["x"][1], pos["y"][1])

    def test_backward_edge_terminates(self):
        nodes = [{"id": a} for a in ("a", "b")]
        edges = [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}]
        pos, _bands = ts.layout(nodes, edges)  # must not loop forever
        self.assertEqual(pos["a"][0], self.X0)
        self.assertEqual(pos["b"][0], self.X0 + ts.COL_W)

    def test_bands_stack_and_do_not_share_lanes(self):
        """A heat's own children must sit inside its band. With one global
        lane counter a seven-heat order came out 2894px tall and a ladle's
        children landed nowhere near it."""
        nodes = [
            {"id": "l1", "band": "A"}, {"id": "p1", "band": "A"},
            {"id": "l2", "band": "B"}, {"id": "p2", "band": "B"},
        ]
        edges = [{"from": "l1", "to": "p1"}, {"from": "l2", "to": "p2"}]
        pos, bands = ts.layout(nodes, edges)
        # each band starts its own lane count, so both ladles sit at lane 0
        self.assertEqual(pos["l1"][1], ts.PAD)
        self.assertLess(pos["l1"][1], pos["l2"][1])
        # a child sits on its parent's row, not stacked under a sibling band
        self.assertEqual(pos["l1"][1], pos["p1"][1])
        self.assertEqual(pos["l2"][1], pos["p2"][1])
        self.assertEqual([b["key"] for b in bands], ["A", "B"])


class GraphTestCase(unittest.TestCase):
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

        self.order = ProductionOrder(order_number="PO-1", target_quantity=6)
        db.session.add(self.order)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self, user=None):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str((user or self.admin).id)
            sess["_fresh"] = True
        # setUp holds an app context open, so Flask-Login's cached user on `g`
        # survives between requests — drop it or the first login sticks
        # (same pattern as tests/test_permissions.py).
        g.pop("_login_user", None)

    def _ladle(self, decision, ladle_id="111082026"):
        ladle = ChemicalAnalysis(test_date=date(2026, 8, 29), ladle_no=1,
                                 ladle_id=ladle_id, decision=decision,
                                 production_order_id=self.order.id)
        db.session.add(ladle)
        db.session.commit()
        return ladle

    def _pipes(self, ladle, specs):
        """specs: list of (lab_decision, role) tuples."""
        pipes = []
        for i, (lab, role) in enumerate(specs):
            p = Pipe(production_date=date(2026, 8, 29),
                     ladle_id=ladle.ladle_id, pipe_code=f"P{i + 1}",
                     no_code=f"N{i + 1}", arrange_pipe=i + 1, diameter=300,
                     pipe_class="K9", production_order_id=self.order.id,
                     lab_decision=lab, mechanical_test_role=role)
            db.session.add(p)
            pipes.append(p)
        db.session.commit()
        return pipes

    @staticmethod
    def _node(graph, nid):
        return next((n for n in graph["nodes"] if n["id"] == nid), None)

    def test_resolve_root_from_every_code(self):
        ladle = self._ladle("فحص أخيرة فقط")
        pipes = self._pipes(ladle, [("ACCEPT", "LAST")])
        pipes[0].warehouse_barcode = "9990001"
        db.session.commit()

        expected = {"PO-1": "order", ladle.ladle_id: "ladle",
                    "P1": "pipe", "9990001": "pipe"}
        for q, kind in expected.items():
            found, obj, focus = ts.resolve_root(q)
            self.assertEqual(found, kind, q)
            self.assertIsNotNone(obj, q)
            self.assertTrue(focus, q)

    def test_rejected_ladle_shows_one_decision_node(self):
        """One node carrying the scheme actually taken. Drawing all four per
        heat put 21 dim boxes on a seven-heat order and buried the real path;
        the alternatives live in the inspector now."""
        ladle = self._ladle("تالف")
        self._pipes(ladle, [("BLOCKED", None), ("BLOCKED", None)])

        graph = ts.build_graph(order=self.order)
        scen = [n for n in graph["nodes"] if n["kind"] == "scenario"]
        self.assertEqual(len(scen), 1)
        self.assertEqual(scen[0]["scenario"], "REJECT")
        self.assertEqual(scen[0]["state"], "fail")
        self.assertFalse([n for n in graph["nodes"] if n.get("dim")])

        group = self._node(graph, f"pipes-{ladle.id}")
        self.assertEqual(group["state"], "blocked")

    def test_the_untaken_schemes_are_in_the_inspector(self):
        ladle = self._ladle("فحص أخيرة فقط")
        self._pipes(ladle, [("ACCEPT", "LAST")])
        d = ts.node_detail("scenario", ladle.id)
        self.assertEqual(len(d["options"]), 4)
        taken = [o for o in d["options"] if o["taken"]]
        self.assertEqual(len(taken), 1)
        self.assertIn("Last only", taken[0]["label"])

    def test_each_heat_gets_its_own_band(self):
        a = self._ladle("فحص أخيرة فقط", ladle_id="111082026")
        b = self._ladle("فحص أخيرة فقط", ladle_id="211082026")
        self._pipes(a, [("ACCEPT", None)])
        self._pipes(b, [("ACCEPT", None)])
        graph = ts.build_graph(order=self.order)
        bands = {n["band"] for n in graph["nodes"] if n["kind"] == "ladle"}
        self.assertEqual(bands, {f"ladle-{a.id}", f"ladle-{b.id}"})
        keys = [x["key"] for x in graph["bands"]]
        self.assertIn("__order__", keys)

    def test_an_expanded_pipe_gets_its_own_lane(self):
        """Stage chains of two expanded pipes must not collide."""
        ladle = self._ladle("فحص أخيرة فقط")
        pipes = self._pipes(ladle, [("ACCEPT", None), ("ACCEPT", None)])
        for p in pipes:
            db.session.add(PipeStage(pipe_id=p.id, stage_name="CCM",
                                     decision="Accept"))
        db.session.commit()

        graph = ts.build_graph(
            order=self.order,
            expand=[f"pipe-{pipes[0].id}", f"pipe-{pipes[1].id}"])
        by_id = {n["id"]: n for n in graph["nodes"]}
        s1 = by_id[f"stage-{pipes[0].id}-CCM"]
        s2 = by_id[f"stage-{pipes[1].id}-CCM"]
        self.assertNotEqual(s1["band"], s2["band"])
        self.assertNotEqual((s1["x"], s1["y"]), (s2["x"], s2["y"]))

    def test_last_only_fail_is_hold_never_fail(self):
        # §4.2 — a mechanical fail on a sample-tested ladle holds the ladle
        ladle = self._ladle("فحص أخيرة فقط")
        self._pipes(ladle, [("HOLD", "ANY"), ("HOLD", "ANY"), ("HOLD", "LAST")])
        db.session.add(MechanicalTest(test_date=date(2026, 8, 29),
                                      ladle_id=ladle.ladle_id,
                                      pipe_code="P3", decision="REJECT",
                                      status="ACTIVE"))
        db.session.commit()

        graph = ts.build_graph(order=self.order)
        group = self._node(graph, f"pipes-{ladle.id}")
        self.assertEqual(group["counts"]["hold"], 3)
        self.assertEqual(group["counts"]["fail"], 0)
        self.assertEqual(group["state"], "hold")
        # the failed test loops the material back to the ladle
        loops = [e for e in graph["edges"] if e["loop"]]
        self.assertTrue(any(e["to"] == f"ladle-{ladle.id}" for e in loops))

    def test_superseded_mechanical_test_not_drawn(self):
        ladle = self._ladle("فحص أخيرة فقط")
        self._pipes(ladle, [("ACCEPT", "LAST")])
        old = MechanicalTest(test_date=date(2026, 8, 28),
                             ladle_id=ladle.ladle_id, pipe_code="P1",
                             decision="REJECT", status="SUPERSEDED")
        new = MechanicalTest(test_date=date(2026, 8, 29),
                             ladle_id=ladle.ladle_id, pipe_code="P1",
                             decision="ACCEPT", status="ACTIVE")
        db.session.add_all([old, new])
        db.session.commit()

        graph = ts.build_graph(order=self.order)
        mech_ids = [n["id"] for n in graph["nodes"] if n["kind"] == "mechanical"]
        self.assertEqual(mech_ids, [f"mech-{new.id}"])

    def test_rework_stage_is_hold_with_backward_wire(self):
        ladle = self._ladle("فحص أخيرة فقط")
        pipes = self._pipes(ladle, [("ACCEPT", "LAST")])
        db.session.add(PipeStage(pipe_id=pipes[0].id, stage_name="CCM",
                                 decision="Accept"))
        db.session.add(PipeStage(pipe_id=pipes[0].id, stage_name="Annealing",
                                 decision="Rework"))
        db.session.commit()

        graph = ts.build_graph(order=self.order,
                               expand=[f"pipe-{pipes[0].id}"])
        stage = self._node(graph, f"stage-{pipes[0].id}-Annealing")
        self.assertEqual(stage["state"], "hold")  # not "none"
        loops = [e for e in graph["edges"]
                 if e["loop"] and e["from"] == stage["id"]]
        self.assertEqual(len(loops), 1)
        self.assertEqual(loops[0]["to"], f"stage-{pipes[0].id}-CCM")
        self.assertEqual(loops[0]["label"], "Rework")

    def test_group_counts_match_pipe_states(self):
        ladle = self._ladle("فحص أخيرة فقط")
        self._pipes(ladle, [("ACCEPT", "ANY"), ("ACCEPT", "ANY"),
                            ("HOLD", "ANY"), ("REJECT", "LAST")])
        graph = ts.build_graph(order=self.order)
        counts = self._node(graph, f"pipes-{ladle.id}")["counts"]
        self.assertEqual((counts["pass"], counts["hold"], counts["fail"]),
                         (2, 1, 1))

    def test_issues_ranked_worst_first_and_resolvable(self):
        ladle = self._ladle("فحص أخيرة فقط")
        pipes = self._pipes(ladle, [("REJECT", None), ("HOLD", None)])
        graph = ts.build_graph(order=self.order,
                               expand=[f"ladle-{ladle.id}"])
        sev = [i["severity"] for i in graph["issues"]]
        self.assertEqual(sev, sorted(sev, key=lambda s: ts._SEVERITY[s]))
        node_ids = {n["id"] for n in graph["nodes"]}
        for i in graph["issues"]:
            self.assertIn(i["node"], node_ids)

    def test_problems_only_keeps_the_ancestry_lit(self):
        ladle = self._ladle("فحص أخيرة فقط")
        self._pipes(ladle, [("ACCEPT", None), ("REJECT", None)])
        graph = ts.build_graph(order=self.order,
                               expand=[f"ladle-{ladle.id}"],
                               problems_only=True)
        by_id = {n["id"]: n for n in graph["nodes"]}
        # the reject pipe and its ancestry stay lit
        reject_pipe = next(n for n in graph["nodes"]
                           if n["kind"] == "pipe" and n["state"] == "fail")
        self.assertFalse(reject_pipe.get("dim"))
        self.assertFalse(by_id[f"ladle-{ladle.id}"].get("dim"))
        self.assertFalse(by_id[f"order-{self.order.id}"].get("dim"))
        # the healthy pipe dims
        ok_pipe = next(n for n in graph["nodes"]
                       if n["kind"] == "pipe" and n["state"] == "pass")
        self.assertTrue(ok_pipe.get("dim"))

    def test_order_reaches_its_ladles_through_its_pipes(self):
        """On production data ChemicalAnalysis.production_order_id is filled on
        almost nothing (2 of 77 rows) — the order finds its ladles through the
        pipes. Reading the direct relation alone drew an order and nothing
        else, which is what the first prod deploy showed."""
        ladle = self._ladle("فحص أخيرة فقط", ladle_id="222082026")
        ladle.production_order_id = None          # exactly how prod looks
        db.session.commit()
        self._pipes(ladle, [("ACCEPT", None), ("HOLD", None)])

        graph = ts.build_graph(order=self.order)
        self.assertIsNotNone(self._node(graph, f"ladle-{ladle.id}"))
        group = self._node(graph, f"pipes-{ladle.id}")
        self.assertEqual(group["counts"]["pass"] + group["counts"]["hold"], 2)

    def test_a_ladles_pipes_are_scoped_to_this_order(self):
        """One ladle can feed several orders; the group must count only the
        pipes belonging to the order on screen."""
        ladle = self._ladle("فحص أخيرة فقط")
        self._pipes(ladle, [("ACCEPT", None)])
        other = ProductionOrder(order_number="PO-2", target_quantity=3)
        db.session.add(other)
        db.session.commit()
        stray = Pipe(production_date=date(2026, 8, 29), ladle_id=ladle.ladle_id,
                     pipe_code="OTHER", no_code="OTHER", arrange_pipe=9,
                     diameter=300, pipe_class="K9",
                     production_order_id=other.id, lab_decision="REJECT")
        db.session.add(stray)
        db.session.commit()

        graph = ts.build_graph(order=self.order)
        counts = self._node(graph, f"pipes-{ladle.id}")["counts"]
        self.assertEqual(counts["pass"], 1)
        self.assertEqual(counts["fail"], 0)   # the other order's pipe stays out

    def test_pipes_without_a_ladle_record_still_appear(self):
        """A pipe whose heat was never recorded is still a real pipe."""
        p1 = Pipe(production_date=date(2026, 8, 29), pipe_code="NOLADLE",
                  no_code="NL1", arrange_pipe=1, diameter=300, pipe_class="K9",
                  production_order_id=self.order.id, lab_decision="REJECT")
        p2 = Pipe(production_date=date(2026, 8, 29), ladle_id="999999999",
                  pipe_code="GHOST", no_code="G1", arrange_pipe=2, diameter=300,
                  pipe_class="K9", production_order_id=self.order.id,
                  lab_decision="HOLD")
        db.session.add_all([p1, p2])
        db.session.commit()

        graph = ts.build_graph(order=self.order)
        group = self._node(graph, f"pipes-unassigned-{self.order.id}")
        self.assertIsNotNone(group)
        self.assertEqual(group["counts"]["fail"], 1)
        self.assertEqual(group["counts"]["hold"], 1)
        self.assertTrue(any(i["node"] == group["id"] for i in graph["issues"]))

        expanded = ts.build_graph(order=self.order, expand=["unassigned"])
        self.assertIsNotNone(self._node(expanded, f"pipe-{p1.id}"))

    def test_node_detail_attribution_honest_when_missing(self):
        ladle = self._ladle("فحص أخيرة فقط")
        pipes = self._pipes(ladle, [("ACCEPT", None)])
        st = PipeStage(pipe_id=pipes[0].id, stage_name="CCM",
                       decision="Accept")
        db.session.add(st)
        db.session.commit()

        d = ts.node_detail("stage", st.id)
        self.assertIsNone(d["who"])  # renders "not recorded", never raises

        st.approved_by_id = self.admin.id
        db.session.commit()
        d = ts.node_detail("stage", st.id)
        self.assertEqual(d["who"], "Admin")

    # --- mode: pipe -----------------------------------------------------

    def test_pipe_mode_is_one_line_cradle_to_grave(self):
        ladle = self._ladle("فحص أخيرة فقط")
        pipes = self._pipes(ladle, [("ACCEPT", "LAST")])
        db.session.add(PipeStage(pipe_id=pipes[0].id, stage_name="CCM",
                                 decision="Accept"))
        db.session.add(MechanicalTest(test_date=date(2026, 8, 29),
                                      ladle_id=ladle.ladle_id,
                                      pipe_id=pipes[0].id, pipe_code="P1",
                                      decision="ACCEPT", status="ACTIVE"))
        db.session.commit()

        graph = ts.build_pipe_graph(pipes[0])
        kinds = [n["kind"] for n in graph["nodes"]]
        self.assertIn("order", kinds)
        self.assertIn("ladle", kinds)
        self.assertIn("mechanical", kinds)
        self.assertIn("pipe", kinds)
        self.assertIn("stage", kinds)
        # one lane: every node shares the pipe's band
        bands = {n["band"] for n in graph["nodes"]}
        self.assertEqual(len(bands), 1)

    def test_pipe_mode_survives_a_pipe_with_no_heat(self):
        p = Pipe(production_date=date(2026, 8, 29), pipe_code="LONE",
                 no_code="L1", arrange_pipe=1, diameter=300, pipe_class="K9",
                 production_order_id=self.order.id, lab_decision="HOLD")
        db.session.add(p)
        db.session.commit()
        graph = ts.build_pipe_graph(p)
        self.assertIsNotNone(self._node(graph, f"pipe-{p.id}"))
        self.assertTrue(graph["issues"])

    # --- mode: live -----------------------------------------------------

    def test_live_mode_hangs_stopped_pipes_off_their_stage(self):
        ladle = self._ladle("فحص أخيرة فقط")
        pipes = self._pipes(ladle, [("HOLD", None), ("ACCEPT", None)])
        for p in pipes:
            db.session.add(PipeStage(pipe_id=p.id, stage_name="Annealing",
                                     decision="Accept"))
        db.session.commit()

        graph = ts.build_live_graph()
        line = [n for n in graph["nodes"] if n["kind"] == "line"]
        self.assertTrue(line)
        annealing = next(n for n in line if n["title"] == "Annealing")
        self.assertEqual(annealing["counts"]["hold"], 1)
        self.assertEqual(annealing["counts"]["pass"], 1)
        # only the stopped one is drawn as its own node
        stuck = [n for n in graph["nodes"] if n["kind"] == "pipe"]
        self.assertEqual(len(stuck), 1)
        self.assertEqual(stuck[0]["state"], "hold")

    def test_live_mode_caps_the_pipes_per_stage(self):
        ladle = self._ladle("فحص أخيرة فقط")
        specs = [("HOLD", None)] * (ts.LIVE_PIPE_LIMIT + 3)
        pipes = self._pipes(ladle, specs)
        for p in pipes:
            db.session.add(PipeStage(pipe_id=p.id, stage_name="Annealing",
                                     decision="Accept"))
        db.session.commit()

        graph = ts.build_live_graph()
        drawn = [n for n in graph["nodes"] if n["kind"] == "pipe"]
        self.assertEqual(len(drawn), ts.LIVE_PIPE_LIMIT)
        more = [n for n in graph["nodes"] if n["kind"] == "more"]
        self.assertEqual(len(more), 1)
        self.assertIn("+3", more[0]["title"])

    def test_live_mode_ignores_finished_pipes(self):
        ladle = self._ladle("فحص أخيرة فقط")
        pipes = self._pipes(ladle, [("ACCEPT", None)])
        pipes[0].final_decision_value = "ACCEPT"
        db.session.commit()
        graph = ts.build_live_graph()
        busy = [n for n in graph["nodes"]
                if n["kind"] == "line" and n["counts"]]
        self.assertEqual(busy, [])

    def test_live_is_the_default_view(self):
        self._login()
        html = self.client.get("/reports/traceability-tree").get_data(as_text=True)
        self.assertIn("kind-line", html)

    def test_modes_are_reachable(self):
        self._login()
        ladle = self._ladle("فحص أخيرة فقط")
        pipes = self._pipes(ladle, [("ACCEPT", None)])
        for url in ("/reports/traceability-tree?mode=live",
                    "/reports/traceability-tree?mode=order&root=PO-1",
                    "/reports/traceability-tree?mode=pipe&root=P1",
                    "/reports/traceability-tree?root=%s" % ladle.ladle_id,
                    "/reports/traceability-tree/canvas?mode=live"):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_page_and_fragments_permission_gated(self):
        self._login()
        ladle = self._ladle("فحص أخيرة فقط")
        for url in ("/reports/traceability-tree?root=PO-1",
                    "/reports/traceability-tree/canvas?root=PO-1",
                    f"/reports/traceability-tree/node/ladle/{ladle.id}"):
            self.assertEqual(self.client.get(url).status_code, 200, url)

        store = User(username="wh", full_name="Store", role="warehouse",
                     is_active=True)
        store.set_password("x")
        db.session.add(store)
        db.session.commit()
        self._login(store)
        resp = self.client.get("/reports/traceability-tree")
        self.assertIn(resp.status_code, (302, 403))


if __name__ == "__main__":
    unittest.main()
