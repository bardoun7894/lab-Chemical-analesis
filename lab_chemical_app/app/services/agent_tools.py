"""Agent tool registry — the tools the AI agent can call.

Each tool is a dataclass with a name, description (written for the model),
JSON Schema parameters, an optional permission gate, and the implementation.

All tools are read-only. No INSERT/UPDATE/DELETE in v1.
"""

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, Optional

from app import db
from app.services.permission_service import has_permission

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool dataclass and registry
# ---------------------------------------------------------------------------


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    permission: Optional[tuple] = None  # (module, screen) or None
    fn: Callable = field(default=lambda **kw: {"error": "not implemented"})


@dataclass(frozen=True)
class UserSnapshot:
    """A plain copy of the identity fields the agent needs.

    The agent runs inside a `stream_with_context` generator, which outlives the
    request. A live `User` row read in there is detached and every attribute
    access raises DetachedInstanceError, so the identity is copied out — with
    its permission grants already resolved — before streaming starts.
    """
    id: Optional[int]
    username: str
    full_name: Optional[str]
    role: str
    is_super_admin: bool
    granted_keys: frozenset

    def can(self, module: str, screen: str) -> bool:
        return self.is_super_admin or f'{module}.{screen}' in self.granted_keys


def snapshot_user(user) -> UserSnapshot:
    """Copy a User (or Flask-Login proxy) into a detach-proof snapshot.

    Call this in the request, before entering the streaming generator.
    """
    from app.models.user import User
    from app.services.permission_service import get_role_permissions

    user = getattr(user, '_get_current_object', lambda: user)()
    role = getattr(user, 'role', '') or ''
    is_owner = role == User.ROLE_SUPER_ADMIN
    return UserSnapshot(
        id=getattr(user, 'id', None),
        username=getattr(user, 'username', '') or '',
        full_name=getattr(user, 'full_name', None),
        role=role,
        is_super_admin=is_owner,
        # Owners bypass the matrix, so there is nothing to resolve for them.
        granted_keys=frozenset() if is_owner else frozenset(get_role_permissions(role)),
    )


def as_snapshot(user) -> UserSnapshot:
    """Accept either a snapshot or a live user, always return a snapshot."""
    return user if isinstance(user, UserSnapshot) else snapshot_user(user)


# Set by execute() before calling a tool fn, so tools that need the caller
# identity (find_page) can read it without touching current_user.
_current_snap: Optional[UserSnapshot] = None

_TOOLS: dict[str, Tool] = {}


def _register(tool: Tool):
    _TOOLS[tool.name] = tool
    return tool


def all_tools() -> list[Tool]:
    return list(_TOOLS.values())


def tools_for_user(user) -> list[Tool]:
    """Return only the tools this user has permission to use."""
    snap = as_snapshot(user)
    if snap.is_super_admin:
        return list(_TOOLS.values())
    return [t for t in _TOOLS.values()
            if t.permission is None or snap.can(*t.permission)]


def tool_declarations_for_user(user) -> list[dict]:
    """OpenAI-compatible function declarations for tools this user may call."""
    decls = []
    for t in tools_for_user(user):
        decls.append({
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        })
    return decls


def gemini_declarations_for_user(user) -> list[dict]:
    """Gemini-native function declarations (strips unsupported JSON Schema keys)."""
    decls = []
    for t in tools_for_user(user):
        params = _strip_gemini_unsupported(t.parameters)
        decls.append({
            "name": t.name,
            "description": t.description,
            "parameters": params,
        })
    return decls


def _strip_gemini_unsupported(schema: dict) -> dict:
    """Remove keys Gemini rejects: additionalProperties, $schema, format on strings."""
    if not isinstance(schema, dict):
        return schema
    out = {}
    for k, v in schema.items():
        if k in ('additionalProperties', '$schema'):
            continue
        if k == 'format' and schema.get('type') == 'string':
            continue
        if isinstance(v, dict):
            out[k] = _strip_gemini_unsupported(v)
        elif isinstance(v, list):
            out[k] = [_strip_gemini_unsupported(i) if isinstance(i, dict) else i for i in v]
        else:
            out[k] = v
    return out


def execute(name: str, args: dict, user) -> dict:
    """Execute a tool by name. Never raises — returns {"error": ...} on failure."""
    tool = _TOOLS.get(name)
    if tool is None:
        valid = ', '.join(sorted(_TOOLS.keys()))
        return {"error": f"Unknown tool '{name}'. Valid tools: {valid}"}

    snap = as_snapshot(user)

    # Belt and braces: re-check permission even if the model shouldn't see it
    if tool.permission and not snap.can(*tool.permission):
        return {"error": f"Permission denied for tool '{name}'"}

    global _current_snap
    try:
        _current_snap = snap
        t0 = time.monotonic()
        result = tool.fn(**args)
        ms = int((time.monotonic() - t0) * 1000)
        _audit_tool_call(snap, name, args, ms, True)
        return result
    except Exception as exc:
        logger.exception("Tool %s failed: %s", name, exc)
        _audit_tool_call(snap, name, args, 0, False)
        return {"error": f"Tool '{name}' failed: {str(exc)}"}
    finally:
        _current_snap = None


def _audit_tool_call(user, tool_name, args, ms, ok):
    """Log a tool call to the audit trail."""
    try:
        from app.models.audit import AuditLog
        AuditLog.log_change(
            table_name='agent',
            record_id=0,
            action='TOOL_CALL',
            field_name=tool_name,
            old_value=json.dumps(_truncate_args(args), default=str),
            new_value=json.dumps({'ms': ms, 'ok': ok}),
            user_id=user.id if user else None,
        )
        db.session.commit()
    except Exception:
        db.session.rollback()


def _truncate_args(args, max_len=500):
    """Truncate arg values for audit logging."""
    out = {}
    for k, v in (args or {}).items():
        s = str(v)
        out[k] = s[:max_len] if len(s) > max_len else s
    return out


# ---------------------------------------------------------------------------
# Tool 1: get_context
# ---------------------------------------------------------------------------

