"""Traceability canvas — the plant's work, wired and lit.

Three views over the same node/edge model, switched by mode:

- ``live``  what is stopped right now, across the whole plant, hung off the
            stage it stopped at. The shift view.
- ``order`` one production order end to end: its heats, their sampling
            scheme, the mechanical tests, the pipes and their stage chains.
- ``pipe``  one pipe cradle to grave on a single line — the certificate you
            hand a customer.

Layout is swim lanes. Each heat owns a horizontal band and its own children
sit inside it, so a ladle's pipes are beside that ladle rather than somewhere
down a shared column. The first cut used one global lane counter per column
(godhome's algorithm, which assumes a nearly-linear phase list) and an order
with seven heats came out 2894px tall with a ladle's children nowhere near it.

Routing is not invented here — it is read from where the decision engine
reads it:

- ladle decision → sampling scheme:  pipe_decision_service.DECISION_SCENARIO
- mechanical → pipe verdict:         pipe_decision_service.propagate_mechanical_result
- stage → next stage:                ProductionStage.active_names()

State classification reuses nonconformance_service, the widest and only
case-insensitive decision vocabulary in the app. Nothing else in this module
may classify a decision string.
"""

from collections import defaultdict
from datetime import datetime

from app.models.chemical import ChemicalAnalysis
from app.models.mechanical import MechanicalTest
from app.models.pipe import Pipe, PipeStage
from app.models.production_order import ProductionOrder
from app.models.stage import ProductionStage
from app.models.stage_history import PipeStageHistory
from app.models.audit import AuditLog
from app.services import nonconformance_service as nc
from app.services import decision_service
from app.services.pipe_decision_service import DECISION_SCENARIO


MODES = ("live", "order", "pipe")


# ---------------------------------------------------------------------------
# State — the one resolver every node colour comes from
# ---------------------------------------------------------------------------

_PIPE_BLOCKED = {"BLOCKED"}
_PIPE_WAIT = {"WAITING"}

# Ladle inspect-level decisions all mean "the chemistry passed, at this
# sampling level" — only تالف / Reject is a failure.
_LADLE_PASS_SCENARIOS = {"LAST_ONLY", "FIRST_LAST", "FULL_100"}

# Amber decisions that mean the material physically goes back and the step
# runs again — these get a backward wire on the canvas.
REWORK_DECISIONS = {
    "rework", "إعادة عمل",
    "retest", "إعادة اختبار",
    "resample", "إعادة عينة",
    "reheat treatment", "إعادة معالجة حرارية",
}

# Severity order for the issues panel: worst first.
_SEVERITY = {"fail": 0, "blocked": 1, "hold": 2}

STATES = ("pass", "fail", "hold", "blocked", "waiting", "none")

# The sampling scheme each ladle decision selects, as a label.
SCENARIO_LABELS = {
    "LAST_ONLY": ("Last only", "فحص أخيرة فقط"),
    "FIRST_LAST": ("1st & Last", "فحص أولى وأخيرة"),
    "FULL_100": ("100% test", "فحص الشحنة 100%"),
    "REJECT": ("Rejected", "تالف"),
}


def state_of(value):
    """Classify any stored decision string into a canvas state.

    Covers all four vocabularies that meet on this screen: the Arabic ladle
    inspect levels, the uppercase pipe lab decisions, the mixed-case bilingual
    stage strings, and the mechanical ACCEPT/REJECT. Built on
    nonconformance_service so amber states (Rework, Retest, DownGrade …)
    classify as hold rather than vanishing into "pending".
    """
    if value in (None, ""):
        return "none"
    v = str(value).strip()
    upper = v.upper()
    if upper in _PIPE_BLOCKED:
        return "blocked"
    if upper in _PIPE_WAIT:
        return "waiting"
    if nc.is_reject(v):
        return "fail"
    if nc.is_non_conforming(v):
        return "hold"
    if PipeStage.classify_decision(v) == "accept":
        return "pass"
    return "none"


def effective_pipe_state(pipe):
    """The pipe's live verdict.

    final_decision_value is sparsely populated on real data and is never
    cleared once written, so lab_decision is the fallback signal — the same
    pattern bi_service uses.
    """
    return state_of(pipe.final_decision_value or pipe.lab_decision)


def is_rework(value):
    """True when a decision means the step runs again (backward wire)."""
    if not value:
        return False
    return str(value).strip().lower() in REWORK_DECISIONS


def worst(states):
    """The most severe state in an iterable, for rolling a group up."""
    for s in ("fail", "blocked", "hold"):
        if s in states:
            return s
    if "waiting" in states:
        return "waiting"
    if "pass" in states:
        return "pass"
    return "none"


# ---------------------------------------------------------------------------
# Root resolution
# ---------------------------------------------------------------------------

