"""Ovality (Annealing) and Visual checklist (Finish) popups in the Stage Console.

Both popups were added to the Register/Edit form in ce77e8d but never ported
to /stages/console, where the operator actually enters data — DrAlaa
2026-08-18: "البوب الاخير اللى على الفنش مش ظاهر فى الكونسول".

update_stage already parses stage_<Annealing>_ov_* and stage_<Finish>_visual_*
out of the console's JSON payload, so only the console markup was missing.
Each modal must live INSIDE its own stage form, or the console autosave will
not serialize its fields.
"""

import unittest
from datetime import date

from app import create_app, db
from app.models.permission import seed_default_permissions
from app.models.pipe import Pipe, PipeStage
from app.models.stage import ProductionStage
from app.models.user import User


class _ConsoleBase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()
        self.client = self.app.test_client()

        u = User(username="admin", full_name="Admin", role="admin",
                 is_active=True)
        u.set_password("x")
        db.session.add(u)
        db.session.commit()
        self.user_id = u.id

        self.pipe = Pipe(
            production_date=date(2026, 8, 18), ladle_id="OV1",
            pipe_code="OV1-P1", no_code="OV1N1", arrange_pipe=1,
            diameter=300, pipe_class="K9",
        )
        db.session.add(self.pipe)
        db.session.commit()

        self.annealing = ProductionStage.name_for_code("annealing")
        self.finish = ProductionStage.name_for_code("finish")

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(self.user_id)
            sess["_fresh"] = True

    def _console_html(self):
        return self.client.get(
            f"/stages/console/pipe/{self.pipe.id}"
        ).get_data(as_text=True)

    def _stage(self, name):
        return PipeStage.query.filter_by(
            pipe_id=self.pipe.id, stage_name=name
        ).first()

    def _form_for(self, html, stage_name):
        """The markup of one stage's <form>, so we can prove containment."""
        after = html.split(f'data-stage="{stage_name}"', 1)
        self.assertEqual(len(after), 2, f"no console form for {stage_name}")
        return after[1].split("</form>", 1)[0]


class ConsoleOvalityPopupTestCase(_ConsoleBase):
    def test_console_pane_has_ovality_modal_inside_annealing_form(self):
        self._login()
        html = self._console_html()
        self.assertIn('id="consoleOvalityModal"', html)
        # ID plus the symbols DN300 defines, on both axes.
        self.assertIn(f'name="stage_{self.annealing}_ov_x_ID"', html)
        self.assertIn(f'name="stage_{self.annealing}_ov_y_d1"', html)
        self.assertIn(f'name="stage_{self.annealing}_ov_x_t6"', html)
        self.assertNotIn(f'name="stage_{self.annealing}_ov_y_D15"', html)
        form = self._form_for(html, self.annealing)
        self.assertIn('id="consoleOvalityModal"', form)

    def test_update_stage_saves_ovality_profile_from_console(self):
        self._login()
        resp = self.client.post(
            f"/stages/{self.pipe.id}/stage/{self.annealing}",
            json={
                "decision": "Accept",
                f"stage_{self.annealing}_ov_x_ID": "21",
                f"stage_{self.annealing}_ov_y_ID": "20",
                f"stage_{self.annealing}_ov_x_D1": "30",
                f"stage_{self.annealing}_ov_y_D1": "30",
            },
        )
        self.assertTrue(resp.get_json()["success"], resp.get_data(as_text=True))
        points = self._stage(self.annealing).ovality_profile["points"]
        self.assertEqual(points["ID"]["x"], 21)
        self.assertEqual(points["ID"]["y"], 20)
        # (21-20)/(21+20)*100 = 2.44 — the value DrAlaa's sheet shows
        self.assertAlmostEqual(points["ID"]["ovality"], 2.44, places=2)
        self.assertAlmostEqual(points["D1"]["ovality"], 0.0, places=2)

    def test_resave_without_ovality_keeps_profile(self):
        self._login()
        self.client.post(
            f"/stages/{self.pipe.id}/stage/{self.annealing}",
            json={"decision": "Accept",
                  f"stage_{self.annealing}_ov_x_ID": "21",
                  f"stage_{self.annealing}_ov_y_ID": "20"},
        )
        before = self._stage(self.annealing).ovality_profile
        resp = self.client.post(
            f"/stages/{self.pipe.id}/stage/{self.annealing}",
            json={"notes": "checked"},
        )
        self.assertTrue(resp.get_json()["success"])
        self.assertEqual(self._stage(self.annealing).ovality_profile, before)


class ConsoleVisualChecklistTestCase(_ConsoleBase):
    def test_console_pane_has_visual_modal_inside_finish_form(self):
        self._login()
        html = self._console_html()
        self.assertIn('id="consoleFinishVisualModal"', html)
        for key in ("marking", "ovality", "straightness",
                    "internal_finish", "external_finish"):
            self.assertIn(f'name="stage_{self.finish}_visual_{key}"', html)
        form = self._form_for(html, self.finish)
        self.assertIn('id="consoleFinishVisualModal"', form)

    def test_update_stage_saves_visual_profile_from_console(self):
        self._login()
        resp = self.client.post(
            f"/stages/{self.pipe.id}/stage/{self.finish}",
            json={
                "decision": "Accept",
                f"stage_{self.finish}_visual_marking": "1",
                f"stage_{self.finish}_visual_ovality": "1",
                f"stage_{self.finish}_visual_straightness_hidden": "0",
            },
        )
        self.assertTrue(resp.get_json()["success"], resp.get_data(as_text=True))
        vp = self._stage(self.finish).visual_profile
        self.assertTrue(vp["marking"])
        self.assertTrue(vp["ovality"])
        self.assertFalse(vp["straightness"])
        # keys never sent still default to False, never missing
        self.assertFalse(vp["internal_finish"])
        self.assertFalse(vp["external_finish"])

    def test_resave_without_visual_keeps_profile(self):
        self._login()
        self.client.post(
            f"/stages/{self.pipe.id}/stage/{self.finish}",
            json={"decision": "Accept",
                  f"stage_{self.finish}_visual_marking": "1"},
        )
        before = self._stage(self.finish).visual_profile
        resp = self.client.post(
            f"/stages/{self.pipe.id}/stage/{self.finish}",
            json={"notes": "checked"},
        )
        self.assertTrue(resp.get_json()["success"])
        self.assertEqual(self._stage(self.finish).visual_profile, before)


class VisualProfileStageRenameTestCase(_ConsoleBase):
    """The Finish stage is renameable from /admin/stages.

    _parse_visual_profile used to compare against the literal "Finish", so
    renaming the stage silently stopped the checklist saving. It must resolve
    the name through the stage code like its ovality sibling does.
    """

    def test_visual_profile_parses_under_a_renamed_finish_stage(self):
        from app.routes.stages import _parse_visual_profile

        # The test DB carries no production_stages rows, so name_for_code()
        # falls back to DEFAULT_STAGES. Insert the row the way /admin/stages
        # would, already renamed.
        stage = ProductionStage.query.filter_by(code="finish").first()
        if stage is None:
            stage = ProductionStage(code="finish", is_builtin=True,
                                    sort_order=10, is_active=True)
            db.session.add(stage)
        stage.name = "Final Inspection"
        db.session.commit()

        renamed = ProductionStage.name_for_code("finish")
        self.assertEqual(renamed, "Final Inspection")

        profile, has_any = _parse_visual_profile(
            [(f"stage_{renamed}_visual_marking", "1")], renamed
        )
        self.assertTrue(has_any, "renamed Finish stage no longer parses")
        self.assertTrue(profile["marking"])


if __name__ == "__main__":
    unittest.main()
