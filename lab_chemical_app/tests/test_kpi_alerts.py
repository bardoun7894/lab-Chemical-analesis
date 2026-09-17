"""Targets an admin set are the ones the system alerts on.

The daily report used to alert on 7% and 4% written into the code, so a plant
that had decided its limit was 3% was never told. And the KPI list itself was
missing the readings the floor asks for — FTY and saving as a share.
"""

import os
import unittest
from datetime import date, datetime, time as dt_time, timedelta

from app import create_app, db
from app.models.chemical import Machine
from app.models.permission import seed_default_permissions
from app.models.pipe import Pipe, PipeStage
from app.models.stage import ProductionStage
from app.models.stage_history import PipeStageHistory
from app.models.user import User
from app.services import (bi_service, kpi_alert_service, kpi_readings_service,
                          kpi_target_service)

# The real today, not a frozen date. The routes and the alert engine read
# date.today(), so a hard-coded fixture day matched them for exactly one day
# and then every page test failed on a date nobody had touched.
TODAY = date.today()
TODAY_DT = datetime.combine(TODAY, dt_time(8, 0))
PREV_MONTH_DAY = (TODAY.replace(day=1) - timedelta(days=12))


class KpiTargetsAndAlertsTestCase(unittest.TestCase):
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
        self.machine = Machine(machine_code="M10", machine_name="Caster 10")
        self.other = Machine(machine_code="M11", machine_name="Caster 11")
        db.session.add_all([self.machine, self.other])
        db.session.commit()
        self.admin_id = admin.id
        self.stages = ProductionStage.active_names()

        # The targets live in the operator settings file, which is real state
        # on disk shared with the running app — snapshot it so a test run
        # leaves the machine exactly as it found it.
        from app.routes.admin import APP_SETTINGS_PATH

        self.settings_path = APP_SETTINGS_PATH
        try:
            with open(self.settings_path, "rb") as f:
                self._settings_backup = f.read()
        except FileNotFoundError:
            self._settings_backup = None
        self._set_targets()

    def tearDown(self):
        if self._settings_backup is None:
            try:
                os.remove(self.settings_path)
            except FileNotFoundError:
                pass
        else:
            with open(self.settings_path, "wb") as f:
                f.write(self._settings_backup)
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.admin_id)
            sess["_fresh"] = True

    def _pipe(self, n, decision="ACCEPT", machine=None, stage_decisions=(),
              history=(), shift=None, diameter=800, day=None):
        pipe = Pipe(production_date=day or TODAY, ladle_id="L1",
                    pipe_code=f"L1-P{n}",
                    no_code=f"N{n}", arrange_pipe=n, diameter=diameter,
                    pipe_class="K9", lab_decision=decision, shift=shift)
        db.session.add(pipe)
        db.session.commit()
        for i, verdict in enumerate(stage_decisions):
            row = PipeStage(pipe_id=pipe.id, stage_name=self.stages[i],
                            decision=verdict,
                            machine_id=machine.id if machine and i == 1 else None)
            db.session.add(row)
        db.session.commit()
        for stage_name, verdict in history:
            row = pipe.get_stage(stage_name) or PipeStage(
                pipe_id=pipe.id, stage_name=stage_name)
            db.session.add(row)
            db.session.commit()
            db.session.add(PipeStageHistory(
                pipe_stage_id=row.id, pipe_id=pipe.id, stage_name=stage_name,
                decision=verdict, action="update"))
        db.session.commit()
        return pipe

    def _set_targets(self, **targets):
        from app.routes.admin import load_app_settings, save_app_settings

        settings = load_app_settings()
        settings[kpi_target_service.SETTINGS_KEY] = targets
        save_app_settings(settings)

    # --- the KPI list --------------------------------------------------------

    def test_the_settings_screen_offers_every_reading_the_floor_asks_for(self):
        keys = [k for k, *_ in kpi_target_service.ALL_KPIS]
        for expected in ("yield_pct", "reject_pct", "fty_pct", "first_pass_pct",
                         "rft_pct", "rework_pct", "defect_pct", "hold_pct",
                         "pending_pct", "chem_reject_pct", "mech_fail_pct",
                         "produced", "actual_mt", "saving_mt", "saving_pct",
                         "machine_reject_pct", "stage_reject_pct",
                         "mold_reject_pct", "shift_reject_pct",
                         "dn_reject_pct", "month_reject_pct",
                         "day_reject_pct"):
            self.assertIn(expected, keys)

    # --- the day reject rate has one target under two names ------------------

    def _four_in_ten_rejected(self):
        for i in range(10):
            self._pipe(i, decision="REJECT" if i < 4 else "ACCEPT")

    def test_day_reject_pct_is_offered_as_its_own_target(self):
        keys = [k for k, *_ in kpi_target_service.ALL_KPIS]
        self.assertIn("day_reject_pct", keys)

    def test_day_reject_pct_alone_raises_the_breach(self):
        self._set_targets(day_reject_pct=10)
        self._four_in_ten_rejected()

        plant = [b for b in kpi_alert_service.evaluate_day(TODAY)
                 if b["scope"] == "plant"]
        self.assertEqual([b["key"] for b in plant], ["day_reject_pct"])
        self.assertEqual(plant[0]["value"], 40.0)
        self.assertEqual(plant[0]["target"], 10)

    def test_the_original_reject_pct_key_still_works(self):
        """An admin who set a target before day_reject_pct existed keeps it."""
        self._set_targets(reject_pct=10)
        self._four_in_ten_rejected()

        plant = [b for b in kpi_alert_service.evaluate_day(TODAY)
                 if b["scope"] == "plant"]
        self.assertEqual([b["key"] for b in plant], ["reject_pct"])
        self.assertEqual(plant[0]["value"], 40.0)

    def test_setting_both_day_keys_does_not_alert_twice(self):
        """They are the same reading; two alerts for one problem is noise."""
        self._set_targets(day_reject_pct=10, reject_pct=15)
        self._four_in_ten_rejected()

        plant = [b for b in kpi_alert_service.evaluate_day(TODAY)
                 if b["scope"] == "plant"]
        self.assertEqual(len(plant), 1)
        # The explicit key wins, and so does its number.
        self.assertEqual(plant[0]["key"], "day_reject_pct")
        self.assertEqual(plant[0]["target"], 10)

    def test_day_under_its_target_is_quiet(self):
        self._set_targets(day_reject_pct=50)
        self._four_in_ten_rejected()
        plant = [b for b in kpi_alert_service.evaluate_day(TODAY)
                 if b["scope"] == "plant"]
        self.assertEqual(plant, [])

    def test_day_and_month_are_separate_alerts(self):
        """One drifting month plus one bad day is two different facts."""
        self._set_targets(day_reject_pct=10, month_reject_pct=10)
        self._four_in_ten_rejected()

        keys = sorted(b["key"] for b in kpi_alert_service.evaluate_day(TODAY))
        self.assertEqual(keys, ["day_reject_pct", "month_reject_pct"])

    def test_a_thin_day_is_not_judged(self):
        self._set_targets(day_reject_pct=10)
        self._pipe(0, decision="REJECT")
        plant = [b for b in kpi_alert_service.evaluate_day(TODAY)
                 if b["scope"] == "plant"]
        self.assertEqual(plant, [])

    def test_the_tile_reads_the_day_key(self):
        """The reject-rate tile must grade against day_reject_pct too."""
        real_today = date.today()
        self._set_targets(day_reject_pct=10)
        for i in range(10):
            self._pipe(i, decision="REJECT" if i < 4 else "ACCEPT",
                       day=real_today)
        self._login()
        self.client.get("/alerts/")  # renders without error

        from app.routes.alerts import index  # noqa: F401
        targets = kpi_target_service.load()
        key, value = kpi_target_service.day_reject_target(targets)
        self.assertEqual((key, value), ("day_reject_pct", 10))

    # --- reject rate per DN, per day, per month ------------------------------

    def test_reject_rate_is_cut_by_dn(self):
        """A DN running hot is invisible in the machine/stage/mould cuts when
        it is spread evenly across all of them."""
        for i in range(6):
            self._pipe(i, decision="REJECT", diameter=100)
        for i in range(6, 12):
            self._pipe(i, decision="ACCEPT", diameter=800)

        rates = kpi_readings_service.scoped_rates(TODAY)
        self.assertEqual(dict(rates["dn"]["DN100"]),
                         {"decided": 6, "rejects": 6})
        self.assertEqual(dict(rates["dn"]["DN800"]),
                         {"decided": 6, "rejects": 0})

    def test_dn_over_its_target_raises_one_alert_naming_the_dn(self):
        self._set_targets(dn_reject_pct=10)
        for i in range(6):
            self._pipe(i, decision="REJECT", diameter=100)
        for i in range(6, 12):
            self._pipe(i, decision="ACCEPT", diameter=800)

        breaches = kpi_alert_service.evaluate_day(TODAY)
        dn = [b for b in breaches if b["key"] == "dn_reject_pct"]
        self.assertEqual(len(dn), 1)
        self.assertEqual(dn[0]["scope"], "dn")
        self.assertEqual(dn[0]["scope_key"], "DN100")
        self.assertEqual(dn[0]["value"], 100.0)
        self.assertIn("DN100", dn[0]["message"])

    def test_a_dn_below_the_sample_floor_is_not_judged(self):
        """One rejected pipe on a DN is 100% and means nothing."""
        self._set_targets(dn_reject_pct=10)
        self._pipe(0, decision="REJECT", diameter=300)
        for i in range(1, 7):
            self._pipe(i, decision="ACCEPT", diameter=800)

        breaches = kpi_alert_service.evaluate_day(TODAY)
        self.assertEqual([b for b in breaches if b["scope"] == "dn"], [])

    def test_day_and_month_reject_rates(self):
        prev_month = PREV_MONTH_DAY
        for i in range(10):
            self._pipe(i, decision="REJECT" if i < 2 else "ACCEPT")
        for i in range(10, 20):
            self._pipe(i, decision="REJECT", day=prev_month)

        day = kpi_readings_service.day_reject_rate(TODAY)
        self.assertEqual(day["decided"], 10)
        self.assertEqual(day["pct"], 20.0)
        self.assertEqual(day["label"], TODAY.isoformat())

        # Month to date is September only — August's ten rejects stay out.
        month = kpi_readings_service.month_reject_rate(TODAY)
        self.assertEqual(month["decided"], 10)
        self.assertEqual(month["pct"], 20.0)
        self.assertEqual(month["label"], TODAY.strftime("%Y-%m"))

    def test_month_over_target_raises_a_breach(self):
        self._set_targets(month_reject_pct=10)
        for i in range(10):
            self._pipe(i, decision="REJECT" if i < 4 else "ACCEPT")

        breaches = kpi_alert_service.evaluate_day(TODAY)
        month = [b for b in breaches if b["key"] == "month_reject_pct"]
        self.assertEqual(len(month), 1)
        self.assertEqual(month[0]["scope"], "month")
        self.assertEqual(month[0]["value"], 40.0)
        self.assertEqual(month[0]["scope_key"], TODAY.strftime("%Y-%m"))

    def test_month_under_target_is_quiet(self):
        self._set_targets(month_reject_pct=50)
        for i in range(10):
            self._pipe(i, decision="REJECT" if i < 4 else "ACCEPT")
        breaches = kpi_alert_service.evaluate_day(TODAY)
        self.assertEqual([b for b in breaches if b["key"] == "month_reject_pct"], [])

    def test_trend_buckets_by_day_and_by_month(self):
        for i in range(10):
            self._pipe(i, decision="REJECT" if i < 2 else "ACCEPT")
        for i in range(10, 20):
            self._pipe(i, decision="REJECT", day=PREV_MONTH_DAY)

        trend = kpi_readings_service.reject_trend(days=60, end=TODAY)
        days = {r["key"]: r for r in trend["by_day"]}
        self.assertEqual(days[TODAY.isoformat()]["pct"], 20.0)
        self.assertEqual(days[PREV_MONTH_DAY.isoformat()]["pct"], 100.0)

        this_month = TODAY.strftime("%Y-%m")
        last_month = PREV_MONTH_DAY.strftime("%Y-%m")
        months = {r["key"]: r for r in trend["by_month"]}
        self.assertEqual(months[this_month]["pct"], 20.0)
        self.assertEqual(months[last_month]["pct"], 100.0)
        # Oldest first, so a screen can reverse it for newest-first.
        self.assertEqual([r["key"] for r in trend["by_month"]],
                         [last_month, this_month])

    def test_trend_marks_a_thin_day_unjudged(self):
        self._pipe(0, decision="REJECT")
        trend = kpi_readings_service.reject_trend(days=10, end=TODAY)
        row = trend["by_day"][0]
        self.assertEqual(row["pct"], 100.0)
        self.assertFalse(row["judged"])

    def test_alerts_page_renders_the_dn_cut_and_the_trend(self):
        # The page reads the real today, not the fixture's frozen TODAY, so
        # seed on the real one or the cards render empty.
        real_today = date.today()
        self._set_targets(dn_reject_pct=10, month_reject_pct=10)
        for i in range(6):
            self._pipe(i, decision="REJECT", diameter=100, day=real_today)
        self._login()
        html = self.client.get("/alerts/").get_data(as_text=True)
        self.assertIn("DN100", html)
        self.assertIn(real_today.strftime("%Y-%m"), html)

    def test_every_offered_kpi_is_actually_read(self):
        """A target on a number nobody computes is a target that never fires."""
        from app.services import kpi_readings_service

        for n in range(1, 7):
            self._pipe(n, stage_decisions=("Accept", "Accept"))
        values = kpi_readings_service.readings(TODAY)
        for key, *_ in kpi_target_service.KPIS:
            self.assertIn(key, values, f"{key} is offered but never computed")

    def test_the_screen_renders_and_saves_the_new_targets(self):
        self._login()
        html = self.client.get("/admin/settings/kpi-targets").get_data(as_text=True)
        self.assertIn('name="fty_pct"', html)
        self.assertIn('name="saving_pct"', html)
        self.assertIn('name="machine_reject_pct"', html)

        self.client.post("/admin/settings/kpi-targets",
                         data={"reject_pct": "3", "machine_reject_pct": "5",
                               "fty_pct": "95"})
        targets = kpi_target_service.load()
        self.assertEqual(targets["reject_pct"], 3.0)
        self.assertEqual(targets["machine_reject_pct"], 5.0)
        self.assertEqual(targets["fty_pct"], 95.0)

    # --- the readings --------------------------------------------------------

    def test_saving_is_reported_as_a_share_not_only_a_tonnage(self):
        """A tonnage cannot be judged without knowing the size of the run."""
        self.assertIn("saving_pct", bi_service.bi_dashboard({})["kpis"])

    def test_fty_counts_only_pipes_that_never_needed_a_second_look(self):
        clean = self._pipe(1, stage_decisions=("Accept", "Accept"))
        held = self._pipe(2, stage_decisions=("Accept", "Accept"),
                          history=(("CCM", "Hold"),))
        m = bi_service.first_time_metrics([clean, held])
        self.assertEqual(m["decided"], 2)
        self.assertEqual(m["fty"], 1, "the held pipe is not first-time-right")

    def test_a_held_pipe_still_counts_as_accepted_without_a_reject(self):
        """The second reading the client asked for: never rejected anywhere."""
        clean = self._pipe(1, stage_decisions=("Accept", "Accept"))
        held = self._pipe(2, stage_decisions=("Accept", "Accept"),
                          history=(("CCM", "Hold"),))
        m = bi_service.first_time_metrics([clean, held])
        self.assertEqual(m["first_pass"], 2)

    def test_rft_reads_the_operative_decision_not_the_stored_column(self):
        """final_decision_value is filled on 7 of 67 prod pipes, so keying off
        it reported RFT as 0% on a day whose FTY was 80%."""
        from app.services import kpi_readings_service

        for n in range(1, 7):
            self._pipe(n, decision="ACCEPT", stage_decisions=("Accept", "Accept"))
        self.assertEqual(
            [p.final_decision_value for p in Pipe.query.all()], [None] * 6)
        self.assertEqual(kpi_readings_service.readings(TODAY)["rft_pct"], 100.0)

    def test_a_reworked_pipe_is_not_right_first_time(self):
        from app.services import kpi_readings_service

        for n in range(1, 6):
            self._pipe(n, decision="ACCEPT", stage_decisions=("Accept", "Accept"))
        self._pipe(6, decision="ACCEPT", stage_decisions=("Accept", "Accept"),
                   history=(("CCM", "Rework"),))
        values = kpi_readings_service.readings(TODAY)
        self.assertEqual(values["rft_pct"], 83.33)
        self.assertEqual(values["rework_pct"], 16.67)

    def test_rft_is_not_dragged_down_by_work_still_in_progress(self):
        """17 of prod's 22 pipes that day were still walking the line, and
        measuring against all of them read RFT as 18% — a number about WIP
        wearing the name of a quality metric."""
        from app.services import kpi_readings_service

        for n in range(1, 6):
            self._pipe(n, decision="ACCEPT", stage_decisions=("Accept", "Accept"))
        for n in range(6, 23):
            self._pipe(n, decision="WAITING", stage_decisions=("Accept",))
        values = kpi_readings_service.readings(TODAY)
        self.assertEqual(values["_counts"]["decided"], 5)
        self.assertEqual(values["rft_pct"], 100.0)
        self.assertEqual(values["pending_pct"], 77.27)

    def test_rft_is_not_judged_before_the_day_has_decided_anything(self):
        self._set_targets(rft_pct=95)
        for n in range(1, 23):
            self._pipe(n, decision="WAITING", stage_decisions=("Accept",))
        self.assertEqual(
            [b for b in kpi_alert_service.evaluate_day(TODAY)
             if b["key"] == "rft_pct"], [])

    def test_a_rejected_stage_breaks_both_readings(self):
        rejected = self._pipe(1, stage_decisions=("Accept", "Accept"),
                              history=(("CCM", "Reject"),))
        m = bi_service.first_time_metrics([rejected])
        self.assertEqual((m["fty"], m["first_pass"]), (0, 0))

    def test_the_reject_rate_is_measured_against_every_decided_pipe(self):
        """It used to count only pipes carrying final_decision_value — 7 of 67
        on prod — and reported a 71% reject rate off that denominator."""
        for n in range(1, 5):
            self._pipe(n, decision="ACCEPT")
        self._pipe(5, decision="REJECT")
        kpis = bi_service.bi_dashboard({})["kpis"]
        self.assertEqual(kpis["decided"], 5)
        self.assertEqual(kpis["reject_pct"], 20.0)

    # --- what a half-finished day must not be accused of ---------------------

    def test_a_day_with_nothing_weighed_yet_is_not_a_zero_percent_yield(self):
        """Registered at 09:00, weighed at 14:00. The first version of this
        alerted 'Yield 0% — critical' on a morning that had gone fine."""
        self._set_targets(yield_pct=92)
        for n in range(1, 7):
            pipe = self._pipe(n, stage_decisions=("Accept",))
            pipe.actual_weight = None
            pipe.iso_weight = 120.0
        db.session.commit()
        self.assertEqual(
            [b for b in kpi_alert_service.evaluate_day(TODAY)
             if b["key"] == "yield_pct"], [])

    def test_a_running_total_is_only_judged_once_the_day_is_over(self):
        """Today's tonnage is under target for most of the day. That is the
        clock, not the plant."""
        from datetime import timedelta

        self._set_targets(produced=100)
        for n in range(1, 7):
            self._pipe(n)
        today = date.today()
        self.assertEqual(
            [b for b in kpi_alert_service.evaluate_day(today)
             if b["key"] == "produced"], [],
            "a day still running was judged on its running total")

        # A finished day is fair game.
        for n in range(7, 13):
            pipe = self._pipe(n)
            pipe.production_date = today - timedelta(days=1)
        db.session.commit()
        self.assertTrue(
            [b for b in kpi_alert_service.evaluate_day(today - timedelta(days=1))
             if b["key"] == "produced"])

    def test_a_rate_off_three_pipes_is_not_judged(self):
        self._set_targets(reject_pct=3)
        for n in range(1, 4):
            self._pipe(n, decision="REJECT")
        self.assertEqual(kpi_alert_service.evaluate_day(TODAY), [])

    # --- the alerts ----------------------------------------------------------

    def test_no_targets_means_no_configured_alerts(self):
        """The system never invents a limit."""
        self._pipe(1, decision="REJECT")
        self.assertEqual(kpi_alert_service.evaluate_day(TODAY), [])

    def test_the_plant_reject_rate_alerts_on_the_configured_target(self):
        self._set_targets(reject_pct=3)
        for n in range(1, 10):
            self._pipe(n, decision="ACCEPT")
        self._pipe(10, decision="REJECT")

        breaches = kpi_alert_service.evaluate_day(TODAY)
        keys = [b["key"] for b in breaches]
        self.assertIn("reject_pct", keys)
        breach = next(b for b in breaches if b["key"] == "reject_pct")
        self.assertEqual(breach["value"], 10.0)
        self.assertEqual(breach["target"], 3)
        self.assertEqual(breach["scope"], "plant")

    def test_a_rate_under_its_target_raises_nothing(self):
        self._set_targets(reject_pct=50)
        for n in range(1, 10):
            self._pipe(n, decision="ACCEPT")
        self._pipe(10, decision="REJECT")
        self.assertEqual(
            [b for b in kpi_alert_service.evaluate_day(TODAY)
             if b["key"] == "reject_pct"], [])

    def test_a_single_machine_over_the_line_is_named(self):
        """The plant can be fine while one machine is not."""
        self._set_targets(machine_reject_pct=10)
        for n in range(1, 7):
            self._pipe(n, decision="REJECT" if n <= 3 else "ACCEPT",
                       machine=self.machine, stage_decisions=("Accept", "Accept"))
        for n in range(7, 13):
            self._pipe(n, decision="ACCEPT", machine=self.other,
                       stage_decisions=("Accept", "Accept"))

        machine_breaches = [b for b in kpi_alert_service.evaluate_day(TODAY)
                            if b["scope"] == "machine"]
        self.assertEqual([b["scope_key"] for b in machine_breaches], ["M10"])
        self.assertEqual(machine_breaches[0]["value"], 50.0)

    def test_the_shift_that_crossed_the_line_is_named(self):
        """"The day was 6%" tells nobody where to go; "night shift was 14%"
        does."""
        self._set_targets(shift_reject_pct=10)
        for n in range(1, 7):
            self._pipe(n, decision="REJECT" if n <= 3 else "ACCEPT", shift=2)
        for n in range(7, 13):
            self._pipe(n, decision="ACCEPT", shift=1)

        shift_breaches = [b for b in kpi_alert_service.evaluate_day(TODAY)
                          if b["scope"] == "shift"]
        self.assertEqual(len(shift_breaches), 1)
        self.assertEqual(shift_breaches[0]["scope_key"], "وردية 2")
        self.assertEqual(shift_breaches[0]["value"], 50.0)
        self.assertIn("وردية 2", shift_breaches[0]["message"])

    def test_a_good_shift_beside_a_bad_one_raises_nothing(self):
        self._set_targets(shift_reject_pct=10)
        for n in range(1, 7):
            self._pipe(n, decision="REJECT" if n <= 3 else "ACCEPT", shift=2)
        for n in range(7, 13):
            self._pipe(n, decision="ACCEPT", shift=1)
        keys = [b["scope_key"] for b in kpi_alert_service.evaluate_day(TODAY)
                if b["scope"] == "shift"]
        self.assertNotIn("وردية 1", keys)

    def test_the_day_is_cut_five_ways_on_the_page(self):
        """Machine, shift, stage, mould and DN — including the ones inside
        their limit, which raise nothing and are what you watch."""
        from app.services import kpi_readings_service

        self._set_targets(shift_reject_pct=10, machine_reject_pct=10)
        for n in range(1, 7):
            self._pipe(n, decision="REJECT" if n <= 3 else "ACCEPT", shift=2,
                       machine=self.machine, stage_decisions=("Accept", "Accept"))
        table = kpi_readings_service.scoped_table(TODAY)
        self.assertEqual({"machine", "stage", "mold", "shift", "dn"}, set(table))
        shift_row = table["shift"][0]
        self.assertEqual(shift_row["key"], "وردية 2")
        self.assertEqual(shift_row["pct"], 50.0)
        self.assertTrue(shift_row["over"])

        self._login()
        html = self.client.get("/alerts/").get_data(as_text=True)
        self.assertIn("Shifts", html)
        self.assertIn("Machines (CCM)", html)
        self.assertIn("وردية 2", html)

    def test_a_machine_with_almost_nothing_on_it_is_not_judged(self):
        """One pipe rejected is 100% and means nothing."""
        self._set_targets(machine_reject_pct=10)
        self._pipe(1, decision="REJECT", machine=self.machine,
                   stage_decisions=("Accept", "Accept"))
        self.assertEqual(
            [b for b in kpi_alert_service.evaluate_day(TODAY)
             if b["scope"] == "machine"], [])

    def test_far_past_the_target_is_critical_not_a_warning(self):
        self._set_targets(reject_pct=5)
        for n in range(1, 9):
            self._pipe(n, decision="ACCEPT")
        for n in range(9, 13):
            self._pipe(n, decision="REJECT")
        breach = next(b for b in kpi_alert_service.evaluate_day(TODAY)
                      if b["key"] == "reject_pct")
        self.assertEqual(breach["severity"], kpi_alert_service.SEVERITY_CRITICAL)

    def test_the_alert_says_the_number_the_target_and_where(self):
        self._set_targets(machine_reject_pct=10)
        for n in range(1, 7):
            self._pipe(n, decision="REJECT" if n <= 3 else "ACCEPT",
                       machine=self.machine, stage_decisions=("Accept", "Accept"))
        breach = next(b for b in kpi_alert_service.evaluate_day(TODAY)
                      if b["scope"] == "machine")
        self.assertIn("M10", breach["message"])
        self.assertIn("50.0", breach["message"])
        self.assertIn("10", breach["message"])

    # --- storing them: the bell, the list, the one message ------------------

    def _over_the_line(self):
        self._set_targets(reject_pct=3)
        for n in range(1, 10):
            self._pipe(n, decision="ACCEPT")
        self._pipe(10, decision="REJECT")

    def test_a_breach_is_stored_once_however_often_the_day_is_synced(self):
        """The same machine over the same target at 11:00 and again at 15:00
        is one problem, not two."""
        from app.models.kpi_alert import KpiAlert

        self._over_the_line()
        kpi_alert_service.sync_day(TODAY)
        kpi_alert_service.sync_day(TODAY, force=True)
        kpi_alert_service.sync_day(TODAY, force=True)
        self.assertEqual(KpiAlert.query.filter_by(day=TODAY).count(), 1)

    def test_a_day_that_recovers_stops_shouting_but_keeps_the_record(self):
        from app.models.kpi_alert import KpiAlert

        self._over_the_line()
        kpi_alert_service.sync_day(TODAY)
        self.assertEqual(len(kpi_alert_service.open_alerts(TODAY)), 1)

        self._set_targets(reject_pct=50)  # the target was moved
        kpi_alert_service.sync_day(TODAY, force=True)
        self.assertEqual(kpi_alert_service.open_alerts(TODAY), [])
        self.assertEqual(KpiAlert.query.filter_by(day=TODAY).count(), 1,
                         "the record of the morning is worth keeping")

    def test_crossing_the_line_again_makes_it_unread_again(self):
        self._over_the_line()
        kpi_alert_service.sync_day(TODAY)
        for alert in kpi_alert_service.open_alerts(TODAY):
            alert.read_at = TODAY_DT
        db.session.commit()

        self._set_targets(reject_pct=50)
        kpi_alert_service.sync_day(TODAY, force=True)
        self._set_targets(reject_pct=3)
        kpi_alert_service.sync_day(TODAY, force=True)

        self.assertEqual(kpi_alert_service.unread_count(TODAY), 1)

    def test_the_sync_is_throttled_between_readers(self):
        """Four gunicorn workers must not each re-aggregate the whole day."""
        self._over_the_line()
        kpi_alert_service.sync_day(TODAY)
        self._set_targets(reject_pct=50)
        # Not forced, and the day was just synced: the stored answer stands.
        self.assertEqual(len(kpi_alert_service.sync_day(TODAY)), 1)
        self.assertEqual(len(kpi_alert_service.sync_day(TODAY, force=True)), 0)

    def test_the_bell_feed_reports_the_unread_count(self):
        self._over_the_line()
        self._login()
        data = self.client.get("/alerts/feed").get_json()
        self.assertEqual(data["unread"], 1)
        self.assertEqual(data["total"], 1)
        self.assertTrue(data["has_targets"])
        self.assertIn("الهدف", data["alerts"][0]["message"])

    def test_opening_the_bell_marks_them_read(self):
        self._over_the_line()
        self._login()
        self.client.get("/alerts/feed")
        r = self.client.post("/alerts/read",
                             headers={"X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(r.get_json()["unread"], 0)
        self.assertEqual(kpi_alert_service.unread_count(TODAY), 0)

    def test_the_alerts_page_lists_them(self):
        self._over_the_line()
        self._login()
        html = self.client.get("/alerts/").get_data(as_text=True)
        self.assertIn("الهدف", html)

    def test_the_page_says_when_nothing_is_configured(self):
        """A blank page must not read as a clean bill of health."""
        self._login()
        html = self.client.get("/alerts/").get_data(as_text=True)
        self.assertIn("No targets are set", html)

    def test_the_dashboard_banner_shows_the_days_misses(self):
        """The point of a target is that missing it is the first thing you see."""
        self._over_the_line()
        self._login()
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn("KPI targets missed today", html)
        self.assertIn("الهدف", html)

    def test_the_dashboard_is_clean_when_nothing_is_missed(self):
        self._login()
        html = self.client.get("/").get_data(as_text=True)
        self.assertNotIn("KPI targets missed today", html)

    def test_the_bell_is_on_every_page(self):
        self._login()
        html = self.client.get("/alerts/").get_data(as_text=True)
        self.assertIn("kpiAlertBell", html)
        self.assertIn("/alerts/feed", html)

    # --- WhatsApp ------------------------------------------------------------

    def test_nothing_is_sent_until_an_admin_turns_it_on(self):
        """A message on a real phone is never the consequence of a default."""
        from app.services import whatsapp_service

        self._over_the_line()
        kpi_alert_service.sync_day(TODAY)
        self.assertFalse(whatsapp_service.is_configured())
        self.assertEqual(kpi_alert_service.push_pending(TODAY), 0)

    def test_a_breach_is_sent_once_however_long_it_lasts(self):
        from unittest import mock

        from app.services import whatsapp_service

        self._over_the_line()
        kpi_alert_service.sync_day(TODAY)
        with mock.patch.object(whatsapp_service, "is_configured",
                               return_value=True), \
             mock.patch.object(whatsapp_service, "config",
                               return_value={"min_severity": "warn"}), \
             mock.patch.object(whatsapp_service, "send",
                               return_value=1) as send:
            self.assertEqual(kpi_alert_service.push_pending(TODAY), 1)
            self.assertEqual(kpi_alert_service.push_pending(TODAY), 0)
        self.assertEqual(send.call_count, 1)
        self.assertIn("الهدف", send.call_args[0][0])

    def test_a_failed_send_is_retried_next_run(self):
        """The stamp is only set once the message actually left."""
        from unittest import mock

        from app.services import whatsapp_service

        self._over_the_line()
        kpi_alert_service.sync_day(TODAY)
        with mock.patch.object(whatsapp_service, "is_configured",
                               return_value=True), \
             mock.patch.object(whatsapp_service, "config",
                               return_value={"min_severity": "warn"}), \
             mock.patch.object(whatsapp_service, "send", return_value=0):
            self.assertEqual(kpi_alert_service.push_pending(TODAY), 0)
        with mock.patch.object(whatsapp_service, "is_configured",
                               return_value=True), \
             mock.patch.object(whatsapp_service, "config",
                               return_value={"min_severity": "warn"}), \
             mock.patch.object(whatsapp_service, "send", return_value=1):
            self.assertEqual(kpi_alert_service.push_pending(TODAY), 1)

    def test_a_bare_number_becomes_a_whatsapp_address(self):
        from app.services import whatsapp_service

        self.assertEqual(whatsapp_service._jid("201001234567"),
                         "201001234567@s.whatsapp.net")
        self.assertEqual(whatsapp_service._jid("+20 100 123 4567"),
                         "201001234567@s.whatsapp.net")
        self.assertEqual(whatsapp_service._jid("1234@g.us"), "1234@g.us")

    def test_the_screen_hides_whatsapp_until_the_bot_is_reachable(self):
        """A switch that cannot send anything is a switch somebody turns on and
        then wonders about."""
        self._login()
        html = self.client.get("/admin/settings/kpi-targets").get_data(as_text=True)
        self.assertNotIn("whatsapp_recipients", html)

    def test_saving_targets_does_not_switch_whatsapp_off_behind_your_back(self):
        """The section is hidden, so its checkbox is absent, and an absent
        checkbox reads as 'off'."""
        from app.services import whatsapp_service

        self._login()
        self.client.post("/admin/settings/kpi-targets",
                         data={"reject_pct": "3", "whatsapp_section": "1",
                               "whatsapp_enabled": "on",
                               "whatsapp_recipients": "201001234567"})
        self.client.post("/admin/settings/kpi-targets", data={"reject_pct": "4"})
        self.assertTrue(whatsapp_service.config()["enabled"])
        self.assertEqual(whatsapp_service.config()["recipients"], ["201001234567"])

    def test_the_numbers_are_saved_from_the_targets_screen(self):
        from app.services import whatsapp_service

        self._login()
        self.client.post("/admin/settings/kpi-targets",
                         data={"reject_pct": "3",
                               "whatsapp_section": "1",
                               "whatsapp_enabled": "on",
                               "whatsapp_recipients": "201001234567",
                               "whatsapp_min_severity": "warn"})
        cfg = whatsapp_service.config()
        self.assertTrue(cfg["enabled"])
        self.assertEqual(cfg["recipients"], ["201001234567"])
        self.assertEqual(cfg["min_severity"], "warn")

    def test_the_daily_report_alerts_come_from_the_targets(self):
        """And the hardcoded 7%/4% no longer speak over them."""
        self._set_targets(reject_pct=3)
        for n in range(1, 10):
            self._pipe(n, decision="ACCEPT")
        self._pipe(10, decision="REJECT")
        report = bi_service.daily_report(TODAY)
        self.assertTrue(any("الهدف" in a for a in report["alerts"]))
        self.assertFalse(any("الحد المسموح (7%)" in a for a in report["alerts"]))


if __name__ == "__main__":
    unittest.main()
