"""
Turns a set of chosen pipes into the rows a certificate prints.

Shared by both forms for the material table; the work test adds the chemical
limits and the mechanical sample results that sit below it.
"""
from app.models.chemical import ChemicalAnalysis, ElementSpecification
from app.models.mechanical import MechanicalTest
from app.models.pipe import Pipe, PipeStage
from app.models.stage import ProductionStage


# The elements as the printed sheet reads them, left to right. The source
# workbook is right-to-left, so column C ("Mg") is its *rightmost* element and
# this list is the workbook's order reversed.
WORK_TEST_ELEMENTS = ['C', 'Si', 'Cu', 'Cr', 'S', 'Mn', 'CE', 'Mg']

# What the form printed before the limits were configurable. Two jobs: it fills
# in for an element the lab has no specification row for, and its decimal places
# set how the configured numbers are written — 0.030 has to keep its trailing
# zero, which a float cannot remember on its own.
FALLBACK_LIMITS = {
    'Mg': '0.030: 0.070',
    'CE': '3.9: 4.35',
    'Mn': '0.15: 0.45',
    'S': 'Max. 0.015',
    'Cr': 'Max. 0.05',
    'Cu': 'Max. 0.08',
    'Si': '1.8: 2.8',
    'C': '3.00: 4.20',
}


def _decimals_for(element):
    """How many decimal places this element is written to on the paper form."""
    printed = FALLBACK_LIMITS.get(element, '')
    widths = [len(part.split('.', 1)[1])
              for part in printed.replace('Max.', '').replace('Min.', '').split(':')
              if '.' in part]
    return max(widths) if widths else 2


def _finish_length(pipe):
    """Measured length from the Finish stage, if it was recorded there."""
    try:
        finish_name = ProductionStage.name_for_code('finish')
    except Exception:
        finish_name = 'Finish'
    stage = pipe.get_stage(finish_name) if finish_name else None
    value = getattr(stage, 'measurement_value', None) if stage else None
    if value in (None, ''):
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _order_lengths(pipes):
    """``{production_order_id: product_length}`` for the pipes' own orders.

    A certificate can now cover pipes drawn from more than one order, so the
    unmeasured-pipe fallback has to be looked up per pipe rather than assumed
    from a single order argument. ``pipe.production_order`` is a lazy
    relationship, so looking it up pipe by pipe would be one query per pipe;
    this loads every order the batch needs in one query instead.
    """
    order_ids = {p.production_order_id for p in pipes if p.production_order_id}
    if not order_ids:
        return {}
    from app.models.production_order import ProductionOrder
    rows = ProductionOrder.query.filter(ProductionOrder.id.in_(order_ids)).all()
    return {row.id: row.product_length for row in rows}


def pipe_lengths(pipes, order=None):
    """``{pipe.id: metres}`` — the length each pipe contributes.

    The Finish measurement when there is one; otherwise, pipe by pipe, that
    pipe's own order's product length — an unmeasured pipe is still a pipe the
    customer takes home, and a blank helps nobody — and only the ``order``
    argument as a last resort, for a pipe with no order of its own. Shared by
    the material table and the picker, so the sum the user confirms on the
    form is the sum the certificate would have printed.
    """
    pipes = [p for p in pipes if p is not None]
    if not pipes:
        return {}
    Pipe.prefill_stage_maps(pipes)
    order_lengths = _order_lengths(pipes)
    default_fallback = getattr(order, 'product_length', None) if order else None

    lengths = {}
    for pipe in pipes:
        length = _finish_length(pipe)
        if length is None:
            fallback = order_lengths.get(pipe.production_order_id, default_fallback)
            if fallback:
                length = round(float(fallback), 2)
        lengths[pipe.id] = length
    return lengths


def group_pipes(pipes, order=None):
    """``[{dn, pipe_class, length, quantity, pipe_codes}]``, longest run first.

    Groups by DN and class only — the printed length is the *sum* of the
    actual lengths of every pipe in that row (see ``pipe_lengths``), not one
    pipe's length copied onto the whole group.
    """
    pipes = [p for p in pipes if p is not None]
    if not pipes:
        return []

    lengths = pipe_lengths(pipes, order)

    groups = {}
    for pipe in pipes:
        dn = pipe.diameter or (order.diameter if order else None)
        pipe_class = pipe.pipe_class or (order.pipe_class if order else None)
        length = lengths.get(pipe.id)

        key = (dn, pipe_class)
        row = groups.setdefault(key, {
            'dn': dn,
            'pipe_class': pipe_class,
            'length': None,
            'quantity': 0,
            'pipe_codes': [],
        })
        row['quantity'] += 1
        if length is not None:
            row['length'] = (row['length'] or 0) + length
        row['pipe_codes'].append(pipe.no_code or pipe.pipe_code or str(pipe.id))

    rows = list(groups.values())
    for row in rows:
        if row['length'] is not None:
            row['length'] = round(row['length'], 2)
    rows.sort(key=lambda r: (-r['quantity'], r['dn'] or 0, r['length'] or 0))
    return rows


