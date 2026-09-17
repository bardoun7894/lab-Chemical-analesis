#!/usr/bin/env python3
import re

css_path = '/var/local/lab-Chemical-analesis/lab_chemical_app/app/static/css/style.css'
with open(css_path, 'r', encoding='utf-8') as f:
    css_content = f.read()

pattern = r'/\* ========================================================\n   ULTRA PREMIUM BRAND & UI OVERRIDES\n   ======================================================== \*/.*'

pq_styles = """/* ========================================================
   ULTRA PREMIUM BRAND & UI OVERRIDES
   ======================================================== */
:root {
    --bs-primary: #0891b2;
    --bs-primary-rgb: 8, 145, 178;
    --navy: #0f2044;
    --navy2: #162d5c;
    --teal: #0891b2;
    --bs-success: #057a55;
    --bs-warning: #b45309;
    --bs-danger: #c81e1e;
    --bs-info: #0e7490;
    --gray100: #f3f4f6;
    --gray900: #111827;
}

body {
    font-family: 'IBM Plex Sans', 'Inter', system-ui, sans-serif !important;
    background-color: var(--gray100) !important;
    color: var(--gray900);
}

/* Sidebar Branding - Exactly like PQ_Analytics_Suite_1.html */
.sidebar {
    background-color: var(--navy) !important;
    border-right: none !important;
    box-shadow: 2px 0 10px rgba(0,0,0,0.1) !important;
}

.sidebar-brand {
    border-bottom: 1px solid rgba(255,255,255,0.08) !important;
    padding: 18px 16px 14px !important;
}

.sidebar-brand a {
    color: #ffffff !important;
    font-size: 15px !important;
    font-weight: 700 !important;
    letter-spacing: 0.3px;
}

.sidebar .nav-link {
    color: rgba(255,255,255,0.65) !important;
    padding: 8px 14px !important;
    font-size: 13px !important;
    font-weight: 500 !important;
    margin: 4px 10px !important; 
    border-radius: 6px !important;
    transition: all 0.12s ease !important;
}

.sidebar .nav-link:hover, .sidebar .nav-link[aria-expanded="true"] {
    background-color: rgba(255,255,255,0.08) !important;
    color: #ffffff !important;
}

.sidebar .nav-link.active {
    background-color: var(--teal) !important;
    color: #ffffff !important;
    font-weight: 600 !important;
    border: none !important;
}
html[dir="rtl"] .sidebar .nav-link.active {
    border-left: none !important;
    border-right: none !important;
}

/* Fix glitching: remove margin on collapse itself, use padding on the inner nav */
.sidebar .nav-item .collapse {
    background-color: var(--navy2) !important;
    border-radius: 6px;
    margin: 0 10px; 
}
.sidebar .nav-item .collapse .nav-link {
    font-size: 12px !important;
    color: rgba(255,255,255,0.5) !important;
    padding: 6px 12px !important;
    margin: 2px 4px !important;
    width: auto !important;
}
.sidebar .nav-item .collapse .nav-link:hover {
    color: #ffffff !important;
    background-color: transparent !important;
}

/* Topbar */
.topbar {
    background: #ffffff !important;
    border-bottom: 1px solid #e5e7eb;
    box-shadow: 0 1px 3px rgba(0,0,0,.1),0 1px 2px rgba(0,0,0,.06) !important;
    padding: 12px 24px !important;
}

/* Page Header */
.page-title {
    font-weight: 700;
    color: var(--gray900);
    font-size: 15px;
    letter-spacing: 0;
}
.page-subtitle {
    color: #9ca3af;
    font-size: 11px;
}

/* Cards (matches PQ) */
.card {
    background: #ffffff !important;
    border-radius: 12px !important;
    box-shadow: 0 1px 3px rgba(0,0,0,.1),0 1px 2px rgba(0,0,0,.06) !important;
    border: 1px solid #e5e7eb !important;
}
.card:hover {
    box-shadow: 0 4px 6px -1px rgba(0,0,0,.1),0 2px 4px -1px rgba(0,0,0,.06) !important;
}

.card-header {
    background: #f9fafb !important;
    border-bottom: 1px solid #e5e7eb !important;
    padding: 12px 16px !important;
}
.card-header h5 {
    color: #1f2937 !important;
    font-weight: 700 !important;
    font-size: 13px !important;
}

.card-body {
    padding: 16px !important;
}

/* Dashboard Statistic Numbers (KPI like PQ) */
.stat-value {
    color: var(--gray900);
    font-weight: 700;
    font-size: 28px;
    line-height: 1;
}

.stat-label {
    color: #6b7280;
    font-size: 11px;
    font-weight: 500;
    margin-bottom: 6px;
    text-transform: none;
}

.stat-icon-wrapper {
    width: 48px;
    height: 48px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 12px;
    font-size: 1.5rem;
}

.bg-primary-soft { background-color: #ecfeff !important; color: var(--teal) !important; }
.bg-success-soft { background-color: #d1fae5 !important; color: #057a55 !important; }
.bg-warning-soft { background-color: #fef3c7 !important; color: #b45309 !important; }
.bg-danger-soft { background-color: #fde8e8 !important; color: #c81e1e !important; }

/* Tables */
.table {
    font-size: 12px;
}
.table th {
    background-color: #f9fafb !important;
    color: #6b7280 !important;
    padding: 8px 12px !important;
    font-weight: 700 !important;
    text-transform: uppercase;
    font-size: 10px !important;
    letter-spacing: 0.4px;
    border-bottom: 2px solid #e5e7eb !important;
    border-top: none !important;
}
.table td {
    padding: 8px 12px !important;
    color: #1f2937 !important;
    border-bottom: 1px solid #f3f4f6 !important;
    vertical-align: middle !important;
}
.table-hover tbody tr:hover td {
    background-color: #f9fafb !important;
}

/* Badges */
.badge {
    padding: 2px 8px !important;
    border-radius: 12px !important;
    font-weight: 700 !important;
    font-size: 10px !important;
    letter-spacing: 0.2px;
}

/* Buttons */
.btn {
    border-radius: 8px;
    font-weight: 600;
    padding: 6px 14px;
    font-size: 12px !important;
    transition: all 0.12s ease;
}
.btn-primary { background-color: var(--teal) !important; border-color: var(--teal) !important; color: #fff !important;}
.btn-primary:hover { background-color: #06b6d4 !important; border-color: #06b6d4 !important; box-shadow: none !important;}

/* Make outlines like TBTN */
.btn-outline-primary { border: 1px solid #e5e7eb !important; background: #ffffff !important; color: #374151 !important;}
.btn-outline-primary:hover { background: #f9fafb !important; color: #374151 !important; }

.btn-outline-success { border: 1px solid #057a55 !important; background: #057a55 !important; color: white !important;}
.btn-outline-success:hover { background: #046c4e !important; color: white !important;}

.btn-outline-warning { border: 1px solid #b45309 !important; background: #b45309 !important; color: white !important;}
.btn-outline-warning:hover { background: #92400e !important; color: white !important; }

.btn-outline-info { border: 1px solid #0e7490 !important; background: #0e7490 !important; color: white !important;}

.card-header.d-flex { align-items: center !important; }
"""

new_css = re.sub(pattern, pq_styles, css_content, flags=re.DOTALL)
with open(css_path, 'w', encoding='utf-8') as f:
    f.write(new_css)