def resolve_root(query):
    """Find what the operator typed.

    Returns (mode, obj, focus_node_id). Mode is 'order' or 'pipe'; obj is the
    ProductionOrder or Pipe it resolved to. A ladle resolves to its order when
    it has one, else to the ladle itself in order mode.
    """
    q = (query or "").strip()
    if not q:
        return None, None, None

    order = ProductionOrder.query.filter_by(order_number=q).first()
    if order:
        return "order", order, f"order-{order.id}"

    ladle = ChemicalAnalysis.query.filter_by(ladle_id=q).first()
    if ladle:
        return "ladle", ladle, f"ladle-{ladle.id}"

    pipe = (
        Pipe.query.filter_by(warehouse_barcode=q).first()
        or Pipe.query.filter_by(pipe_code=q).order_by(Pipe.id.desc()).first()
        or Pipe.query.filter_by(no_code=q).order_by(Pipe.id.desc()).first()
    )
    if pipe:
        return "pipe", pipe, f"pipe-{pipe.id}"

    return None, None, None


# ---------------------------------------------------------------------------
# Layout — swim lanes: columns by depth, a band per heat
# ---------------------------------------------------------------------------

NODE_W = 190
NODE_H = 74
COL_W = 240
ROW_H = 100
BAND_GAP = 34
PAD = 40
BAND_LABEL_W = 150


def _depths(nodes, edges):
    """Longest-path depth per node; backward edges excluded.

    A node sits one column right of its deepest predecessor. Excluding
    backward edges (target earlier in the node array) is what terminates the
    relaxation and what keeps a rework arc visibly backward.
    """
    idx = {n["id"]: i for i, n in enumerate(nodes)}
    depth = {n["id"]: 0 for n in nodes}
    for _ in range(len(nodes)):
        moved = False
        for e in edges:
            to, frm = e.get("to"), e.get("from")
            if not to or to not in idx or frm not in idx:
                continue
            if idx[to] <= idx[frm]:
                continue
            d = depth[frm] + 1
            if d > depth[to]:
                depth[to] = d
                moved = True
        if not moved:
            break
    return depth


def layout(nodes, edges):
    """Place every node and describe the bands.

    Returns (positions, bands). Within a band, lanes are counted per column,
    so a heat only grows as tall as that heat actually needs; bands then stack
    with a gap. A node with no band shares one implicit band.
    """
    depth = _depths(nodes, edges)

    band_order, seen = [], set()
    for n in nodes:
        b = n.get("band")
        if b not in seen:
            seen.add(b)
            band_order.append(b)

    lanes, rel = {}, {}
    rows = {b: 0 for b in band_order}
    for n in nodes:
        b, d = n.get("band"), depth[n["id"]]
        lane = lanes.get((b, d), 0)
        lanes[(b, d)] = lane + 1
        rel[n["id"]] = lane
        if lane + 1 > rows[b]:
            rows[b] = lane + 1

    offset, cursor = {}, 0
    bands = []
    for b in band_order:
        offset[b] = cursor
        height = rows[b] * ROW_H
        bands.append({"key": b, "y": PAD + cursor - 8, "height": height + 4})
        cursor += height + BAND_GAP

    pos = {}
    for n in nodes:
        pos[n["id"]] = (PAD + BAND_LABEL_W + depth[n["id"]] * COL_W,
                        PAD + offset[n.get("band")] + rel[n["id"]] * ROW_H)
    return pos, bands


def edge_path(a, b, loop):
    """The SVG path for one wire.

    Forward: out of the right edge at mid-height, control points at the
    horizontal midpoint. Backward/rework: out of the bottom of both nodes,
    dipping below the lower one, so a loop reads as a loop.
    """
    ax, ay = a
    bx, by = b
    if loop:
        x1, y1 = ax + NODE_W / 2, ay + NODE_H
        x2, y2 = bx + NODE_W / 2, by + NODE_H
        if abs(x1 - x2) < 1:
            x2 += 30
        dip = max(y1, y2) + 46
        return f"M{x1:g} {y1:g} C {x1:g} {dip:g}, {x2:g} {dip:g}, {x2:g} {y2:g}"
    x1, y1 = ax + NODE_W, ay + NODE_H / 2
    x2, y2 = bx, by + NODE_H / 2
    m = (x1 + x2) / 2
    return f"M{x1:g} {y1:g} C {m:g} {y1:g}, {m:g} {y2:g}, {x2:g} {y2:g}"


# ---------------------------------------------------------------------------
# Attribution — who / when, with honest fallbacks
# ---------------------------------------------------------------------------

def _name(user):
    if not user:
        return None
    return user.full_name or user.username


def _who(record, *attrs):
    """First recorded user on the record, or None (renders 'not recorded')."""
    for attr in attrs:
        name = _name(getattr(record, attr, None))
        if name:
            return name
    return None


def _stage_when(stage):
    if stage is None:
        return None
    if stage.stage_date and stage.stage_time:
        return datetime.combine(stage.stage_date, stage.stage_time)
    if stage.stage_date:
        return datetime.combine(stage.stage_date, datetime.min.time())
    return stage.updated_at or stage.created_at


def _sortable(when):
    if when is None:
        return datetime.max
    if isinstance(when, datetime):
        return when
    return datetime.combine(when, datetime.min.time())


# ---------------------------------------------------------------------------
# Graph primitives
# ---------------------------------------------------------------------------

def _node(nodes, id, kind, title, subtitle="", state="none", band=None, **extra):
    nodes.append({"id": id, "kind": kind, "title": title, "subtitle": subtitle,
                  "state": state, "band": band, **extra})
    return nodes[-1]


