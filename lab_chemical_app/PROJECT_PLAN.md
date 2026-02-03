# Lab Chemical Analysis App - Complete Implementation Plan

## Overview
A **Multi-User Flask Web Application** (تطبيق ويب متعدد المستخدمين) for:
- تصميم قاعده بيانات لمتابعه مراحل منتج على الخط
- Multi-user production tracking system (accessible from any browser)
- Reports generation and printing (PDF/Excel)
- Product sticker printing with QR codes (custom sizes)
- Bilingual interface (Arabic عربي + English)

## Current Status: Database COMPLETE ✓
- 11 tables created and seeded
- Services (validation, ladle_utils) implemented
- Web UI layer: **TO BE IMPLEMENTED**

## Key Features:
1. **Web-based** - Access from any device with browser
2. **Multi-user** - Multiple users can work simultaneously
3. **Bilingual** - Arabic/English toggle
4. **Reports** - PDF/Excel with Arabic support
5. **QR Stickers** - Custom size label printing
6. **Excel Import** - Import from existing Master.xlsb

## All 4 Sheets Analyzed:
1. **Lab Chemical Analysis Table** - Chemical composition testing
2. **Defects** - Defect types and decisions configuration
3. **Stages Tables** - Production tracking through 8 stages
4. **Lab Mech. Tables** - Mechanical testing results

---

## Complete Database Schema (11 Tables)

### Table 1: `furnaces` (Reference)
```sql
CREATE TABLE furnaces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    furnace_code VARCHAR(10) UNIQUE NOT NULL,  -- A1, A2, B1, B2
    furnace_name VARCHAR(100),
    is_active BOOLEAN DEFAULT TRUE
);

INSERT INTO furnaces (furnace_code, furnace_name) VALUES
    ('A1', 'Furnace A1'), ('A2', 'Furnace A2'),
    ('B1', 'Furnace B1'), ('B2', 'Furnace B2');
```

### Table 2: `machines` (Reference)
```sql
CREATE TABLE machines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_code VARCHAR(20) UNIQUE NOT NULL,  -- M10, M11, M12, M100, AF1, ZC1...
    machine_name VARCHAR(100),
    stage VARCHAR(50),  -- CCM, Annealing, Zinc, etc.
    is_active BOOLEAN DEFAULT TRUE
);

INSERT INTO machines (machine_code, stage) VALUES
    ('M10', 'Melting'), ('M11', 'Melting'), ('M12', 'Melting'), ('M100', 'Melting'),
    ('AF1', 'CCM'), ('ZC2', 'CCM'), ('ZC3', 'CCM'),
    ('ZC1', 'Annealing'), ('CH2', 'Annealing'), ('CH3', 'Annealing'),
    ('CH1', 'Zinc'), ('HT2', 'Zinc'), ('HT3', 'Zinc'),
    ('HT1', 'Cutting'), ('CL2', 'Cutting'), ('CL3', 'Cutting'),
    ('CL1', 'Hydrotest'), ('BC2', 'Hydrotest'), ('BC3', 'Hydrotest'),
    ('BC1', 'Coating');
```

### Table 3: `defect_types` (Reference - from Sheet 2)
```sql
CREATE TABLE defect_types (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    defect_code VARCHAR(50),
    defect_name_ar VARCHAR(100) NOT NULL,
    defect_name_en VARCHAR(100),
    applies_to_stages TEXT,  -- JSON: ["CCM", "Annealing"]
    is_active BOOLEAN DEFAULT TRUE
);

INSERT INTO defect_types (defect_name_ar, defect_name_en, applies_to_stages) VALUES
    ('Out of specification', 'Out of specification', '["all"]'),
    ('رمال', 'Sand', '["Melting","CCM","Annealing"]'),
    ('نقل معدن/بقايا', 'Metal transfer/residue', '["Melting","CCM"]'),
    ('على معدن', 'On metal', '["Cutting"]'),
    ('سمك على', 'Thickness over', '["CCM"]'),
    ('سمك ضعيف', 'Thickness low', '["CCM"]'),
    ('خرافيت', 'Graphite', '["Lab"]'),
    ('SL خط/يسيت', 'SL Line', '["CCM"]'),
    ('كسر في الراس', 'Head break', '["Cutting"]'),
    ('Short pipe', 'Short pipe', '["Cutting"]'),
    ('D4', 'D4', '["CCM"]'),
    ('Other', 'Other', '["all"]');
```

