"""
Chatbot Context Service — Gathers real-time database context based on user
message intent, injects it into the AI prompt so the chatbot can answer
questions about actual pipes, ladles, orders, and production data.

No vector DB needed — uses keyword/regex intent detection + structured SQL.
"""
import re
from datetime import date, timedelta
from collections import defaultdict

from app import db
from app.models.pipe import Pipe, PipeStage
from app.models.chemical import ChemicalAnalysis
from app.models.mechanical import MechanicalTest
from app.models.production_order import ProductionOrder


# ---------------------------------------------------------------------------
# Intent detection patterns
# ---------------------------------------------------------------------------

# Ladle ID pattern: 8-14 digits (like 4713012026, 131032026)
LADLE_PATTERN = re.compile(r'\b(\d{8,14})\b')

# Pipe code patterns: N followed by digits, or alphanumeric with dashes
PIPE_PATTERN = re.compile(r'\b([A-Z]\d{3,6})\b', re.IGNORECASE)

# Order pattern: PO- or MO followed by digits
ORDER_PATTERN = re.compile(r'\b((?:PO|MO)[-\d]+)\b', re.IGNORECASE)

# Keywords grouped by intent
INTENT_KEYWORDS = {
    'today_summary': [
        'today', 'اليوم', 'production', 'انتاج', 'summary', 'ملخص',
        'how many', 'كم', 'count', 'عدد',
    ],
    'rejections': [
        'reject', 'مرفوض', 'تالف', 'defect', 'عيب', 'failed', 'فشل',
        'scrap', 'خردة',
    ],
    'waiting': [
        'waiting', 'انتظار', 'pending', 'معلق', 'mechanical', 'ميكانيكي',
        'lab', 'معمل',
    ],
    'trend': [
        'trend', 'اتجاه', 'statistics', 'احصائيات', 'week', 'اسبوع',
        'month', 'شهر', 'performance', 'أداء',
    ],
    'chemical': [
        'chemical', 'كيميائي', 'element', 'عنصر', 'carbon', 'silicon',
        'ladle', 'مغرفة', 'furnace', 'فرن',
    ],
    'delivery': [
        'deliver', 'تسليم', 'shipped', 'شحن', 'bundle', 'حزمة',
    ],
}


def _detect_intents(message):
    """Return set of matched intent categories from the message."""
    msg_lower = message.lower()
    matched = set()
    for intent, keywords in INTENT_KEYWORDS.items():
        if any(kw in msg_lower for kw in keywords):
            matched.add(intent)
    return matched


def _extract_identifiers(message):
    """Extract specific IDs from the message."""
    ids = {}
    ladle_match = LADLE_PATTERN.search(message)
    if ladle_match:
        ids['ladle_id'] = ladle_match.group(1)
    pipe_match = PIPE_PATTERN.search(message)
    if pipe_match:
        ids['pipe_code'] = pipe_match.group(1)
    order_match = ORDER_PATTERN.search(message)
    if order_match:
        ids['order_number'] = order_match.group(1)
    return ids


# ---------------------------------------------------------------------------
# Query functions — each returns a formatted string
# ---------------------------------------------------------------------------

def _query_today_summary():
    today = date.today()
    pipes_today = Pipe.query.filter_by(production_date=today).all()
    total = len(pipes_today)
    accepted = sum(1 for p in pipes_today if (p.final_decision_value or '') == 'ACCEPT')
    rejected = sum(1 for p in pipes_today if (p.final_decision_value or '') == 'REJECT')
    hold = sum(1 for p in pipes_today if (p.final_decision_value or '') == 'HOLD')
    pending = total - accepted - rejected - hold

    chem_today = ChemicalAnalysis.query.filter_by(test_date=today).count()
    mech_today = MechanicalTest.query.filter_by(test_date=today, status='ACTIVE').count()

    by_shift = defaultdict(int)
    by_dn = defaultdict(int)
    for p in pipes_today:
        by_shift[p.shift or 0] += 1
        by_dn[p.diameter or 0] += 1

    lines = [
        f"Today ({today}):",
        f"  Pipes produced: {total} (Accepted: {accepted}, Rejected: {rejected}, Hold: {hold}, Pending: {pending})",
        f"  Chemical analyses: {chem_today}",
        f"  Mechanical tests: {mech_today}",
        f"  By shift: {dict(by_shift)}",
        f"  By diameter: {dict(by_dn)}",
    ]
    return '\n'.join(lines)