def _edge(edges, frm, to, label="", taken=True, loop=False, state="none"):
    edges.append({"from": frm, "to": to, "label": label, "taken": taken,
                  "loop": loop, "state": state})
    return edges[-1]


def _pipe_counts(pipes):
    counts = {s: 0 for s in STATES}
    for p in pipes:
        counts[effective_pipe_state(p)] += 1
    return counts


def _group_state(counts):
    return worst({s for s, n in counts.items() if n})


def _issue(issues, node, title, decision, severity, who=None, when=None):
    issues.append({"node": node, "title": title, "decision": decision,
                   "severity": severity, "who": who, "when": when})


def _finish(nodes, edges, issues, timeline, bands_meta=None,
            problems_only=False):
    """Shared tail: rank issues, dim non-problem branches, lay out, measure."""
    issues.sort(key=lambda i: (_SEVERITY.get(i["severity"], 9), i["title"] or ""))
    timeline.sort(key=lambda t: _sortable(t["when"]))

    if problems_only:
        keep = _problem_closure(edges, issues)
        for n in nodes:
            if n["id"] not in keep:
                n["dim"] = True

    pos, bands = layout(nodes, edges)
    for n in nodes:
        n["x"], n["y"] = pos[n["id"]]
    live = []
    for e in edges:
        if e["from"] in pos and e.get("to") in pos:
            a, b = pos[e["from"]], pos[e["to"]]
            e["path"] = edge_path(a, b, e["loop"])
            if e["loop"]:
                e["lx"] = (a[0] + b[0]) / 2 + NODE_W / 2
                e["ly"] = max(a[1], b[1]) + NODE_H + 60
            else:
                e["lx"] = (a[0] + NODE_W + b[0]) / 2
                e["ly"] = (a[1] + b[1]) / 2 + NODE_H / 2 - 8
            live.append(e)

    labels = bands_meta or {}
    for band in bands:
        band["label"] = labels.get(band["key"], "")

    width = max((n["x"] for n in nodes), default=0) + NODE_W + PAD * 2
    height = max((n["y"] for n in nodes), default=0) + NODE_H + PAD * 2
    return {"nodes": nodes, "edges": live, "bands": bands, "issues": issues,
            "timeline": timeline, "width": width, "height": height}


def _problem_closure(edges, issues):
    """Issue nodes plus their ancestry, so a lit node is never floating."""
    parents = defaultdict(list)
    for e in edges:
        if e.get("to") and not e["loop"]:
            parents[e["to"]].append(e["from"])
    keep, stack = set(), [i["node"] for i in issues]
    while stack:
        nid = stack.pop()
        if nid in keep:
            continue
        keep.add(nid)
        stack.extend(parents.get(nid, ()))
    return keep


# ---------------------------------------------------------------------------
# Shared builders
# ---------------------------------------------------------------------------

def _chain_names():
    """The stage line a pipe walks, minus the melting ladle (that is the heat,
    already drawn as its own node)."""
    names = ProductionStage.active_names()
    melting = ProductionStage.name_for_code("melting_ladle")
    return [s for s in names if s != melting]


def _stage_chain(nodes, edges, issues, timeline, pipe, band, start_from,
                 chain_names=None):
    """Draw one pipe's stage chain, with backward wires where it went back."""
    stage_map = {s.stage_name: s for s in pipe.stages}
    prev, first = start_from, None
    for name in (chain_names or _chain_names()):
        st = stage_map.get(name)
        sid = f"stage-{pipe.id}-{name}"
        sstate = state_of(st.decision) if st else "none"
        redecided = 0
        if st is not None:
            redecided = (PipeStageHistory.query
                         .filter_by(pipe_stage_id=st.id)
                         .filter(PipeStageHistory.decision.isnot(None)).count())
        when = _stage_when(st)
        _node(nodes, sid, "stage", name,
              subtitle=(st.decision or "") if st else "",
              state=sstate, band=band,
              badge=(f"↺{redecided}" if redecided > 1 else ""),
              stage_id=(st.id if st else None),
              detail_url=("stages.view", pipe.id) if st else None,
              who=_who(st, "approved_by", "updated_by") if st else None,
              when=when)
        _edge(edges, prev, sid, taken=st is not None,
              state=sstate if sstate != "none" else "none")
        if first is None:
            first = sid
        if st and when:
            timeline.append({"label": f"{pipe.pipe_code or pipe.id} · {name}",
                             "when": when, "state": sstate})
        if st and sstate in _SEVERITY:
            _issue(issues, sid, f"{pipe.pipe_code or pipe.id} · {name}",
                   st.decision, sstate,
                   _who(st, "approved_by", "updated_by"), when)
        # a fail or a rework sends the material back to the start of the line
        if st and (is_rework(st.decision) or sstate == "fail") \
                and first and sid != first:
            _edge(edges, sid, first, label=(st.decision or "").strip(),
                  loop=True, state=sstate)
        prev = sid
    return prev


# ---------------------------------------------------------------------------
# Mode: order
# ---------------------------------------------------------------------------