### Table 4: `decision_types` (Reference - from Sheet 2)
```sql
CREATE TABLE decision_types (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_code VARCHAR(50) UNIQUE NOT NULL,
    decision_name_ar VARCHAR(100),
    decision_name_en VARCHAR(100),
    color_code VARCHAR(20)
);

INSERT INTO decision_types (decision_code, decision_name_ar, decision_name_en) VALUES
    ('ACCEPT', 'قبول', 'Accept'),
    ('REJECT', 'رفض', 'Reject'),
    ('HOLD', 'انتظار', 'Hold'),
    ('INSPECT_FIRST_LAST', 'فحص أول وآخر', 'Inspect 1st and Last'),
    ('INSPECT_100', 'فحص 100%', 'Inspect 100%'),
    ('DOWNGRADE', 'تخفيض', 'DownGrade'),
    ('REHEAT_TREATMENT', 'إعادة معالجة', 'Reheat Treatment'),
    ('REWORK', 'إعادة تشغيل', 'Rework');
```

### Table 5: `element_specifications` (Reference - from Sheet 1 Row 12)
```sql
CREATE TABLE element_specifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    element_code VARCHAR(10) NOT NULL,
    element_name VARCHAR(50),
    min_value DECIMAL(8,4),
    max_value DECIMAL(8,4),
    unit VARCHAR(10) DEFAULT '%'
);

INSERT INTO element_specifications (element_code, element_name, min_value, max_value) VALUES
    ('C', 'Carbon', 3.0, 3.9),
    ('Si', 'Silicon', 1.86, 2.7),
    ('Mg', 'Magnesium', 0.031, 0.07),
    ('Cu', 'Copper', NULL, 0.1),
    ('Cr', 'Chromium', NULL, 0.1),
    ('S', 'Sulfur', NULL, 0.02),
    ('Mn', 'Manganese', NULL, 0.4),
    ('P', 'Phosphorus', NULL, 0.059),
    ('Pb', 'Lead', NULL, 0.003),
    ('Al', 'Aluminum', NULL, 0.049),
    ('CE', 'Carbon Equivalent', 3.62, 4.83),
    ('MnE', 'Manganese Equivalent', 0.1, 0.85),
    ('MgE', 'Magnesium Equivalent', 0.023, NULL);
```

### Table 6: `shifts` (Reference)
```sql
CREATE TABLE shifts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shift_number INTEGER UNIQUE NOT NULL,
    shift_name VARCHAR(50),
    start_time TIME,
    end_time TIME
);

INSERT INTO shifts (shift_number, shift_name) VALUES
    (1, 'Morning'), (2, 'Afternoon'), (3, 'Night');
```

### Table 7: `engineers` (Reference)
```sql
CREATE TABLE engineers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name VARCHAR(100) NOT NULL,
    name_ar VARCHAR(100),
    role VARCHAR(50),
    is_active BOOLEAN DEFAULT TRUE
);
```

### Table 8: `chemical_analyses` (Sheet 1 Data)
```sql
CREATE TABLE chemical_analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Identification
    test_date DATE NOT NULL,
    furnace_id INTEGER REFERENCES furnaces(id),
    ladle_no INTEGER NOT NULL,
    day INTEGER,
    month INTEGER,
    year INTEGER,
    ladle_id VARCHAR(20) UNIQUE,  -- 471212025

    -- Chemical Elements (%)
    carbon DECIMAL(6,4),
    silicon DECIMAL(6,4),
    magnesium DECIMAL(6,4),
    copper DECIMAL(6,4),
    chromium DECIMAL(6,4),
    sulfur DECIMAL(6,4),
    manganese DECIMAL(6,4),
    phosphorus DECIMAL(6,4),
    lead DECIMAL(6,4),
    aluminum DECIMAL(6,4),

    -- Calculated Values
    carbon_equivalent DECIMAL(6,4),
    manganese_equivalent DECIMAL(6,4),
    magnesium_equivalent DECIMAL(6,4),

    -- Quality Control
    engineer_notes TEXT,
    decision VARCHAR(20),
    reason TEXT,
    has_defect BOOLEAN DEFAULT FALSE,
    defect_reason TEXT,
    notes TEXT,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_chem_ladle_id ON chemical_analyses(ladle_id);
CREATE INDEX idx_chem_test_date ON chemical_analyses(test_date);
```

