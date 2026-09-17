"""
Email Service - Daily Report Automation
Sends scheduled email reports to configured recipients
"""

import os
import json
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import date, timedelta
from app import db
from app.models.pipe import Pipe, PipeStage
from app.models.chemical import ChemicalAnalysis
from app.models.mechanical import MechanicalTest


# Path to app settings JSON
APP_SETTINGS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "app_settings.json"
)


def load_email_config():
    """Load email configuration from app settings file"""
    # First try to load from settings file
    try:
        with open(APP_SETTINGS_PATH, "r", encoding="utf-8") as f:
            settings = json.load(f)
        email_cfg = settings.get("email", {})
    except Exception:
        email_cfg = {}

    # Also check Flask app config (for runtime overrides)
    try:
        from flask import current_app

        if current_app.config.get("MAIL_SERVER"):
            return {
                "mail_server": current_app.config.get("MAIL_SERVER", ""),
                "mail_port": current_app.config.get("MAIL_PORT", 587),
                "mail_use_tls": current_app.config.get("MAIL_USE_TLS", True),
                "mail_username": current_app.config.get("MAIL_USERNAME", ""),
                "mail_password": current_app.config.get("MAIL_PASSWORD", ""),
                "mail_default_sender": current_app.config.get(
                    "MAIL_DEFAULT_SENDER", "noreply@gcpipes.com"
                ),
                "recipients": current_app.config.get("DAILY_REPORT_RECIPIENTS", []),
            }
    except:
        pass

    # Return from settings file
    return {
        "mail_server": email_cfg.get("mail_server", ""),
        "mail_port": email_cfg.get("mail_port", 587),
        "mail_use_tls": email_cfg.get("mail_use_tls", True),
        "mail_username": email_cfg.get("mail_username", ""),
        "mail_password": email_cfg.get("mail_password", ""),
        "mail_default_sender": email_cfg.get(
            "mail_default_sender", "noreply@gcpipes.com"
        ),
        "recipients": [
            r.strip() for r in email_cfg.get("recipients", "").split(",") if r.strip()
        ],
    }


