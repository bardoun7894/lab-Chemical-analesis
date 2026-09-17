"""Site map — every navigable page in the app, with permission and purpose."""

from flask import current_app
from app.services.permission_service import has_permission


# Hand-written purposes for pages that matter. Keyed by endpoint name.
_PURPOSES = {
    # Dashboard
    'main.index': 'Home page / redirect to dashboard',
    'main.dashboard': 'Daily production dashboard with KPIs, charts, and AI summary',
    # Chemical
    'chemical.index': 'List all chemical analyses (ladles) with filters',
    'chemical.add': 'Record a new chemical analysis / ladle',
    # Mechanical
    'mechanical.index': 'List all mechanical tests with filters',
    'mechanical.add': 'Record a new mechanical test',
    # Stages (production)
    'stages.index': 'List all pipes with stage decisions and filters',
    'stages.add': 'Register a new pipe into production',
    'stages.console': 'Stage console — scan or search a pipe and record any stage decision including zinc coating thickness, wall thickness, dimensions, visual inspection, and all other measurements',
    'stages.shift_dashboard': 'Shift dashboard — live production overview for the current shift',
    'stages.deliveries': 'Delivery log — pipes shipped to customers',
    'stages.go': 'Quick pipe lookup — enter a code and jump to its detail',
    'stages.view': 'Pipe detail — full record for one pipe with all stages, decisions, and measurements',
    'stages.edit_pipe': 'Edit pipe — modify pipe data, diameter, class, mold, order assignment',
    'stages.tracking': 'Pipe tracking — visual timeline of a pipe through production stages',
    'stages.console_pipe': 'Console pipe view — record stage decisions and measurements for one pipe (zinc thickness, wall thickness, dimensions, defects)',
    'stages.zinc_sheet': 'Zinc coating sheet — printable zinc coating-mass report for one pipe',
    'stages.history': 'Pipe history — all decision changes and overwrites for one pipe',
    'stages.stage_history': 'Stage history — decision change log for one stage of one pipe',
    # Orders
    'production_orders.index': 'List all production orders with progress',
    'production_orders.add': 'Create a new production order',
    # Warehouse
    'warehouse.index': 'Warehouse receiving — scan barcode to receive a pipe',
    # Stickers
    'stickers.index': 'Sticker printing — search and print pipe labels',
    'stickers.search': 'Search pipes for sticker printing',
    'stickers.bulk': 'Bulk sticker printing for multiple pipes',
    # Reports
    'reports.index': 'Reports hub — links to all available reports',
    'reports.bi_dashboard': 'BI dashboard — interactive daily production report with AI analysis',
    'reports.management_dashboard': 'Management dashboard — executive KPI overview',
    'reports.production_summary': 'Production summary — pipes by date, DN, class, shift',
    'reports.order_performance': 'Order performance — progress and yield per production order',
    'reports.customer_production': 'Customer production — output grouped by customer',
    'reports.delivery_report': 'Delivery report — shipped pipes and quantities',
    'reports.daily_production': 'Daily production report — detailed day-by-day breakdown',
    'reports.annealing_hourly': 'Annealing hourly — furnace throughput per hour',
    'reports.chemical_analysis_report': 'Chemical analysis report — element trends and decisions',
    'reports.defect_summary': 'Defect summary — defect counts by type and stage',
    'reports.non_conformance': 'Non-conformance register — flagged pipes with corrective actions',
    'reports.rft_report': 'Right First Time — first-pass acceptance rate',
    'reports.rework_report': 'Rework report — pipes re-decided after initial rejection or hold',
    'reports.defect_analysis': 'Defect analysis — Pareto of defect types with trends',
    'reports.defect_heatmap': 'Defect heatmap — stage × defect-type matrix',
    'reports.heat_traceability': 'Heat traceability — ladle-to-pipe chain',
    'reports.traceability_tree': 'Traceability tree — visual pipe lineage (ladle → order → stages)',
    'reports.andon': 'Andon board — live production status for the shop floor',
    'reports.mechanical_statistical': 'Mechanical statistical report — test result distributions',
    'reports.mechanical_properties': 'Mechanical properties — tensile, elongation, hardness trends',
    'reports.lab_trends': 'Lab trends — chemical element control charts',
    'reports.data_quality': 'Data quality — completeness and consistency checks',
    'reports.diagnosis': 'Diagnosis — automated root-cause suggestions',
    'reports.approvals': 'Approvals report — lab approval status and bottlenecks',
    'reports.stage_performance': 'Stage performance — throughput and yield per stage',
    'reports.machine_performance': 'Machine performance — output by machine',
    'reports.mold_performance': 'Mold performance — output and defects by mold',
    'reports.weight_saving': 'Weight saving — actual vs ISO weight comparison',
    'reports.saving_matrix': 'Saving matrix — weight saving by DN and class',
    'reports.stage_cycle_time': 'Stage cycle time — time between stages',
    'reports.stage_funnel': 'Stage funnel — pipe flow through production stages',
    'reports.stage_audit': 'Stage audit — decision history and overwrites',
    'reports.stage_measurements': 'Stage measurements — dimensional data per stage',
    'reports.shift_engineer': 'Shift engineer report — per-engineer production output',
    'reports.shift_engineer_comparison': 'Shift engineer comparison — side-by-side engineer performance',
    'reports.delivery_overview': 'Delivery overview — delivery status and pipeline',
    'reports.delivery_comparison': 'Delivery comparison — period-over-period delivery volumes',
    'reports.ytd_comparison': 'YTD comparison — year-to-date metrics vs prior year',
    'reports.period_compare': 'Period comparison — two arbitrary date ranges side by side',
    'reports.spc': 'SPC charts — statistical process control (I-MR, X̄-R, P, C)',
    'reports.capability': 'Process capability — Cp / Cpk indices',
    # Analytics
    'analytics.pivot': 'Pivot table — cross-tabulate any dimension and measure',
    'analytics.kpis': 'KPI cards — saved KPI definitions and values',
    'analytics.dashboards': 'Custom dashboards — user-built dashboard layouts',
    'analytics.query_manager': 'Query manager — run SQL queries on production data',
    'analytics.import_data': 'Data import — upload Excel/CSV into the system',
    # Chatbot
    'chatbot.index': 'AI assistant — ask questions about production data',
    # Admin
    'admin.index': 'Admin settings hub',
    'admin.users_list': 'User management — list, add, edit users',
    'admin.users_add': 'Add a new user',
    'admin.settings': 'Application settings',
    'admin.backups': 'Database backups — create, download, delete',
    'admin.element_rules': 'Element rules — chemical acceptance thresholds',
    'admin.stage_flow_settings': 'Stage flow settings — sequential gating rules',
    'admin.kpi_targets': 'KPI targets — acceptance rate and production goals',
    'admin.dimension_standards': 'Dimension standards — pipe measurement specs (TA 1012)',
    'admin.mechanical_rules': 'Mechanical rules — test acceptance thresholds',
    'admin.ai_settings': 'AI settings — provider, model, prompts',
    'admin.sticker_settings': 'Sticker settings — label layout and content',
    'admin.email_settings': 'Email settings — SMTP configuration',
    'admin.defect_types_list': 'Defect types — manage defect categories',
    'admin.defect_types_add': 'Add a new defect type',
    'admin.decision_types_list': 'Decision types — manage stage decision options',
    'admin.decision_types_add': 'Add a new decision type',
    'admin.defect_reasons_list': 'Defect reasons — manage rejection reasons',
    'admin.defect_reasons_add': 'Add a new defect reason',
    'admin.reason_types_list': 'Reason types — manage reason categories',
    'admin.reason_types_add': 'Add a new reason type',
    'admin.machines_list': 'Machines — manage CCM, annealing, coating machines',
    'admin.machines_add': 'Add a new machine',
    'admin.furnaces_list': 'Furnaces — manage melting furnaces',
    'admin.furnaces_add': 'Add a new furnace',
    'admin.stages_list': 'Stage management — add, reorder, rename production stages',
    'admin.stages_add': 'Add a new production stage',
    'admin.product_parameters': 'Product parameters — DN, class, weight, length specs',
    'admin.product_parameters_add': 'Add a new product parameter',
    'admin.products_list': 'Products — manage product catalog',
    'admin.products_add': 'Add a new product',
    'admin.product_code_order': 'Product code order — configure pipe code segment order',
    'admin.customers_list': 'Customers — manage customer list',
    'admin.customers_add': 'Add a new customer',
    'admin.molds_list': 'Molds — manage casting molds',
    'admin.molds_add': 'Add a new mold',
    'admin.audit_log': 'Audit log — all data changes with user, timestamp, before/after',
    'admin.permissions_page': 'Permissions — role-based access control matrix',
}

