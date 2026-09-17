"""
Flask Application Factory
Lab Chemical Analysis - Multi-User Web Application
"""
import os

from flask import Flask, request, g
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFProtect
from flask_babel import Babel
from flask.sessions import SecureCookieSessionInterface
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import config

# Initialize extensions
db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()
babel = Babel()
# Registering Migrate only adds the `flask db` commands; it changes nothing at
# runtime. Migrations are applied deliberately from the command line, never on
# boot — four gunicorn workers starting at once would race each other through
# the same ALTER statements.
migrate = Migrate()


class MountedSessionInterface(SecureCookieSessionInterface):
    """Scope the session cookie to the path the app is actually mounted on.

    Flask hands out Path=/ by default, which was harmless while the app owned
    its own origin on :9999. Mounted under www.mbardouni.dev/lab_chemical it is
    not: a cookie on / rides along on every request to the other apps sharing
    that hostname. SCRIPT_NAME is the mount point, so use it, and fall back to
    Flask's own answer when the app is served from the root.
    """

    def get_cookie_path(self, app):
        return request.script_root or super().get_cookie_path(app)


def get_locale():
    """Get locale for Flask-Babel"""
    # Try to get from session, then from request
    locale = request.cookies.get('locale')
    if locale in ['ar', 'en']:
        return locale
    return request.accept_languages.best_match(['ar', 'en']) or 'en'


