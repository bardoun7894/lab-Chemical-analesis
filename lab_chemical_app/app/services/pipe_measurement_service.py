"""One pipe's complete measurement record — raw readings plus statistics.

The lab needs a single sheet per pipe: when it was made, every reading taken
on it, and the descriptive statistics for each set. The readings already exist
scattered across the stage JSON profiles (wall thickness on CCM, lining on
Coating, ovality and the TA 1012 symbols on Annealing); this module gathers
them into one structure the report and its export both render from.

Nothing here computes a verdict. Out-of-tolerance is reported per symbol
because the standard defines it; a thickness reading has no tolerance on file
and is never called good or bad.
"""

from app.models.pipe import Pipe, PipeStage
from app.models.stage import ProductionStage
from app.services import dimension_standard_service as std
from app.services import measurement_stats_service as stats


def pipe_measurement_sheet(pipe_id):
    """Everything measured on one pipe, or None if the pipe does not exist."""
    pipe = Pipe.query.get(pipe_id)
    if pipe is None:
        return None

    stages = (
        PipeStage.query.filter_by(pipe_id=pipe.id)
        .order_by(PipeStage.id)
        .all()
    )
    by_name = {s.stage_name: s for s in stages}
    ccm = by_name.get(ProductionStage.name_for_code("ccm"))
    annealing = by_name.get(ProductionStage.name_for_code("annealing"))
    coating = by_name.get(ProductionStage.name_for_code("coating"))

    return {
        "pipe": pipe,
        "identity": _identity(pipe, ccm),
        "thickness": _thickness_block(ccm),
        "diameter": _diameter_block(ccm),
        "lining": _lining_block(coating),
        "symbols": _symbols_block(pipe, ccm, annealing),
        "ovality": _ovality_block(annealing),
    }


def _identity(pipe, ccm):
    order = pipe.production_order
    return {
        "pipe_code": pipe.pipe_code or pipe.no_code,
        "no_code": pipe.no_code,
        "ladle_id": pipe.ladle_id,
        "dn": pipe.diameter,
        "pipe_class": pipe.pipe_class,
        "production_date": pipe.production_date,
        # The CCM stage timestamp is when the pipe was actually cast; the
        # pipe's production_date is the shift date it was booked under.
        "cast_date": ccm.stage_date if ccm else None,
        "cast_time": ccm.stage_time if ccm else None,
        "order_number": order.order_number if order else None,
        "customer": order.customer_name if order else None,
        "lab_decision": pipe.lab_decision,
        "final_decision": pipe.final_decision,
    }


def _thickness_block(ccm):
    """Wall thickness: the raw 7x3 grid plus per-position and overall stats."""
    if ccm is None:
        return None
    result = stats.thickness_stats(ccm.dimension_profile)
    if not result or not result["overall"]["n"]:
        return None
    matrix = stats.thickness_matrix(ccm.dimension_profile)
    return {
        "positions": list(matrix.keys()),
        "matrix": matrix,
        "by_position": result["by_position"],
        "overall": result["overall"],
        "readings_per_position": stats.THICKNESS_READINGS_PER_POSITION,
    }


def _diameter_block(ccm):
    if ccm is None:
        return None
    result = stats.diameter_stats(ccm.dimension_profile)
    if not result:
        return None
    samples = (ccm.dimension_profile.get("diameter") or {}).get("samples") or {}
    return {
        "samples": dict(sorted(samples.items())),
        "by_sample": result["by_sample"],
        "overall": result["overall"],
    }


def _lining_block(coating):
    if coating is None:
        return None
    result = stats.lining_stats(coating.thickness_profile)
    if not result:
        return None
    return {
        "layers": {
            layer: {
                "readings": (coating.thickness_profile or {}).get(layer) or [],
                "stats": layer_stats,
            }
            for layer, layer_stats in result.items()
        }
    }


def _symbols_block(pipe, ccm, annealing):
    """TA 1012 symbol readings from either stage, against the DN's standard.

    A symbol measured at both stages gets one row per stage, since the two are
    separate observations and averaging them would hide a drift between them.
    """
    rows = []
    entries = std.for_dn(pipe.diameter) if pipe.diameter else {}
    for stage in (ccm, annealing):
        if stage is None:
            continue
        symbols = (stage.dimension_profile or {}).get("symbols") or {}
        if not symbols:
            continue
        for symbol in std.symbol_keys():
            if symbol not in symbols or symbols[symbol] is None:
                continue
            verdict = std.evaluate(pipe.diameter, symbol, symbols[symbol])
            entry = entries.get(symbol) or {}
            rows.append({
                "stage": stage.stage_name,
                "symbol": symbol,
                "label": std.symbol_label(symbol),
                "actual": symbols[symbol],
                "nominal": verdict["nominal"],
                "deviation": verdict["deviation"],
                "lsl": entry.get("lsl"),
                "usl": entry.get("usl"),
                "status": verdict["status"],
            })
    if not rows:
        return None
    return {"rows": rows, "dn": pipe.diameter,
            "standard_verified": std.is_verified()}


def _ovality_block(annealing):
    if annealing is None or not annealing.ovality_profile:
        return None
    points = (annealing.ovality_profile or {}).get("points") or {}
    if not points:
        return None
    percentages = [
        p["ovality"] for p in points.values()
        if isinstance(p, dict) and p.get("ovality") is not None
    ]
    return {
        "points": points,
        "overall": stats.describe(percentages),
    }


def flatten_for_export(sheet):
    """One row per reading — the shape a spreadsheet wants.

    Every row names what was measured, where, and its value, so the export can
    be pivoted without knowing the JSON shapes.
    """
    if not sheet:
        return []
    ident = sheet["identity"]
    base = {
        "pipe_code": ident["pipe_code"],
        "ladle_id": ident["ladle_id"],
        "dn": ident["dn"],
        "pipe_class": ident["pipe_class"],
        "production_date": (
            ident["production_date"].isoformat()
            if ident["production_date"] else ""
        ),
    }
    rows = []

    thickness = sheet.get("thickness")
    if thickness:
        for position in thickness["positions"]:
            for idx, value in enumerate(thickness["matrix"][position], start=1):
                if value is None:
                    continue
                rows.append(dict(base, measurement="wall_thickness",
                                 location=f"{position} m", reading=idx,
                                 value=value, nominal="", deviation="",
                                 status=""))

    diameter = sheet.get("diameter")
    if diameter:
        for label, values in diameter["samples"].items():
            for idx, value in enumerate(values or [], start=1):
                if value is None:
                    continue
                rows.append(dict(base, measurement="diameter",
                                 location=f"{label} D{idx}", reading=idx,
                                 value=value, nominal="", deviation="",
                                 status=""))

    lining = sheet.get("lining")
    if lining:
        for layer, block in lining["layers"].items():
            for idx, value in enumerate(block["readings"], start=1):
                if value is None:
                    continue
                rows.append(dict(base, measurement=layer,
                                 location=f"{idx} m", reading=1,
                                 value=value, nominal="", deviation="",
                                 status=""))

    symbols = sheet.get("symbols")
    if symbols:
        for row in symbols["rows"]:
            rows.append(dict(
                base, measurement=f"dim_{row['symbol']}",
                location=row["stage"], reading=1, value=row["actual"],
                nominal=row["nominal"] if row["nominal"] is not None else "",
                deviation=row["deviation"] if row["deviation"] is not None else "",
                status=row["status"]))

    return rows