# Map endpoint -> (module, screen) for permission checking.
# Built from the @requires_permission decorators on the routes.
_PERMISSIONS = {
    'main.dashboard': ('main', 'dashboard'),
    'chemical.index': ('chemical', 'list'),
    'chemical.add': ('chemical', 'add'),
    'mechanical.index': ('mechanical', 'list'),
    'mechanical.add': ('mechanical', 'add'),
    'stages.index': ('stages', 'list'),
    'stages.add': ('stages', 'add'),
    'stages.console': ('stages', 'edit'),
    'stages.console_pipe': ('stages', 'edit'),
    'stages.view': ('stages', 'list'),
    'stages.edit_pipe': ('stages', 'edit'),
    'stages.tracking': ('stages', 'tracking'),
    'stages.zinc_sheet': ('stages', 'list'),
    'stages.history': ('stages', 'list'),
    'stages.stage_history': ('stages', 'list'),
    'stages.shift_dashboard': ('stages', 'shift_dashboard'),
    'stages.deliveries': ('stages', 'delivery'),
    'stages.go': ('stages', 'list'),
    'production_orders.index': ('orders', 'list'),
    'production_orders.add': ('orders', 'add'),
    'warehouse.index': ('warehouse', 'scan'),
    'stickers.index': ('stickers', 'list'),
    'stickers.search': ('stickers', 'list'),
    'stickers.bulk': ('stickers', 'bulk_print'),
    'reports.index': ('reports', 'list'),
    'reports.bi_dashboard': ('reports', 'bi_dashboard'),
    'reports.management_dashboard': ('reports', 'management_dashboard'),
    'reports.production_summary': ('reports', 'production_summary'),
    'reports.order_performance': ('reports', 'order_performance'),
    'reports.customer_production': ('reports', 'customer_production'),
    'reports.delivery_report': ('reports', 'delivery_report'),
    'reports.daily_production': ('reports', 'daily_production'),
    'reports.annealing_hourly': ('reports', 'annealing_hourly'),
    'reports.chemical_analysis_report': ('reports', 'chemical_analysis'),
    'reports.defect_summary': ('reports', 'defect_summary'),
    'reports.non_conformance': ('reports', 'non_conformance'),
    'reports.rft_report': ('reports', 'rft_report'),
    'reports.rework_report': ('reports', 'rework_report'),
    'reports.defect_analysis': ('reports', 'defect_analysis'),
    'reports.defect_heatmap': ('reports', 'defect_heatmap'),
    'reports.heat_traceability': ('reports', 'heat_traceability'),
    'reports.traceability_tree': ('reports', 'traceability_tree'),
    'reports.andon': ('reports', 'andon'),
    'reports.mechanical_statistical': ('reports', 'mechanical_statistical'),
    'reports.mechanical_properties': ('reports', 'mechanical_properties'),
    'reports.lab_trends': ('reports', 'lab_trends'),
    'reports.data_quality': ('reports', 'data_quality'),
    'reports.diagnosis': ('reports', 'diagnosis'),
    'reports.approvals': ('reports', 'approvals'),
    'reports.stage_performance': ('reports', 'stage_performance'),
    'reports.machine_performance': ('reports', 'machine_performance'),
    'reports.mold_performance': ('reports', 'mold_performance'),
    'reports.weight_saving': ('reports', 'weight_saving'),
    'reports.saving_matrix': ('reports', 'saving_matrix'),
    'reports.stage_cycle_time': ('reports', 'stage_cycle_time'),
    'reports.stage_funnel': ('reports', 'stage_funnel'),
    'reports.stage_audit': ('reports', 'stage_audit'),
    'reports.stage_measurements': ('reports', 'stage_measurements'),
    'reports.shift_engineer': ('reports', 'shift_engineer'),
    'reports.shift_engineer_comparison': ('reports', 'shift_engineer_comparison'),
    'reports.delivery_overview': ('reports', 'delivery_overview'),
    'reports.delivery_comparison': ('reports', 'delivery_comparison'),
    'reports.ytd_comparison': ('reports', 'ytd_comparison'),
    'reports.period_compare': ('reports', 'period_compare'),
    'reports.spc': ('reports', 'spc'),
    'reports.capability': ('reports', 'capability'),
    'analytics.pivot': ('analytics', 'pivot'),
    'analytics.kpis': ('analytics', 'kpis'),
    'analytics.dashboards': ('analytics', 'dashboards'),
    'analytics.query_manager': ('analytics', 'query_manager'),
    'analytics.import_data': ('analytics', 'import'),
    'chatbot.index': ('chatbot', 'view'),
    'admin.index': ('admin', 'settings_list'),
    'admin.users_list': ('admin', 'users_list'),
    'admin.users_add': ('admin', 'users_add'),
    'admin.settings': ('admin', 'settings_list'),
    'admin.backups': ('admin', 'backups_list'),
    'admin.element_rules': ('admin', 'element_rules_list'),
    'admin.stage_flow_settings': ('admin', 'settings_list'),
    'admin.kpi_targets': ('admin', 'settings_list'),
    'admin.dimension_standards': ('admin', 'dimension_standards_list'),
    'admin.mechanical_rules': ('admin', 'mechanical_rules_list'),
    'admin.ai_settings': ('admin', 'ai_settings_list'),
    'admin.sticker_settings': ('admin', 'sticker_settings_list'),
    'admin.email_settings': ('admin', 'email_settings_list'),
    'admin.defect_types_list': ('admin', 'defect_types_list'),
    'admin.defect_types_add': ('admin', 'defect_types_add'),
    'admin.decision_types_list': ('admin', 'decision_types_list'),
    'admin.decision_types_add': ('admin', 'decision_types_add'),
    'admin.defect_reasons_list': ('admin', 'defect_reasons_list'),
    'admin.defect_reasons_add': ('admin', 'defect_reasons_add'),
    'admin.reason_types_list': ('admin', 'reason_types_list'),
    'admin.reason_types_add': ('admin', 'reason_types_add'),
    'admin.machines_list': ('admin', 'machines_list'),
    'admin.machines_add': ('admin', 'machines_add'),
    'admin.furnaces_list': ('admin', 'furnaces_list'),
    'admin.furnaces_add': ('admin', 'furnaces_add'),
    'admin.stages_list': ('admin', 'production_stages_list'),
    'admin.stages_add': ('admin', 'production_stages_add'),
    'admin.product_parameters': ('admin', 'product_parameters_list'),
    'admin.product_parameters_add': ('admin', 'product_parameters_add'),
    'admin.products_list': ('admin', 'products_list'),
    'admin.products_add': ('admin', 'products_add'),
    'admin.product_code_order': ('admin', 'product_code_order_list'),
    'admin.customers_list': ('admin', 'customers_list'),
    'admin.customers_add': ('admin', 'customers_add'),
    'admin.molds_list': ('admin', 'molds_list'),
    'admin.molds_add': ('admin', 'molds_add'),
    'admin.audit_log': ('admin', 'audit_list'),
    'admin.permissions_page': ('admin', 'permissions_list'),
}