def total_metres(rows):
    """Total metres across every row.

    ``row['length']`` is already the group's total (see ``group_pipes``), so
    this is a plain sum — multiplying by quantity again would double count
    now that length stopped being a single pipe's own figure.
    """
    return round(sum(r['length'] or 0 for r in rows), 2)


def customer_name(order):
    """Customer as it should read on the certificate.

    Orders carry both a linked customer and a legacy free-text name; the linked
    record wins when there is one.
    """
    if order is None:
        return ''
    linked = getattr(order, 'customer_ref', None)
    if linked is not None:
        return linked.name_en or linked.name_ar or ''
    return (order.customer_name or '').strip()


def item_description(order):
    """The Item Description line: what the pipes are, plus the standards met."""
    parts = []
    description = (order.product_description or '').strip() if order else ''
    if not description and order and order.product:
        description = (order.product.description_en
                       or order.product.description_ar or '').strip()
    parts.append(description or 'Ductile Iron Pipes')

    labels = application_standard_labels(order)
    if labels:
        parts.append('as per ' + ', '.join(labels))
    return ' — '.join(parts)


def dn_range_label(pipes, order=None):
    """``DN`` column for the analysis rows: ``800`` or ``100-800``."""
    values = sorted({p.diameter for p in pipes if p.diameter})
    if not values and order and order.diameter:
        values = [order.diameter]
    if not values:
        return ''
    if len(values) == 1:
        return str(values[0])
    return f'{values[0]}-{values[-1]}'


def _limit_label(spec, element):
    """A specification row as the certificate writes it.

    The form uses two shapes: ``Max. 0.015`` when only a ceiling is set, and
    ``0.030: 0.070`` for a band.
    """
    lo, hi = spec.min_value, spec.max_value
    places = _decimals_for(element)

    def fmt(value):
        return f'{value:.{places}f}'

    # A zero floor reads as no floor: none of these elements has a real minimum
    # of zero, and the form writes those as "Max. X" rather than "0.00: X".
    if lo in (None, 0) and hi is not None:
        return f'Max. {fmt(hi)}'
    if lo is not None and hi is None:
        return f'Min. {fmt(lo)}'
    if lo is None and hi is None:
        return ''
    return f'{fmt(lo)}: {fmt(hi)}'


def chemical_limits():
    """``[(element, limit_label)]`` for the certificate's analysis strip.

    Reads the lab's configured element specifications so the printed limits
    cannot drift from the ones decisions are actually made against; an element
    with no row falls back to what the paper form used to say.
    """
    by_code = {}
    try:
        for spec in ElementSpecification.query.all():
            code = (spec.element_code or '').strip()
            if code:
                by_code.setdefault(code.upper(), spec)
    except Exception:  # noqa: BLE001 - a certificate still prints without them
        by_code = {}

    rows = []
    for element in WORK_TEST_ELEMENTS:
        spec = by_code.get(element.upper())
        label = _limit_label(spec, element) if spec else ''
        rows.append((element, label or FALLBACK_LIMITS.get(element, '')))
    return rows


WORK_TEST_STANDARD_FALLBACK = 'ISO 2531 & EN 545'


def certificate_orders(source):
    """Return the production orders whose compatible claims are printed."""
    if source is None:
        return []
    if hasattr(source, 'cert_type'):
        orders = list(getattr(source, 'orders', None) or [])
        if orders:
            return orders
        primary = getattr(source, 'production_order', None)
        return [primary] if primary is not None else []
    return [source]


