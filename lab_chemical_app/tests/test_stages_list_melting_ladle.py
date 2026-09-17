"""Stages list: the Melting Ladle cell shows the chemical decision as decided.

DrAlaa 2026-09-15: the ladle decision ("فحص أولى وأخيرة", "Inspect Last
pipes", "تالف"...) showed as a grey/blue in-progress icon on the stages list,
so a pipe looked stuck at Melting Ladle. Inspect outcomes are green, تالف red.
"""

import re
import unittest
from datetime import date

from app import create_app, db
from app.models.permission import seed_default_permissions
from app.models.pipe import Pipe, PipeStage
from app.models.user import User


class StagesListMeltingLadleTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()
        u = User(username="admin", full_name="Admin", role="admin")
        u.set_password("x")
        db.session.add(u)
        db.session.commit()
        self.client = self.app.test_client()
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(u.id)
            sess["_fresh"] = True

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _pipe(self, n, melting_decision, ccm_decision=None):
        p = Pipe(
            production_date=date(2026, 9, 15),
            ladle_id=f"L{n}",
            pipe_code=f"L{n}-P1",
            no_code=f"MLT{n}",
            arrange_pipe=1,
            diameter=300,
            pipe_class="K9",
        )
        db.session.add(p)
        db.session.flush()
        db.session.add(PipeStage(pipe_id=p.id, stage_name="Melting Ladle",
                                 decision=melting_decision))
        if ccm_decision:
            db.session.add(PipeStage(pipe_id=p.id, stage_name="CCM",
                                     decision=ccm_decision))
        db.session.commit()

    def _badge_for(self, body, title):
        match = re.search(r'<span class="badge ([^"]+)" title="%s">' % re.escape(title), body)
        self.assertIsNotNone(match, f"no stage badge titled {title!r}")
        return match.group(1)

    def test_inspect_decisions_are_green_and_reject_is_red(self):
        self._pipe(1, "فحص أولى وأخيرة")
        self._pipe(2, "Inspect Last pipes")
        self._pipe(3, "تالف")
        resp = self.client.get("/stages/", follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertEqual(self._badge_for(body, "فحص أولى وأخيرة"), "bg-success")
        self.assertEqual(self._badge_for(body, "Inspect Last pipes"), "bg-success")
        self.assertEqual(self._badge_for(body, "تالف"), "bg-danger")

    def test_other_stages_keep_their_colours(self):
        self._pipe(4, "فحص أخيرة فقط", ccm_decision="Rework")
        body = self.client.get("/stages/", follow_redirects=True).get_data(as_text=True)
        self.assertEqual(self._badge_for(body, "Rework"), "bg-info")


if __name__ == "__main__":
    unittest.main()