def _query_rejections():
    week_ago = date.today() - timedelta(days=7)
    rejected_pipes = Pipe.query.filter(
        Pipe.production_date >= week_ago,
        Pipe.final_decision_value == 'REJECT',
    ).order_by(Pipe.production_date.desc()).limit(20).all()

    if not rejected_pipes:
        return "No rejected pipes in the last 7 days."

    lines = [f"Rejected pipes (last 7 days): {len(rejected_pipes)}"]
    for p in rejected_pipes[:10]:
        lines.append(
            f"  - {p.no_code} | DN{p.diameter} | {p.production_date} | "
            f"Lab: {p.lab_decision} | Ladle: {p.ladle_id}"
        )
    if len(rejected_pipes) > 10:
        lines.append(f"  ... and {len(rejected_pipes) - 10} more")

    # Top defect types
    defects = defaultdict(int)
    for p in rejected_pipes:
        for s in p.stages:
            if s.has_defect and s.defect_type:
                defects[s.defect_type] += 1
    if defects:
        top = sorted(defects.items(), key=lambda x: x[1], reverse=True)[:5]
        lines.append("  Top defects: " + ', '.join(f"{d}({c})" for d, c in top))

    return '\n'.join(lines)


def _query_waiting():
    waiting_pipes = Pipe.query.filter(
        Pipe.lab_decision == 'WAITING',
        Pipe.mechanical_test_role.isnot(None),
    ).order_by(Pipe.production_date.desc()).limit(20).all()

    if not waiting_pipes:
        return "No pipes currently waiting for mechanical test."

    lines = [f"Pipes WAITING for mechanical test: {len(waiting_pipes)}"]
    for p in waiting_pipes[:10]:
        lines.append(
            f"  - {p.no_code} | DN{p.diameter} | Role: {p.mechanical_test_role} | Ladle: {p.ladle_id}"
        )
    return '\n'.join(lines)


def _query_trends():
    today = date.today()
    month_ago = today - timedelta(days=30)
    pipes = Pipe.query.filter(Pipe.production_date >= month_ago).all()
    total = len(pipes)
    accepted = sum(1 for p in pipes if (p.final_decision_value or '') == 'ACCEPT')
    rejected = sum(1 for p in pipes if (p.final_decision_value or '') == 'REJECT')

    # Weekly breakdown
    weeks = defaultdict(lambda: {'total': 0, 'accept': 0, 'reject': 0})
    for p in pipes:
        week = p.production_date.isocalendar()[1]
        weeks[week]['total'] += 1
        fd = (p.final_decision_value or '').upper()
        if fd == 'ACCEPT':
            weeks[week]['accept'] += 1
        elif fd == 'REJECT':
            weeks[week]['reject'] += 1

    lines = [
        f"30-day trend (since {month_ago}):",
        f"  Total: {total} | Accepted: {accepted} ({round(accepted/total*100,1) if total else 0}%) | Rejected: {rejected}",
    ]
    for week_num in sorted(weeks.keys()):
        w = weeks[week_num]
        lines.append(f"  Week {week_num}: {w['total']} pipes, {w['accept']} acc, {w['reject']} rej")

    return '\n'.join(lines)


def _query_ladle(ladle_id):
    analysis = ChemicalAnalysis.query.filter_by(ladle_id=ladle_id).first()
    if not analysis:
        return f"Ladle {ladle_id}: Not found in database."

    pipes = list(analysis.pipes)
    mech = list(analysis.mechanical_tests)

    lines = [
        f"Ladle {ladle_id}:",
        f"  Test date: {analysis.test_date}",
        f"  Furnace: {analysis.furnace.furnace_code if analysis.furnace else 'N/A'}",
        f"  Decision: {analysis.decision}",
        f"  Elements: C={analysis.carbon}, Si={analysis.silicon}, Mg={analysis.magnesium}, "
        f"Cu={analysis.copper}, Cr={analysis.chromium}, S={analysis.sulfur}, Mn={analysis.manganese}, "
        f"P={analysis.phosphorus}",
        f"  CE={analysis.carbon_equivalent}, MnE={analysis.manganese_equivalent}, MgE={analysis.magnesium_equivalent}",
        f"  Linked pipes: {len(pipes)}",
    ]
    for p in pipes[:5]:
        lines.append(
            f"    - {p.no_code} | DN{p.diameter} | Lab: {p.lab_decision} | Final: {p.final_decision_value or 'pending'} | Role: {p.mechanical_test_role}"
        )
    if mech:
        lines.append(f"  Mechanical tests: {len(mech)}")
        for m in mech[:3]:
            lines.append(
                f"    - Pipe {m.pipe_code} | Tensile: {m.tensile_strength} KgF/mm² ({m.tensile_mpa} MPa) | "
                f"Decision: {m.decision} | Status: {m.status}"
            )

    return '\n'.join(lines)


