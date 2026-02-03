"""
Authentication Routes - Login/Logout
"""
from flask import Blueprint, render_template, redirect, url_for, flash, request, make_response
from flask_login import login_user, logout_user, login_required, current_user
from app import db
from app.models.user import User

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """Login page"""
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        remember = request.form.get('remember', False)

        if not username or not password:
            flash('Please enter username and password', 'error')
            return render_template('auth/login.html')

        user = User.query.filter_by(username=username).first()

        if user is None or not user.check_password(password):
            flash('Invalid username or password', 'error')
            return render_template('auth/login.html')

        if not user.is_active:
            flash('Your account is disabled. Contact administrator.', 'error')
            return render_template('auth/login.html')

        # Login successful
        login_user(user, remember=remember)
        user.update_last_login()
        db.session.commit()

        flash(f'Welcome, {user.display_name}!', 'success')

        # Redirect to requested page or dashboard
        next_page = request.args.get('next')
        if next_page:
            return redirect(next_page)
        return redirect(url_for('main.dashboard'))

    return render_template('auth/login.html')


@auth_bp.route('/logout')
@login_required
def logout():
    """Logout user"""
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.login'))


@auth_bp.route('/set-language/<lang>')
def set_language(lang):
    """Set language preference"""
    if lang not in ['ar', 'en']:
        lang = 'en'

    # Get the page to redirect back to
    next_url = request.referrer or url_for('main.dashboard')

    response = make_response(redirect(next_url))
    response.set_cookie('locale', lang, max_age=60*60*24*365)  # 1 year
    return response