def build_graph(order=None, ladles=None, expand=None, problems_only=False):
    """One production order, heat by heat, each heat in its own band."""
    expand = set(expand or ())
    nodes, edges, issues, timeline = [], [], [], []
    band_labels = {}
    chain_names = _chain_names()

    # On real data ChemicalAnalysis.production_order_id is filled on almost
    # nothing (2 of 77 rows in production) — the order reaches its heats
    # through its pipes, which carry ladle_id as a string.
    order_pipes_by_ladle = defaultdict(list)
    orphan_pipes = []
    if order is not None:
        for p in order.pipes.order_by(Pipe.arrange_pipe.asc(), Pipe.id.asc()):
            (order_pipes_by_ladle[p.ladle_id] if p.ladle_id
             else orphan_pipes).append(p)

    if ladles is None and order is not None:
        found, seen = [], set()
        for ladle in order.chemical_analyses.order_by(
                ChemicalAnalysis.test_date.asc(), ChemicalAnalysis.id.asc()):
            found.append(ladle)
            seen.add(ladle.ladle_id)
        referenced = [lid for lid in order_pipes_by_ladle if lid not in seen]
        if referenced:
            found.extend(ChemicalAnalysis.query
                         .filter(ChemicalAnalysis.ladle_id.in_(referenced))
                         .order_by(ChemicalAnalysis.test_date.asc(),
                                   ChemicalAnalysis.id.asc()).all())
            seen.update(l.ladle_id for l in found)
        for lid in order_pipes_by_ladle:
            if lid not in seen:
                orphan_pipes.extend(order_pipes_by_ladle[lid])
        ladles = found
    elif ladles is None:
        ladles = []

    oid = None
    if order:
        oid = f"order-{order.id}"
        band_labels["__order__"] = order.order_number
        _node(nodes, oid, "order", order.order_number,
              subtitle=(order.customer_name or ""), band="__order__",
              state="pass" if order.status == "completed" else "none",
              badge=f"{order.produced_quantity}/{order.target_quantity}",
              detail_url=("production_orders.view", order.id),
              who=_who(order, "created_by"), when=order.order_date)

    for ladle in ladles:
        band = f"ladle-{ladle.id}"
        band_labels[band] = f"Ladle {ladle.ladle_id}"
        lid = f"ladle-{ladle.id}"
        scenario = DECISION_SCENARIO.get((ladle.decision or "").strip())
        ladle_state = ("fail" if scenario == "REJECT"
                       else "pass" if scenario in _LADLE_PASS_SCENARIOS
                       else "none")
        pipes = (order_pipes_by_ladle.get(ladle.ladle_id, []) if order is not None
                 else list(ladle.pipes.order_by(Pipe.arrange_pipe.asc(),
                                                Pipe.id.asc())))

        _node(nodes, lid, "ladle", f"Ladle {ladle.ladle_id}",
              subtitle=(ladle.decision or ""), state=ladle_state, band=band,
              badge=(ladle.furnace.furnace_code if ladle.furnace else ""),
              detail_url=("chemical.detail", ladle.id),
              who=_who(ladle, "modified_by", "created_by"), when=ladle.test_date)
        if oid:
            _edge(edges, oid, lid, state=ladle_state)
        if ladle.test_date:
            timeline.append({"label": f"Ladle {ladle.ladle_id}",
                             "when": ladle.test_date, "state": ladle_state})
        if ladle_state == "fail":
            _issue(issues, lid, f"Ladle {ladle.ladle_id}", ladle.decision,
                   "fail", _who(ladle, "modified_by", "created_by"),
                   ladle.test_date)

        # One decision node carrying the scheme this heat actually took. The
        # alternatives live in the inspector — drawing all four per heat put
        # 21 dim boxes on a seven-heat order and buried the real path.
        sid = f"scen-{ladle.id}"
        label_en, label_ar = SCENARIO_LABELS.get(
            scenario, ("Not decided", "لم يتقرر"))
        _node(nodes, sid, "scenario", label_en, subtitle=label_ar, band=band,
              state=("fail" if scenario == "REJECT"
                     else "pass" if scenario else "waiting"),
              scenario=scenario, ladle_id=ladle.id)
        _edge(edges, lid, sid, label=label_en, state=ladle_state)

        active_tests = list(
            ladle.mechanical_tests.filter(MechanicalTest.status == "ACTIVE")
            .order_by(MechanicalTest.test_date.asc(), MechanicalTest.id.asc()))
        mech_ids = []
        for t in active_tests:
            mid = f"mech-{t.id}"
            mstate = state_of(t.decision) if t.decision else "waiting"
            tested = next((p for p in pipes
                           if (t.pipe_id and p.id == t.pipe_id)
                           or (t.pipe_code and p.pipe_code == t.pipe_code)), None)
            role = (tested.mechanical_test_role or "") if tested else ""
            _node(nodes, mid, "mechanical", f"Mech {t.test_number or t.id}",
                  subtitle=(f"sample: {role}" if role else (t.pipe_code or "")),
                  state=mstate, band=band, badge=(t.decision or "…"),
                  detail_url=("mechanical.detail", t.id),
                  who=_who(t, "modified_by", "created_by"), when=t.test_date)
            _edge(edges, sid, mid, label=role, state=mstate)
            if t.test_date:
                timeline.append({"label": f"Mech {t.test_number or t.id}",
                                 "when": t.test_date, "state": mstate})
            if mstate == "fail":
                _issue(issues, mid, f"Mech {t.test_number or t.id}",
                       t.decision, "fail",
                       _who(t, "modified_by", "created_by"), t.test_date)
                # a failed sample sends the heat back to be re-sampled
                _edge(edges, mid, lid, label="retest", loop=True, state="fail")
            mech_ids.append(mid)

        counts = _pipe_counts(pipes)
        gid = f"pipes-{ladle.id}"
        gstate = _group_state(counts)
        expanded = f"ladle-{ladle.id}" in expand or any(
            f"pipe-{p.id}" in expand for p in pipes)
        _node(nodes, gid, "pipes", f"{len(pipes)} pipes",
              subtitle="" if expanded else "click to expand",
              state=gstate, band=band, counts=counts,
              expandable=bool(pipes), expand_token=f"ladle-{ladle.id}",
              expanded=expanded)
        _edge(edges, mech_ids[-1] if mech_ids else sid, gid,
              label="BLOCKED" if scenario == "REJECT" else "",
              state="blocked" if scenario == "REJECT" else gstate)

        if not expanded:
            for sev in ("fail", "blocked", "hold"):
                if counts[sev]:
                    _issue(issues, gid, f"Ladle {ladle.ladle_id} pipes",
                           f"{counts[sev]} {sev}", sev)
            continue

        for pipe in pipes:
            pid = f"pipe-{pipe.id}"
            pstate = effective_pipe_state(pipe)
            open_pipe = pid in expand
            # An expanded pipe gets its own band so its stage chain runs on a
            # clear line instead of colliding with its siblings' chains.
            pband = pid if open_pipe else band
            if open_pipe:
                band_labels[pband] = pipe.pipe_code or pipe.no_code or f"#{pipe.id}"
            _node(nodes, pid, "pipe",
                  pipe.pipe_code or pipe.no_code or f"#{pipe.id}",
                  subtitle=(pipe.cascade_from or ""), state=pstate, band=pband,
                  badge=(pipe.lab_decision or ""),
                  detail_url=("stages.view", pipe.id),
                  expandable=True, expand_token=pid, expanded=open_pipe,
                  who=_who(pipe, "modified_by", "created_by"),
                  when=pipe.production_date)
            _edge(edges, gid, pid, label=(pipe.mechanical_test_role or ""),
                  state=pstate)
            if pstate in _SEVERITY:
                _issue(issues, pid,
                       pipe.pipe_code or pipe.no_code or f"#{pipe.id}",
                       pipe.lab_decision or pipe.final_decision_value, pstate,
                       _who(pipe, "modified_by", "created_by"),
                       pipe.production_date)
            if open_pipe:
                _stage_chain(nodes, edges, issues, timeline, pipe, pband, pid,
                             chain_names)

    # Pipes the order owns that no heat accounts for.
    if orphan_pipes and oid:
        band = "__unassigned__"
        band_labels[band] = "no heat record"
        counts = _pipe_counts(orphan_pipes)
        gid = f"pipes-unassigned-{order.id}"
        gstate = _group_state(counts)
        expanded = "unassigned" in expand
        _node(nodes, gid, "pipes", f"{len(orphan_pipes)} pipes",
              subtitle="no ladle record", state=gstate, band=band,
              counts=counts, expandable=True, expand_token="unassigned",
              expanded=expanded)
        _edge(edges, oid, gid, label="no ladle", state=gstate)
        if expanded:
            for pipe in orphan_pipes:
                pid = f"pipe-{pipe.id}"
                pstate = effective_pipe_state(pipe)
                _node(nodes, pid, "pipe",
                      pipe.pipe_code or pipe.no_code or f"#{pipe.id}",
                      subtitle=(pipe.ladle_id or ""), state=pstate, band=band,
                      badge=(pipe.lab_decision or ""),
                      detail_url=("stages.view", pipe.id),
                      who=_who(pipe, "modified_by", "created_by"),
                      when=pipe.production_date)
                _edge(edges, gid, pid, state=pstate)
                if pstate in _SEVERITY:
                    _issue(issues, pid,
                           pipe.pipe_code or pipe.no_code or f"#{pipe.id}",
                           pipe.lab_decision, pstate,
                           _who(pipe, "modified_by", "created_by"),
                           pipe.production_date)
        else:
            for sev in ("fail", "blocked", "hold"):
                if counts[sev]:
                    _issue(issues, gid, "Pipes with no heat record",
                           f"{counts[sev]} {sev}", sev)

    return _finish(nodes, edges, issues, timeline, band_labels, problems_only)


