#!/usr/bin/env python3
import os
import re

css_path = '/var/local/lab-Chemical-analesis/lab_chemical_app/app/static/css/style.css'
with open(css_path, 'r', encoding='utf-8') as f:
    css_content = f.read()

sidebar_pattern = r'/\* Sidebar Branding \*/.*?/\* Topbar \*/'

stitch_sidebar = """/* Sidebar Branding */
.sidebar {
    background-color: #1e1e2d !important;
    border-right: none;
    box-shadow: 2px 0 10px rgba(0,0,0,0.1);
}

.sidebar-brand {
    border-bottom: 1px solid rgba(255, 255, 255, 0.1) !important;
    padding: 24px 20px !important;
}

.sidebar-brand a {
    color: #ffffff !important;
    font-size: 1.25rem;
    letter-spacing: 0.5px;
    font-weight: 600;
}

.sidebar .nav-link {
    color: #a1a5b7 !important;
    padding: 12px 24px !important;
    font-size: 0.95rem;
    font-weight: 500;
}

.sidebar .nav-link:hover, .sidebar .nav-link[aria-expanded="true"] {
    background-color: rgba(255, 255, 255, 0.05) !important;
    color: #ffffff !important;
}

.sidebar .nav-link.active {
    background-color: rgba(255, 255, 255, 0.1) !important;
    color: #ffffff !important;
    border-left: 3px solid #667eea !important;
}
html[dir="rtl"] .sidebar .nav-link.active {
    border-left: none !important;
    border-right: 3px solid #667eea !important;
}

.sidebar .nav-item .collapse {
    background-color: rgba(0,0,0,0.15) !important;
}
.sidebar .nav-item .collapse .nav-link {
    font-size: 0.85rem;
    color: #a1a5b7 !important;
}
.sidebar .nav-item .collapse .nav-link:hover {
    color: #ffffff !important;
}

/* Topbar */"""

new_css = re.sub(sidebar_pattern, stitch_sidebar, css_content, flags=re.DOTALL)

with open(css_path, 'w', encoding='utf-8') as f:
    f.write(new_css)
