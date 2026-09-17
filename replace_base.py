new_base_html = """<!DOCTYPE html>
<html lang="{{ current_locale }}" dir="{{ 'rtl' if current_locale == 'ar' else 'ltr' }}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}GCP QC Pipes Traceability{% endblock %}</title>

    <!-- Bootstrap 5 CSS (RTL support) -->
    {% if current_locale == 'ar' %}
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.rtl.min.css" rel="stylesheet">
    {% else %}
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
    {% endif %}

    <!-- Bootstrap Icons -->
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.2/font/bootstrap-icons.min.css">

    <!-- Custom CSS -->
    <link rel="stylesheet" href="{{ url_for('static', filename='css/style.css') }}">

    <style>
        body {
            background-color: #f4f6f9;
        }
        .dashboard-wrapper {
            display: flex;
            min-height: 100vh;
        }
        .sidebar {
            width: 260px;
            background-color: #1e1e2d;
            color: #a1a5b7;
            display: flex;
            flex-direction: column;
            position: fixed;
            top: 0;
            bottom: 0;
            z-index: 1030;
            overflow-y: auto;
            transition: all 0.3s;
        }
        html[dir="rtl"] .sidebar {
            right: 0;
            left: auto;
        }
        html[dir="ltr"] .sidebar {
            left: 0;
            right: auto;
        }

        .main-content {
            flex: 1;
            transition: all 0.3s;
            display: flex;
            flex-direction: column;
            min-height: 100vh;
        }
        html[dir="ltr"] .main-content {
            margin-left: 260px;
        }
        html[dir="rtl"] .main-content {
            margin-right: 260px;
        }

        .sidebar-brand {
            padding: 24px 20px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.1);
            font-size: 1.25rem;
            font-weight: 600;
        }
        
        .sidebar-brand a {
            color: #fff;
            text-decoration: none;
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .sidebar-nav {
            padding: 20px 0;
        }

        .sidebar .nav-link {
            color: #a1a5b7;
            padding: 12px 24px;
            transition: all 0.3s ease;
            position: relative;
            display: flex;
            align-items: center;
            gap: 12px;
            font-weight: 500;
        }
        .sidebar .nav-link:hover, .sidebar .nav-link[aria-expanded="true"] {
            color: #fff;
            background: rgba(255, 255, 255, 0.05);
        }
        .sidebar .nav-link.active {
            color: #fff;
            background: rgba(255, 255, 255, 0.1);
            border-left: 3px solid #667eea;
        }
        html[dir="rtl"] .sidebar .nav-link.active {
            border-left: none;
            border-right: 3px solid #667eea;
        }
        
        .sidebar .nav-item .collapse {
            background: rgba(0,0,0,0.15);
        }
        .sidebar .nav-item .collapse .nav-link {
            padding-left: 45px;
            font-size: 0.9em;
        }
        html[dir="rtl"] .sidebar .nav-item .collapse .nav-link {
            padding-left: 24px;
            padding-right: 45px;
        }
        .sidebar-toggler {
            display: none;
        }
        
        .topbar {
            background: #fff;
            box-shadow: 0 1px 4px rgba(0,0,0,0.05);
            padding: 15px 25px;
            display: flex;
            justify-content: flex-end;
            align-items: center;
            z-index: 1020;
        }
        
        @media (max-width: 991.98px) {
            .sidebar {
                transform: translateX(-100%);
            }
            html[dir="rtl"] .sidebar {
                transform: translateX(100%);
            }
            .sidebar.show {
                transform: translateX(0);
            }
            html[dir="ltr"] .main-content {
                margin-left: 0;
            }
            html[dir="rtl"] .main-content {
                margin-right: 0;
            }
            .sidebar-toggler {
                display: block;
                margin-right: auto;
            }
            html[dir="rtl"] .sidebar-toggler {
                margin-right: 0;
                margin-left: auto;
            }
        }
    </style>

    {% block extra_css %}{% endblock %}
</head>
<body>

<div class="dashboard-wrapper">
    <!-- Sidebar -->
    <aside class="sidebar" id="sidebar">
        <div class="sidebar-brand">
            <a href="{{ url_for('main.index') }}">
                <i class="bi bi-flask"></i>
                <span>
                {% if current_locale == 'ar' %}
                نظام GCP
                {% else %}
                GCP QC Trace
                {% endif %}
                </span>
            </a>
        </div>
        
        {% if current_user.is_authenticated %}
        <ul class="nav flex-column sidebar-nav">
            <!-- Dashboard -->
            <li class="nav-item">
                <a class="nav-link" href="{{ url_for('main.index') }}">
                    <i class="bi bi-house"></i>
                    <span>{{ 'الرئيسية' if current_locale == 'ar' else 'Dashboard' }}</span>
                </a>
            </li>

            <!-- Chemical Analysis -->
            <li class="nav-item">
                <a class="nav-link collapsed" data-bs-toggle="collapse" href="#chem-collapse" role="button">
                    <i class="bi bi-flask"></i>
                    <span>{{ 'التحليل الكيميائي' if current_locale == 'ar' else 'Chemical' }}</span>
                    <i class="bi bi-chevron-down ms-auto" style="font-size:0.8em;"></i>
                </a>
                <div class="collapse" id="chem-collapse">
                    <ul class="nav flex-column">
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('chemical.list') }}">{{ 'القائمة' if current_locale == 'ar' else 'List' }}</a></li>
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('chemical.add') }}">{{ 'إضافة جديد' if current_locale == 'ar' else 'Add New' }}</a></li>
                    </ul>
                </div>
            </li>

            <!-- Production Orders -->
            <li class="nav-item">
                <a class="nav-link collapsed" data-bs-toggle="collapse" href="#orders-collapse" role="button">
                    <i class="bi bi-clipboard-check"></i>
                    <span>{{ 'أوامر الإنتاج' if current_locale == 'ar' else 'Orders' }}</span>
                    <i class="bi bi-chevron-down ms-auto" style="font-size:0.8em;"></i>
                </a>
                <div class="collapse" id="orders-collapse">
                    <ul class="nav flex-column">
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('production_orders.index') }}">{{ 'قائمة الأوامر' if current_locale == 'ar' else 'Orders List' }}</a></li>
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('production_orders.add') }}">{{ 'أمر إنتاج جديد' if current_locale == 'ar' else 'New Order' }}</a></li>
                    </ul>
                </div>
            </li>

            <!-- Production Stages -->
            <li class="nav-item">
                <a class="nav-link collapsed" data-bs-toggle="collapse" href="#stages-collapse" role="button">
                    <i class="bi bi-diagram-3"></i>
                    <span>{{ 'مراحل الإنتاج' if current_locale == 'ar' else 'Stages' }}</span>
                    <i class="bi bi-chevron-down ms-auto" style="font-size:0.8em;"></i>
                </a>
                <div class="collapse" id="stages-collapse">
                    <ul class="nav flex-column">
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('stages.list') }}">{{ 'قائمة الأنابيب' if current_locale == 'ar' else 'Pipes List' }}</a></li>
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('stages.add') }}">{{ 'تسجيل أنبوب' if current_locale == 'ar' else 'Register Pipe' }}</a></li>
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('stages.shift_dashboard') }}"><i class="bi bi-person-badge"></i> {{ 'لوحة مهندس الوردية' if current_locale == 'ar' else 'Shift Engineer' }}</a></li>
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('stages.delivery_list') }}"><i class="bi bi-truck"></i> {{ 'سجل التسليم' if current_locale == 'ar' else 'Deliveries' }}</a></li>
                    </ul>
                </div>
            </li>

            <!-- Mechanical Tests -->
            <li class="nav-item">
                <a class="nav-link collapsed" data-bs-toggle="collapse" href="#mech-collapse" role="button">
                    <i class="bi bi-gear"></i>
                    <span>{{ 'الاختبارات الميكانيكية' if current_locale == 'ar' else 'Mechanical' }}</span>
                    <i class="bi bi-chevron-down ms-auto" style="font-size:0.8em;"></i>
                </a>
                <div class="collapse" id="mech-collapse">
                    <ul class="nav flex-column">
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('mechanical.list') }}">{{ 'القائمة' if current_locale == 'ar' else 'List' }}</a></li>
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('mechanical.add') }}">{{ 'إضافة اختبار' if current_locale == 'ar' else 'Add Test' }}</a></li>
                    </ul>
                </div>
            </li>

            <!-- Reports -->
            <li class="nav-item">
                <a class="nav-link collapsed" data-bs-toggle="collapse" href="#reports-collapse" role="button">
                    <i class="bi bi-file-earmark-bar-graph"></i>
                    <span>{{ 'التقارير' if current_locale == 'ar' else 'Reports' }}</span>
                    <i class="bi bi-chevron-down ms-auto" style="font-size:0.8em;"></i>
                </a>
                <div class="collapse" id="reports-collapse">
                    <ul class="nav flex-column">
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('reports.index') }}"><i class="bi bi-house"></i> {{ 'لوحة التقارير' if current_locale == 'ar' else 'Reports Dash' }}</a></li>
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('reports.management_dashboard') }}"><i class="bi bi-speedometer2"></i> {{ 'لوحة الإدارة' if current_locale == 'ar' else 'Management Dash' }}</a></li>
                        
                        <li class="nav-item px-3 py-1 text-muted small mt-2 fw-bold text-uppercase">{{ 'الإنتاج' if current_locale == 'ar' else 'Production' }}</li>
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('reports.production_summary') }}">Production Summary</a></li>
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('reports.order_performance') }}">Order Performance</a></li>
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('reports.customer_production') }}">Customer Production</a></li>
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('reports.delivery_report') }}">Delivery Report</a></li>
                        
                        <li class="nav-item px-3 py-1 text-muted small mt-2 fw-bold text-uppercase">{{ 'الجودة' if current_locale == 'ar' else 'Quality' }}</li>
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('reports.rft_report') }}">RFT Report</a></li>
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('reports.defect_analysis') }}">Defect Analysis (Pareto)</a></li>
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('reports.heat_traceability') }}">Heat / Ladle Traceability</a></li>
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('reports.mechanical_statistical') }}">Mechanical Statistical</a></li>
                        
                        <li class="nav-item px-3 py-1 text-muted small mt-2 fw-bold text-uppercase">{{ 'الأداء' if current_locale == 'ar' else 'Performance' }}</li>
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('reports.stage_performance_v2') }}">Stage Performance</a></li>
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('reports.machine_performance_v2') }}">Machine Performance</a></li>
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('reports.mold_performance') }}">Mold Performance</a></li>
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('reports.weight_saving') }}">Weight Saving</a></li>
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('reports.annealing_hourly') }}">Annealing Hourly</a></li>
                        
                        <li class="nav-item px-3 py-1 text-muted small mt-2 fw-bold text-uppercase">{{ 'أدوات التحليل' if current_locale == 'ar' else 'Analytics Suite' }}</li>
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('analytics.pivot') }}"><i class="bi bi-table"></i> Pivot Designer</a></li>
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('analytics.kpis') }}"><i class="bi bi-speedometer"></i> KPI Designer</a></li>
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('analytics.dashboards') }}"><i class="bi bi-grid-3x3-gap"></i> Dashboards</a></li>
                        {% if current_user.is_admin %}
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('analytics.query_manager') }}"><i class="bi bi-code-square"></i> Query Manager</a></li>
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('analytics.import_data') }}"><i class="bi bi-upload"></i> Import Data</a></li>
                        {% endif %}
                    </ul>
                </div>
            </li>

            <!-- Stickers -->
            <li class="nav-item">
                <a class="nav-link" href="{{ url_for('stickers.index') }}">
                    <i class="bi bi-qr-code"></i>
                    <span>{{ 'الملصقات' if current_locale == 'ar' else 'Stickers' }}</span>
                </a>
            </li>

            <!-- AI Chatbot -->
            <li class="nav-item">
                <a class="nav-link" href="{{ url_for('chatbot.index') }}">
                    <i class="bi bi-robot"></i>
                    <span>{{ 'المساعد الذكي' if current_locale == 'ar' else 'AI Assistant' }}</span>
                </a>
            </li>

            <!-- Admin (only for admin users) -->
            {% if current_user.is_admin %}
            <li class="nav-item">
                <a class="nav-link collapsed" data-bs-toggle="collapse" href="#admin-collapse" role="button">
                    <i class="bi bi-shield-lock"></i>
                    <span>{{ 'الإدارة' if current_locale == 'ar' else 'Admin' }}</span>
                    <i class="bi bi-chevron-down ms-auto" style="font-size:0.8em;"></i>
                </a>
                <div class="collapse" id="admin-collapse">
                    <ul class="nav flex-column">
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('admin.users') }}">{{ 'المستخدمين' if current_locale == 'ar' else 'Users' }}</a></li>
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('admin.settings') }}">{{ 'الإعدادات' if current_locale == 'ar' else 'Settings' }}</a></li>
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('admin.product_parameters') }}">{{ 'معلمات المنتج' if current_locale == 'ar' else 'Product Parameters' }}</a></li>
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('admin.products') }}">{{ 'المنتجات' if current_locale == 'ar' else 'Products' }}</a></li>
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('admin.customers') }}">{{ 'العملاء' if current_locale == 'ar' else 'Customers' }}</a></li>
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('admin.molds') }}">{{ 'القوالب' if current_locale == 'ar' else 'Molds' }}</a></li>
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('admin.audit_log') }}">{{ 'سجل المراجعة' if current_locale == 'ar' else 'Audit Log' }}</a></li>
                        <li class="nav-item"><a class="nav-link" href="{{ url_for('admin.permissions') }}">{{ 'الصلاحيات' if current_locale == 'ar' else 'Permissions' }}</a></li>
                    </ul>
                </div>
            </li>
            {% endif %}
        </ul>
        {% endif %}
    </aside>

    <!-- Main Content -->
    <div class="main-content flex-grow-1">
        <!-- Topbar -->
        <div class="topbar">
            <button class="btn btn-light sidebar-toggler border" id="sidebarToggle">
                <i class="bi bi-list fs-4"></i>
            </button>
            
            {% if current_user.is_authenticated %}
            <ul class="nav ms-auto align-items-center">
                <!-- Language Switcher -->
                <li class="nav-item dropdown">
                    <a class="nav-link dropdown-toggle text-dark" href="#" data-bs-toggle="dropdown">
                        <i class="bi bi-globe"></i>
                        {{ 'العربية' if current_locale == 'ar' else 'English' }}
                    </a>
                    <ul class="dropdown-menu dropdown-menu-end shadow border-0">
                        <li><a class="dropdown-item" href="{{ url_for('auth.set_language', lang='en') }}">🇬🇧 English</a></li>
                        <li><a class="dropdown-item" href="{{ url_for('auth.set_language', lang='ar') }}">🇸🇦 العربية</a></li>
                    </ul>
                </li>

                <!-- User Menu -->
                <li class="nav-item dropdown ms-3">
                    <a class="nav-link dropdown-toggle text-dark d-flex align-items-center gap-2" href="#" data-bs-toggle="dropdown">
                        <i class="bi bi-person-circle fs-5"></i>
                        <span>{{ current_user.full_name or current_user.username }}</span>
                    </a>
                    <ul class="dropdown-menu dropdown-menu-end shadow border-0">
                        <li><span class="dropdown-item-text text-muted"><small>{{ current_user.role }}</small></span></li>
                        <li><hr class="dropdown-divider"></li>
                        <li><a class="dropdown-item" href="{{ url_for('auth.logout') }}"><i class="bi bi-box-arrow-right"></i> {{ 'تسجيل الخروج' if current_locale == 'ar' else 'Logout' }}</a></li>
                    </ul>
                </li>
            </ul>
            {% endif %}
        </div>

        <!-- Flash Messages -->
        <div class="container-fluid mt-4">
            {% with messages = get_flashed_messages(with_categories=true) %}
                {% if messages %}
                    {% for category, message in messages %}
                    <div class="alert alert-{{ 'danger' if category == 'error' else category }} alert-dismissible fade show shadow-sm" role="alert">
                        {{ message }}
                        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
                    </div>
                    {% endfor %}
                {% endif %}
            {% endwith %}
        </div>

        <!-- Page Content -->
        <div class="container-fluid py-3">
            {% block content %}{% endblock %}
        </div>

        <!-- Spacer for footer -->
        <div class="flex-grow-1"></div>

        <!-- Footer -->
        <footer class="footer mt-auto py-3 bg-white border-top">
            <div class="container-fluid text-center">
                <span class="text-muted">
                    {{ 'نظام تتبع جودة الأنابيب GCP' if current_locale == 'ar' else 'GCP QC Pipes Traceability' }}
                    &copy; 2025
                </span>
            </div>
        </footer>
    </div>
</div>

<!-- Floating Chat Button -->
{% if current_user.is_authenticated %}
<a href="{{ url_for('chatbot.index') }}" class="floating-chat-btn" title="{{ 'المساعد الذكي' if current_locale == 'ar' else 'AI Assistant' }}">
    <i class="bi bi-robot"></i>
</a>
<style>
.floating-chat-btn {
    position: fixed;
    bottom: 30px;
    {{ 'left' if current_locale == 'ar' else 'right' }}: 30px;
    width: 60px;
    height: 60px;
    background: linear-gradient(135deg, #0d6efd, #6f42c1);
    color: white;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 1.5rem;
    text-decoration: none;
    box-shadow: 0 4px 15px rgba(13, 110, 253, 0.4);
    transition: all 0.3s ease;
    z-index: 1000;
}
.floating-chat-btn:hover {
    transform: scale(1.1);
    color: white;
    box-shadow: 0 6px 20px rgba(13, 110, 253, 0.6);
}
.floating-chat-btn:active {
    transform: scale(0.95);
}
</style>
{% endif %}

<!-- Bootstrap JS -->
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js"></script>

<!-- Sidebar Toggle Script -->
<script>
    document.addEventListener('DOMContentLoaded', function() {
        var sidebarToggle = document.getElementById('sidebarToggle');
        if (sidebarToggle) {
            sidebarToggle.addEventListener('click', function() {
                document.getElementById('sidebar').classList.toggle('show');
            });
        }
    });
</script>

<!-- Custom JS -->
<script src="{{ url_for('static', filename='js/app.js') }}"></script>

{% block extra_js %}{% endblock %}
</body>
</html>
"""

with open('/var/local/lab-Chemical-analesis/lab_chemical_app/app/templates/base.html', 'w', encoding='utf-8') as f:
    f.write(new_base_html)