_AREA_MAP = {
    'main': 'Dashboard',
    'chemical': 'Chemical Analysis',
    'mechanical': 'Mechanical Tests',
    'stages': 'Production / Stages',
    'production_orders': 'Production Orders',
    'orders': 'Production Orders',
    'warehouse': 'Warehouse',
    'stickers': 'Stickers',
    'reports': 'Reports',
    'analytics': 'Analytics',
    'chatbot': 'AI Assistant',
    'admin': 'Administration',
}

_cache_key = '_site_map_cache'


def _humanise(endpoint):
    """Turn 'reports.defect_analysis' into 'Defect Analysis'."""
    _, _, name = endpoint.rpartition('.')
    return name.replace('_', ' ').title()


def build_site_map(user=None):
    """Build the full site map, optionally filtered for a user's permissions.

    Cached on the app object (routes don't change at runtime).
    """
    if hasattr(current_app, _cache_key):
        full_map = getattr(current_app, _cache_key)
    else:
        full_map = _build_full_map()
        setattr(current_app, _cache_key, full_map)

    if user is None:
        return full_map

    from app.services.agent_tools import as_snapshot
    snap = as_snapshot(user)
    if snap.is_super_admin:
        return full_map

    return [p for p in full_map if _user_can_see(p, snap)]