def application_standard_labels(source):
    """Exact configured Application labels, in configuration order.

    Order compatibility is validated before issuance, so the first populated
    signature represents every selected order.  No companion ISO/EN standard
    is inferred from a selected standard's group.
    """
    from app.services.certificate_issuance_service import specification_for

    for selected_order in certificate_orders(source):
        try:
            labels = list(specification_for(selected_order).application_standards)
        except Exception:  # noqa: BLE001 - legacy certificates still print
            labels = []
        if labels:
            return labels
    return []


def certificate_standards(source, separator=' & ', fallback=None):
    """Application standards formatted for a certificate sentence."""
    labels = application_standard_labels(source)
    return separator.join(labels) if labels else (fallback or '')


def work_test_standards(order):
    """The exact Application standards the selected order was built to."""
    return certificate_standards(
        order, separator=' & ', fallback=WORK_TEST_STANDARD_FALLBACK,
    )


def _parameter_name(parameter):
    if parameter is None:
        return ''
    return ((getattr(parameter, 'name_en', None)
             or getattr(parameter, 'description_en', None)
             or getattr(parameter, 'code', None) or '').strip())


def _has_zinc(parameter):
    """Whether a configured product parameter represents a zinc layer."""
    name = _parameter_name(parameter)
    if not name:
        return False
    normalized = name.casefold().replace('-', ' ').replace('_', ' ')
    absent = ('no zinc', 'without zinc', 'non zinc', 'none', 'بدون زنك')
    return not any(marker in normalized for marker in absent)


def external_coating_statement(source):
    """Accurate Work Test coating claim from the product choices."""
    orders = certificate_orders(source)
    selected_order = orders[0] if orders else None
    product = (getattr(selected_order, 'product', None)
               if selected_order is not None else None)
    zinc = getattr(product, 'zinc_type_param', None) if product else None
    finish_parameter = (getattr(product, 'external_finish_param', None)
                        if product else None)
    finish = _parameter_name(finish_parameter) or 'the specified external finish'

    lowered_finish = finish.casefold()
    if 'bitumen' in lowered_finish:
        finish = 'Bitumen'
    elif 'epoxy' in lowered_finish:
        finish = 'Epoxy'

    if _has_zinc(zinc):
        zinc_name = _parameter_name(zinc)
        return (
            'The DI pipes have been externally coated with a zinc layer '
            f'({zinc_name}) and a {finish} finishing layer conforming to '
            'ISO 8179.'
        )
    return f'The DI pipes have been externally coated directly with {finish}.'


def _active_tests_for(pipes):
    """Every ACTIVE mechanical test on the chosen pipes or their ladles.

    A test is cut from the ladle rather than from the pipe being shipped, so a
    ladle-level test counts for every pipe poured from it; a test recorded
    against a pipe with no ladle on file still counts for that pipe.
    """
    pipe_ids = [p.id for p in pipes if p.id]
    ladle_ids = {p.ladle_id for p in pipes if getattr(p, 'ladle_id', None)}
    if not pipe_ids and not ladle_ids:
        return []

    conditions = []
    if pipe_ids:
        conditions.append(MechanicalTest.pipe_id.in_(pipe_ids))
    if ladle_ids:
        conditions.append(MechanicalTest.ladle_id.in_(ladle_ids))

    from sqlalchemy import or_
    return (MechanicalTest.query
            .filter(MechanicalTest.status == 'ACTIVE')
            .filter(or_(*conditions))
            .order_by(MechanicalTest.test_date.asc(), MechanicalTest.id.asc())
            .all())


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _tensile_mpa(test):
    """Tensile in MPa; older rows predate the stored MPa column."""
    value = getattr(test, 'tensile_mpa', None)
    if value is None and test.tensile_strength is not None:
        value = test.tensile_strength * 9.8
    return value


def mechanical_sample(pipes):
    """The tensile/elongation figures the work test certificate quotes.

    The average over the chosen ladles — the client wants the sheet to speak
    for the delivery, not for whichever single test happened to be newest.
    Each heat is averaged first and the heats are then averaged with equal
    weight, so a ladle that happened to be tested twice does not count twice
    (and the figure agrees with the MTC's per-heat means). A test recorded
    against a pipe with no ladle on file stands as a heat of its own. ``None``
    when nothing was tested, so the sheet still prints with the standards
    alone.
    """
    tests = _active_tests_for(pipes)
    if not tests:
        return None

    by_heat = {}
    for test in tests:
        key = test.ladle_id or f'pipe:{test.pipe_id}'
        by_heat.setdefault(key, []).append(test)
    heat_tensile = [_mean(t.tensile_strength for t in group) for group in by_heat.values()]
    heat_elongation = [_mean(t.elongation for t in group) for group in by_heat.values()]
    tensile = _mean(heat_tensile)
    elongation = _mean(heat_elongation)
    return {
        'elongation': round(elongation, 1) if elongation is not None else None,
        'tensile': round(tensile, 1) if tensile is not None else None,
        'dn': dn_range_label(pipes),
        'tests': len(tests),
        'heats': len(by_heat),
    }


