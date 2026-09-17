"""Tests for the agent tool registry and SQL guard."""

import json
import unittest
from datetime import date

from app import create_app, db
from app.models.chemical import ChemicalAnalysis, Furnace
from app.models.pipe import Pipe, PipeStage
from app.models.production_order import ProductionOrder
from app.models.stage import ProductionStage
from app.models.user import User
from app.models.permission import seed_default_permissions


class SQLGuardTestCase(unittest.TestCase):
    """SQL guard must block dangerous queries and pass safe ones."""

    def setUp(self):
        self.app = create_app("testing")
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()

        u = User(username="admin", full_name="Admin", role="super_admin")
        u.set_password("x")
        db.session.add(u)
        db.session.commit()
        self.user = u

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _exec(self, sql, **kw):
        from app.services.agent_tools import execute
        return execute("run_sql", {"sql": sql, **kw}, self.user)

    def test_select_ok(self):
        r = self._exec("SELECT 1 AS n")
        self.assertNotIn("error", r)
        self.assertEqual(r["columns"], ["n"])
        self.assertEqual(r["rows"], [[1]])

    def test_with_select_ok(self):
        r = self._exec("WITH cte AS (SELECT 1 AS n) SELECT * FROM cte")
        self.assertNotIn("error", r)
        self.assertEqual(r["rows"], [[1]])

    def test_insert_blocked(self):
        r = self._exec("INSERT INTO users (username) VALUES ('x')")
        self.assertIn("error", r)

    def test_update_blocked(self):
        r = self._exec("UPDATE users SET username='x'")
        self.assertIn("error", r)

    def test_delete_blocked(self):
        r = self._exec("DELETE FROM users")
        self.assertIn("error", r)

    def test_drop_blocked(self):
        r = self._exec("DROP TABLE users")
        self.assertIn("error", r)

    def test_alter_blocked(self):
        r = self._exec("ALTER TABLE users ADD COLUMN x TEXT")
        self.assertIn("error", r)

    def test_create_blocked(self):
        r = self._exec("CREATE TABLE evil (id INT)")
        self.assertIn("error", r)

    def test_truncate_blocked(self):
        r = self._exec("TRUNCATE users")
        self.assertIn("error", r)

    def test_copy_blocked(self):
        r = self._exec("COPY users TO '/tmp/x.csv'")
        self.assertIn("error", r)

    def test_multi_statement_blocked(self):
        r = self._exec("SELECT 1; SELECT 2")
        self.assertIn("error", r)
        self.assertIn("Multiple", r["error"])

    def test_pg_sleep_blocked(self):
        r = self._exec("SELECT pg_sleep(100)")
        self.assertIn("error", r)
        self.assertIn("pg_sleep", r["error"])

    def test_pg_read_file_blocked(self):
        r = self._exec("SELECT pg_read_file('/etc/passwd')")
        self.assertIn("error", r)

    def test_dblink_blocked(self):
        r = self._exec("SELECT * FROM dblink('dbname=x', 'SELECT 1')")
        self.assertIn("error", r)

    def test_password_text_blocked(self):
        r = self._exec("SELECT password_hash FROM users")
        self.assertIn("error", r)
        self.assertIn("password", r["error"].lower())

    def test_select_star_redacts_password(self):
        """SELECT * FROM users must not include password_hash in the result."""
        r = self._exec("SELECT * FROM users LIMIT 2")
        self.assertNotIn("error", r)
        self.assertNotIn("password_hash", r["columns"])
        self.assertIn("password_hash", r.get("redacted_columns", []))

    def test_limit_enforcement(self):
        for i in range(5):
            u = User(username=f"u{i}", full_name=f"U{i}", role="viewer")
            u.set_password("x")
            db.session.add(u)
        db.session.commit()

        r = self._exec("SELECT username FROM users", limit=3)
        self.assertNotIn("error", r)
        self.assertEqual(r["row_count"], 3)
        self.assertTrue(r["truncated"])

    def test_limit_floor_at_1(self):
        """Negative or zero limit is floored to 1."""
        r = self._exec("SELECT 1 AS n", limit=-1)
        self.assertNotIn("error", r)
        self.assertEqual(r["row_count"], 1)

    def test_pg_catalog_blocked(self):
        """System catalogs are rejected outright."""
        r = self._exec("SELECT * FROM pg_catalog.pg_authid")
        self.assertIn("error", r)
        self.assertIn("pg_catalog", r["error"])

    def test_information_schema_blocked(self):
        r = self._exec("SELECT * FROM information_schema.tables")
        self.assertIn("error", r)
        self.assertIn("information_schema", r["error"])

    def test_unlisted_table_blocked(self):
        """Tables not on the allowlist are rejected."""
        r = self._exec("SELECT * FROM alembic_version")
        self.assertIn("error", r)
        self.assertIn("not in the allowed list", r["error"])

    def test_permission_tables_denied(self):
        r = self._exec("SELECT * FROM role_permissions")
        self.assertIn("error", r)
        self.assertIn("not in the allowed list", r["error"])

    def test_allowlist_follows_schema(self):
        """Every real business table is queryable, including ones added after
        the old hard-coded list was written (certificates, kpi_alerts)."""
        from sqlalchemy import inspect as sa_inspect
        from app.services.agent_tools import _allowed_tables, _DENIED_TABLES
        real = set(sa_inspect(db.engine).get_table_names())
        self.assertEqual(_allowed_tables(), frozenset(real - _DENIED_TABLES))
        for t in ("certificates", "kpi_alerts", "kpi_alert_runs"):
            self.assertIn(t, real, f"fixture schema lacks {t}")
            r = self._exec(f"SELECT COUNT(*) AS n FROM {t}")
            self.assertNotIn("error", r, r)

    def test_describe_schema_shows_json_shapes(self):
        from app.services.agent_tools import execute
        r = execute("describe_schema", {"tables": ["pipe_stages"]}, self.user)
        cols = {c["name"]: c for c in r["tables"]["pipe_stages"]["columns"]}
        self.assertIn("zinc_profile", cols)
        self.assertIn("json_shape", cols["zinc_profile"])
        self.assertIn("mass", cols["zinc_profile"]["json_shape"])

    def test_cte_single(self):
        """A single CTE referencing an allowed table must work."""
        r = self._exec(
            "WITH cte AS (SELECT 1 AS n) SELECT * FROM cte"
        )
        self.assertNotIn("error", r)
        self.assertEqual(r["rows"], [[1]])

    def test_cte_multiple(self):
        """Multiple comma-separated CTEs must work."""
        r = self._exec(
            "WITH a AS (SELECT 1 AS x), b AS (SELECT 2 AS y) "
            "SELECT * FROM a CROSS JOIN b"
        )
        self.assertNotIn("error", r)
        self.assertEqual(r["rows"], [[1, 2]])

    def test_cte_with_allowed_table(self):
        """A CTE referencing an allowed base table must work."""
        r = self._exec(
            "WITH counts AS (SELECT count(*) AS n FROM pipes) "
            "SELECT n FROM counts"
        )
        self.assertNotIn("error", r)

    def test_trailing_line_comment_limit(self):
        """A query ending with a line comment must not break the LIMIT wrapper."""
        r = self._exec("SELECT 1 AS n -- just a test")
        self.assertNotIn("error", r)
        self.assertEqual(r["rows"], [[1]])

    def test_limit_clamped_at_500(self):
        r = self._exec("SELECT 1 AS n", limit=9999)
        self.assertNotIn("error", r)
        # Should not error — limit is clamped silently


