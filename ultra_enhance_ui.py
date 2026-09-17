#!/usr/bin/env python3
import os
import re

APP_DIR = '/var/local/lab-Chemical-analesis/lab_chemical_app/app'

# 1. Update base.html to add Google Fonts
base_path = os.path.join(APP_DIR, 'templates', 'base.html')
with open(base_path, 'r', encoding='utf-8') as f:
    base_html = f.read()

font_link = """
    <!-- Google Fonts: Inter -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
    
    <!-- Custom CSS -->
"""
if 'family=Inter' not in base_html:
    base_html = base_html.replace('    <!-- Custom CSS -->', font_link)
    with open(base_path, 'w', encoding='utf-8') as f:
        f.write(base_html)

# 2. Add an ultra-premium CSS block to style.css
css_path = os.path.join(APP_DIR, 'static', 'css', 'style.css')
with open(css_path, 'r', encoding='utf-8') as f:
    css_content = f.read()

if 'STITCH DASHBOARD UI OVERRIDES' in css_content:
    css_content = css_content.split('/* ========================================================')[0]

premium_css = """
/* ========================================================
   ULTRA PREMIUM BRAND & UI OVERRIDES
   ======================================================== */
:root {
    --bs-primary: #4338ca;
    --bs-primary-rgb: 67, 56, 202;
    --bs-success: #059669;
    --bs-success-rgb: 5, 150, 105;
    --bs-warning: #d97706;
    --bs-warning-rgb: 217, 119, 6;
    --bs-danger: #dc2626;
    --bs-danger-rgb: 220, 38, 38;
    --bs-info: #0284c7;
    --bs-info-rgb: 2, 132, 199;
}

body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif !important;
    background-color: #f1f5f9 !important;
    color: #334155;
    -webkit-font-smoothing: antialiased;
    letter-spacing: -0.01em;
}

/* Sidebar Branding */
.sidebar {
    background-color: #0f172a !important;
    border-right: 1px solid #1e293b;
    box-shadow: 2px 0 10px rgba(0,0,0,0.1);
}

.sidebar-brand {
    border-bottom: 1px solid #1e293b !important;
    padding: 28px 24px !important;
}

.sidebar-brand a {
    color: #f8fafc !important;
    font-size: 1.3rem;
    letter-spacing: -0.02em;
}

.sidebar .nav-link {
    color: #94a3b8 !important;
    padding: 14px 24px !important;
    font-size: 0.95rem;
    font-weight: 500;
}

.sidebar .nav-link:hover, .sidebar .nav-link[aria-expanded="true"] {
    background-color: #1e293b !important;
    color: #f1f5f9 !important;
}

.sidebar .nav-link.active {
    background-color: #1e293b !important;
    color: #38bdf8 !important;
    border-left: 4px solid #38bdf8 !important;
}
html[dir="rtl"] .sidebar .nav-link.active {
    border-left: none !important;
    border-right: 4px solid #38bdf8 !important;
}

.sidebar .nav-item .collapse {
    background-color: #0b1120 !important;
}
.sidebar .nav-item .collapse .nav-link {
    font-size: 0.85rem;
    color: #64748b !important;
}
.sidebar .nav-item .collapse .nav-link:hover {
    color: #e2e8f0 !important;
}

/* Topbar */
.topbar {
    background: rgba(255, 255, 255, 0.8) !important;
    backdrop-filter: blur(12px);
    border-bottom: 1px solid #e2e8f0;
    box-shadow: none !important;
    padding: 12px 32px !important;
}

/* Page Header */
.page-title {
    font-weight: 700;
    color: #0f172a;
    letter-spacing: -0.03em;
    font-size: 1.75rem;
    margin-bottom: 0.25rem;
}
.page-subtitle {
    color: #64748b;
    font-weight: 400;
    font-size: 1rem;
}

/* Cards */
.card {
    background: #ffffff !important;
    border-radius: 16px !important;
    box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -2px rgba(0, 0, 0, 0.05) !important;
    border: 1px solid #edf2f7 !important;
    transition: all 0.25s ease-in-out;
}
.card:hover {
    box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.05), 0 4px 6px -4px rgba(0, 0, 0, 0.05) !important;
}

.card-header {
    background: transparent !important;
    border-bottom: 1px solid #f1f5f9 !important;
    padding: 24px 28px 16px 28px !important;
}
.card-header h5 {
    color: #1e293b !important;
    font-weight: 700 !important;
    font-size: 1.15rem;
    letter-spacing: -0.02em;
}

.card-body {
    padding: 24px 28px !important;
}

/* Dashboard Statistic Numbers */
.stat-value {
    color: #0f172a;
    font-weight: 800;
    font-size: 2.25rem;
    letter-spacing: -0.04em;
    line-height: 1;
}

.stat-label {
    color: #64748b;
    font-size: 0.85rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}

.stat-icon-wrapper {
    width: 60px;
    height: 60px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 16px;
    font-size: 1.7rem;
}

.bg-primary-soft { background-color: #e0e7ff !important; color: #4338ca !important; }
.bg-success-soft { background-color: #d1fae5 !important; color: #059669 !important; }
.bg-warning-soft { background-color: #fef3c7 !important; color: #d97706 !important; }
.bg-danger-soft { background-color: #fee2e2 !important; color: #dc2626 !important; }

/* Tables */
.table {
    font-size: 0.9rem;
    margin-bottom: 0 !important;
}
.table th {
    background-color: #f8fafc !important;
    color: #64748b !important;
    padding: 16px 20px !important;
    font-weight: 600 !important;
    text-transform: uppercase;
    font-size: 0.75rem !important;
    letter-spacing: 0.05em;
    border-bottom: 2px solid #e2e8f0 !important;
    border-top: none !important;
}
.table td {
    padding: 16px 20px !important;
    color: #334155 !important;
    border-bottom: 1px solid #f1f5f9 !important;
    vertical-align: middle !important;
    font-weight: 500;
}
.table-hover tbody tr:hover td {
    background-color: #f8fafc !important;
}

/* Badges */
.badge {
    padding: 6px 12px;
    border-radius: 20px;
    font-weight: 600;
    font-size: 0.75rem;
    letter-spacing: 0.02em;
}

/* Buttons */
.btn {
    border-radius: 8px;
    font-weight: 600;
    padding: 10px 20px;
    letter-spacing: -0.01em;
    transition: all 0.2s ease;
}
.btn-primary { background-color: var(--bs-primary); border-color: var(--bs-primary); }
.btn-primary:hover { background-color: #3730a3; border-color: #3730a3; box-shadow: 0 4px 12px rgba(67,56,202,0.3); }

/* Remove Bootstrap default outline styling to be solid softer buttons */
.btn-outline-primary { border: 1px solid #e0e7ff !important; background: #e0e7ff !important; color: #4338ca !important;}
.btn-outline-primary:hover { background: #4338ca !important; color: white !important;}

.btn-outline-success { border: 1px solid #d1fae5 !important; background: #d1fae5 !important; color: #059669 !important;}
.btn-outline-success:hover { background: #059669 !important; color: white !important;}

.btn-outline-warning { border: 1px solid #fef3c7 !important; background: #fef3c7 !important; color: #d97706 !important;}
.btn-outline-warning:hover { background: #d97706 !important; color: white !important;}

.btn-outline-info { border: 1px solid #e0f2fe !important; background: #e0f2fe !important; color: #0284c7 !important;}
.btn-outline-info:hover { background: #0284c7 !important; color: white !important;}

/* Fix alignment in card headers */
.card-header.d-flex {
    align-items: center !important;
}
.card-header .btn-sm {
    padding: 6px 14px;
    font-size: 0.8rem;
}
"""

