#!/usr/bin/env python3
import os
import re

APP_DIR = '/var/local/lab-Chemical-analesis/lab_chemical_app/app'

css_path = os.path.join(APP_DIR, 'static', 'css', 'style.css')
with open(css_path, 'r', encoding='utf-8') as f:
    css_content = f.read()

sidebar_pattern = r'/\* Sidebar Branding \*/.*?/\* Topbar \*/'

exact_stitch_sidebar = """/* Sidebar Branding */
.sidebar {
    background-color: #b9bfc6 !important;
    border-right: none !important;
    box-shadow: none !important;
}

.sidebar-brand {
    border-bottom: 1px solid rgba(255,255,255,0.2) !important;
    padding: 24px 20px !important;
}

.sidebar-brand a {
    color: #ffffff !important;
    font-size: 1.25rem;
    letter-spacing: 0px;
    font-weight: 700;
}

.sidebar .nav-link {
    color: #ffffff !important;
    padding: 12px 24px !important;
    font-size: 0.95rem;
    font-weight: 500;
    margin: 4px 16px;
    border-radius: 8px;
    width:calc(100% - 32px);
    transition: all 0.2s ease;
}

.sidebar .nav-link:hover, .sidebar .nav-link[aria-expanded="true"] {
    background-color: rgba(255, 255, 255, 0.2) !important;
    color: #ffffff !important;
}

.sidebar .nav-link.active {
    background-color: #ffffff !important;
    color: #b9bfc6 !important;
    border: none !important;
}
html[dir="rtl"] .sidebar .nav-link.active {
    border-left: none !important;
    border-right: none !important;
}

.sidebar .nav-item .collapse {
    background-color: rgba(255, 255, 255, 0.1) !important;
    margin: 4px 16px;
    border-radius: 8px;
}
.sidebar .nav-item .collapse .nav-link {
    font-size: 0.85rem;
    color: #ffffff !important;
    margin: 2px 0 2px 8px;
    width:calc(100% - 16px);
}
.sidebar .nav-item .collapse .nav-link:hover {
    color: #0f172a !important;
    background-color: #ffffff !important;
}
/* Topbar */"""

if '/* Sidebar Branding */' in css_content:
    new_css = re.sub(sidebar_pattern, exact_stitch_sidebar, css_content, flags=re.DOTALL)
    with open(css_path, 'w', encoding='utf-8') as f:
        f.write(new_css)