### Table 9: `pipes` (Sheet 3 - Main Production)
```sql
CREATE TABLE pipes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Production Info
    production_date DATE NOT NULL,
    shift INTEGER,
    shift_engineer VARCHAR(100),
    manufacturing_order VARCHAR(50),

    -- Pipe Identification
    pipe_code VARCHAR(50),
    diameter INTEGER,                -- DN: 300, 500, 600
    pipe_type VARCHAR(20),           -- K9, C25, Fittings
    machine_id INTEGER REFERENCES machines(id),
    mold_number VARCHAR(20),
    iso_weight DECIMAL(10,2),
    no_code VARCHAR(50) UNIQUE,      -- N8739, N8740...
    arrange_pipe INTEGER,            -- 1-6

    -- Link to Chemical Analysis
    ladle_id VARCHAR(20) REFERENCES chemical_analyses(ladle_id),

    -- Measurements
    thickness DECIMAL(8,3),
    actual_weight DECIMAL(10,2),

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_pipes_ladle ON pipes(ladle_id);
CREATE INDEX idx_pipes_date ON pipes(production_date);
CREATE INDEX idx_pipes_no_code ON pipes(no_code);
```

### Table 10: `pipe_stages` (Sheet 3 - 8 Stages per Pipe)
```sql
CREATE TABLE pipe_stages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pipe_id INTEGER NOT NULL REFERENCES pipes(id),
    stage_name VARCHAR(50) NOT NULL,  -- CCM, Annealing, Zinc, Cutting, Hydrotest, Cement, Coating, Finish

    -- Stage timestamps
    stage_date DATE,
    stage_time TIME,

    -- Stage measurements
    measurement_value DECIMAL(10,3),  -- Zinc Slide, Cement thick, Coating thick, Length
    measurement_type VARCHAR(50),

    -- Quality Control
    decision VARCHAR(20),
    reason TEXT,
    has_defect BOOLEAN DEFAULT FALSE,
    defect_type_id INTEGER REFERENCES defect_types(id),
    defect_reason TEXT,
    notes TEXT,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(pipe_id, stage_name)
);

CREATE INDEX idx_stages_pipe ON pipe_stages(pipe_id);
CREATE INDEX idx_stages_decision ON pipe_stages(decision);
```

### Table 11: `mechanical_tests` (Sheet 4 Data)
```sql
CREATE TABLE mechanical_tests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Identification
    test_date DATE NOT NULL,
    test_number INTEGER,
    diameter INTEGER,
    code VARCHAR(50),                -- A2989, V4783
    pipe_no INTEGER,
    ladle_id VARCHAR(20) REFERENCES chemical_analyses(ladle_id),
    day INTEGER,
    month INTEGER,
    year INTEGER,

    -- Sample Measurements
    sample_thickness DECIMAL(8,3),
    d1 DECIMAL(8,3),
    d2 DECIMAL(8,3),
    d3 DECIMAL(8,3),
    avg_dimension DECIMAL(8,3),
    original_length DECIMAL(8,3),
    final_length DECIMAL(8,3),
    area_d_squared DECIMAL(12,4),

    -- Test Results
    force_kgf DECIMAL(12,4),
    tensile_strength DECIMAL(10,4),
    elongation DECIMAL(8,4),

    -- Microstructure
    microstructure TEXT,
    percent_85 DECIMAL(6,2),
    percent_70 DECIMAL(6,2),
    percent_40 DECIMAL(6,2),
    percent_1 DECIMAL(6,2),
    nodularity_percent DECIMAL(6,2),
    nodule_count INTEGER,
    hardness DECIMAL(8,2),
    carbides DECIMAL(6,2),

    -- Quality Control
    shift INTEGER,
    tester_name VARCHAR(100),
    decision VARCHAR(20),
    reason TEXT,
    has_defect BOOLEAN DEFAULT FALSE,
    defect_reason TEXT,
    comments TEXT,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_mech_ladle ON mechanical_tests(ladle_id);
CREATE INDEX idx_mech_date ON mechanical_tests(test_date);
```

---

## Entity Relationships

```
furnaces ──────< chemical_analyses >────── pipes ──────< pipe_stages
                        │                    │
                        │                    └──── machines
                        │
                        └──────< mechanical_tests

defect_types ──────< pipe_stages
decision_types (lookup for all decisions)
element_specifications (validation rules)
```

**Key Link:** `ladle_id` connects chemical_analyses → pipes → mechanical_tests

---

## Technology Stack (Updated for Flask Web App)