def _get_context():
    from app.models.pipe import Pipe
    from app.models.chemical import ChemicalAnalysis
    from app.models.mechanical import MechanicalTest
    from app.models.production_order import ProductionOrder
    from app.models.stage import ProductionStage

    today = date.today()
    iso_cal = today.isocalendar()
    now = datetime.now()
    hour = now.hour
    if 6 <= hour < 14:
        shift = 1
    elif 14 <= hour < 22:
        shift = 2
    else:
        shift = 3

    stages = ProductionStage.active_names()

    return {
        "today": today.isoformat(),
        "iso_week": iso_cal[1],
        "iso_year": iso_cal[0],
        "day_of_week": today.strftime("%A"),
        "current_shift": shift,
        "db_counts": {
            "pipes": Pipe.query.count(),
            "chemical_analyses": ChemicalAnalysis.query.count(),
            "mechanical_tests": MechanicalTest.query.filter_by(status='ACTIVE').count(),
            "production_orders": ProductionOrder.query.count(),
            "pipes_today": Pipe.query.filter_by(production_date=today).count(),
        },
        "production_stages": stages,
        "decision_states": ["WAITING", "ACCEPT", "HOLD", "REJECT", "BLOCKED"],
    }


_register(Tool(
    name="get_context",
    description=(
        "Get today's date, ISO week, current shift, and database row counts. "
        "Call this first if the question involves today, this week, the current shift, "
        "or how much data exists. Cheap — always safe to call."
    ),
    parameters={"type": "object", "properties": {}, "required": []},
    permission=None,
    fn=_get_context,
))

# ---------------------------------------------------------------------------
# Tool 2: describe_schema
# ---------------------------------------------------------------------------

_TABLE_PURPOSES = {
    'pipes': 'Production pipes — the main entity. Each row is one pipe with dimensions, decisions, and stage links.',
    'chemical_analyses': 'Ladle chemical analyses — one row per melt (ladle). Elements: C, Si, Mn, Mg, S, Cr, Cu, Al, P, Pb, CE, MnE, MgE.',
    'mechanical_tests': 'Mechanical test results — tensile, elongation, hardness, nodularity, ferrite, carbides. Linked to pipes and ladles.',
    'pipe_stages': 'Per-pipe stage records — each row is a pipe visiting a production stage with a decision, date, operator, and optional defect.',
    'pipe_stage_history': 'Stage decision history — when a stage decision is overwritten, the old value is pushed here.',
    'production_orders': 'Production orders — customer orders with target quantities, DN, class, dates.',
    'production_stages': 'Admin-configurable production stage definitions (name, order, active flag).',
    'products': 'Product catalog — DN/class/weight/length combinations.',
    'product_parameters': 'Product specs — weight and length parameters per DN/class.',
    'furnaces': 'Melting furnaces (A1, A2, B1, B2).',
    'machines': 'Production machines (CCM, annealing, zinc, etc.).',
    'customers': 'Customer list.',
    'molds': 'Casting molds.',
    'defect_types': 'Defect type definitions (bilingual).',
    'defect_reasons': 'Rejection reason definitions.',
    'decision_types': 'Stage decision type definitions (Accept, Reject, Hold, etc.).',
    'reason_types': 'Reason type categories.',
    'users': 'System users (never expose password_hash).',
    'chat_sessions': 'Chat conversation sessions.',
    'chat_messages': 'Chat messages within sessions.',
    'audit_logs': 'Audit trail — all data changes.',
    'permissions': 'Permission definitions (module, screen, action).',
    'role_permissions': 'Role-to-permission grants.',
    'bundles': 'Pipe bundles for delivery grouping.',
    'attachments': 'File attachments.',
    'non_conformance_actions': 'Corrective actions for non-conforming pipes.',
    'shifts': 'Shift definitions.',
    'engineers': 'Engineer records.',
    'element_specifications': 'Chemical element specification limits.',
    'stage_decision_types': 'Which decision types are available per stage.',
    'stage_defect_types': 'Which defect types apply to each stage.',
    'certificates': 'Customer certificates (warranty, work test, MTC) — which pipes/orders were certified and when.',
    'kpi_alerts': 'KPI alert events raised when a target threshold is crossed (kpi key, value, target, severity, acknowledged).',
    'kpi_alert_runs': 'KPI alert evaluation runs — when the alert engine last ran and what it found.',
}

# JSON columns whose shape the model cannot learn from the type alone.
# Keys are 'table.column'; the value is shown next to the column in
# describe_schema so the model can write ->> / json_extract paths.
_JSON_SHAPES = {
    'pipe_stages.thickness_profile': 'Coating stage: {"cement": [m1..m6], "coating": [m1..m6], "<layer>_std", "<layer>_std_min", "<layer>_std_max"}',
    'pipe_stages.dimension_profile': 'CCM stage: {"thickness": {"positions": {"1".."6": [r1,r2,r3]}, "standard", "standard_min", "standard_max"}, "diameter": {"samples": {"S1": [...]}}, "ovality": {"points": {...}}}',
    'pipe_stages.ovality_profile': 'Annealing stage: {"points": {"D1".."D15": {"x", "y", "ovality"}}} — ovality % = (x-y)/(x+y)*100',
    'pipe_stages.visual_profile': 'Finish stage checklist: {"marking", "ovality", "straightness", "internal_finish", "external_finish"} booleans',
    'pipe_stages.zinc_profile': 'Zinc stage: {"m1", "m2", "area", "c", "mass"} — mass in g/m2 = C*(m2-m1)/area',
    'pipe_stages.ring_profile': 'Cutting stage ring test: {"force" kN, "od_initial", "od_final", "od_diff" mm, "deflection" %}',
    'products.application_profile': 'Application spec: {"standards": [...], "layers": {"thickness"|"cement"|"coating": {min,max,std}}}',
    'production_orders.application_profile': 'Snapshot of the product application spec at order time (same shape as products.application_profile)',
    'chat_messages.tool_calls': 'Agent tool calls made while answering: [{"tool", "args", "summary"}]',
}


