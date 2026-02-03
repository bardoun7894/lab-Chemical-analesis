"""
Flask Application Factory
Lab Chemical Analysis - Multi-User Web Application
"""
from flask import Flask, request, g
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from flask_babel import Babel

from .config import config

# Initialize extensions
db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()
babel = Babel()


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

    # Initialize extensions with app
    db.init_app(app)
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

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(chemical_bp, url_prefix='/chemical')
    app.register_blueprint(stages_bp, url_prefix='/stages')
    app.register_blueprint(mechanical_bp, url_prefix='/mechanical')
    app.register_blueprint(reports_bp, url_prefix='/reports')
    app.register_blueprint(stickers_bp, url_prefix='/stickers')
    app.register_blueprint(admin_bp, url_prefix='/admin')
    app.register_blueprint(production_orders_bp, url_prefix='/orders')

    # Context processors
    @app.context_processor
    def inject_globals():
        """Inject global variables into templates"""
        return {
            'current_locale': get_locale(),
            'app_name': 'Lab Chemical Analysis',
            'app_name_ar': 'نظام تحليل المعمل الكيميائي'
        }

    # Create database tables
    with app.app_context():
        db.create_all()

        # Create default admin user
        from .models.user import User
        User.create_default_admin()

    return app
