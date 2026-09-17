"""
Filter Service - Reusable filter logic for all list views.

Provides a standard set of filters that can be applied to any SQLAlchemy query.
"""
from datetime import date, timedelta
from flask import request


def get_filter_values():
    """Extract standard filter values from request args"""
    filters = {}

    # Date range
    filters['date_from'] = request.args.get('date_from', '')
    filters['date_to'] = request.args.get('date_to', '')

    # Common filters
    filters['shift'] = request.args.get('shift', '', type=str)
    filters['production_order'] = request.args.get('production_order', '')
    filters['sales_order'] = request.args.get('sales_order', '')
    filters['customer'] = request.args.get('customer', '')
    filters['dn'] = request.args.get('dn', '', type=str)
    filters['pipe_class'] = request.args.get('pipe_class', '')
    filters['product'] = request.args.get('product', '')
    filters['pipe_code'] = request.args.get('pipe_code', '')
    filters['line'] = request.args.get('line', '')
    filters['stage'] = request.args.get('stage', '')
    filters['machine'] = request.args.get('machine', '')
    filters['mold'] = request.args.get('mold', '')
    filters['decision'] = request.args.get('decision', '')
    filters['final_decision'] = request.args.get('final_decision', '')
    filters['responsible'] = request.args.get('responsible', '')
    filters['search'] = request.args.get('search', '')
    filters['furnace_id'] = request.args.get('furnace_id', '', type=str)
    filters['ladle_id'] = request.args.get('ladle_id', '')

    return filters


def apply_pipe_filters(query, filters):
    """Apply standard filters to a Pipe query"""
    from app.models.pipe import Pipe
    from app.models.production_order import ProductionOrder

    if filters.get('date_from'):
        try:
            query = query.filter(Pipe.production_date >= filters['date_from'])
        except (ValueError, TypeError):
            pass
    if filters.get('date_to'):
        try:
            query = query.filter(Pipe.production_date <= filters['date_to'])
        except (ValueError, TypeError):
            pass
    if filters.get('shift'):
        try:
            query = query.filter(Pipe.shift == int(filters['shift']))
        except (ValueError, TypeError):
            pass
    if filters.get('dn'):
        try:
            query = query.filter(Pipe.diameter == int(filters['dn']))
        except (ValueError, TypeError):
            pass
    if filters.get('pipe_class'):
        query = query.filter(Pipe.pipe_class == filters['pipe_class'])
    if filters.get('mold'):
        query = query.filter(Pipe.mold_number == filters['mold'])
    if filters.get('ladle_id'):
        query = query.filter(Pipe.ladle_id.ilike(f"%{filters['ladle_id']}%"))
    if filters.get('pipe_code'):
        query = query.filter(
            Pipe.no_code.ilike(f"%{filters['pipe_code']}%") |
            Pipe.pipe_code.ilike(f"%{filters['pipe_code']}%")
        )
    if filters.get('final_decision'):
        query = query.filter(Pipe.final_decision_value == filters['final_decision'])
    if filters.get('production_order'):
        query = query.join(ProductionOrder, isouter=True).filter(
            ProductionOrder.order_number.ilike(f"%{filters['production_order']}%")
        )
    if filters.get('search'):
        search = filters['search']
        query = query.filter(
            Pipe.no_code.ilike(f'%{search}%') |
            Pipe.ladle_id.ilike(f'%{search}%') |
            Pipe.pipe_code.ilike(f'%{search}%')
        )

    return query


def apply_chemical_filters(query, filters):
    """Apply standard filters to a ChemicalAnalysis query"""
    from app.models.chemical import ChemicalAnalysis

    if filters.get('date_from'):
        try:
            query = query.filter(ChemicalAnalysis.test_date >= filters['date_from'])
        except (ValueError, TypeError):
            pass
    if filters.get('date_to'):
        try:
            query = query.filter(ChemicalAnalysis.test_date <= filters['date_to'])
        except (ValueError, TypeError):
            pass
    if filters.get('furnace_id'):
        try:
            query = query.filter(ChemicalAnalysis.furnace_id == int(filters['furnace_id']))
        except (ValueError, TypeError):
            pass
    if filters.get('decision'):
        query = query.filter(ChemicalAnalysis.decision == filters['decision'])
    if filters.get('ladle_id'):
        query = query.filter(ChemicalAnalysis.ladle_id.ilike(f"%{filters['ladle_id']}%"))

    return query


def apply_mechanical_filters(query, filters):
    """Apply standard filters to a MechanicalTest query"""
    from app.models.mechanical import MechanicalTest

    if filters.get('date_from'):
        try:
            query = query.filter(MechanicalTest.test_date >= filters['date_from'])
        except (ValueError, TypeError):
            pass
    if filters.get('date_to'):
        try:
            query = query.filter(MechanicalTest.test_date <= filters['date_to'])
        except (ValueError, TypeError):
            pass
    if filters.get('dn'):
        try:
            query = query.filter(MechanicalTest.diameter == int(filters['dn']))
        except (ValueError, TypeError):
            pass
    if filters.get('decision'):
        query = query.filter(MechanicalTest.decision == filters['decision'])
    if filters.get('ladle_id'):
        query = query.filter(MechanicalTest.ladle_id.ilike(f"%{filters['ladle_id']}%"))

    return query
