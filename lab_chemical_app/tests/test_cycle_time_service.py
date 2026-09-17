"""Tests for the stage cycle-time service: per-stage dwell, inter-stage
waiting, lead time, and timestamp-coverage accounting.
"""
from datetime import date, time
import unittest

from werkzeug.datastructures import MultiDict

from app import create_app, db
from app.models.pipe import Pipe, PipeStage
from app.services import analytics_service, cycle_time_service


def _args(d=None):
    return MultiDict(d or {})


class CycleTimeTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _pipe(self, no_code, stages):
        """stages: list of (stage_name, date_or_None, time_or_None)."""
        p = Pipe(
            production_date=date(2026, 3, 1), shift=1, ladle_id="L1",
            pipe_code=no_code, no_code=no_code, arrange_pipe=1, diameter=100,
        )
        db.session.add(p)
        db.session.flush()
        for name, d, t in stages:
            db.session.add(PipeStage(
                pipe_id=p.id, stage_name=name, stage_date=d, stage_time=t,
            ))
        db.session.commit()
        return p

    def test_dwell_and_lead_hand_computed(self):
        self._pipe("N1", [
            ("CCM", date(2026, 3, 1), time(8, 0)),
            ("Annealing", date(2026, 3, 1), time(20, 0)),
            ("Zinc", date(2026, 3, 2), time(8, 0)),
        ])
        data = cycle_time_service.stage_dwell(
            analytics_service.parse_filters(_args())
        )
        stages = {s["stage"]: s for s in data["stages"]}
        self.assertEqual(stages["CCM"]["count"], 1)
        self.assertAlmostEqual(stages["CCM"]["avg"], 12.0, places=6)
        self.assertAlmostEqual(stages["CCM"]["min"], 12.0, places=6)
        self.assertAlmostEqual(stages["CCM"]["max"], 12.0, places=6)
        self.assertAlmostEqual(stages["Annealing"]["avg"], 12.0, places=6)
        # Lead time: first -> last timestamp = 24h
        self.assertAlmostEqual(data["lead_times"]["avg"], 24.0, places=6)
        self.assertEqual(data["lead_times"]["count"], 1)
        # Full coverage
        cov = data["coverage"]
        self.assertEqual(cov["intervals_used"], 2)
        self.assertEqual(cov["excluded_missing_ts"], 0)
        self.assertEqual(cov["excluded_negative"], 0)
        self.assertAlmostEqual(cov["fraction"], 1.0, places=6)

    def test_bottleneck_ranking_sorted_by_avg_desc(self):
        self._pipe("N1", [
            ("CCM", date(2026, 3, 1), time(0, 0)),
            ("Annealing", date(2026, 3, 1), time(10, 0)),  # 10h after CCM
            ("Zinc", date(2026, 3, 1), time(12, 0)),        # 2h after Annealing
        ])
        data = cycle_time_service.stage_dwell(
            analytics_service.parse_filters(_args())
        )
        avgs = [s["avg"] for s in data["stages"]]
        self.assertEqual(avgs, sorted(avgs, reverse=True))
        self.assertEqual(data["stages"][0]["stage"], "CCM")

    def test_missing_timestamp_excluded_and_counted(self):
        self._pipe("N1", [
            ("CCM", date(2026, 3, 1), time(8, 0)),
            ("Annealing", None, None),                      # no timestamp
            ("Zinc", date(2026, 3, 2), time(8, 0)),
        ])
        data = cycle_time_service.stage_dwell(
            analytics_service.parse_filters(_args())
        )
        cov = data["coverage"]
        # CCM->Annealing and Annealing->Zinc both excluded (Annealing has no ts)
        self.assertEqual(cov["intervals_used"], 0)
        self.assertEqual(cov["excluded_missing_ts"], 2)
        self.assertEqual(data["stages"], [])
        # Lead time still computable from the two timestamped stages
        self.assertAlmostEqual(data["lead_times"]["avg"], 24.0, places=6)

    def test_date_without_time_excluded(self):
        self._pipe("N1", [
            ("CCM", date(2026, 3, 1), time(8, 0)),
            ("Annealing", date(2026, 3, 1), None),          # date but no time
        ])
        data = cycle_time_service.stage_dwell(
            analytics_service.parse_filters(_args())
        )
        self.assertEqual(data["coverage"]["excluded_missing_ts"], 1)
        self.assertEqual(data["coverage"]["intervals_used"], 0)

    def test_negative_interval_excluded(self):
        self._pipe("N1", [
            ("CCM", date(2026, 3, 2), time(8, 0)),
            ("Annealing", date(2026, 3, 1), time(8, 0)),   # before CCM
        ])
        data = cycle_time_service.stage_dwell(
            analytics_service.parse_filters(_args())
        )
        self.assertEqual(data["coverage"]["excluded_negative"], 1)
        self.assertEqual(data["coverage"]["intervals_used"], 0)

    def test_waiting_pairs_aggregated(self):
        for code in ("N1", "N2"):
            self._pipe(code, [
                ("CCM", date(2026, 3, 1), time(8, 0)),
                ("Annealing", date(2026, 3, 1), time(18, 0)),
            ])
        data = cycle_time_service.stage_dwell(
            analytics_service.parse_filters(_args())
        )
        pair = [p for p in data["pairs"]
                if p["from"] == "CCM" and p["to"] == "Annealing"]
        self.assertEqual(len(pair), 1)
        self.assertEqual(pair[0]["count"], 2)
        self.assertAlmostEqual(pair[0]["avg"], 10.0, places=6)

    def test_empty_db_safe(self):
        data = cycle_time_service.stage_dwell(
            analytics_service.parse_filters(_args())
        )
        self.assertEqual(data["stages"], [])
        self.assertEqual(data["pairs"], [])
        self.assertEqual(data["lead_times"]["count"], 0)
        self.assertEqual(data["coverage"]["fraction"], 0.0)


if __name__ == "__main__":
    unittest.main()