def _describe_schema(tables=None):
    from sqlalchemy import inspect as sa_inspect

    inspector = sa_inspect(db.engine)
    all_tables = inspector.get_table_names()

    if tables:
        target_tables = [t for t in tables if t in all_tables]
        if not target_tables:
            return {"error": f"No matching tables. Available: {', '.join(sorted(all_tables))}"}
    else:
        # No args = summary only
        return {
            "tables": {
                t: _TABLE_PURPOSES.get(t, '')
                for t in sorted(all_tables)
                if t != 'alembic_version'
            }
        }

    result = {}
    for table in target_tables:
        columns = inspector.get_columns(table)
        fks = inspector.get_foreign_keys(table)
        fk_map = {}
        for fk in fks:
            for col in fk.get('constrained_columns', []):
                fk_map[col] = f"{fk['referred_table']}.{fk['referred_columns'][0]}" if fk.get('referred_columns') else fk.get('referred_table', '')

        col_info = []
        for c in columns:
            info = {
                "name": c['name'],
                "type": str(c['type']),
                "nullable": c.get('nullable', True),
            }
            if c['name'] in fk_map:
                info['fk'] = fk_map[c['name']]
            shape = _JSON_SHAPES.get(f"{table}.{c['name']}")
            if shape:
                info['json_shape'] = shape
            col_info.append(info)

        result[table] = {
            "purpose": _TABLE_PURPOSES.get(table, ''),
            "columns": col_info,
        }

    return {"tables": result}


_register(Tool(
    name="describe_schema",
    description=(
        "Describe the database schema. With no arguments, returns all table names "
        "and their one-line purposes. With a list of table names, returns columns "
        "(name, type, nullable, FK targets) for those tables. Call this before writing "
        "SQL to learn the actual column names — do not guess."
    ),
    parameters={
        "type": "object",
        "properties": {
            "tables": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Table names to describe in detail. Omit for a summary of all tables.",
            }
        },
        "required": [],
    },
    permission=None,
    fn=_describe_schema,
))

# ---------------------------------------------------------------------------
# Tool 3: run_sql
# ---------------------------------------------------------------------------

_FORBIDDEN_KEYWORDS = re.compile(
    r'\b(?:INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|COPY|'
    r'VACUUM|REINDEX|CALL|DO\s|EXECUTE|MERGE|LOCK)\b',
    re.IGNORECASE,
)

_FORBIDDEN_FUNCTIONS = re.compile(
    r'\b(?:pg_read_file|pg_read_binary_file|pg_ls_dir|pg_sleep|dblink|'
    r'lo_import|lo_export|pg_terminate_backend|pg_reload_conf|set_config|'
    r'current_setting)\b',
    re.IGNORECASE,
)

_PASSWORD_PATTERN = re.compile(r'\bpassword', re.IGNORECASE)

MAX_SQL_LIMIT = 500
MAX_RESULT_BYTES = 40_000

_REDACTED_COLUMNS = re.compile(
    r'(?:password|passwd|secret|token|api_key|hash)',
    re.IGNORECASE,
)

# F15: tables the agent may query. Previously a hard-coded allowlist that
# silently fell behind the schema (certificates, kpi_alerts, kpi_alert_runs
# were unreachable), so the agent kept telling users "that table is not
# allowed". Now: every real table in the database minus a denylist of
# tables that hold nothing a QC question needs.
_DENIED_TABLES = frozenset({
    'alembic_version',
    'permissions', 'role_permissions',
})


def _allowed_tables() -> frozenset:
    """Live set of queryable tables (schema minus denylist)."""
    try:
        from sqlalchemy import inspect as sa_inspect
        names = sa_inspect(db.engine).get_table_names()
    except Exception:
        names = list(_TABLE_PURPOSES.keys())
    return frozenset(n for n in names if n not in _DENIED_TABLES)

_FORBIDDEN_SCHEMAS = re.compile(
    r'\b(?:pg_catalog|information_schema|pg_authid|pg_roles|pg_shadow|pg_user)\b',
    re.IGNORECASE,
)

# Extracts unquoted identifiers that look like table references
_TABLE_REF = re.compile(
    r'\b(?:FROM|JOIN|INTO|TABLE)\s+([a-z_][a-z0-9_]*)\b',
    re.IGNORECASE,
)


def _validate_sql(sql: str) -> Optional[str]:
    """Validate SQL for safety. Returns an error message or None if OK."""
    stripped = sql.strip()
    if not stripped:
        return "Empty SQL query"

    # Shape: must start with SELECT or WITH
    if not re.match(r'^\s*(SELECT|WITH)\b', stripped, re.IGNORECASE):
        return "Only SELECT and WITH...SELECT queries are allowed"

    # Single statement: strip trailing semicolon, reject if any remain
    if stripped.endswith(';'):
        stripped = stripped[:-1].strip()
    if ';' in stripped:
        return "Multiple SQL statements are not allowed"

    # Keyword blacklist
    m = _FORBIDDEN_KEYWORDS.search(stripped)
    if m:
        return f"Forbidden SQL keyword: {m.group()}"

    # Function blacklist
    m = _FORBIDDEN_FUNCTIONS.search(stripped)
    if m:
        return f"Forbidden SQL function: {m.group()}"

    # F15: reject pg_catalog / information_schema outright
    m = _FORBIDDEN_SCHEMAS.search(stripped)
    if m:
        return f"Access to system catalog '{m.group()}' is not allowed"

    # Password column
    if _PASSWORD_PATTERN.search(stripped):
        return "Queries referencing 'password' columns are not allowed"

    # F15: table allowlist — extract table references and reject unknown ones.
    # Collect CTE names so they aren't rejected as unknown tables.
    cte_names = set()
    for m in re.finditer(r'\bWITH\s+(\w+)\s+AS\b', stripped, re.IGNORECASE):
        cte_names.add(m.group(1).lower())
    # Also catch comma-separated CTEs: WITH a AS (...), b AS (...)
    for m in re.finditer(r',\s*(\w+)\s+AS\s*\(', stripped, re.IGNORECASE):
        cte_names.add(m.group(1).lower())

    allowed_tables = _allowed_tables()
    refs = _TABLE_REF.findall(stripped)
    for ref in refs:
        name = ref.lower()
        if name in cte_names:
            continue
        if name not in allowed_tables:
            allowed = ', '.join(sorted(allowed_tables))
            return f"Table '{ref}' is not in the allowed list. Allowed tables: {allowed}"

    return None