# ---------------------------------------------------------------------------
# Material test certificate (MTC)
#
# The MTC lists one row per heat — a ladle — with that heat's chemistry beside
# its mechanical results. Its element list is the sheet's own: seven columns,
# no carbon equivalent, and tensile in MPa rather than Kg/mm².
# ---------------------------------------------------------------------------

MTC_ELEMENTS = [
    ('Mg', 'magnesium'),
    ('Mn', 'manganese'),
    ('S', 'sulfur'),
    ('Cr', 'chromium'),
    ('Cu', 'copper'),
    ('Si', 'silicon'),
    ('C', 'carbon'),
]

# The mechanical limits the MTC prints, in its own units.
MTC_MECHANICAL_STANDARDS = {
    'elongation': 'Min. :10%',
    'tensile': 'Min. : 420 (MPa )',
    'hardness': 'Max. 230',
    'source': 'ISO 2531',
}

MTC_CHEMICAL_STANDARD_SOURCE = 'ASTM A536'


def _fmt(value, places=2):
    return '' if value is None else f'{value:.{places}f}'


def _natural_key(text):
    """Sort ladle ids the way a person reads them: 2 before 10, not after.

    Heat numbers are digits separated by slashes (``2/11/2/2026``), so a plain
    string sort files every heat starting with "1" ahead of heat 2.
    """
    import re
    return [int(part) if part.isdigit() else part.lower()
            for part in re.split(r'(\d+)', text or '')]


def _annealing_batches(pipes):
    """``{pipe_id: batch}`` — the Annealing batch each chosen pipe carries.

    Annealing is where the batch is recorded (ANN-YYYYMMDD-N, stamped by the
    server since 2026-08-10), so the batch is read from that stage only. The
    lookup used to take any stage's ``bundle_number``, which also picked up
    Finish and Delivery bundle numbers.
    """
    pipe_ids = [p.id for p in pipes if p.id]
    if not pipe_ids:
        return {}
    rows = (PipeStage.query
            .filter(PipeStage.pipe_id.in_(pipe_ids))
            .filter(PipeStage.stage_name == ProductionStage.name_for_code('annealing'))
            .filter(PipeStage.bundle_number.isnot(None))
            .all())
    batches = {}
    for row in rows:
        number = (row.bundle_number or '').strip()
        if number:
            batches[row.pipe_id] = number
    return batches


def batch_numbers(pipes):
    """The batch numbers the chosen pipes were annealed under.

    The MTC lists them under Batch#, one per line. The client confirmed on
    2026-09-10 that this is the Annealing batch, not the number the lab writes
    per ladle on the chemical sheet. Pipes that never reached Annealing simply
    contribute nothing rather than an empty line.
    """
    seen = []
    for number in _annealing_batches(pipes).values():
        if number not in seen:
            seen.append(number)
    return sorted(seen, key=_natural_key)


def _analyses_for(pipes):
    """``{ladle_id: ChemicalAnalysis}`` for the ladles the pipes were cast from."""
    ladle_ids = {p.ladle_id for p in pipes if getattr(p, 'ladle_id', None)}
    if not ladle_ids:
        return {}
    rows = ChemicalAnalysis.query.filter(
        ChemicalAnalysis.ladle_id.in_(ladle_ids)).all()
    return {row.ladle_id: row for row in rows}


def _mechanical_by_ladle(pipes):
    """``{ladle_id: {tensile, elongation, hardness, tests}}`` — each figure the
    mean over every ACTIVE test on that ladle, tensile in MPa.

    A field missing on one test (hardness is entered separately and often
    is) drops out of that field's mean rather than dragging it down.
    """
    ladle_ids = {p.ladle_id for p in pipes if getattr(p, 'ladle_id', None)}
    if not ladle_ids:
        return {}
    rows = (MechanicalTest.query
            .filter(MechanicalTest.status == 'ACTIVE')
            .filter(MechanicalTest.ladle_id.in_(ladle_ids))
            .all())
    by_ladle = {}
    for row in rows:
        by_ladle.setdefault(row.ladle_id, []).append(row)
    return {
        ladle: {
            'tensile': _mean(_tensile_mpa(t) for t in tests),
            'elongation': _mean(t.elongation for t in tests),
            'hardness': _mean(t.hardness for t in tests),
            'tests': len(tests),
        }
        for ladle, tests in by_ladle.items()
    }