class ToolRegistryTestCase(unittest.TestCase):
    """Tool registry basics."""

    def setUp(self):
        self.app = create_app("testing")
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()

        self.admin = User(username="admin", full_name="Admin", role="super_admin")
        self.admin.set_password("x")
        db.session.add(self.admin)

        self.operator = User(username="op", full_name="Operator", role="operator")
        self.operator.set_password("x")
        db.session.add(self.operator)

        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_all_tools_count(self):
        from app.services.agent_tools import all_tools
        tools = all_tools()
        self.assertEqual(len(tools), 11)

    def test_tools_for_operator_excludes_sql(self):
        from app.services.agent_tools import tools_for_user
        tools = tools_for_user(self.operator)
        names = [t.name for t in tools]
        self.assertNotIn("run_sql", names)

    def test_execute_denies_sql_for_operator(self):
        from app.services.agent_tools import execute
        r = execute("run_sql", {"sql": "SELECT 1"}, self.operator)
        self.assertIn("error", r)
        self.assertIn("Permission denied", r["error"])

    def test_execute_unknown_tool(self):
        from app.services.agent_tools import execute
        r = execute("nonexistent_tool", {}, self.admin)
        self.assertIn("error", r)
        self.assertIn("Unknown tool", r["error"])


class ToolOutputShapeTestCase(unittest.TestCase):
    """Each tool returns its documented shape against fixture data."""

    def setUp(self):
        self.app = create_app("testing")
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        seed_default_permissions()

        self.user = User(username="admin", full_name="Admin", role="super_admin")
        self.user.set_password("x")
        db.session.add(self.user)

        # Seed production stages
        for i, name in enumerate(["CCM", "Annealing", "Lab Approval", "Zinc"]):
            s = ProductionStage(
                name=name, sort_order=i+1, is_active=True,
                code=name.lower().replace(' ', '_'),
            )
            db.session.add(s)

        # Seed a furnace and ladle
        furnace = Furnace(furnace_code="F1", furnace_name="Furnace 1")
        db.session.add(furnace)
        db.session.flush()

        analysis = ChemicalAnalysis(
            ladle_id="1234567890",
            ladle_no=1,
            furnace_id=furnace.id,
            test_date=date(2026, 8, 1),
            carbon=3.5, silicon=2.1, manganese=0.3,
        )
        db.session.add(analysis)
        db.session.flush()

        # Seed a production order
        order = ProductionOrder(
            order_number="ORD-001",
            customer_name="TestCo",
            target_quantity=10,
            diameter=300,
            pipe_class="K9",
            order_date=date(2026, 8, 1),
        )
        db.session.add(order)
        db.session.flush()

        # Seed a pipe
        pipe = Pipe(
            no_code="N0001",
            pipe_code="N0001-1-300012026",
            diameter=300,
            pipe_class="K9",
            production_date=date(2026, 8, 1),
            shift=1,
            ladle_id="1234567890",
            production_order_id=order.id,
        )
        db.session.add(pipe)
        db.session.flush()

        # Seed a stage
        stage = PipeStage(
            pipe_id=pipe.id,
            stage_name="CCM",
            stage_date=date(2026, 8, 1),
            decision="Accept",
        )
        db.session.add(stage)
        db.session.commit()

        self.pipe = pipe
        self.order = order

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _exec(self, name, args=None):
        from app.services.agent_tools import execute
        return execute(name, args or {}, self.user)

    def test_get_context_shape(self):
        r = self._exec("get_context")
        self.assertIn("today", r)
        self.assertIn("iso_week", r)
        self.assertIn("current_shift", r)
        self.assertIn("db_counts", r)
        self.assertIn("production_stages", r)
        self.assertIsInstance(r["production_stages"], list)

    def test_describe_schema_summary(self):
        r = self._exec("describe_schema")
        self.assertIn("tables", r)
        self.assertIsInstance(r["tables"], dict)
        self.assertIn("pipes", r["tables"])

    def test_describe_schema_detail(self):
        r = self._exec("describe_schema", {"tables": ["pipes"]})
        self.assertIn("tables", r)
        pipes = r["tables"]["pipes"]
        self.assertIn("columns", pipes)
        self.assertIsInstance(pipes["columns"], list)

    def test_get_ladle_found(self):
        r = self._exec("get_ladle", {"ladle_id": "1234567890"})
        self.assertNotIn("error", r)
        self.assertEqual(r["ladle_id"], "1234567890")
        self.assertIn("elements", r)

    def test_get_ladle_not_found(self):
        r = self._exec("get_ladle", {"ladle_id": "0000000000"})
        self.assertIn("error", r)

    def test_get_pipe_found(self):
        r = self._exec("get_pipe", {"code": "N0001"})
        self.assertNotIn("error", r)
        self.assertEqual(r["no_code"], "N0001")
        self.assertIn("stages", r)
        self.assertEqual(len(r["stages"]), 1)

    def test_get_pipe_not_found(self):
        r = self._exec("get_pipe", {"code": "NXXXX"})
        self.assertIn("error", r)

    def test_get_order_found(self):
        r = self._exec("get_order", {"order_number": "ORD-001"})
        self.assertNotIn("error", r)
        self.assertEqual(r["order_number"], "ORD-001")
        self.assertEqual(r["target_quantity"], 10)

    def test_get_order_not_found(self):
        r = self._exec("get_order", {"order_number": "NONEXISTENT"})
        self.assertIn("error", r)

    def test_search_pipes(self):
        r = self._exec("search_pipes", {"dn": "300"})
        self.assertIn("pipes", r)
        self.assertEqual(r["total_matching"], 1)

    def test_production_summary(self):
        r = self._exec("production_summary", {
            "date_from": "2026-08-01", "date_to": "2026-08-31"
        })
        self.assertNotIn("error", r)
        self.assertIn("grand_total", r)

    def test_defect_analysis(self):
        r = self._exec("defect_analysis", {
            "date_from": "2026-08-01", "date_to": "2026-08-31"
        })
        self.assertNotIn("error", r)
        self.assertIn("total_defects", r)

    def test_find_page(self):
        r = self._exec("find_page", {"query": "chemical"})
        self.assertIn("results", r)
        self.assertIsInstance(r["results"], list)


if __name__ == "__main__":
    unittest.main()
