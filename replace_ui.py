import re

with open('/var/local/lab-Chemical-analesis/PQ_Analytics_Suite_Complete.html', 'r', encoding='utf-8') as f:
    content = f.read()

new_styles = """
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Inter', 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: #f4f6f9;
            color: #333;
            min-height: 100vh;
        }

        .dashboard-wrapper {
            display: flex;
            min-height: 100vh;
        }

        /* Sidebar similar to Stitch */
        .sidebar {
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
        }

        .main-content {
            flex: 1;
            margin-left: 260px;
            padding: 30px;
            display: flex;
            flex-direction: column;
        }
        
        .header {
            margin-bottom: 30px;
        }
        
        .header h1 {
            font-size: 2em;
            margin-bottom: 8px;
            color: #2c3e50;
        }
        
        .header p {
            font-size: 1em;
            color: #7f8c8d;
        }
        
        .container-fluid {
            width: 100%;
        }
        
        .report-section {
            display: none;
            animation: fadeIn 0.4s ease;
        }
        
        .report-section.active {
            display: block;
        }
        
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }
        
        .card {
            background: white;
            border-radius: 8px;
            padding: 24px;
            margin-bottom: 24px;
            box-shadow: 0 2px 12px rgba(0,0,0,0.04);
            border: 1px solid #edf2f7;
        }
        
        .card h2 {
            color: #2c3e50;
            margin-bottom: 20px;
            font-size: 1.4em;
            padding-bottom: 12px;
            border-bottom: 1px solid #edf2f7;
        }

        h3 {
            color: #34495e;
            margin: 20px 0 15px 0;
            font-size: 1.1em;
        }
        
        .chart-container {
            position: relative;
            height: 400px;
            margin-bottom: 30px;
        }
        
        .table-container {
            overflow-x: auto;
            margin-bottom: 24px;
            border-radius: 6px;
            border: 1px solid #edf2f7;
        }
        
        table {
            width: 100%;
            border-collapse: collapse;
            background: white;
            font-size: 0.9em;
        }
        
        th {
            background: #f8fafc;
            color: #64748b;
            padding: 12px 16px;
            text-align: left;
            font-weight: 600;
            border-bottom: 1px solid #edf2f7;
            text-transform: uppercase;
            font-size: 0.85em;
            letter-spacing: 0.5px;
        }
        
        td {
            padding: 12px 16px;
            border-bottom: 1px solid #edf2f7;
            color: #475569;
        }
        
        tr:last-child td {
            border-bottom: none;
        }

        tr:hover {
            background: #f8fafc;
        }
        
        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        
        .metric-box {
            background: white;
            padding: 24px;
            border-radius: 8px;
            border: 1px solid #edf2f7;
            box-shadow: 0 2px 8px rgba(0,0,0,0.02);
            transition: transform 0.2s ease;
        }

        .metric-box:hover {
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(0,0,0,0.05);
        }
        
        .metric-box h3 {
            font-size: 0.85em;
            color: #64748b;
            margin: 0 0 10px 0;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        
        .metric-box .value {
            font-size: 2.2em;
            font-weight: 700;
            color: #0f172a;
            margin-bottom: 4px;
        }
        
        .metric-box .unit {
            font-size: 0.85em;
            color: #94a3b8;
        }
        
        .highlight td {
            background-color: #f8fafc;
            font-weight: 600;
        }
        
        .positive {
            color: #10b981;
            font-weight: 600;
        }
        
        .negative {
            color: #ef4444;
            font-weight: 600;
        }
        
        .footer {
            margin-top: auto;
            text-align: center;
            color: #94a3b8;
            padding: 20px 0;
            font-size: 0.9em;
            border-top: 1px solid #edf2f7;
        }
        
        .two-column {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 24px;
            margin-bottom: 24px;
        }
        
        @media (max-width: 992px) {
            .sidebar {
                width: 200px;
            }
            .main-content {
                margin-left: 200px;
            }
        }

        @media (max-width: 768px) {
            .dashboard-wrapper {
                flex-direction: column;
            }
            .sidebar {
                position: static;
                width: 100%;
                height: auto;
            }
            .nav-tabs {
                flex-direction: row;
                overflow-x: auto;
                padding: 10px;
            }
            .nav-tabs button {
                white-space: nowrap;
                border-left: none;
                border-bottom: 3px solid transparent;
            }
            .nav-tabs button.active {
                border-left: none;
                border-bottom: 3px solid #667eea;
            }
            .main-content {
                margin-left: 0;
                padding: 15px;
            }
            .two-column {
                grid-template-columns: 1fr;
            }
        }
        
        .status-badge {
            display: inline-block;
            padding: 6px 12px;
            border-radius: 20px;
            font-size: 0.75em;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        
        .status-good { background: #d1fae5; color: #065f46; }
        .status-warning { background: #fef3c7; color: #92400e; }
        .status-alert { background: #fee2e2; color: #991b1b; }
    </style>
"""