def _run_sql(sql, limit=200):
    from sqlalchemy import text

    error = _validate_sql(sql)
    if error:
        return {"error": error}

    # F16: floor limit at 1, clamp at MAX_SQL_LIMIT
    if limit is None:
        limit = 200
    limit = max(1, min(int(limit), MAX_SQL_LIMIT))

    cleaned = sql.strip().rstrip(';').strip()

    dialect = db.engine.dialect.name

    try:
        conn = db.engine.connect()
        try:
            if dialect == 'postgresql':
                trans = conn.begin()
                conn.execute(text("SET TRANSACTION READ ONLY"))
                conn.execute(text("SET LOCAL statement_timeout = '8s'"))
            # F16: newline before closing paren so a trailing line comment
            # (-- ...) doesn't swallow it
            wrapped = f"SELECT * FROM (\n{cleaned}\n) AS _agent_q LIMIT {limit + 1}"
            result = conn.execute(text(wrapped))
            columns = list(result.keys())
            rows = [list(row) for row in result.fetchall()]

            truncated = len(rows) > limit
            if truncated:
                rows = rows[:limit]

            if dialect == 'postgresql':
                trans.rollback()

            # Redact sensitive columns from results (SELECT * bypass)
            redacted = []
            safe_indices = []
            for i, col in enumerate(columns):
                if _REDACTED_COLUMNS.search(col):
                    redacted.append(col)
                else:
                    safe_indices.append(i)
            columns = [columns[i] for i in safe_indices]
            rows = [[row[i] for i in safe_indices] for row in rows]

            # Serialize for JSON
            serialized_rows = []
            for row in rows:
                serialized_rows.append([
                    _serialize_value(v) for v in row
                ])

            payload = {
                "sql": cleaned,
                "columns": columns,
                "rows": serialized_rows,
                "row_count": len(serialized_rows),
                "truncated": truncated,
            }
            if redacted:
                payload["redacted_columns"] = redacted

            # Check result size
            payload_json = json.dumps(payload, default=str)
            if len(payload_json) > MAX_RESULT_BYTES:
                # Truncate rows further
                while len(serialized_rows) > 1 and len(json.dumps(payload, default=str)) > MAX_RESULT_BYTES:
                    serialized_rows.pop()
                    payload['rows'] = serialized_rows
                    payload['row_count'] = len(serialized_rows)
                    payload['truncated'] = True
                payload['note'] = 'Result truncated to fit size limit'

            return payload
        finally:
            conn.close()

    except Exception as exc:
        return {"error": f"SQL execution failed: {str(exc)}"}


def _serialize_value(v):
    if v is None:
        return None
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, (int, float, bool)):
        return v
    return str(v)


_register(Tool(
    name="run_sql",
    description=(
        "Run a read-only SQL SELECT query against the production database. "
        "Use this for questions the curated tools cannot answer. Always call "
        "describe_schema first to learn the actual column names. Only SELECT "
        "and WITH...SELECT are allowed; writes are blocked. Results are capped."
    ),
    parameters={
        "type": "object",
        "properties": {
            "sql": {
                "type": "string",
                "description": "The SELECT query to run.",
            },
            "limit": {
                "type": "integer",
                "description": "Max rows to return (default 200, max 500).",
            },
        },
        "required": ["sql"],
    },
    permission=("chatbot", "sql"),
    fn=_run_sql,
))

# ---------------------------------------------------------------------------
# Tool 4: get_ladle
# ---------------------------------------------------------------------------

def _get_ladle(ladle_id):
    from app.models.chemical import ChemicalAnalysis
    from app.services.decision_service import get_element_decision, ELEMENT_MAP, CODE_TO_FIELD

    analysis = ChemicalAnalysis.query.filter_by(ladle_id=str(ladle_id)).first()
    if not analysis:
        return {"error": f"Ladle '{ladle_id}' not found"}

    # Element values and spec verdicts
    # F3: distinguish optimal (فحص أخيرة فقط), acceptable (other non-تالف),
    # and out-of-spec (تالف or no matching range)
    elements = {}
    element_verdicts = {}
    for field_name, code in ELEMENT_MAP.items():
        val = getattr(analysis, field_name, None)
        if val is not None:
            elements[code] = float(val)
            verdict = get_element_decision(code, val)
            if verdict:
                decision = verdict['decision']
                is_out_of_spec = (decision == 'تالف')
                is_optimal = verdict['in_spec']  # True only for فحص أخيرة فقط
                if is_optimal:
                    band = 'optimal'
                elif is_out_of_spec:
                    band = 'out_of_spec'
                else:
                    band = 'acceptable'
                element_verdicts[code] = {
                    "value": float(val),
                    "decision": decision,
                    "band": band,
                    "in_spec": not is_out_of_spec,
                }

    # Linked pipes
    pipes_data = []
    for p in analysis.pipes.limit(20):
        pipes_data.append({
            "no_code": p.no_code,
            "pipe_code": p.pipe_code,
            "diameter": p.diameter,
            "pipe_class": p.pipe_class,
            "lab_decision": p.lab_decision,
            "final_decision": p.final_decision_value,
            "mechanical_test_role": p.mechanical_test_role,
        })

    # Mechanical tests
    mech_data = []
    for m in analysis.mechanical_tests.limit(10):
        mech_data.append({
            "pipe_code": m.pipe_code,
            "tensile_strength": float(m.tensile_strength) if m.tensile_strength else None,
            "tensile_mpa": float(m.tensile_mpa) if m.tensile_mpa else None,
            "elongation": float(m.elongation) if m.elongation else None,
            "hardness": float(m.hardness) if m.hardness else None,
            "nodularity": float(m.nodularity_percent) if m.nodularity_percent else None,
            "decision": m.decision,
            "status": m.status,
        })

    return {
        "ladle_id": analysis.ladle_id,
        "test_date": analysis.test_date.isoformat() if analysis.test_date else None,
        "furnace": analysis.furnace.furnace_code if analysis.furnace else None,
        "decision": analysis.decision,
        "elements": elements,
        "element_verdicts": element_verdicts,
        "out_of_spec": [code for code, v in element_verdicts.items() if v.get('band') == 'out_of_spec'],
        "pipes_count": analysis.pipes.count() if hasattr(analysis.pipes, 'count') else len(list(analysis.pipes)),
        "pipes": pipes_data,
        "mechanical_tests": mech_data,
    }


_register(Tool(
    name="get_ladle",
    description=(
        "Look up a ladle (chemical analysis / heat) by its ladle_id. Returns the "
        "chemical composition, element-by-element spec verdict (in-spec or out), "
        "furnace, decision, linked pipes with their decisions, and mechanical tests. "
        "Use this when the user asks about a specific ladle or heat number."
    ),
    parameters={
        "type": "object",
        "properties": {
            "ladle_id": {
                "type": "string",
                "description": "The ladle identifier (8-14 digit number).",
            }
        },
        "required": ["ladle_id"],
    },
    permission=("chemical", "list"),
    fn=_get_ladle,
))

