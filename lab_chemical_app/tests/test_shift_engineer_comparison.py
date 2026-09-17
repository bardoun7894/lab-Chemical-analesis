"""Tests for the Shift Engineer COMPARISON report.

Unlike shift_engineer_report (one aggregate block, engineer used as a filter),
this report groups BY engineer so shifts can be compared side by side:
per-engineer production, defect rate, reject rate, saving weight, and a
per-CCM-machine breakdown.

Definitions pinned here (grounded in real prod data shape):
- Attribution: everything attributed to Pipe.shift_engineer (the only
  populated field; PipeStage.shift_responsible is 100% NULL in prod).
  NULL/'' engineer is bucketed as the "غير محدد" (unspecified) row.
- Decision signal: lab_decision (final_decision_value is ~empty in prod).
  Accepted = ACCEPT; Rejected = REJECT + BLOCKED; Hold = HOLD; Pending = rest.
- Defect rate: pipes with >=1 defective stage / total pipes * 100.
- Machine: taken from the pipe's CCM stage (Pipe.machine_id is always NULL).
- Saving weight: sum(iso_weight - actual_weight) over pipes having both.
"""
from datetime import date
import unittest

from app import create_app, db
from app.models.pipe import Pipe, PipeStage
from app.models.chemical import Machine
from app.models.user import User
from app.services import analytics_service
from app.models.permission import seed_default_permissions


class ShiftEngineerComparisonTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()
        self.client = self.app.test_client()

        admin = User(username="boss", full_name="Boss", role="admin")
        admin.set_password("x")
        db.session.add(admin)
        db.session.commit()
        self.user_id = admin.id

        # CCM machines (machine lives on the stage, not the pipe)
        self.m10 = Machine(machine_code="M10", stage="CCM")
        self.m11 = Machine(machine_code="M11", stage="CCM")
        db.session.add_all([self.m10, self.m11])
        db.session.commit()
        self.m10_id, self.m11_id = self.m10.id, self.m11.id

        self._seed()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_id)
            sess["_fresh"] = True

    def _pipe(self, no_code, engineer, lab, ccm_machine_id,
              iso=None, actual=None, defect=False, shift=1):
        p = Pipe(
            production_date=date(2026, 4, 14),
            shift=shift,
            shift_engineer=engineer,
            ladle_id="L1",
            pipe_code=no_code,
            no_code=no_code,
            arrange_pipe=1,
            diameter=100,
            pipe_class="K9",
            iso_weight=iso,
            actual_weight=actual,
            lab_decision=lab,
        )
        db.session.add(p)
        db.session.commit()
        # CCM stage carries the machine
        db.session.add(PipeStage(
            pipe_id=p.id, stage_name="CCM", machine_id=ccm_machine_id,
        ))
        # optional defect on a separate (Lab) stage
        if defect:
            db.session.add(PipeStage(
                pipe_id=p.id, stage_name="Lab",
                has_defect=True, defect_type="Crack",
            ))
        db.session.commit()
        return p

    def _seed(self):
        # Ali: 3 pipes -> ACCEPT(M10,save10), BLOCKED+defect(M10,save5), REJECT(M11)
        self._pipe("A-1", "Ali", "ACCEPT", self.m10_id, iso=60, actual=50)
        self._pipe("A-2", "Ali", "BLOCKED", self.m10_id, iso=60, actual=55, defect=True)
        self._pipe("A-3", "Ali", "REJECT", self.m11_id)
        # Sami: 1 pipe -> WAITING + defect (M11, save2)
        self._pipe("S-1", "Sami", "WAITING", self.m11_id, iso=40, actual=38,
                   defect=True, shift=2)
        # Unspecified engineer (None) -> HOLD (M10)
        self._pipe("U-1", None, "HOLD", self.m10_id)

    def _filters(self, **kw):
        base = {"date_from": None, "date_to": None, "shift": None,
                "shift_engineer": None}
        base.update(kw)
        return base

    # ---- per-engineer rows -------------------------------------------------

    def _by_engineer(self, data):
        return {r["engineer"]: r for r in data["engineer_rows"]}

    def test_ali_aggregates(self):
        data = analytics_service.shift_engineer_comparison(self._filters())
        ali = self._by_engineer(data)["Ali"]
        self.assertEqual(ali["total"], 3)
        self.assertEqual(ali["accepted"], 1)        # ACCEPT
        self.assertEqual(ali["rejected"], 2)        # REJECT + BLOCKED
        self.assertEqual(ali["defect_pipes"], 1)
        self.assertEqual(ali["defect_rate"], 33.3)  # 1/3
        self.assertEqual(ali["saving"], 15.0)       # (60-50)+(60-55)
        self.assertEqual(ali["by_machine"], {"M10": 2, "M11": 1})

    def test_sami_aggregates(self):
        data = analytics_service.shift_engineer_comparison(self._filters())
        sami = self._by_engineer(data)["Sami"]
        self.assertEqual(sami["total"], 1)
        self.assertEqual(sami["pending"], 1)        # WAITING
        self.assertEqual(sami["defect_rate"], 100.0)
        self.assertEqual(sami["saving"], 2.0)

    def test_unspecified_engineer_bucket(self):
        data = analytics_service.shift_engineer_comparison(self._filters())
        rows = self._by_engineer(data)
        # NULL engineer must show as its own labeled row, not be dropped
        self.assertIn("غير محدد", rows)
        self.assertEqual(rows["غير محدد"]["total"], 1)
        self.assertEqual(rows["غير محدد"]["hold"], 1)

    def test_zero_iso_weight_is_not_saving(self):
        # Real prod data stores iso_weight=0.0 when the standard weight was
        # never entered. A 0 standard must NOT produce a giant negative saving;
        # such pipes count as "no weight data" (excluded from saving + coverage).
        self._pipe("Z-1", "Zed", "WAITING", self.m10_id, iso=0.0, actual=900.0)
        data = analytics_service.shift_engineer_comparison(self._filters())
        zed = self._by_engineer(data)["Zed"]
        self.assertEqual(zed["saving"], 0.0)
        self.assertEqual(zed["saving_pct"], 0)
        self.assertEqual(zed["weight_pipes"], 0)  # excluded from coverage

    # ---- per-CCM-machine rows ---------------------------------------------

    def test_machine_rows(self):
        data = analytics_service.shift_engineer_comparison(self._filters())
        m = {r["machine"]: r for r in data["machine_rows"]}
        self.assertEqual(m["M10"]["total"], 3)      # A-1, A-2, U-1
        self.assertEqual(m["M10"]["defects"], 1)    # A-2
        self.assertEqual(m["M10"]["rejected"], 1)   # A-2 BLOCKED
        self.assertEqual(m["M11"]["total"], 2)      # A-3, S-1
        self.assertEqual(m["M11"]["defects"], 1)    # S-1

    # ---- filters still apply ----------------------------------------------

    def test_filter_by_shift(self):
        data = analytics_service.shift_engineer_comparison(self._filters(shift=2))
        rows = self._by_engineer(data)
        self.assertIn("Sami", rows)
        self.assertNotIn("Ali", rows)

    # ---- route renders -----------------------------------------------------

    def test_route_renders(self):
        self._login()
        r = self.client.get("/reports/shift-engineer-comparison")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Ali".encode(), r.data)
        self.assertIn("M10".encode(), r.data)


if __name__ == "__main__":
    unittest.main()