# Replace styles
content = re.sub(r'<style>.*?</style>', new_styles.strip(), content, flags=re.DOTALL)

# Refactor the HTML
html_to_replace = """<body>
    <div class="header">
        <h1>📊 PQ Analytics Suite</h1>
        <p>Production Quality & Process Performance Dashboard</p>
    </div>
    
    <div class="container">
        <div class="nav-tabs">
            <button class="tab-btn active" onclick="showReport('production')">Production Overview</button>
            <button class="tab-btn" onclick="showReport('weight')">Weight Saving Analysis</button>
            <button class="tab-btn" onclick="showReport('ccm')">CCM Quality Report</button>
            <button class="tab-btn" onclick="showReport('defects')">Defect Analysis</button>
            <button class="tab-btn" onclick="showReport('annealing')">Annealing Process</button>
            <button class="tab-btn" onclick="showReport('ht')">Heat Treatment</button>
            <button class="tab-btn" onclick="showReport('coating')">Coating Analysis</button>
            <button class="tab-btn" onclick="showReport('lab')">Lab Testing</button>
            <button class="tab-btn" onclick="showReport('summary')">Executive Summary</button>
        </div>"""

new_html = """<body>
    <div class="dashboard-wrapper">
        <aside class="sidebar">
            <div class="sidebar-brand">
                <h2>📊 PQ Analytics</h2>
            </div>
            <div class="nav-tabs">
                <button class="tab-btn active" onclick="showReport('production')">Production Overview</button>
                <button class="tab-btn" onclick="showReport('weight')">Weight Saving Analysis</button>
                <button class="tab-btn" onclick="showReport('ccm')">CCM Quality Report</button>
                <button class="tab-btn" onclick="showReport('defects')">Defect Analysis</button>
                <button class="tab-btn" onclick="showReport('annealing')">Annealing Process</button>
                <button class="tab-btn" onclick="showReport('ht')">Heat Treatment</button>
                <button class="tab-btn" onclick="showReport('coating')">Coating Analysis</button>
                <button class="tab-btn" onclick="showReport('lab')">Lab Testing</button>
                <button class="tab-btn" onclick="showReport('summary')">Executive Summary</button>
            </div>
        </aside>
        
        <main class="main-content">
            <div class="header">
                <h1>📊 PQ Analytics Suite</h1>
                <p>Production Quality & Process Performance Dashboard</p>
            </div>
            
            <div class="container-fluid">"""

content = content.replace(html_to_replace, new_html)

# Add closing tags for the main content and wrapper before footer
footer_and_closing = """    </div>
    
    <div class="footer">"""

new_footer_and_closing = """    </div>
            
            <div class="footer">"""

content = content.replace(footer_and_closing, new_footer_and_closing)

script_and_closing = """    <script>"""
new_script_and_closing = """        </main>
    </div>
    
    <script>"""
content = content.replace(script_and_closing, new_script_and_closing)

# Write back
with open('/var/local/lab-Chemical-analesis/PQ_Analytics_Suite_Complete.html', 'w', encoding='utf-8') as f:
    f.write(content)