| Component | Technology |
|-----------|------------|
| **Backend** | **Flask** (Python web framework) |
| **Database** | **SQLite** → **PostgreSQL** (production) |
| **ORM** | **SQLAlchemy + Flask-SQLAlchemy** |
| **Frontend** | **Bootstrap 5 + Jinja2** (RTL support) |
| **Authentication** | **Flask-Login + Flask-Bcrypt** |
| **Excel Import** | **openpyxl** |
| **Reports** | **ReportLab** (PDF) + **xlsxwriter** (Excel) |
| **QR Code** | **qrcode + Pillow** |
| **Printing** | **Browser print + PDF download** |
| **i18n** | **Flask-Babel** (Arabic/English) |

---

## NEW: Multi-User & Authentication

### Table 12: `users` (NEW)
```sql
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username VARCHAR(50) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    full_name VARCHAR(100),
    full_name_ar VARCHAR(100),
    role VARCHAR(20) NOT NULL,  -- admin, supervisor, operator, viewer
    department VARCHAR(50),     -- Lab, Production, QC
    is_active BOOLEAN DEFAULT TRUE,
    last_login TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### User Roles & Permissions:
| Role | Permissions |
|------|-------------|
| **admin** | Full access, user management, settings |
| **supervisor** | All data entry, reports, approve decisions |
| **operator** | Data entry for assigned stages only |
| **viewer** | Read-only access, view reports |

---

## NEW: Reports Module

### Report Types:
1. **Daily Production Report** - Pipes produced per shift
2. **Chemical Analysis Report** - Test results with pass/fail
3. **Stage Tracking Report** - Pipe journey through stages
4. **Defect Analysis Report** - Defects by type, stage, date
5. **Mechanical Test Report** - Test results summary

### Report Features:
- PDF export with Arabic support
- Excel export
- Print directly
- Date range filters
- Filter by furnace, machine, decision

---

## NEW: QR Code & Sticker Printing

### Sticker Content:
```
┌─────────────────────────────┐
│  [COMPANY LOGO]             │
│  No. Code: N8739            │
│  Ladle#: 4713012025         │
│  DN: 600  Type: K9          │
│  Date: 13/01/2025           │
│  Weight: 817 kg             │
│  [QR CODE]                  │
│  Decision: ACCEPT           │
└─────────────────────────────┘
```

### QR Code Contains:
- no_code (pipe ID)
- ladle_id
- production_date
- diameter
- decision
- URL to view full details (optional)

---

## Updated Project Structure (Flask Web App)

```
lab_chemical_app/
├── app/
│   ├── __init__.py           ○ Flask app factory
│   ├── config.py             ○ Configuration settings
│   ├── models/
│   │   ├── __init__.py
│   │   ├── user.py           ○ User model (NEW)
│   │   ├── chemical.py       ✓ (migrate from database/)
│   │   ├── pipe.py           ✓ (migrate from database/)
│   │   └── mechanical.py     ✓ (migrate from database/)
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── auth.py           ○ Login/logout routes
│   │   ├── chemical.py       ○ Chemical analysis CRUD
│   │   ├── stages.py         ○ Production stages CRUD
│   │   ├── mechanical.py     ○ Mechanical tests CRUD
│   │   ├── reports.py        ○ Report generation
│   │   ├── stickers.py       ○ QR/sticker printing
│   │   └── admin.py          ○ User management
│   ├── services/
│   │   ├── validation.py     ✓ DONE
│   │   ├── ladle_utils.py    ✓ DONE
│   │   ├── excel_import.py   ○ NEW
│   │   ├── report_service.py ○ NEW
│   │   └── qr_service.py     ○ NEW
│   ├── templates/
│   │   ├── base.html         ○ Base layout (RTL support)
│   │   ├── auth/
│   │   │   └── login.html
│   │   ├── chemical/
│   │   │   ├── list.html
│   │   │   ├── form.html
│   │   │   └── detail.html
│   │   ├── stages/
│   │   │   ├── list.html
│   │   │   ├── form.html
│   │   │   └── tracking.html
│   │   ├── mechanical/
│   │   │   ├── list.html
│   │   │   └── form.html
│   │   ├── reports/
│   │   │   └── index.html
│   │   ├── stickers/
│   │   │   └── print.html
│   │   └── admin/
│   │       └── users.html
│   └── static/
│       ├── css/
│       │   └── style.css     ○ Custom styles + RTL
│       ├── js/
│       │   └── app.js        ○ JavaScript helpers
│       └── img/
│           └── logo.png
├── migrations/               ○ Database migrations
├── translations/             ○ i18n files (ar/en)
├── run.py                    ○ App entry point
├── requirements.txt          ○ Updated dependencies
└── lab_chemical.db           ✓ DONE (existing data)
```

---

## Implementation Plan (Phases)

### Phase 1: Flask App Setup & Auth
- [ ] Create Flask app factory (app/__init__.py)
- [ ] Add User model with roles (admin, supervisor, operator, viewer)
- [ ] Implement login/logout with Flask-Login
- [ ] Create base template with Bootstrap 5 RTL
- [ ] Add language switcher (Arabic/English)

### Phase 2: Core Pages & Navigation
- [ ] Dashboard home page with summary stats
- [ ] Navigation menu with role-based visibility
- [ ] Responsive design for tablets/mobile

### Phase 3: Chemical Analysis Module
- [ ] List view with filters (date, furnace, decision)
- [ ] Add/Edit form with validation
- [ ] Auto-calculate CE, MnE, MgE
- [ ] Highlight out-of-spec values in red

### Phase 4: Production Stages Module
- [ ] Pipe registration form
- [ ] Stage tracking view (8 stages grid)
- [ ] Update decisions at each stage
- [ ] Defect logging

### Phase 5: Mechanical Tests Module
- [ ] Test entry form
- [ ] Link to ladle_id
- [ ] Microstructure data

### Phase 6: Excel Import
- [ ] Upload Excel file (Master.xlsb)
- [ ] Preview data before import
- [ ] Validate and import to database
- [ ] Show import results/errors

### Phase 7: Reports
- [ ] Daily production report (PDF)
- [ ] Chemical analysis report
- [ ] Stage tracking report
- [ ] Defect summary report
- [ ] Export to Excel

### Phase 8: QR Code & Stickers
- [ ] Generate QR code for pipe
- [ ] Custom sticker size selector
- [ ] Preview sticker layout
- [ ] Print (browser print dialog)

---

## Required Dependencies (Flask Web App)

```
# Flask Core
Flask>=3.0.0
Flask-SQLAlchemy>=3.1.0
Flask-Login>=0.6.3
Flask-WTF>=1.2.0
Flask-Babel>=4.0.0
Flask-Bcrypt>=1.0.1

