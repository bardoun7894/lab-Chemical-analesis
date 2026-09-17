#!/usr/bin/env python3
with open('/var/local/lab-Chemical-analesis/lab_chemical_app/app/static/css/style.css', 'a', encoding='utf-8') as f:
    f.write('''\n
/* ========================================================
   STITCH DASHBOARD UI OVERRIDES
   ======================================================== */
body {
    font-family: 'Inter', 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    background-color: #f4f6f9 !important;
}

.card {
    background: white !important;
    border-radius: 8px !important;
    box-shadow: 0 2px 12px rgba(0,0,0,0.04) !important;
    border: 1px solid #edf2f7 !important;
}

.card-header {
    background: white !important;
    border-bottom: 1px solid #edf2f7 !important;
    padding: 20px 24px !important;
}

.card-header h5 {
    color: #2c3e50 !important;
    font-weight: 600 !important;
}

.card-body {
    padding: 24px !important;
}

.table th {
    background-color: #f8fafc !important;
    color: #64748b !important;
    padding: 12px 16px !important;
    font-weight: 600 !important;
    text-transform: uppercase !important;
    font-size: 0.85em !important;
    letter-spacing: 0.5px !important;
    border-bottom: 1px solid #edf2f7 !important;
    border-top: none !important;
}

.table td {
    padding: 12px 16px !important;
    color: #475569 !important;
    border-bottom: 1px solid #edf2f7 !important;
    vertical-align: middle !important;
}

.table-hover tbody tr:hover td {
    background-color: #f8fafc !important;
}

h1, h2, h3, h4, h5, h6 {
    color: #2c3e50;
}

/* Enhancing Quick Action Buttons */
.btn-outline-primary, .btn-outline-success, .btn-outline-warning, .btn-outline-info {
    border-width: 1px !important;
    background-color: white !important;
    box-shadow: 0 2px 8px rgba(0,0,0,0.02) !important;
    border-color: #edf2f7 !important;
    color: #475569 !important;
}

.btn-outline-primary:hover { background-color: #0d6efd !important; border-color: #0d6efd !important; color: white !important; }
.btn-outline-success:hover { background-color: #198754 !important; border-color: #198754 !important; color: white !important; }
.btn-outline-warning:hover { background-color: #ffc107 !important; border-color: #ffc107 !important; color: black !important; }
.btn-outline-info:hover    { background-color: #0dcaf0 !important; border-color: #0dcaf0 !important; color: black !important; }

/* Dashboard Numbers */
.card-body h2 {
    color: #0f172a !important;
    font-weight: 700 !important;
}

.text-muted {
    color: #94a3b8 !important;
}
''')