# ---------------------------------------------------------------------------
# Mode: pipe — one pipe, cradle to grave, on one line
# ---------------------------------------------------------------------------

def build_pipe_graph(pipe, problems_only=False):
    """The certificate view: order → heat → sample test → this pipe → stages."""
    nodes, edges, issues, timeline = [], [], [], []
    band = f"pipe-{pipe.id}"
    label = pipe.pipe_code or pipe.no_code or f"#{pipe.id}"
    band_labels = {band: label}

    prev = None
    order = pipe.production_order
    if order:
        oid = f"order-{order.id}"
        _node(nodes, oid, "order", order.order_number,
              subtitle=(order.customer_name or ""), band=band,
              state="pass" if order.status == "completed" else "none",
              detail_url=("production_orders.view", order.id),
              who=_who(order, "created_by"), when=order.order_date)
        prev = oid

    ladle = pipe.chemical_analysis
    if ladle:
        lid = f"ladle-{ladle.id}"
        scenario = DECISION_SCENARIO.get((ladle.decision or "").strip())
        lstate = ("fail" if scenario == "REJECT"
                  else "pass" if scenario in _LADLE_PASS_SCENARIOS else "none")
        _node(nodes, lid, "ladle", f"Ladle {ladle.ladle_id}",
              subtitle=(ladle.decision or ""), state=lstate, band=band,
              badge=(ladle.furnace.furnace_code if ladle.furnace else ""),
              detail_url=("chemical.detail", ladle.id),
              who=_who(ladle, "modified_by", "created_by"), when=ladle.test_date)
        if prev:
            _edge(edges, prev, lid, state=lstate)
        prev = lid
        if ladle.test_date:
            timeline.append({"label": f"Ladle {ladle.ladle_id}",
                             "when": ladle.test_date, "state": lstate})
        if lstate == "fail":
            _issue(issues, lid, f"Ladle {ladle.ladle_id}", ladle.decision,
                   "fail", _who(ladle, "modified_by", "created_by"),
                   ladle.test_date)

        # Only the tests that actually decided this pipe: its own, and the
        # heat's samples when it inherited their verdict.
        tests = list(ladle.mechanical_tests
                     .filter(MechanicalTest.status == "ACTIVE")
                     .order_by(MechanicalTest.test_date.asc()))
        relevant = [t for t in tests
                    if t.pipe_id == pipe.id
                    or (t.pipe_code and t.pipe_code == pipe.pipe_code)
                    or (pipe.cascade_from or "").startswith("cascade")]
        for t in relevant:
            mid = f"mech-{t.id}"
            mstate = state_of(t.decision) if t.decision else "waiting"
            own = t.pipe_id == pipe.id or (t.pipe_code == pipe.pipe_code)
            _node(nodes, mid, "mechanical", f"Mech {t.test_number or t.id}",
                  subtitle=("this pipe" if own else f"sample {t.pipe_code or ''}"),
                  state=mstate, band=band, badge=(t.decision or "…"),
                  detail_url=("mechanical.detail", t.id),
                  who=_who(t, "modified_by", "created_by"), when=t.test_date)
            _edge(edges, prev, mid, state=mstate)
            prev = mid
            if t.test_date:
                timeline.append({"label": f"Mech {t.test_number or t.id}",
                                 "when": t.test_date, "state": mstate})
            if mstate == "fail":
                _issue(issues, mid, f"Mech {t.test_number or t.id}",
                       t.decision, "fail",
                       _who(t, "modified_by", "created_by"), t.test_date)

    pid = f"pipe-{pipe.id}"
    pstate = effective_pipe_state(pipe)
    _node(nodes, pid, "pipe", label, subtitle=(pipe.cascade_from or ""),
          state=pstate, band=band, badge=(pipe.lab_decision or ""),
          detail_url=("stages.view", pipe.id),
          who=_who(pipe, "modified_by", "created_by"),
          when=pipe.production_date)
    if prev:
        _edge(edges, prev, pid, label=(pipe.mechanical_test_role or ""),
              state=pstate)
    if pstate in _SEVERITY:
        _issue(issues, pid, label,
               pipe.lab_decision or pipe.final_decision_value, pstate,
               _who(pipe, "modified_by", "created_by"), pipe.production_date)

    _stage_chain(nodes, edges, issues, timeline, pipe, band, pid)
    return _finish(nodes, edges, issues, timeline, band_labels, problems_only)