def send_email(subject, body, recipients, html=None):
    """Send email with optional HTML content"""
    config = load_email_config()

    if not config["mail_server"] or not recipients:
        return {"success": False, "error": "Email not configured or no recipients"}

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = config["mail_default_sender"]
        msg["To"] = ", ".join(recipients)

        # Plain text part
        msg.attach(MIMEText(body, "plain", "utf-8"))

        # HTML part if provided
        if html:
            msg.attach(MIMEText(html, "html", "utf-8"))

        # Connect to server and send
        server = smtplib.SMTP(config["mail_server"], config["mail_port"])
        if config["mail_use_tls"]:
            server.starttls()
        if config["mail_username"] and config["mail_password"]:
            server.login(config["mail_username"], config["mail_password"])

        server.send_message(msg)
        server.quit()

        return {
            "success": True,
            "message": f"Email sent to {len(recipients)} recipients",
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


def generate_daily_report_html(report_date=None):
    """Generate HTML content for daily report"""
    if report_date is None:
        report_date = date.today()

    yesterday = report_date - timedelta(days=1)

    # Get data
    pipes_today = Pipe.query.filter_by(production_date=report_date).all()
    pipes_yesterday = Pipe.query.filter_by(production_date=yesterday).all()

    chem_today = ChemicalAnalysis.query.filter_by(test_date=report_date).all()
    mech_today = MechanicalTest.query.filter_by(test_date=report_date).all()

    # Calculate stats
    total_pipes = len(pipes_today)
    accepted_pipes = len([p for p in pipes_today if p.final_decision_value == "ACCEPT"])
    rejected_pipes = len([p for p in pipes_today if p.final_decision_value == "REJECT"])
    hold_pipes = len([p for p in pipes_today if p.final_decision_value == "HOLD"])

    # By diameter
    by_diameter = {}
    for pipe in pipes_today:
        dn = pipe.diameter or "Unknown"
        by_diameter[dn] = by_diameter.get(dn, 0) + 1

    # By shift
    by_shift = {1: 0, 2: 0, 3: 0}
    for pipe in pipes_today:
        shift = pipe.shift or 1
        if shift in by_shift:
            by_shift[shift] += 1

    # Chemical analysis stats
    chem_accepted = len([c for c in chem_today if c.decision == "ACCEPT"])
    chem_rejected = len([c for c in chem_today if c.decision == "REJECT"])

    # Mechanical test stats
    mech_accepted = len([m for m in mech_today if m.decision == "ACCEPT"])
    mech_rejected = len([m for m in mech_today if m.decision == "REJECT"])

    # Generate HTML
    html = f"""
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; direction: ltr; }}
            .header {{ background: #0066CC; color: white; padding: 20px; text-align: center; }}
            .section {{ margin: 20px 0; padding: 15px; border: 1px solid #ddd; }}
            .stats {{ display: flex; flex-wrap: wrap; }}
            .stat-box {{ flex: 1; min-width: 150px; margin: 10px; padding: 15px; background: #f5f5f5; border-radius: 5px; text-align: center; }}
            .stat-number {{ font-size: 24px; font-weight: bold; }}
            .stat-label {{ color: #666; }}
            .accepted {{ color: green; }}
            .rejected {{ color: red; }}
            .hold {{ color: orange; }}
            table {{ border-collapse: collapse; width: 100%; margin: 10px 0; }}
            th, td {{ border: 1px solid #ddd; padding: 8px; text-align: center; }}
            th {{ background: #0066CC; color: white; }}
            .footer {{ margin-top: 20px; text-align: center; color: #666; font-size: 12px; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>GCP QC Pipes Traceability</h1>
            <h2>Daily Production Report - {report_date.strftime("%Y-%m-%d")}</h2>
        </div>
        
        <div class="section">
            <h3>Production Summary</h3>
            <div class="stats">
                <div class="stat-box">
                    <div class="stat-number">{total_pipes}</div>
                    <div class="stat-label">Total Pipes</div>
                </div>
                <div class="stat-box">
                    <div class="stat-number accepted">{accepted_pipes}</div>
                    <div class="stat-label">Accepted</div>
                </div>
                <div class="stat-box">
                    <div class="stat-number rejected">{rejected_pipes}</div>
                    <div class="stat-label">Rejected</div>
                </div>
                <div class="stat-box">
                    <div class="stat-number hold">{hold_pipes}</div>
                    <div class="stat-label">Hold</div>
                </div>
            </div>
        </div>
        
        <div class="section">
            <h3>By Diameter</h3>
            <table>
                <tr>
                    <th>Diameter</th>
                    {"".join(f"<th>DN{dn}</th>" for dn in sorted(by_diameter.keys()))}
                </tr>
                <tr>
                    <td>Count</td>
                    {"".join(f"<td>{by_diameter[dn]}</td>" for dn in sorted(by_diameter.keys()))}
                </tr>
            </table>
        </div>
        
        <div class="section">
            <h3>By Shift</h3>
            <table>
                <tr>
                    <th>Shift</th>
                    <th>Shift 1 (8:00-4:00)</th>
                    <th>Shift 2 (4:00-12:00)</th>
                    <th>Shift 3 (12:00-8:00)</th>
                </tr>
                <tr>
                    <td>Count</td>
                    <td>{by_shift[1]}</td>
                    <td>{by_shift[2]}</td>
                    <td>{by_shift[3]}</td>
                </tr>
            </table>
        </div>
        
        <div class="section">
            <h3>Chemical Analysis</h3>
            <div class="stats">
                <div class="stat-box">
                    <div class="stat-number">{len(chem_today)}</div>
                    <div class="stat-label">Total Tests</div>
                </div>
                <div class="stat-box">
                    <div class="stat-number accepted">{chem_accepted}</div>
                    <div class="stat-label">Accepted</div>
                </div>
                <div class="stat-box">
                    <div class="stat-number rejected">{chem_rejected}</div>
                    <div class="stat-label">Rejected</div>
                </div>
            </div>
        </div>
        
        <div class="section">
            <h3>Mechanical Tests</h3>
            <div class="stats">
                <div class="stat-box">
                    <div class="stat-number">{len(mech_today)}</div>
                    <div class="stat-label">Total Tests</div>
                </div>
                <div class="stat-box">
                    <div class="stat-number accepted">{mech_accepted}</div>
                    <div class="stat-label">Accepted</div>
                </div>
                <div class="stat-box">
                    <div class="stat-number rejected">{mech_rejected}</div>
                    <div class="stat-label">Rejected</div>
                </div>
            </div>
        </div>
        
        <div class="footer">
            <p>Generated by GCP QC Pipes Traceability System</p>
            <p>Report Date: {report_date.strftime("%Y-%m-%d %H:%M")}</p>
        </div>
    </body>
    </html>
    """

    return html


def send_daily_report(report_date=None):
    """Send daily production report email"""
    config = load_email_config()

    if not config["recipients"] or not config["mail_server"]:
        return {"success": False, "error": "Email not configured"}

    if report_date is None:
        report_date = date.today()

    # Generate content
    html_content = generate_daily_report_html(report_date)

    # Plain text summary
    pipes_today = Pipe.query.filter_by(production_date=report_date).all()
    total = len(pipes_today)
    accepted = len([p for p in pipes_today if p.final_decision_value == "ACCEPT"])
    rejected = len([p for p in pipes_today if p.final_decision_value == "REJECT"])

    text_content = f"""GCP QC Pipes - Daily Report {report_date}

Total Pipes: {total}
Accepted: {accepted}
Rejected: {rejected}

This is an automated report from GCP QC Pipes Traceability System.
"""

    # Send email
    subject = f"GCP QC Report - {report_date.strftime('%Y-%m-%d')}"
    return send_email(subject, text_content, config["recipients"], html_content)


def send_weekly_report():
    """Send weekly summary report"""
    config = load_email_config()

    if not config["recipients"] or not config["mail_server"]:
        return {"success": False, "error": "Email not configured"}

    end_date = date.today()
    start_date = end_date - timedelta(days=7)

    # Get weekly data
    pipes = Pipe.query.filter(
        Pipe.production_date >= start_date, Pipe.production_date <= end_date
    ).all()

    total = len(pipes)
    accepted = len([p for p in pipes if p.final_decision_value == "ACCEPT"])
    rejected = len([p for p in pipes if p.final_decision_value == "REJECT"])

    html = f"""
    <html>
    <head><style>body {{ font-family: Arial; }}</style></head>
    <body>
        <h2>Weekly Summary - {start_date} to {end_date}</h2>
        <h3>Production</h3>
        <p>Total: {total} | Accepted: {accepted} | Rejected: {rejected}</p>
        <p>Acceptance Rate: {round(accepted / total * 100, 1) if total > 0 else 0}%</p>
    </body>
    </html>
    """

    text = f"Weekly Summary: Total {total}, Accepted {accepted}, Rejected {rejected}"

    subject = f"GCP QC Weekly Report - {end_date.strftime('%Y-%m-%d')}"
    return send_email(subject, text, config["recipients"], html)