# ---------------------------------------------------------------------------
# Tool 5: get_pipe
# ---------------------------------------------------------------------------

def _get_pipe(code):
    from app.models.pipe import Pipe

    pipe = Pipe.query.filter(
        db.or_(
            Pipe.no_code == code,
            Pipe.pipe_code == code,
            Pipe.warehouse_barcode == code,
        )
    ).first()
    if not pipe:
        return {"error": f"Pipe '{code}' not found"}

    stages_info = []
    for stage in (pipe.stages.order_by(None).all() if hasattr(pipe.stages, 'order_by') else list(pipe.stages)):
        info = {
            "stage": stage.stage_name,
            "decision": stage.decision,
            "date": stage.stage_date.isoformat() if stage.stage_date else None,
            "has_defect": stage.has_defect,
        }
        if stage.has_defect:
            info["defect_type"] = stage.defect_type
            info["defect_reason"] = stage.defect_reason
        stages_info.append(info)

    order_info = None
    if pipe.production_order:
        o = pipe.production_order
        order_info = {
            "order_number": o.order_number,
            "customer": o.customer_name,
            "status": o.status,
        }

    product_info = None
    if pipe.product_id:
        from app.models.product import Product
        prod = Product.query.get(pipe.product_id)
        if prod:
            product_info = {"product_code": prod.product_code}

    # History count
    from app.models.pipe import PipeStage
    history_count = db.session.query(db.func.count()).select_from(
        db.Table('pipe_stage_history', db.metadata, autoload_with=db.engine)
    ).filter(db.text(f"pipe_id = {pipe.id}")).scalar() if _table_exists('pipe_stage_history') else 0

    return {
        "no_code": pipe.no_code,
        "pipe_code": pipe.pipe_code,
        "warehouse_barcode": pipe.warehouse_barcode,
        "diameter": pipe.diameter,
        "pipe_class": pipe.pipe_class,
        "production_date": pipe.production_date.isoformat() if pipe.production_date else None,
        "shift": pipe.shift,
        "ladle_id": pipe.ladle_id,
        "mold_number": pipe.mold_number,
        "lab_decision": pipe.lab_decision,
        "final_decision": pipe.final_decision_value,
        "mechanical_test_role": pipe.mechanical_test_role,
        "order": order_info,
        "product": product_info,
        "stages": stages_info,
        "history_change_count": history_count,
    }


def _table_exists(name):
    from sqlalchemy import inspect as sa_inspect
    try:
        return name in sa_inspect(db.engine).get_table_names()
    except Exception:
        return False


_register(Tool(
    name="get_pipe",
    description=(
        "Look up a pipe by its no_code (short serial like N1234), pipe_code "
        "(long composite code), or warehouse_barcode. Returns the pipe details, "
        "all stage decisions with dates and defects, order/customer info, and "
        "lab/final decisions. Use this when the user asks about a specific pipe."
    ),
    parameters={
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "The pipe identifier: no_code, pipe_code, or warehouse_barcode.",
            }
        },
        "required": ["code"],
    },
    permission=("stages", "list"),
    fn=_get_pipe,
))

# ---------------------------------------------------------------------------
# Tool 6: get_order
# ---------------------------------------------------------------------------

def _get_order(order_number):
    from app.models.production_order import ProductionOrder
    from app.models.pipe import Pipe

    order = ProductionOrder.query.filter(
        ProductionOrder.order_number.ilike(f'%{order_number}%')
    ).first()
    if not order:
        return {"error": f"Order '{order_number}' not found"}

    pipes = order.pipes.all() if hasattr(order.pipes, 'all') else list(order.pipes)
    total = len(pipes)

    # F6: use single classifier per pipe, no double-counting
    buckets = {"accept": 0, "reject": 0, "hold": 0, "blocked": 0, "waiting": 0}
    for p in pipes:
        buckets[_classify_decision(p)] += 1

    # Per-stage progress — single query instead of N+1
    from app.models.pipe import PipeStage
    from sqlalchemy import func
    pipe_ids = [p.id for p in pipes]
    stage_progress = {}
    if pipe_ids:
        stage_counts = (
            db.session.query(PipeStage.stage_name, func.count(PipeStage.id))
            .filter(PipeStage.pipe_id.in_(pipe_ids), PipeStage.decision.isnot(None))
            .group_by(PipeStage.stage_name)
            .all()
        )
        for sn, cnt in stage_counts:
            stage_progress[sn] = cnt

    return {
        "order_number": order.order_number,
        "customer": order.customer_name,
        "status": order.status,
        "priority": order.priority,
        "target_quantity": order.target_quantity,
        "produced": total,
        "accept": buckets["accept"],
        "reject": buckets["reject"],
        "hold": buckets["hold"],
        "blocked": buckets["blocked"],
        "waiting": buckets["waiting"],
        "diameter": order.diameter,
        "pipe_class": order.pipe_class,
        "product_code": order.product_code,
        "order_date": order.order_date.isoformat() if order.order_date else None,
        "expected_end_date": order.expected_end_date.isoformat() if order.expected_end_date else None,
        "stage_progress": stage_progress,
    }


_register(Tool(
    name="get_order",
    description=(
        "Look up a production order by its order number. Returns the order details, "
        "customer, target vs produced counts, accept/reject/hold split, and per-stage "
        "progress. Use when the user asks about a specific order."
    ),
    parameters={
        "type": "object",
        "properties": {
            "order_number": {
                "type": "string",
                "description": "The production order number (or a partial match).",
            }
        },
        "required": ["order_number"],
    },
    permission=("orders", "list"),
    fn=_get_order,
))

# ---------------------------------------------------------------------------
# Tool 7: search_pipes
# ---------------------------------------------------------------------------