def _query_pipe(identifier):
    pipe = Pipe.query.filter(
        db.or_(Pipe.no_code == identifier, Pipe.pipe_code.contains(identifier))
    ).first()
    if not pipe:
        return f"Pipe '{identifier}': Not found."

    stages_info = []
    for sn in Pipe.STAGES:
        s = pipe.get_stage(sn)
        if s and s.decision:
            stages_info.append(f"{sn}={s.decision}")

    lines = [
        f"Pipe {pipe.no_code} ({pipe.pipe_code}):",
        f"  DN{pipe.diameter} | Class: {pipe.pipe_class} | Date: {pipe.production_date} | Shift: {pipe.shift}",
        f"  Ladle: {pipe.ladle_id} | Mold: {pipe.mold_number}",
        f"  Lab decision: {pipe.lab_decision} | Final decision: {pipe.final_decision_value or 'pending'}",
        f"  Mech role: {pipe.mechanical_test_role}",
        f"  Stages: {', '.join(stages_info) if stages_info else 'No stages recorded'}",
    ]
    if pipe.production_order:
        lines.append(f"  Order: {pipe.production_order.order_number} | Customer: {pipe.production_order.customer_name}")

    return '\n'.join(lines)


def _query_order(order_number):
    order = ProductionOrder.query.filter(
        ProductionOrder.order_number.ilike(f'%{order_number}%')
    ).first()
    if not order:
        return f"Order '{order_number}': Not found."

    pipes = list(order.pipes)
    accepted = sum(1 for p in pipes if (p.final_decision_value or '') == 'ACCEPT')
    rejected = sum(1 for p in pipes if (p.final_decision_value or '') == 'REJECT')

    lines = [
        f"Order {order.order_number}:",
        f"  Customer: {order.customer_name} | Status: {order.status}",
        f"  Target: {order.target_quantity} | Produced: {len(pipes)} | Accepted: {accepted} | Rejected: {rejected}",
        f"  DN: {order.diameter} | Class: {order.pipe_class}",
        f"  Product: {order.product_code}",
        f"  Date: {order.order_date} → {order.expected_end_date or 'no deadline'}",
        f"  Progress: {order.progress_percentage}%",
    ]
    return '\n'.join(lines)


def _query_delivery():
    week_ago = date.today() - timedelta(days=7)
    from app.models.stage import ProductionStage
    delivery_name = ProductionStage.name_for_code("delivery")
    deliveries = PipeStage.query.filter(
        PipeStage.stage_name == delivery_name,
        PipeStage.delivery_date >= week_ago,
    ).order_by(PipeStage.delivery_date.desc()).limit(10).all()

    if not deliveries:
        return "No deliveries in the last 7 days."

    lines = [f"Recent deliveries (last 7 days): {len(deliveries)}"]
    for d in deliveries:
        lines.append(
            f"  - {d.pipe.no_code if d.pipe else '?'} | {d.delivery_date} | "
            f"Customer: {d.delivery_customer} | Receipt: {d.delivery_receipt} | Bundle: {d.bundle_number}"
        )
    return '\n'.join(lines)


def _query_general_stats():
    """Always-included baseline context."""
    total_pipes = Pipe.query.count()
    total_ladles = ChemicalAnalysis.query.count()
    total_orders = ProductionOrder.query.count()
    total_mech = MechanicalTest.query.filter_by(status='ACTIVE').count()
    today_count = Pipe.query.filter_by(production_date=date.today()).count()

    return (
        f"Database totals: {total_pipes} pipes, {total_ladles} ladles, "
        f"{total_orders} production orders, {total_mech} mechanical tests. "
        f"Today: {today_count} pipes produced."
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def gather_context(message):
    """Analyze user message and return relevant database context as a string.

    Called before each chatbot AI request. The returned string is injected
    into the system prompt so the AI can reference real data.
    """
    intents = _detect_intents(message)
    ids = _extract_identifiers(message)

    sections = []

    # Always include general stats (one line)
    sections.append(_query_general_stats())

    # Specific identifier lookups
    if ids.get('ladle_id'):
        sections.append(_query_ladle(ids['ladle_id']))
    if ids.get('pipe_code'):
        sections.append(_query_pipe(ids['pipe_code']))
    if ids.get('order_number'):
        sections.append(_query_order(ids['order_number']))

    # Intent-driven queries
    if 'today_summary' in intents:
        sections.append(_query_today_summary())
    if 'rejections' in intents:
        sections.append(_query_rejections())
    if 'waiting' in intents:
        sections.append(_query_waiting())
    if 'trend' in intents:
        sections.append(_query_trends())
    if 'delivery' in intents:
        sections.append(_query_delivery())
    if 'chemical' in intents and not ids.get('ladle_id'):
        # Show latest ladle if no specific one requested
        latest = ChemicalAnalysis.query.order_by(ChemicalAnalysis.id.desc()).first()
        if latest:
            sections.append(_query_ladle(latest.ladle_id))

    # Cap total context length (~2000 chars max to leave room for conversation)
    context = '\n\n'.join(sections)
    if len(context) > 2500:
        context = context[:2500] + '\n... (context truncated)'

    return context