# ---------------------------------------------------------------------------
# Mode: live — what is stopped right now, hung off the stage it stopped at
# ---------------------------------------------------------------------------

LIVE_PIPE_LIMIT = 8


def build_live_graph(limit_per_stage=LIVE_PIPE_LIMIT, problems_only=False):
    """The shift view: the stage line, with what is stuck at each step.

    Every active stage is a node carrying how many pipes sit there; the ones
    that are stopped hang off it, worst and oldest first. This is the screen
    that answers "where is the plant hurting right now".
    """
    nodes, edges, issues, timeline = [], [], [], []
    band_labels = {"__line__": "production line"}
    order_names = ProductionStage.active_names()

    open_pipes = (Pipe.query
                  .filter(db_or_not_accepted())
                  .all())
    stages_by_pipe = defaultdict(dict)
    if open_pipes:
        ids = [p.id for p in open_pipes]
        for st in PipeStage.query.filter(PipeStage.pipe_id.in_(ids)).all():
            stages_by_pipe[st.pipe_id][st.stage_name] = st

    at_stage = defaultdict(list)
    for pipe in open_pipes:
        rows = stages_by_pipe.get(pipe.id, {})
        current = order_names[0] if order_names else None
        for name in reversed(order_names):
            st = rows.get(name)
            if st is not None and (st.decision or "").strip():
                current = name
                break
        if current:
            at_stage[current].append(pipe)

    prev = None
    for name in order_names:
        sid = f"line-{name}"
        here = at_stage.get(name, [])
        counts = _pipe_counts(here)
        _node(nodes, sid, "line", name,
              subtitle=f"{len(here)} pipes" if here else "clear",
              state=_group_state(counts) if here else "none",
              band="__line__", counts=counts if here else None,
              badge=str(len(here)) if here else "")
        if prev:
            _edge(edges, prev, sid, state="none")
        prev = sid

        stopped = [p for p in here
                   if effective_pipe_state(p) in _SEVERITY]
        if not stopped:
            continue
        stopped.sort(key=lambda p: _SEVERITY.get(effective_pipe_state(p), 9))
        band = f"stuck-{name}"
        band_labels[band] = f"stuck at {name}"
        for pipe in stopped[:limit_per_stage]:
            pid = f"pipe-{pipe.id}"
            pstate = effective_pipe_state(pipe)
            st = stages_by_pipe.get(pipe.id, {}).get(name)
            when = _stage_when(st)
            _node(nodes, pid, "pipe",
                  pipe.pipe_code or pipe.no_code or f"#{pipe.id}",
                  subtitle=(pipe.ladle_id or ""), state=pstate, band=band,
                  badge=(pipe.lab_decision or ""),
                  detail_url=("stages.view", pipe.id),
                  who=_who(pipe, "modified_by", "created_by"), when=when)
            _edge(edges, sid, pid, state=pstate)
            _issue(issues, pid, pipe.pipe_code or pipe.no_code or f"#{pipe.id}",
                   f"{pipe.lab_decision or pstate} @ {name}", pstate,
                   _who(pipe, "modified_by", "created_by"), when)
            if when:
                timeline.append({"label": pipe.pipe_code or str(pipe.id),
                                 "when": when, "state": pstate})
        extra = len(stopped) - limit_per_stage
        if extra > 0:
            mid = f"more-{name}"
            _node(nodes, mid, "more", f"+{extra} more",
                  subtitle=f"stopped at {name}", state=_group_state(
                      _pipe_counts(stopped[limit_per_stage:])), band=band)
            _edge(edges, sid, mid, state="none")

    return _finish(nodes, edges, issues, timeline, band_labels, problems_only)