# Database
SQLAlchemy>=2.0.0

# Excel Import/Export
openpyxl>=3.1.0
xlsxwriter>=3.1.0

# PDF Reports (Arabic support)
reportlab>=4.0.0
arabic-reshaper>=3.0.0
python-bidi>=0.4.2

# QR Code
qrcode>=7.4.0
Pillow>=10.0.0

# Utilities
python-dateutil>=2.8.0
Werkzeug>=3.0.0
```

---

## Verification Plan

1. **Server Start**: `python run.py` - App runs on http://localhost:5000
2. **Login Test**: Admin login works, role-based access enforced
3. **Language Test**: Switch between Arabic/English
4. **Data Entry Test**: Add chemical analysis, pipe, stages
5. **Validation Test**: Out-of-spec values highlighted in red
6. **Excel Import Test**: Import from Master.xlsb successfully
7. **Report Test**: Generate PDF with Arabic text, download works
8. **QR Code Test**: Generate sticker, print preview works
9. **Multi-User Test**: Open in 2 browsers, both work simultaneously

---

## Critical Files to Create (Priority Order)

1. `app/__init__.py` - Flask app factory
2. `app/config.py` - Configuration
3. `app/models/user.py` - User model
4. `app/routes/auth.py` - Login/logout
5. `app/templates/base.html` - Base template with RTL
6. `app/templates/auth/login.html` - Login page
7. `app/routes/chemical.py` - Chemical analysis CRUD
8. `app/templates/chemical/list.html` - List view
9. `app/services/excel_import.py` - Excel import
10. `app/services/qr_service.py` - QR generation

---

## Sticker Customization

User can select sticker size:
- Small: 50x30mm (barcode style)
- Medium: 70x50mm (product label)
- Large: 100x70mm (detailed info)
- Custom: User enters width x height

Sticker contains:
- Company logo (optional)
- Pipe No. Code (N8739)
- Ladle ID (4713012025)
- DN (diameter) & Type (K9)
- Production Date
- Weight
- QR Code (encodes all above data)
- Decision status (Accept/Reject with color)
