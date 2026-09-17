"""Policy-free helpers shared by pipe-selection screens.

This module finds and orders records only.  Delivery, sticker, and certificate
eligibility belongs to the route performing that module's final action.
"""

from collections.abc import Iterable

from app import db
from app.models.pipe import Pipe


def dedupe_pipe_ids(raw_ids):
    """Return positive integer IDs in first-seen order.

    Invalid values are rejected instead of disappearing so action routes can
    fail the whole operation rather than silently processing a partial basket.
    """
    if raw_ids is None:
        raw_ids = ()
    elif isinstance(raw_ids, (str, bytes)) or not isinstance(raw_ids, Iterable):
        raw_ids = (raw_ids,)

    ids = []
    seen = set()
    for raw in raw_ids:
        if isinstance(raw, bool):
            raise ValueError(f"invalid pipe id: {raw!r}")
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            raise ValueError(f"invalid pipe id: {raw!r}") from None
        if value <= 0:
            raise ValueError(f"invalid pipe id: {raw!r}")
        if value not in seen:
            seen.add(value)
            ids.append(value)
    return ids


def parse_order_ids(values):
    """Parse one or more posted/query-string production-order IDs."""
    return dedupe_pipe_ids(values)


def pipes_for_orders(order_ids):
    """Load pipes for the given orders in selector order and pipe sequence."""
    ids = parse_order_ids(order_ids)
    if not ids:
        return []
    position = {order_id: index for index, order_id in enumerate(ids)}
    pipes = Pipe.query.filter(Pipe.production_order_id.in_(ids)).all()
    return sorted(
        pipes,
        key=lambda pipe: (
            position[pipe.production_order_id],
            pipe.arrange_pipe is None,
            pipe.arrange_pipe or 0,
            pipe.id,
        ),
    )


def lookup_pipes(term):
    """Find pipes by exact scan/code first, then a case-insensitive partial.

    A warehouse barcode is unique and therefore wins outright.  Pipe and batch
    codes can identify more than one row, so all exact matches are returned in
    production sequence.  Partial matches retain the incumbent newest-first
    search order.
    """
    term = (term or "").strip()
    if not term:
        return []

    barcode = Pipe.query.filter(Pipe.warehouse_barcode == term).first()
    if barcode is not None:
        return [barcode]

    for column in (Pipe.pipe_code, Pipe.no_code):
        exact = (
            Pipe.query.filter(db.func.lower(column) == term.lower())
            .order_by(Pipe.arrange_pipe.asc(), Pipe.id.asc())
            .all()
        )
        if exact:
            return exact

    like = f"%{term}%"
    return (
        Pipe.query.filter(
            db.or_(
                Pipe.warehouse_barcode.ilike(like),
                Pipe.pipe_code.ilike(like),
                Pipe.no_code.ilike(like),
            )
        )
        .order_by(Pipe.production_date.desc(), Pipe.id.desc())
        .all()
    )