def db_or_not_accepted():
    """Pipes still in play — nothing final, or stopped short of accepted."""
    from app import db
    return db.or_(Pipe.final_decision_value.is_(None),
                  Pipe.final_decision_value != "ACCEPT")


# ---------------------------------------------------------------------------
# Node inspector
# ---------------------------------------------------------------------------

def _audit_trail(table, record_id, limit=30):
    rows = (AuditLog.query.filter_by(table_name=table, record_id=record_id)
            .order_by(AuditLog.timestamp.desc()).limit(limit).all())
    return [{"when": r.timestamp, "who": _name(r.user), "action": r.action,
             "field": r.field_name, "old": r.old_value, "new": r.new_value,
             "reason": r.reason} for r in rows]


def node_detail(kind, id):
    """Everything the inspector shows for one node, or None if unknown.

    Carries a diagnosis when something is wrong: what, why, and the buttons
    that go to the screen where it gets fixed.
    """
    detail = _node_detail(kind, id)
    if detail is not None:
        from app.services import diagnosis_service
        # scenario nodes diagnose their heat
        detail["diagnosis"] = diagnosis_service.diagnose(kind, id)
    return detail


def _node_detail(kind, id):
    if kind == "order":
        order = ProductionOrder.query.get(id)
        if not order:
            return None
        return {"kind": kind, "title": order.order_number,
                "state": "pass" if order.status == "completed" else "none",
                "decision": order.status, "reason": None,
                "who": _who(order, "modified_by", "created_by"),
                "when": order.order_date,
                "link": ("production_orders.view", order.id),
                "history": _audit_trail("production_orders", order.id),
                "measurements": [], "options": []}

    if kind == "ladle":
        ladle = ChemicalAnalysis.query.get(id)
        if not ladle:
            return None
        measurements = []
        for code, value in ladle.get_element_values().items():
            if value is None:
                continue
            verdict = decision_service.get_element_decision(code, value)
            measurements.append({"label": code, "value": value,
                                 "note": verdict.get("decision"),
                                 "ok": bool(verdict.get("in_spec"))})
        return {"kind": kind, "title": f"Ladle {ladle.ladle_id}",
                "state": state_of(ladle.decision), "decision": ladle.decision,
                "reason": ladle.reason or ladle.defect_reason,
                "who": _who(ladle, "modified_by", "created_by"),
                "when": ladle.test_date,
                "link": ("chemical.detail", ladle.id),
                "history": _audit_trail("chemical_analyses", ladle.id),
                "measurements": measurements, "options": []}

    if kind == "scenario":
        ladle = ChemicalAnalysis.query.get(id)
        if not ladle:
            return None
        taken = DECISION_SCENARIO.get((ladle.decision or "").strip())
        # The routes this heat could have taken — the three not chosen are
        # what the canvas no longer draws, so they belong here.
        options = [{"label": f"{en} — {ar}", "taken": key == taken}
                   for key, (en, ar) in SCENARIO_LABELS.items()]
        en, ar = SCENARIO_LABELS.get(taken, ("Not decided", "لم يتقرر"))
        return {"kind": kind, "title": en, "state": (
                    "fail" if taken == "REJECT" else "pass" if taken else "waiting"),
                "decision": ladle.decision, "reason": ar,
                "who": _who(ladle, "modified_by", "created_by"),
                "when": ladle.test_date,
                "link": ("chemical.detail", ladle.id),
                "history": _audit_trail("chemical_analyses", ladle.id),
                "measurements": [], "options": options}

    if kind == "mechanical":
        test = MechanicalTest.query.get(id)
        if not test:
            return None
        measurements = [{"label": label, "value": value, "note": None, "ok": None}
                        for label, value in (
                            ("Tensile (MPa)", test.tensile_mpa),
                            ("Elongation %", test.elongation),
                            ("Hardness", test.hardness),
                            ("Nodularity %", test.nodularity_percent),
                            ("Force (kgf)", test.force_kgf))
                        if value is not None]
        return {"kind": kind, "title": f"Mech {test.test_number or test.id}",
                "state": state_of(test.decision) if test.decision else "waiting",
                "decision": test.decision,
                "reason": test.reason or test.defect_reason,
                "who": _who(test, "modified_by", "created_by"),
                "when": test.test_date,
                "link": ("mechanical.detail", test.id),
                "history": _audit_trail("mechanical_tests", test.id),
                "measurements": measurements, "options": []}

    if kind == "pipe":
        pipe = Pipe.query.get(id)
        if not pipe:
            return None
        return {"kind": kind,
                "title": pipe.pipe_code or pipe.no_code or f"#{pipe.id}",
                "state": effective_pipe_state(pipe),
                "decision": pipe.lab_decision or pipe.final_decision_value,
                "reason": (pipe.final_decision_reason or pipe.lab_decision_reason
                           or pipe.cascade_from),
                "who": _who(pipe, "modified_by", "created_by"),
                "when": pipe.production_date,
                "link": ("stages.view", pipe.id),
                "history": _audit_trail("pipes", pipe.id),
                "measurements": [], "options": []}

    if kind == "stage":
        st = PipeStage.query.get(id)
        if not st:
            return None
        history = [{"when": h.changed_at, "who": _name(h.changed_by),
                    "action": h.action, "field": "decision", "old": None,
                    "new": h.decision, "reason": h.reason}
                   for h in (PipeStageHistory.query
                             .filter_by(pipe_stage_id=st.id)
                             .order_by(PipeStageHistory.changed_at.desc())
                             .limit(30))]
        history += _audit_trail("pipe_stages", st.id)
        history.sort(key=lambda h: _sortable(h["when"]), reverse=True)
        return {"kind": kind, "title": st.stage_name,
                "state": state_of(st.decision), "decision": st.decision,
                "reason": st.reason or st.defect_reason,
                "who": _who(st, "approved_by", "updated_by"),
                "when": _stage_when(st),
                "link": ("stages.view", st.pipe_id), "history": history,
                "measurements": _stage_measurements(st), "options": []}

    return None