def mtc_chemical_standards():
    """``{element: limit_label}`` for the MTC's standard-values strip."""
    return dict(
        (code, label) for code, label in chemical_limits()
        if code in {c for c, _ in MTC_ELEMENTS})


def mtc_chemical_summary(pipes, mode):
    """The one-line chemistry row at the top of the MTC.

    In standard mode it is the accepted limits. In actual mode it is the mean
    across the ladles the chosen pipes were cast from — the customer asked for
    the average of what was really poured, not a limit.
    """
    standards = mtc_chemical_standards()
    if mode != 'actual':
        return {code: standards.get(code, '') for code, _ in MTC_ELEMENTS}

    analyses = _analyses_for(pipes).values()
    summary = {}
    for code, field in MTC_ELEMENTS:
        values = [getattr(a, field) for a in analyses
                  if getattr(a, field, None) is not None]
        summary[code] = (_fmt(sum(values) / len(values), _decimals_for(code))
                         if values else standards.get(code, ''))
    return summary


def mtc_heats(pipes, mode):
    """One row per heat: its batch, its chemistry, then its mechanical results.

    Both follow the mode. ``standard`` prints the accepted limits in every
    column — the sheet the customer asked for by that name; ``actual`` prints
    what that ladle measured: its own analysis, and the mean of every active
    mechanical test cut from it. The mechanical columns used to print the
    ladle's own results in both modes; the client asked for them to follow
    the toggle like the chemistry does (2026-09-05).
    """
    ladle_ids = []
    for pipe in pipes:
        ladle = getattr(pipe, 'ladle_id', None)
        if ladle and ladle not in ladle_ids:
            ladle_ids.append(ladle)
    if not ladle_ids:
        return []

    standards = mtc_chemical_standards()
    analyses = _analyses_for(pipes)
    mechanical = _mechanical_by_ladle(pipes)

    # A heat's pipes can be annealed in more than one batch, so the row lists
    # every batch that heat's chosen pipes went through.
    per_pipe = _annealing_batches(pipes)
    batches_by_heat = {}
    pipe_numbers_by_heat = {}
    for pipe in pipes:
        number = per_pipe.get(pipe.id)
        heat = getattr(pipe, 'ladle_id', None)
        if number and heat:
            batches_by_heat.setdefault(heat, [])
            if number not in batches_by_heat[heat]:
                batches_by_heat[heat].append(number)
        if heat:
            pipe_number = pipe.no_code or pipe.pipe_code or str(pipe.id)
            pipe_numbers_by_heat.setdefault(heat, [])
            if pipe_number not in pipe_numbers_by_heat[heat]:
                pipe_numbers_by_heat[heat].append(pipe_number)

    rows = []
    for ladle in sorted(ladle_ids, key=_natural_key):
        analysis = analyses.get(ladle)
        chemistry = {}
        for code, field in MTC_ELEMENTS:
            if mode == 'actual':
                value = getattr(analysis, field, None) if analysis else None
                chemistry[code] = (_fmt(value, _decimals_for(code))
                                   if value is not None else '')
            else:
                chemistry[code] = standards.get(code, '')

        if mode == 'actual':
            stats = mechanical.get(ladle) or {}
            tensile = stats.get('tensile')
            elongation = stats.get('elongation')
            hardness = stats.get('hardness')
            mech = {
                'tensile': round(tensile, 1) if tensile is not None else None,
                'elongation': round(elongation, 2) if elongation is not None else None,
                'hardness': round(hardness) if hardness is not None else None,
            }
        else:
            mech = {key: MTC_MECHANICAL_STANDARDS[key]
                    for key in ('tensile', 'elongation', 'hardness')}

        rows.append({
            'heat_no': ladle,
            'batch_no': ' / '.join(sorted(batches_by_heat.get(ladle, []),
                                          key=_natural_key)),
            'pipe_numbers': pipe_numbers_by_heat.get(ladle, []),
            'chemistry': chemistry,
            **mech,
        })
    return rows