with open(css_path, 'w', encoding='utf-8') as f:
    f.write(css_content + '\n' + premium_css)

# 3. Enhance dashboard.html specifically for layout alignment
html_path = os.path.join(APP_DIR, 'templates', 'dashboard.html')
with open(html_path, 'r', encoding='utf-8') as f:
    dashboard_html = f.read()

# Replace Page Header
dashboard_html = dashboard_html.replace('<h2>', '<h2 class="page-title">')
dashboard_html = dashboard_html.replace('<p class="text-muted">', '<p class="page-subtitle">')

# Modify Stat Cards structure
dashboard_html = re.sub(r'<h6 class="text-muted mb-2">(.*?)</h6>', r'<div class="stat-label mb-2">\1</div>', dashboard_html, flags=re.DOTALL)
dashboard_html = re.sub(r'<h2 class="mb-0[^>]*">(.*?)</h2>', r'<div class="stat-value mb-2">\1</div>', dashboard_html, flags=re.DOTALL)

# Replace old icon wraps with the new CSS wrappers
dashboard_html = dashboard_html.replace('bg-primary bg-opacity-10 rounded-circle p-3', 'stat-icon-wrapper bg-primary-soft')
dashboard_html = dashboard_html.replace('text-primary fs-3', '')

dashboard_html = dashboard_html.replace('bg-success bg-opacity-10 rounded-circle p-3', 'stat-icon-wrapper bg-success-soft')
dashboard_html = dashboard_html.replace('text-success fs-3', '')

dashboard_html = dashboard_html.replace('bg-warning bg-opacity-10 rounded-circle p-3', 'stat-icon-wrapper bg-warning-soft')
dashboard_html = dashboard_html.replace('text-warning fs-3', '')

dashboard_html = dashboard_html.replace('bg-danger bg-opacity-10 rounded-circle p-3', 'stat-icon-wrapper bg-danger-soft')
dashboard_html = dashboard_html.replace('text-danger fs-3', '')

# Quick Action Cards Polish
dashboard_html = dashboard_html.replace('class="btn btn-outline-primary', 'class="btn btn-outline-primary shadow-sm')
dashboard_html = dashboard_html.replace('class="btn btn-outline-success', 'class="btn btn-outline-success shadow-sm')
dashboard_html = dashboard_html.replace('class="btn btn-outline-warning', 'class="btn btn-outline-warning shadow-sm')
dashboard_html = dashboard_html.replace('class="btn btn-outline-info', 'class="btn btn-outline-info shadow-sm')

with open(html_path, 'w', encoding='utf-8') as f:
    f.write(dashboard_html)
