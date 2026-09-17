#!/usr/bin/env python3
import os
import re

# ----------------------------------------------------
# 1. FIX THE FLASK APP SIDEBAR (style.css)
# ----------------------------------------------------
css_path = '/var/local/lab-Chemical-analesis/lab_chemical_app/app/static/css/style.css'
with open(css_path, 'r', encoding='utf-8') as f:
    css_content = f.read()

sidebar_pattern = r'/\* Sidebar Branding \*/.*?/\* Topbar \*/'

light_stitch_sidebar = """/* Sidebar Branding */
.sidebar {
    background-color: #ffffff !important;
    border-right: 1px solid #edf2f7 !important;
    box-shadow: none !important;
}

.sidebar-brand {
    border-bottom: 0px solid rgba(0,0,0,0.05) !important;
    padding: 24px 20px !important;
}

.sidebar-brand a {
    color: #0f172a !important;
    font-size: 1.25rem;
    letter-spacing: 0px;
    font-weight: 700;
}

.sidebar .nav-link {
    color: #64748b !important;
    padding: 12px 24px !important;
    font-size: 0.95rem;
    font-weight: 500;
    margin: 4px 16px;
    border-radius: 8px;
    width:calc(100% - 32px);
    transition: all 0.2s ease;
}

.sidebar .nav-link:hover, .sidebar .nav-link[aria-expanded="true"] {
    background-color: #f5f7fb !important;
    color: #0f172a !important;
}

.sidebar .nav-link.active {
    background-color: #f5f7fb !important;
    color: #6e62e5 !important;
    border-left: none !important;
    border-right: none !important;
}
html[dir="rtl"] .sidebar .nav-link.active {
    border-left: none !important;
    border-right: none !important;
}

.sidebar .nav-item .collapse {
    background-color: #ffffff !important;
    margin: 4px 16px;
}
.sidebar .nav-item .collapse .nav-link {
    font-size: 0.85rem;
    color: #64748b !important;
    margin: 2px 0 2px 16px;
    width:calc(100% - 16px);
}
.sidebar .nav-item .collapse .nav-link:hover {
    color: #0f172a !important;
}

/* Fix active indicator on the left for LTR and right for RTL */
.sidebar .nav-link.active::before {
    content: '';
    position: absolute;
    left: -16px;
    top: 50%;
    transform: translateY(-50%);
    height: 60%;
    width: 4px;
    background-color: #6e62e5;
    border-radius: 0 4px 4px 0;
}
html[dir="rtl"] .sidebar .nav-link.active::before {
    left: auto;
    right: -16px;
    border-radius: 4px 0 0 4px;
}

/* Topbar */"""

if '/* Sidebar Branding */' in css_content:
    new_css = re.sub(sidebar_pattern, light_stitch_sidebar, css_content, flags=re.DOTALL)
    with open(css_path, 'w', encoding='utf-8') as f:
        f.write(new_css)

# ----------------------------------------------------
# 2. FIX THE MAIN DASHBOARD STANDALONE HTML
# ----------------------------------------------------
html_path = '/var/local/lab-Chemical-analesis/PQ_Analytics_Suite_Complete.html'
with open(html_path, 'r', encoding='utf-8') as f:
    html_content = f.read()

# Replace the specific hardcoded dark CSS in PQ_Analytics_Suite_Complete.html
dark_sidebar_css = """.sidebar {
            width: 260px;
            background-color: #1e1e2d;
            color: #fff;
            display: flex;
            flex-direction: column;
            position: fixed;
            top: 0;
            left: 0;
            bottom: 0;
            z-index: 1000;
        }

        .sidebar-brand {
            padding: 24px 20px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.1);
        }

        .sidebar-brand h2 {
            font-size: 1.25rem;
            font-weight: 600;
            letter-spacing: 0.5px;
            margin: 0;
            color: #fff;
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .nav-tabs {
            display: flex;
            flex-direction: column;
            padding: 20px 0;
            gap: 5px;
        }
        
        .nav-tabs button {
            padding: 12px 24px;
            border: none;
            background: transparent;
            color: #a1a5b7;
            text-align: left;
            cursor: pointer;
            font-size: 0.95em;
            font-weight: 500;
            transition: all 0.3s ease;
            position: relative;
        }
        
        .nav-tabs button:hover {
            color: #fff;
            background: rgba(255, 255, 255, 0.05);
        }
        
        .nav-tabs button.active {
            color: #fff;
            background: rgba(255, 255, 255, 0.1);
            font-weight: 600;
            border-left: 3px solid #667eea;
        }"""

light_sidebar_css = """.sidebar {
            width: 260px;
            background-color: #ffffff;
            color: #0f172a;
            display: flex;
            flex-direction: column;
            position: fixed;
            top: 0;
            left: 0;
            bottom: 0;
            z-index: 1000;
            border-right: 1px solid #edf2f7;
        }

        .sidebar-brand {
            padding: 32px 24px 16px 24px;
            border-bottom: none;
        }

        .sidebar-brand h2 {
            font-size: 1.4rem;
            font-weight: 700;
            letter-spacing: -0.02em;
            margin: 0;
            color: #0f172a;
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .nav-tabs {
            display: flex;
            flex-direction: column;
            padding: 10px 16px;
            gap: 6px;
        }
        
        .nav-tabs button {
            padding: 12px 16px;
            border: none;
            border-radius: 8px;
            background: transparent;
            color: #64748b;
            text-align: left;
            cursor: pointer;
            font-size: 0.95em;
            font-weight: 500;
            transition: all 0.2s ease;
            position: relative;
        }
        
        .nav-tabs button:hover {
            color: #0f172a;
            background: #f5f7fb;
        }
        
        .nav-tabs button.active {
            color: #6e62e5;
            background: #f5f7fb;
            font-weight: 600;
        }
        
        .nav-tabs button.active::before {
            content: '';
            position: absolute;
            left: -16px;
            top: 50%;
            transform: translateY(-50%);
            height: 60%;
            width: 4px;
            background-color: #6e62e5;
            border-radius: 0 4px 4px 0;
        }"""

html_content = html_content.replace(dark_sidebar_css, light_sidebar_css)

with open(html_path, 'w', encoding='utf-8') as f:
    f.write(html_content)
