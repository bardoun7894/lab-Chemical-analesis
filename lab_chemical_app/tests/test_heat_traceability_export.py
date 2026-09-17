from datetime import date
import unittest

from app import create_app, db
from app.models.chemical import ChemicalAnalysis, Furnace
from app.models.mechanical import MechanicalTest
from app.models.pipe import Pipe, PipeStage
from app.models.production_order import ProductionOrder
from app.models.user import User
from app.services import analytics_service
from app.models.permission import seed_default_permissions


class HeatTraceabilityExportTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_heat_traceability_export_includes_pipe_stage_and_mechanical_data(self):
        furnace = Furnace(furnace_code="A1", furnace_name="Furnace A1")
        order = ProductionOrder(
            order_number="PO-TEST-001",
            customer_name="Water Co",
            sales_number="SO-1",
            target_quantity=1,
            order_date=date(2026, 4, 14),
        )
        chemical = ChemicalAnalysis(
            test_date=date(2026, 4, 14),
            furnace=furnace,
            ladle_no=1,
            ladle_id="L100",
            decision="Inspect Last pipes",
            carbon=3.5,
            silicon=2.2,
            magnesium=0.05,
        )
        pipe = Pipe(
            production_date=date(2026, 4, 14),
            production_order=order,
            ladle_id="L100",
            pipe_code="P1000",
            no_code="N1000",
            arrange_pipe=1,
            diameter=300,
            pipe_class="K9",
            mechanical_test_role="LAST",
            lab_decision="ACCEPT",
            final_decision_value="ACCEPT",
        )
        lab_stage = PipeStage(
            pipe=pipe, stage_name="Lab Approval", stage_date=date(2026, 4, 14), decision="Accept"
        )
        finish_stage = PipeStage(
            pipe=pipe,
            stage_name="Finish",
            stage_date=date(2026, 4, 15),
            decision="Accept",
        )
        delivery_stage = PipeStage(
            pipe=pipe,
            stage_name="Delivery",
            stage_date=date(2026, 4, 16),
            decision="Accept",
            sales_order="SO-1",
            delivery_customer="Water Co",
            delivery_receipt="DR-1",
            bundle_number="B-1",
        )
        mechanical = MechanicalTest(
            test_date=date(2026, 4, 15),
            ladle_id="L100",
            pipe=pipe,
            pipe_code="P1000",
            decision="ACCEPT",
            tensile_strength=42.0,
            tensile_mpa=411.6,
            elongation=12.5,
            hardness=180,
            nodularity_percent=88.0,
            status="ACTIVE",
        )

        db.session.add_all(
            [
                furnace,
                order,
                chemical,
                pipe,
                lab_stage,
                finish_stage,
                delivery_stage,
                mechanical,
            ]
        )
        db.session.commit()

        data = analytics_service.heat_traceability(
            {
                "date_from": date(2026, 4, 1),
                "date_to": date(2026, 4, 30),
            }
        )

        self.assertEqual(1, len(data["rows"]))
        self.assertEqual(1, len(data["export_rows"]))

        row = data["export_rows"][0]
        self.assertEqual("L100", row["ladle_id"])
        self.assertEqual("P1000", row["pipe_code"])
        self.assertEqual("SO-1", row["sales_order"])
        self.assertEqual("Water Co", row["customer"])
        self.assertEqual("ACCEPT", row["mechanical_decision"])
        self.assertEqual("ACCEPT", row["pipe_lab_decision"])
        self.assertEqual("Accept", row["lab_approval_decision"])
        self.assertEqual("Accept", row["finish_decision"])
        self.assertEqual("Accept", row["delivery_decision"])
        self.assertEqual("B-1", row["delivery_bundle_number"])

    def test_finish_stage_update_saves_bundle_number(self):
        user = User(
            username="operator",
            role=User.ROLE_OPERATOR,
            department=User.DEPT_PRODUCTION,
        )
        user.set_password("secret")
        pipe = Pipe(
            production_date=date(2026, 4, 14),
            no_code="N2000",
            arrange_pipe=1,
        )

        db.session.add_all([user, pipe])
        db.session.commit()

        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(user.id)
            session["_fresh"] = True

        response = client.post(
            f"/stages/{pipe.id}/stage/Finish",
            json={
                "decision": "Accept",
                "stage_date": "2026-04-15",
                "measurement_value": 6.0,
                "bundle_number": "FIN-22",
            },
        )

        self.assertEqual(200, response.status_code)
        stage = PipeStage.query.filter_by(pipe_id=pipe.id, stage_name="Finish").first()
        self.assertIsNotNone(stage)
        self.assertEqual("FIN-22", stage.bundle_number)


if __name__ == "__main__":
    unittest.main()