def _search_pipes(date_from=None, date_to=None, dn=None, pipe_class=None,
                  lab_decision=None, final_decision=None, ladle_id=None,
                  order_number=None, customer=None, shift=None, machine=None,
                  stage=None, stage_decision=None, has_defect=None, limit=50):
    from app.models.pipe import Pipe, PipeStage
    from app.models.production_order import ProductionOrder

    limit = min(int(limit or 50), 200)
    query = Pipe.query

    if date_from:
        query = query.filter(Pipe.production_date >= date_from)
    if date_to:
        query = query.filter(Pipe.production_date <= date_to)
    if dn:
        query = query.filter(Pipe.diameter == int(dn))
    if pipe_class:
        query = query.filter(Pipe.pipe_class.ilike(pipe_class))
    if lab_decision:
        query = query.filter(db.func.upper(Pipe.lab_decision) == lab_decision.upper())
    if final_decision:
        query = query.filter(db.func.upper(Pipe.final_decision_value) == final_decision.upper())
    if ladle_id:
        query = query.filter(Pipe.ladle_id == str(ladle_id))
    if shift:
        query = query.filter(Pipe.shift == int(shift))
    # F4: join ProductionOrder once even when both order_number and customer are given
    if order_number or customer:
        query = query.join(ProductionOrder, Pipe.production_order_id == ProductionOrder.id)
        if order_number:
            query = query.filter(ProductionOrder.order_number.ilike(f'%{order_number}%'))
        if customer:
            query = query.filter(ProductionOrder.customer_name.ilike(f'%{customer}%'))
    if machine:
        from app.models.chemical import Machine
        query = query.join(Machine).filter(Machine.machine_code.ilike(f'%{machine}%'))

    # Stage-level filters require a join
    if stage or stage_decision or has_defect is not None:
        stage_q = query.join(PipeStage, Pipe.id == PipeStage.pipe_id)
        if stage:
            stage_q = stage_q.filter(PipeStage.stage_name.ilike(f'%{stage}%'))
        if stage_decision:
            stage_q = stage_q.filter(db.func.upper(PipeStage.decision) == stage_decision.upper())
        if has_defect is not None:
            stage_q = stage_q.filter(PipeStage.has_defect == bool(has_defect))
        query = stage_q.distinct()

    total_count = query.count()
    pipes = query.order_by(Pipe.production_date.desc(), Pipe.id.desc()).limit(limit).all()

    results = []
    for p in pipes:
        results.append({
            "no_code": p.no_code,
            "pipe_code": p.pipe_code,
            "diameter": p.diameter,
            "pipe_class": p.pipe_class,
            "production_date": p.production_date.isoformat() if p.production_date else None,
            "shift": p.shift,
            "ladle_id": p.ladle_id,
            "lab_decision": p.lab_decision,
            "final_decision": p.final_decision_value,
        })

    return {
        "pipes": results,
        "count": len(results),
        "total_matching": total_count,
        "truncated": total_count > limit,
    }


_register(Tool(
    name="search_pipes",
    description=(
        "Search for pipes with multiple filter criteria. All filters are optional. "
        "Returns matching pipes with basic info. Use for questions like 'how many "
        "pipes are on HOLD', 'show rejected pipes this week', 'pipes from shift 2'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "date_from": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
            "date_to": {"type": "string", "description": "End date (YYYY-MM-DD)"},
            "dn": {"type": "string", "description": "Diameter nominal (e.g. 300, 500)"},
            "pipe_class": {"type": "string", "description": "Pipe class (K9, C25, etc.)"},
            "lab_decision": {"type": "string", "description": "Lab decision: WAITING, ACCEPT, REJECT, HOLD, BLOCKED"},
            "final_decision": {"type": "string", "description": "Final decision value"},
            "ladle_id": {"type": "string", "description": "Ladle ID to filter by"},
            "order_number": {"type": "string", "description": "Production order number"},
            "customer": {"type": "string", "description": "Customer name (partial match)"},
            "shift": {"type": "string", "description": "Shift number (1, 2, or 3)"},
            "machine": {"type": "string", "description": "Machine code"},
            "stage": {"type": "string", "description": "Stage name to filter by"},
            "stage_decision": {"type": "string", "description": "Decision at that stage"},
            "has_defect": {"type": "boolean", "description": "Filter for pipes with defects"},
            "limit": {"type": "integer", "description": "Max results (default 50, max 200)"},
        },
        "required": [],
    },
    permission=("stages", "list"),
    fn=_search_pipes,
))

# ---------------------------------------------------------------------------
# Tool 8: production_summary
# ---------------------------------------------------------------------------

def _classify_decision(pipe):
    """One case-insensitive classifier over the effective decision.

    Uses final_decision_value when set, otherwise lab_decision.
    Reuses PipeStage.classify_decision for consistent accept/reject mapping.
    Returns 'accept', 'reject', 'hold', 'blocked', or 'waiting'.
    """
    from app.models.pipe import PipeStage

    raw = (pipe.final_decision_value or '').strip() or (pipe.lab_decision or '').strip()
    if not raw:
        return 'waiting'
    upper = raw.upper()
    if upper == 'BLOCKED':
        return 'blocked'
    if upper == 'FROZEN':
        return 'blocked'
    if upper == 'HOLD':
        return 'hold'
    cls = PipeStage.classify_decision(raw)
    if cls == 'accept':
        return 'accept'
    if cls == 'reject':
        return 'reject'
    return 'waiting'