def create_app(config_name='default'):
    """Application factory"""
    app = Flask(__name__)

    # Load configuration
    app.config.from_object(config[config_name])

    # Behind the nginx vhost the app is mounted on a sub-path
    # (https://www.mbardouni.dev/lab_chemical). nginx strips that prefix before
    # proxying and re-announces it in X-Forwarded-Prefix; x_prefix puts it back
    # into SCRIPT_NAME so url_for(), redirects and the session cookie path all
    # carry it. x_proto is what stops Flask emitting http:// redirects that the
    # browser then refuses as mixed content. Nothing changes when the app is
    # reached directly on :9999 — the headers are simply absent.
    app.wsgi_app = ProxyFix(
        app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1
    )

    # Keep both auth cookies inside the mount. The session cookie works this
    # out per request; Flask-Login reads its path straight from config, so that
    # one is told the prefix up front via the environment.
    app.session_interface = MountedSessionInterface()
    app.config['REMEMBER_COOKIE_PATH'] = os.environ.get('LAB_MOUNT_PREFIX', '/')

    # Initialize extensions with app
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    babel.init_app(app, locale_selector=get_locale)

    # Configure login manager
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access this page.'
    login_manager.login_message_category = 'info'

    @login_manager.user_loader
    def load_user(user_id):
        from .models.user import User
        return User.query.get(int(user_id))

    # Register blueprints
    from .routes.auth import auth_bp
    from .routes.main import main_bp
    from .routes.chemical import chemical_bp
    from .routes.stages import stages_bp
    from .routes.mechanical import mechanical_bp
    from .routes.reports import reports_bp
    from .routes.stickers import stickers_bp
    from .routes.admin import admin_bp
    from .routes.production_orders import production_orders_bp
    from .routes.chatbot import chatbot_bp
    from .routes.api import api_bp
    from .routes.analytics import analytics_bp
    from .routes.warehouse import warehouse_bp
    from .routes.alerts import alerts_bp
    from .routes.certificates import certificates_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(chemical_bp, url_prefix='/chemical')
    app.register_blueprint(stages_bp, url_prefix='/stages')
    app.register_blueprint(mechanical_bp, url_prefix='/mechanical')
    app.register_blueprint(reports_bp, url_prefix='/reports')
    app.register_blueprint(stickers_bp, url_prefix='/stickers')
    app.register_blueprint(admin_bp, url_prefix='/admin')
    app.register_blueprint(production_orders_bp, url_prefix='/orders')
    app.register_blueprint(chatbot_bp, url_prefix='/chatbot')
    app.register_blueprint(api_bp, url_prefix='/api/v1')
    app.register_blueprint(analytics_bp, url_prefix='/analytics')
    app.register_blueprint(warehouse_bp, url_prefix='/warehouse')
    app.register_blueprint(alerts_bp, url_prefix='/alerts')
    app.register_blueprint(certificates_bp, url_prefix='/certificates')

    # Capture an optional audit reason from any POST so the audit listeners
    # can attach it to every row written during the request. Edit forms submit
    # `audit_reason`; the chemical/mechanical forms' existing record-level
    # `reason` field doubles as the audit reason. AJAX/JSON posts are covered
    # too. Blank means the listeners fall back to an auto-generated summary.
    @app.before_request
    def capture_audit_reason():
        if request.method not in ('POST', 'PUT', 'PATCH', 'DELETE'):
            return None
        reason = request.form.get('audit_reason') or request.form.get('reason')
        if not reason and request.is_json:
            payload = request.get_json(silent=True) or {}
            reason = payload.get('audit_reason') or payload.get('reason')
        g.audit_reason = reason.strip() if reason and reason.strip() else None
        return None

    # Context processors
    @app.context_processor
    def inject_globals():
        """Inject global variables into templates"""
        from .models.stage import ProductionStage, BUILTIN_CODES
        from .models.reason_type import ReasonType
        from .models.defect_reason import DefectReason
        from .services import measurement_stats_service
        from .services import dimension_standard_service
        from .services import lining_points_service
        from .services import application_spec_service
        stage_code_to_name = {
            code: ProductionStage.name_for_code(code)
            for code in BUILTIN_CODES.values()
        }
        return {
            'current_locale': get_locale(),
            'app_name': 'GCP QC Pipes Traceability',
            'app_name_ar': 'نظام تتبع جودة الأنابيب GCP',
            'production_stages': ProductionStage.active_names(),
            'stage_names_by_code': stage_code_to_name,
            # Global quick-pick reasons for decision/change reason fields.
            'reason_choices': ReasonType.active_list(),
            # Per-stage defect-reason options for the Defect Reason dropdowns.
            'defect_reasons_by_stage': DefectReason.active_by_stage(),
            # Wall-thickness grid shape (7 positions x 3 readings) plus the
            # normaliser that folds a legacy flat 21-cell profile into it, so
            # every template rendering the grid agrees on one source.
            'thickness_positions': measurement_stats_service.THICKNESS_POSITIONS,
            'thickness_readings': (
                measurement_stats_service.THICKNESS_READINGS_PER_POSITION
            ),
            'thickness_matrix': measurement_stats_service.thickness_matrix,
            'measurement_stats': measurement_stats_service.thickness_stats,
            'dimension_standard_for_dn': dimension_standard_service.for_dn,
            # Grid columns come off the standard now, so the templates ask
            # for them per pipe instead of receiving one fixed list.
            'ovality_columns_for': dimension_standard_service.ovality_columns_for,
            'is_ovality_point': dimension_standard_service.is_ovality_point,
            'lining_points_cols': lining_points_service.POINTS,
            'ovality_readings': dimension_standard_service.merged_readings,
            'dimension_symbol_label': dimension_standard_service.symbol_label,
            # Application spec (standards + wall/cement/coating thicknesses).
            # normalise() reads a pre-2026-08-27 single "tolerance" key into
            # both sides, so old orders stop rendering empty +/-Tol cells.
            'application_standards': application_spec_service.APPLICATION_STANDARDS,
            'application_layers': application_spec_service.APPLICATION_LAYERS,
            'application_view': application_spec_service.normalise,
            'application_labels': application_spec_service.standard_labels,
            'application_summary': application_spec_service.layer_summary,
            'application_groups_for': application_spec_service.groups_for_intent,
            # The block is single-choice: one group, one standard inside it.
            'application_standard': application_spec_service.selected_standard,
            'application_group': application_spec_service.selected_group,
            # AWWA applies to sewage and water alike, so it is rendered inside
            # both columns rather than in a third one of its own.
            'application_ungated_standards': dict(
                application_spec_service.APPLICATION_STANDARDS
            )[application_spec_service.UNGATED_GROUP],
            # Every standard keeps its own thickness table, shown as tabs.
            'application_layers_for': application_spec_service.layers_for,
            'application_spec_has': application_spec_service.spec_has_values,
            # S is sewage, L and E are water — read off the parameter's own
            # code when admin has not tagged it, so the gate works on the
            # parameters already in the database.
            'application_group_for_param': application_spec_service.group_for_intent,
            # The order picks one of the product's standards rather than
            # authoring tables of its own.
            'application_standard_options': application_spec_service.standard_options,
            # The order shows the product's tables and its own choice of which
            # one the run is built to.
            'application_for_order': application_spec_service.for_order,
        }

    @app.context_processor
    def inject_permissions():
        """Expose can(module, screen) so the nav renders from the same matrix
        the routes are enforced against."""
        from .services.permission_service import can
        return {'can': can}

    @app.context_processor
    def inject_kpi_alerts():
        """A callable, not a value: the dashboard's banner is the only caller,
        and evaluating the day's alerts on every render of every page would
        cost that for nothing."""
        def kpi_alerts_today():
            from .services import kpi_alert_service
            try:
                return kpi_alert_service.sync_day()
            except Exception:
                # A banner is never worth a 500 on the dashboard.
                db.session.rollback()
                return []

        return {'kpi_alerts_today': kpi_alerts_today}

    # Create database tables.
    #
    # LAB_SKIP_DB_BOOTSTRAP exists for the `flask db` commands. create_all()
    # below builds the whole schema from the models, so without this an
    # autogenerate run against an empty database would create every table and
    # then diff the models against them, finding no changes and writing an
    # empty migration. It also keeps `flask db upgrade` from racing the seeds.
    # Never set it for the web app itself — a fresh install needs this block.
    _skip_bootstrap = os.environ.get("LAB_SKIP_DB_BOOTSTRAP") == "1"

    with app.app_context():
        # These imports happen either way: they register the models on
        # db.metadata, which is exactly what autogenerate diffs against, so
        # skipping them would make Alembic think the tables should be dropped.
        #
        # Ensure late-added models are registered on db.metadata BEFORE
        # create_all() so their tables are created on first boot (same reason
        # ProductionStage is imported above). ReasonType has no seed step — the
        # admin populates it — so no race-safe seeding is required here.
        from .models.reason_type import ReasonType  # noqa: F401
        from .models.defect_reason import DefectReason  # noqa: F401

        if _skip_bootstrap:
            app.logger.info(
                "LAB_SKIP_DB_BOOTSTRAP=1: skipping create_all and seeding"
            )
            return app

        db.create_all()

        # Apply any pending ALTER TABLE migrations (for columns added after create_all)
        from .services.migration_service import apply_pending_migrations
        try:
            apply_pending_migrations()
        except Exception:
            pass

        # Seed built-in production stages (if table empty)
        from .models.stage import ProductionStage
        # Each step is independently guarded: a failure in one (e.g. a legacy DB
        # missing a column a later step also needs) must not skip the others.
        # In particular ensure_added_stages() must run even if backfill_codes()
        # trips on a partially-migrated table.
        for _seed_step in (
            ProductionStage.seed_defaults,      # no-op once table has rows
            ProductionStage.backfill_codes,     # fill `code` for pre-column rows
            ProductionStage.ensure_added_stages,  # insert any post-seed built-ins
            ProductionStage.collapse_lab_approval,  # merge Lab + Lab Approval → one
        ):
            try:
                _seed_step()
            except Exception:
                db.session.rollback()

        # Guarantee an app owner exists (promotes a legacy admin if needed)
        from .models.user import User
        try:
            User.create_default_owner()
        except Exception:
            # Concurrent gunicorn workers race here; a loser rolls back and the
            # winner's row is already committed.
            db.session.rollback()
            app.logger.exception('create_default_owner failed')

        # Register audit trail listeners
        from .services.audit_service import register_audit_listeners
        register_audit_listeners(app)

        # Reconcile the permission table with the taxonomy. Idempotent, so it
        # runs every boot and picks up screens added since the last deploy.
        from .models.permission import seed_default_permissions
        try:
            if not seed_default_permissions():
                app.logger.info(
                    'Permission seed: another worker seeded first; nothing to do.'
                )
        except Exception:
            db.session.rollback()
            app.logger.exception('seed_default_permissions failed')

    # Register CLI commands for scheduled jobs and manual triggering
    @app.cli.command('backup-now')
    def backup_now_cmd():
        """Create a backup and prune old ones. Entry point for the cron job."""
        from .services import backup_service
        info = backup_service.create_backup(created_by='cron', note='scheduled')
        manifest = info.get('manifest') or {}
        counts = manifest.get('row_counts', {})
        print(
            f"backup {info['filename']} {info['size']} bytes "
            f"engine={manifest.get('engine')} pipes={counts.get('pipes')}"
        )
        if info['corrupt']:
            # Exit non-zero so the cron log shows a failure, rather than a
            # reassuring line about a backup that could not be restored.
            raise SystemExit('backup reported corrupt')
        removed = backup_service.prune_backups(keep=14)
        if removed:
            print(f'pruned {len(removed)} old backup(s)')

    @app.cli.command('kpi-alerts')
    def kpi_alerts_cmd():
        """Recalculate today's KPI breaches and push the new ones to WhatsApp.

        The app has no scheduler: in the browser the bell triggers the same
        sync, but a message has to go out whether or not anyone is looking, so
        cron calls this.
        """
        from .services import kpi_alert_service
        open_alerts = kpi_alert_service.sync_day(force=True)
        pushed = kpi_alert_service.push_pending()
        print(f'{len(open_alerts)} open alert(s), {pushed} pushed')

    @app.cli.command('send-daily-report')
    def send_daily_report_cmd():
        """Send today's production report to DAILY_REPORT_RECIPIENTS."""
        from .services.email_service import send_daily_report
        result = send_daily_report()
        print(result)

    @app.cli.command('send-weekly-report')
    def send_weekly_report_cmd():
        """Send weekly summary report."""
        from .services.email_service import send_weekly_report
        result = send_weekly_report()
        print(result)

    @app.cli.command('regenerate-product-codes')
    def regenerate_product_codes_cmd():
        """Rebuild Product.product_code for all active products (applies format fix)."""
        from .models.product import Product
        products = Product.query.all()
        updated = 0
        for p in products:
            old = p.product_code
            p.generate_product_code()
            if p.product_code != old:
                updated += 1
        db.session.commit()
        print(f'Regenerated {updated} / {len(products)} product codes')

    return app