def _user_can_see(page, snap):
    perm = page.get('permission')
    if perm is None:
        # F17: default deny for unmapped endpoints. Only main.index (a
        # redirect) is intentionally permission-free; everything else
        # missing from _PERMISSIONS is hidden from non-admin users.
        ep = page.get('endpoint', '')
        return ep == 'main.index'
    return snap.can(perm[0], perm[1])


def _build_full_map():
    pages = []
    seen = set()

    for rule in current_app.url_map.iter_rules():
        if rule.endpoint in seen:
            continue
        if 'GET' not in rule.methods:
            continue
        path = rule.rule
        if path.startswith('/static') or path.startswith('/api'):
            continue
        if '/api/' in path:
            continue
        if path.endswith('.xlsx') or path.endswith('.json'):
            continue

        # Include parameterized routes only if they have a purpose entry
        if rule.arguments and rule.endpoint not in _PURPOSES:
            continue

        seen.add(rule.endpoint)

        # Replace path parameters with readable placeholders
        display_path = path
        if rule.arguments:
            import re as _re
            display_path = _re.sub(r'<int:(\w+)>', r'{\1}', display_path)
            display_path = _re.sub(r'<(\w+)>', r'{\1}', display_path)

        blueprint = rule.endpoint.split('.')[0] if '.' in rule.endpoint else ''
        area = _AREA_MAP.get(blueprint, blueprint.title() if blueprint else 'Other')
        purpose = _PURPOSES.get(rule.endpoint, _humanise(rule.endpoint))
        perm = _PERMISSIONS.get(rule.endpoint)

        title_en = purpose.split(' — ')[0] if ' — ' in purpose else purpose.split(' - ')[0] if ' - ' in purpose else _humanise(rule.endpoint)
        title_ar = ''

        pages.append({
            'url': display_path,
            'endpoint': rule.endpoint,
            'title_en': title_en,
            'title_ar': title_ar,
            'purpose': purpose,
            'area': area,
            'permission': perm,
        })

    pages.sort(key=lambda p: (p['area'], p['url']))
    return pages


def search_pages(query, user=None, limit=8):
    """Search the site map for pages matching a query string."""
    pages = build_site_map(user)
    q = query.lower()
    scored = []
    for p in pages:
        text = f"{p['title_en']} {p['purpose']} {p['area']} {p['url']}".lower()
        if q in text:
            # Exact match in title scores highest
            if q in p['title_en'].lower():
                scored.append((0, p))
            elif q in p['purpose'].lower():
                scored.append((1, p))
            else:
                scored.append((2, p))
    scored.sort(key=lambda x: x[0])
    return [s[1] for s in scored[:limit]]