def _production_summary(date_from=None, date_to=None, group_by="day"):
    from app.models.pipe import Pipe, PipeStage
    from app.models.production_order import ProductionOrder
    from sqlalchemy import func
    from sqlalchemy.orm import joinedload

    valid_groups = {'day', 'week', 'month', 'shift', 'dn', 'pipe_class', 'machine', 'order', 'customer', 'ladle', 'stage', 'engineer'}
    if group_by not in valid_groups:
        return {"error": f"Invalid group_by. Must be one of: {', '.join(sorted(valid_groups))}"}

    query = Pipe.query
    if date_from:
        query = query.filter(Pipe.production_date >= date_from)
    if date_to:
        query = query.filter(Pipe.production_date <= date_to)

    period = f"{date_from or 'all'} to {date_to or 'all'}"

    if group_by == 'stage':
        query = query.join(PipeStage, Pipe.id == PipeStage.pipe_id)
        rows = query.with_entities(
            PipeStage.stage_name,
            func.count(Pipe.id),
        ).group_by(PipeStage.stage_name).all()
        result = []
        grand = 0
        for stage_name, cnt in rows:
            result.append({"group": stage_name or 'unknown', "total": cnt,
                           "accept": 0, "reject": 0, "hold": 0, "blocked": 0, "waiting": cnt,
                           "accept_rate": 0})
            grand += cnt
        return {"period": period, "group_by": group_by, "groups": result, "grand_total": grand}

    # F13: eager-load order relation to avoid N+1
    if group_by in ('order', 'customer'):
        from sqlalchemy.orm import contains_eager
        query = query.outerjoin(ProductionOrder, Pipe.production_order_id == ProductionOrder.id)
        query = query.options(contains_eager(Pipe.production_order))

    pipes = query.all()

    # F13+F5: pre-fetch pipe→machine mapping in one query instead of per-pipe stages loop
    pipe_machine = {}
    if group_by == 'machine':
        from app.models.chemical import Machine
        pipe_ids = [p.id for p in pipes]
        if pipe_ids:
            rows = (
                db.session.query(PipeStage.pipe_id, Machine.machine_code)
                .join(Machine, PipeStage.machine_id == Machine.id)
                .filter(PipeStage.pipe_id.in_(pipe_ids))
                .distinct()
                .all()
            )
            for pid, mc in rows:
                if pid not in pipe_machine:
                    pipe_machine[pid] = mc

    groups = {}
    for p in pipes:
        if group_by == 'day':
            key = p.production_date.isoformat() if p.production_date else 'unknown'
        elif group_by == 'week':
            key = f"W{p.production_date.isocalendar()[1]}" if p.production_date else 'unknown'
        elif group_by == 'month':
            key = p.production_date.strftime('%Y-%m') if p.production_date else 'unknown'
        elif group_by == 'shift':
            key = f"Shift {p.shift}" if p.shift else 'unknown'
        elif group_by == 'dn':
            key = f"DN{p.diameter}" if p.diameter else 'unknown'
        elif group_by == 'pipe_class':
            key = p.pipe_class or 'unknown'
        elif group_by == 'machine':
            key = pipe_machine.get(p.id, 'unknown')
        elif group_by == 'order':
            key = p.production_order.order_number if p.production_order else 'no order'
        elif group_by == 'customer':
            key = p.production_order.customer_name if p.production_order else 'no customer'
        elif group_by == 'ladle':
            key = p.ladle_id or 'unknown'
        elif group_by == 'engineer':
            key = p.shift_engineer or 'unknown'
        else:
            key = 'all'

        if key not in groups:
            groups[key] = {"total": 0, "accept": 0, "reject": 0, "hold": 0, "blocked": 0, "waiting": 0}
        g = groups[key]
        g["total"] += 1

        bucket = _classify_decision(p)
        g[bucket] += 1

    result = []
    for key in sorted(groups.keys()):
        g = groups[key]
        g["group"] = key
        g["accept_rate"] = round(g["accept"] / g["total"] * 100, 1) if g["total"] else 0
        result.append(g)

    return {
        "period": period,
        "group_by": group_by,
        "groups": result,
        "grand_total": sum(g["total"] for g in result),
    }


_register(Tool(
    name="production_summary",
    description=(
        "Get a production summary grouped by day/week/month/shift/"
        "dn/pipe_class/machine/order/customer/ladle/stage/engineer. Returns per-group "
        "counts of total, accept, reject, hold, blocked, waiting, and accept rate. "
        "date_from and date_to are optional — omit both for all time. "
        "Do not invent a date range the user did not specify."
    ),
    parameters={
        "type": "object",
        "properties": {
            "date_from": {"type": "string", "description": "Start date (YYYY-MM-DD). Omit for all time."},
            "date_to": {"type": "string", "description": "End date (YYYY-MM-DD). Omit for all time."},
            "group_by": {
                "type": "string",
                "description": "Group by: day, week, month, shift, dn, pipe_class, machine, order, customer, ladle, stage, engineer",
            },
        },
        "required": [],
    },
    permission=("reports", "production_summary"),
    fn=_production_summary,
))

# ---------------------------------------------------------------------------
# Tool 9: defect_analysis
# ---------------------------------------------------------------------------

def _defect_analysis(date_from=None, date_to=None, group_by="defect_type"):
    from app.models.pipe import Pipe, PipeStage
    from sqlalchemy import text

    valid_groups = {'defect_type', 'defect_reason', 'stage', 'dn', 'shift', 'machine'}
    if group_by not in valid_groups:
        return {"error": f"Invalid group_by. Must be one of: {', '.join(sorted(valid_groups))}"}

    period = f"{date_from or 'all'} to {date_to or 'all'}"

    # F7: union pipe_stages + pipe_stage_history for defect records
    dialect = db.engine.dialect.name
    has_history = _table_exists('pipe_stage_history')

    date_filters = []
    if date_from:
        date_filters.append(f"p.production_date >= '{date_from}'")
    if date_to:
        date_filters.append(f"p.production_date <= '{date_to}'")
    date_clause = (" AND " + " AND ".join(date_filters)) if date_filters else ""

    parts = [f"""
        SELECT ps.defect_type, ps.defect_reason, ps.stage_name,
               m.machine_code, p.diameter, p.shift, p.id as pipe_id
        FROM pipe_stages ps
        JOIN pipes p ON ps.pipe_id = p.id
        LEFT JOIN machines m ON ps.machine_id = m.id
        WHERE ps.has_defect = true{date_clause}
    """]

    if has_history:
        parts.append(f"""
        UNION ALL
        SELECT h.defect_type, h.defect_reason, h.stage_name,
               h.machine_code, p.diameter, p.shift, p.id as pipe_id
        FROM pipe_stage_history h
        JOIN pipes p ON h.pipe_id = p.id
        WHERE h.has_defect = true{date_clause}
        """)

    sql = "\n".join(parts)
    rows = db.session.execute(text(sql)).fetchall()

    groups = {}
    total = 0
    for row in rows:
        total += 1
        defect_type, defect_reason, stage_name, machine_code, diameter, shift, pipe_id = row

        if group_by == 'defect_type':
            key = defect_type or 'unknown'
        elif group_by == 'defect_reason':
            key = defect_reason or 'unknown'
        elif group_by == 'stage':
            key = stage_name or 'unknown'
        elif group_by == 'dn':
            key = f"DN{diameter}" if diameter else 'unknown'
        elif group_by == 'shift':
            key = f"Shift {shift}" if shift else 'unknown'
        elif group_by == 'machine':
            key = machine_code or 'unknown'
        else:
            key = 'all'

        groups[key] = groups.get(key, 0) + 1

    result = []
    for key, count in sorted(groups.items(), key=lambda x: -x[1]):
        result.append({
            "group": key,
            "count": count,
            "share_pct": round(count / total * 100, 1) if total else 0,
        })

    return {
        "period": period,
        "group_by": group_by,
        "total_defects": total,
        "groups": result,
    }