def _band_ok(value, lo, hi):
    if value is None or (lo is None and hi is None):
        return None
    if lo is not None and value < lo:
        return False
    if hi is not None and value > hi:
        return False
    return True


def _stage_measurements(st):
    """The readings behind a stage verdict, judged against their stored band
    where one exists."""
    out = []

    tp = st.thickness_profile or {}
    for layer in ("cement", "coating"):
        lo, hi = tp.get(f"{layer}_std_min"), tp.get(f"{layer}_std_max")
        for i, v in enumerate(tp.get(layer) or []):
            if v is None:
                continue
            out.append({"label": f"{layer} m{i + 1}", "value": v,
                        "note": None, "ok": _band_ok(v, lo, hi)})

    dp = st.dimension_profile or {}
    thick = dp.get("thickness") or {}
    lo, hi = thick.get("standard_min"), thick.get("standard_max")
    for posn, readings in (thick.get("positions") or {}).items():
        for j, v in enumerate(readings or []):
            if v is None:
                continue
            out.append({"label": f"thickness p{posn} r{j + 1}", "value": v,
                        "note": None, "ok": _band_ok(v, lo, hi)})

    zp = st.zinc_profile or {}
    if zp.get("mass") is not None:
        out.append({"label": "zinc mass g/m²", "value": zp["mass"],
                    "note": None, "ok": None})

    rp = st.ring_profile or {}
    if rp.get("deflection") is not None:
        out.append({"label": "ring deflection %", "value": rp["deflection"],
                    "note": None, "ok": None})

    if st.measurement_value is not None:
        out.append({"label": st.measurement_type or "measurement",
                    "value": st.measurement_value, "note": None, "ok": None})
    return out