_register(Tool(
    name="defect_analysis",
    description=(
        "Analyze defects grouped by defect_type/defect_reason/"
        "stage/dn/shift/machine. Includes both current and historical (re-decided) "
        "defect records. Returns counts, share of total, and top contributors. "
        "date_from and date_to are optional — omit both for all time. "
        "Do not invent a date range the user did not specify."
    ),
    parameters={
        "type": "object",
        "properties": {
            "date_from": {"type": "string", "description": "Start date (YYYY-MM-DD). Omit for all time."},
            "date_to": {"type": "string", "description": "End date (YYYY-MM-DD). Omit for all time."},
            "group_by": {
                "type": "string",
                "description": "Group by: defect_type, defect_reason, stage, dn, shift, machine",
            },
        },
        "required": [],
    },
    permission=("reports", "defect_analysis"),
    fn=_defect_analysis,
))

# ---------------------------------------------------------------------------
# Tool 10: quality_stats
# ---------------------------------------------------------------------------

def _quality_stats(metric, name, date_from=None, date_to=None):
    from app.services.spc_service import (
        MECHANICAL_CHARACTERISTICS, CHEMICAL_CHARACTERISTICS,
        build_series, imr_limits,
    )
    from app.services.capability_service import capability_indices, spec_limits_for

    valid_metrics = {'element', 'mechanical', 'dimension'}
    if metric not in valid_metrics:
        return {"error": f"Invalid metric type. Must be one of: {', '.join(sorted(valid_metrics))}"}

    # Map name to characteristic key
    if metric == 'element':
        char_map = {v[1]: k for k, v in CHEMICAL_CHARACTERISTICS.items()}
        char_key = char_map.get(name) or name
    elif metric == 'mechanical':
        char_map = {v[1]: k for k, v in MECHANICAL_CHARACTERISTICS.items()}
        char_key = char_map.get(name) or name
    else:
        char_key = name

    filters = {}
    if date_from:
        filters['date_from'] = date_from
    if date_to:
        filters['date_to'] = date_to

    try:
        series = build_series(char_key, filters)
    except ValueError as e:
        return {"error": str(e)}

    points = series['points']
    if not points:
        return {"error": f"No data found for {name}", "n": 0}

    values = [p['value'] for p in points]
    n = len(values)
    mean_val = sum(values) / n
    variance = sum((v - mean_val) ** 2 for v in values) / n if n > 1 else 0
    sd = variance ** 0.5
    min_val = min(values)
    max_val = max(values)

    result = {
        "metric": metric,
        "name": name,
        "characteristic": char_key,
        "n": n,
        "mean": round(mean_val, 4),
        "sd": round(sd, 4),
        "min": round(min_val, 4),
        "max": round(max_val, 4),
    }

    # F2: spec_limits_for returns a dict, capability_indices returns a dict
    limits = spec_limits_for(char_key)
    if limits:
        lsl = limits.get('lsl')
        usl = limits.get('usl')
        result['lsl'] = lsl
        result['usl'] = usl
        result['spec_source'] = limits.get('source')
        if sd > 0:
            cap = capability_indices(values, lsl, usl)
            if cap:
                result['cp'] = round(cap['cp'], 3) if cap.get('cp') is not None else None
                result['cpk'] = round(cap['cpk'], 3) if cap.get('cpk') is not None else None
                result['pp'] = round(cap['pp'], 3) if cap.get('pp') is not None else None
                result['ppk'] = round(cap['ppk'], 3) if cap.get('ppk') is not None else None

        # Out-of-spec points
        oos = []
        for p in points:
            v = p['value']
            if (lsl is not None and v < lsl) or (usl is not None and v > usl):
                oos.append({
                    "value": round(v, 4),
                    "source_id": p.get('source_id'),
                    "subgroup": p.get('subgroup'),
                    "date": p['date'].isoformat() if hasattr(p['date'], 'isoformat') else str(p['date']),
                })
        result['out_of_spec_count'] = len(oos)
        result['out_of_spec'] = oos[:20]

    return result


_register(Tool(
    name="quality_stats",
    description=(
        "Get statistical quality data for a specific measurement. Metric types: "
        "'element' (chemical: carbon, silicon, etc.), 'mechanical' (tensile_strength, "
        "elongation, hardness, nodularity, carbides), 'dimension'. Returns n, mean, "
        "std dev, min, max, spec limits, Cp, Cpk, Pp, Ppk, and out-of-spec counts. "
        "date_from and date_to are optional — omit both for all time."
    ),
    parameters={
        "type": "object",
        "properties": {
            "metric": {
                "type": "string",
                "description": "Type: element, mechanical, or dimension",
            },
            "name": {
                "type": "string",
                "description": "Property name, e.g. 'carbon', 'tensile_strength', 'elongation', 'hardness'",
            },
            "date_from": {"type": "string", "description": "Start date (YYYY-MM-DD, optional)"},
            "date_to": {"type": "string", "description": "End date (YYYY-MM-DD, optional)"},
        },
        "required": ["metric", "name"],
    },
    permission=("reports", "spc"),
    fn=_quality_stats,
))

# ---------------------------------------------------------------------------
# Tool 11: find_page
# ---------------------------------------------------------------------------

def _find_page(query):
    from app.services.site_map import search_pages

    pages = search_pages(query, user=_current_snap, limit=8)
    return {
        "query": query,
        "results": [
            {
                "url": p['url'],
                "title_en": p['title_en'],
                "title_ar": p.get('title_ar', ''),
                "purpose": p['purpose'],
                "area": p['area'],
            }
            for p in pages
        ],
        "count": len(pages),
    }


_register(Tool(
    name="find_page",
    description=(
        "Search for pages in the application by keyword. Returns matching pages "
        "with URL, title, and purpose, filtered for the current user's permissions. "
        "Use when the user asks 'where do I do X' or 'where can I find Y'. "
        "Link pages as markdown links in your answer. "
        "IMPORTANT: if this tool returns zero results, tell the user you could not "
        "find a matching page. Never guess or invent a URL that was not in the results."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query — a keyword or phrase describing what the user is looking for.",
            }
        },
        "required": ["query"],
    },
    permission=None,
    fn=_find_page,
))
