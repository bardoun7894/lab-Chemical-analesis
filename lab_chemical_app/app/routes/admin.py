"""
Admin Routes - User Management & Settings
"""

import os
import json
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
from flask_login import login_required, current_user
from functools import wraps
from sqlalchemy.exc import IntegrityError
from app import db, csrf
from app.models.user import User
from app.models.stage_defect_type import StageDefectType
from app.models.stage_decision_type import StageDecisionType
from app.models.reason_type import ReasonType
from app.models.defect_reason import DefectReason
from app.models.pipe import Pipe, PipeStage
from app.models.stage import ProductionStage
from app.models.chemical import Machine, Furnace, ChemicalAnalysis
from app.models.product import ProductParameter, Product, Customer, Mold
from app.models.audit import AuditLog
from app.services.barcode_service import clean_barcode
from app.services.permission_service import requires_permission, super_admin_required
from app.services import dimension_standard_service
from app.services import application_spec_service

# Path to element rules JSON
ELEMENT_RULES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "element_rules.json"
)
MECHANICAL_RULES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "mechanical_rules.json"
)
APP_SETTINGS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "app_settings.json"
)

admin_bp = Blueprint("admin", __name__)


@admin_bp.route("/")
@login_required
@requires_permission("admin", "settings_list", "read")
def index():
    """Admin dashboard"""
    user_count = User.query.count()
    active_users = User.query.filter_by(is_active=True).count()

    return render_template(
        "admin/index.html", user_count=user_count, active_users=active_users
    )


@admin_bp.route("/users")
@requires_permission("admin", "users_list", "read")
def users():
    """List all users"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    if per_page not in (10, 20, 50, 100):
        per_page = 20

    users_list = User.query.order_by(User.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    return render_template(
        "admin/users.html",
        users=users_list,
        per_page=per_page,
        per_page_options=(10, 20, 50, 100),
    )


@admin_bp.route("/users/new", methods=["GET", "POST"])
@requires_permission("admin", "users_add", "create")
def create_user():
    """Create new user"""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        full_name = request.form.get("full_name", "").strip()
        full_name_ar = request.form.get("full_name_ar", "").strip()
        role = request.form.get("role", "viewer")
        department = request.form.get("department", "").strip()

        # Validation
        errors = []
        if not username:
            errors.append("Username is required")
        if not password:
            errors.append("Password is required")
        if len(password) < 6:
            errors.append("Password must be at least 6 characters")
        if User.query.filter_by(username=username).first():
            errors.append("Username already exists")
        if role not in assignable_roles():
            errors.append("Invalid role")

        if errors:
            for error in errors:
                flash(error, "error")
            return render_template("admin/user_form.html", user=None, roles=get_roles())

        # Create user
        user = User(
            username=username,
            full_name=full_name or None,
            full_name_ar=full_name_ar or None,
            role=role,
            department=department or None,
        )
        user.set_password(password)

        db.session.add(user)
        db.session.commit()

        flash(f"User {username} created successfully", "success")
        return redirect(url_for("admin.users"))

    return render_template("admin/user_form.html", user=None, roles=get_roles())


@admin_bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@requires_permission("admin", "users_edit", "write")
def edit_user(user_id):
    """Edit user"""
    user = User.query.get_or_404(user_id)

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        full_name_ar = request.form.get("full_name_ar", "").strip()
        role = request.form.get("role", "viewer")
        department = request.form.get("department", "").strip()
        new_password = request.form.get("new_password", "")
        is_active = request.form.get("is_active") == "on"

        # Validation
        errors = []
        if role not in assignable_roles():
            errors.append("Invalid role")

        guard = owner_guard(user)
        if guard:
            errors.append(guard)

        # The app must never be left without a reachable owner.
        if last_owner_standing(user) and (
            role != User.ROLE_SUPER_ADMIN or not is_active
        ):
            errors.append(
                "Cannot demote or deactivate the last owner. "
                "Promote another user to owner first."
            )

        # Prevent locking yourself out of your own account
        if user.id == current_user.id:
            if not is_active:
                errors.append("Cannot deactivate your own account")
            if not user.is_super_admin and role != user.role:
                errors.append("Cannot change the role on your own account")

        if errors:
            for error in errors:
                flash(error, "error")
            return render_template("admin/user_form.html", user=user, roles=get_roles())

        # Update user
        user.full_name = full_name or None
        user.full_name_ar = full_name_ar or None
        user.role = role
        user.department = department or None
        user.is_active = is_active

        # Update password if provided
        if new_password:
            if len(new_password) < 6:
                flash("Password must be at least 6 characters", "error")
                return render_template(
                    "admin/user_form.html", user=user, roles=get_roles()
                )
            user.set_password(new_password)

        db.session.commit()

        flash(f"User {user.username} updated successfully", "success")
        return redirect(url_for("admin.users"))

    return render_template("admin/user_form.html", user=user, roles=get_roles())


@admin_bp.route("/users/<int:user_id>/delete", methods=["POST"])
@requires_permission("admin", "users_delete", "delete")
def delete_user(user_id):
    """Delete user"""
    user = User.query.get_or_404(user_id)

    # Prevent deleting own account
    if user.id == current_user.id:
        flash("Cannot delete your own account", "error")
        return redirect(url_for("admin.users"))

    guard = owner_guard(user)
    if guard:
        flash(guard, "error")
        return redirect(url_for("admin.users"))

    if last_owner_standing(user):
        flash("Cannot delete the last owner account", "error")
        return redirect(url_for("admin.users"))

    username = user.username
    db.session.delete(user)
    db.session.commit()

    flash(f"User {username} deleted successfully", "success")
    return redirect(url_for("admin.users"))


@admin_bp.route("/users/<int:user_id>/toggle-active", methods=["POST"])
@requires_permission("admin", "users_list", "read")
def toggle_user_active(user_id):
    """Toggle user active status"""
    user = User.query.get_or_404(user_id)

    # Prevent deactivating own account
    if user.id == current_user.id:
        return jsonify({"error": "Cannot deactivate your own account"}), 400

    guard = owner_guard(user)
    if guard:
        return jsonify({"error": guard}), 403

    if user.is_active and last_owner_standing(user):
        return jsonify({"error": "Cannot deactivate the last owner account"}), 400

    user.is_active = not user.is_active
    db.session.commit()

    status = "activated" if user.is_active else "deactivated"
    return jsonify(
        {
            "success": True,
            "message": f"User {user.username} {status}",
            "is_active": user.is_active,
        }
    )


@admin_bp.route("/settings")
@requires_permission("admin", "settings_list", "read")
def settings():
    """Application settings"""
    return render_template("admin/settings.html", summary=system_summary())


def system_summary():
    """Counts behind the System Summary card on the settings page."""
    return {
        "elements": len(load_element_rules().get("rules", [])),
        "users": User.query.count(),
        "analyses": ChemicalAnalysis.query.count(),
        "pipes": Pipe.query.count(),
    }


@admin_bp.route("/settings/backups")
@requires_permission("admin", "backups_list", "read")
def backups():
    """Backups screen — see what exists, take a new one, download or delete."""
    from app.services import backup_service

    return render_template(
        "admin/backups.html",
        backups=backup_service.list_backups(),
        summary=backup_service.storage_summary(),
        backup_dir=backup_service.backup_dir(),
    )


@admin_bp.route("/settings/backups/create", methods=["POST"])
@requires_permission("admin", "backups_create", "execute")
def backups_create():
    """Take a backup now (DB snapshot + settings JSON, one zip)."""
    from app.services import backup_service

    try:
        info = backup_service.create_backup(
            created_by=current_user.username, note=request.form.get("note", "")
        )
        flash(
            f"Backup created: {info['filename']} ({info['size'] // 1024} KB)",
            "success",
        )
    except Exception as exc:
        flash(f"Backup failed: {exc}", "error")
    return redirect(url_for("admin.backups"))


@admin_bp.route("/settings/backups/download/<path:filename>")
@requires_permission("admin", "backups_list", "read")
def backups_download(filename):
    """Download one backup zip."""
    from flask import send_file
    from app.services import backup_service

    path = backup_service.backup_path(filename)
    if not path:
        flash("Backup not found.", "error")
        return redirect(url_for("admin.backups"))
    return send_file(path, as_attachment=True, download_name=filename)


@admin_bp.route("/settings/backups/delete/<path:filename>", methods=["POST"])
@requires_permission("admin", "backups_delete", "delete")
def backups_delete(filename):
    """Delete one backup."""
    from app.services import backup_service

    if backup_service.delete_backup(filename):
        flash(f"Backup deleted: {filename}", "success")
    else:
        flash("Backup not found.", "error")
    return redirect(url_for("admin.backups"))


@admin_bp.route("/settings/element-rules")
@requires_permission("admin", "element_rules_list", "read")
def element_rules():
    """Element rules configuration"""
    rules = load_element_rules()
    return render_template("admin/element_rules.html", rules=rules)


@admin_bp.route("/settings/element-rules/<element>", methods=["GET", "POST"])
@requires_permission("admin", "element_rules_list", "read")
def edit_element_rule(element):
    """Edit element rule"""
    rules = load_element_rules()

    # Find the element
    element_rule = None
    for rule in rules.get("rules", []):
        if rule["element"] == element:
            element_rule = rule
            break

    if not element_rule:
        flash(f"العنصر {element} غير موجود", "error")
        return redirect(url_for("admin.element_rules"))

    if request.method == "POST":
        try:
            # Get ranges from form
            new_ranges = []
            range_count = int(request.form.get("range_count", 0))

            for i in range(range_count):
                min_val = request.form.get(f"min_{i}")
                max_val = request.form.get(f"max_{i}")
                decision = request.form.get(f"decision_{i}")

                if min_val and max_val and decision:
                    new_ranges.append(
                        {
                            "min": float(min_val),
                            "max": float(max_val),
                            "decision": decision,
                        }
                    )

            # Update the element's ranges
            element_rule["ranges"] = new_ranges

            # Save back to file
            save_element_rules(rules)

            flash(f"تم تحديث قواعد {element} بنجاح", "success")
            return redirect(url_for("admin.element_rules"))

        except Exception as e:
            flash(f"خطأ: {str(e)}", "error")

    decisions = ["فحص أخيرة فقط", "فحص أولى وأخيرة", "فحص الشحنة 100%", "تالف"]

    return render_template(
        "admin/edit_element_rule.html",
        element=element,
        rule=element_rule,
        decisions=decisions,
    )


@admin_bp.route("/settings/stage-flow", methods=["GET", "POST"])
@requires_permission("admin", "settings_list", "read")
def stage_flow_settings():
    """Whether a stage decision requires the previous stage to be accepted.

    Off by default. Turning it on changes what the shop floor is allowed to
    save, so the screen states plainly what it will and will not block.
    """
    from app.services import stage_flow_service

    settings = load_app_settings()
    if request.method == "POST":
        section = settings.setdefault(stage_flow_service.SETTINGS_KEY, {})
        section[stage_flow_service.REQUIRE_PREVIOUS] = (
            request.form.get(stage_flow_service.REQUIRE_PREVIOUS) == "1"
        )
        save_app_settings(settings)
        flash(
            "تم تفعيل ترتيب المراحل" if section[stage_flow_service.REQUIRE_PREVIOUS]
            else "تم إيقاف ترتيب المراحل",
            "success",
        )
        return redirect(url_for("admin.stage_flow_settings"))

    stages = ProductionStage.active_names()
    return render_template(
        "admin/stage_flow.html",
        enabled=stage_flow_service.is_enabled(settings),
        stages=stages,
    )


@admin_bp.route("/settings/kpi-targets", methods=["GET", "POST"])
@requires_permission("admin", "settings_list", "read")
def kpi_target_settings():
    """Targets for the dashboard KPI tiles.

    A blank field means "no target" and is stored as such, so clearing one
    removes the verdict from the tile instead of grading it against zero.
    """
    from app.services import kpi_target_service

    settings = load_app_settings()
    if request.method == "POST":
        section = {}
        for key, _ar, _en, _unit, _direction in kpi_target_service.ALL_KPIS:
            raw = (request.form.get(key) or "").strip()
            if raw == "":
                continue
            try:
                section[key] = float(raw)
            except ValueError:
                flash(f"قيمة غير صالحة للهدف: {key}", "error")
                return redirect(url_for("admin.kpi_target_settings"))
        settings[kpi_target_service.SETTINGS_KEY] = section

        # Who gets told, on the same screen as what they get told about.
        # Credentials are not here on purpose — they come from the environment
        # (see whatsapp_service), because this file is edited through a web
        # form and backed up in the clear.
        from app.services import whatsapp_service

        # Only when the form actually carried the section: it is hidden until
        # the bot's credentials exist, and an absent checkbox reads as "off",
        # which would silently switch off a working configuration.
        if request.form.get("whatsapp_section"):
            settings[whatsapp_service.SETTINGS_KEY] = {
                "enabled": bool(request.form.get("whatsapp_enabled")),
                "recipients": (request.form.get("whatsapp_recipients") or "").strip(),
                "min_severity": (request.form.get("whatsapp_min_severity")
                                 or "critical"),
            }
        save_app_settings(settings)
        flash("تم حفظ أهداف المؤشرات", "success")
        return redirect(url_for("admin.kpi_target_settings"))

    from app.services import whatsapp_service

    return render_template(
        "admin/kpi_targets.html",
        kpis=kpi_target_service.KPIS,
        scoped=kpi_target_service.SCOPED_KPIS,
        targets=kpi_target_service.load(settings),
        higher=kpi_target_service.HIGHER_IS_BETTER,
        whatsapp=(settings or {}).get(whatsapp_service.SETTINGS_KEY) or {},
        whatsapp_ready=whatsapp_service.is_configured(settings),
        # The section stays out of sight until the bot's credentials are in
        # the environment. A switch that cannot send anything is a switch that
        # gets turned on and then quietly does nothing.
        whatsapp_available=whatsapp_service.has_credentials(),
    )


@admin_bp.route("/settings/dimension-standards")
@requires_permission("admin", "dimension_standards_list", "read")
def dimension_standards():
    """TA 1012 dimensional standard — DN index.

    The nominals were transcribed from a scan of the standard sheet and several
    tolerance cells were unreadable; this screen is where they get completed
    and the document marked verified. Until it is, capability declines to
    compute Cp/Cpk for any symbol whose tolerance is still missing.
    """
    doc = dimension_standard_service.load_standards()
    summary = []
    for dn in dimension_standard_service.available_dns():
        entries = dimension_standard_service.for_dn(dn, doc)
        defined = [e for e in entries.values() if e["nominal"] is not None]
        with_tol = [
            e for e in defined
            if e["tol_plus"] is not None or e["tol_minus"] is not None
        ]
        summary.append({
            "dn": dn,
            "symbols": len(defined),
            "with_tolerance": len(with_tol),
            "missing": len(defined) - len(with_tol),
        })
    return render_template(
        "admin/dimension_standards.html",
        doc=doc,
        summary=summary,
        symbols=doc.get("symbols", []),
    )


@admin_bp.route("/settings/dimension-standards/<dn>", methods=["GET", "POST"])
@requires_permission("admin", "dimension_standards_list", "read")
def edit_dimension_standard(dn):
    """Edit one DN's nominals and tolerances."""
    doc = dimension_standard_service.load_standards()
    by_dn = doc.setdefault("by_dn", {})
    if dn not in by_dn:
        flash(f"DN{dn} غير موجود في الاستاندرد", "error")
        return redirect(url_for("admin.dimension_standards"))

    if request.method == "POST":
        try:
            updated = {}
            for symbol in [s["key"] for s in doc.get("symbols", [])]:
                updated[symbol] = {
                    "nominal": _optional_float(request.form.get(f"nominal_{symbol}")),
                    "tol_plus": _optional_float(request.form.get(f"tol_plus_{symbol}")),
                    "tol_minus": _optional_float(
                        request.form.get(f"tol_minus_{symbol}")
                    ),
                }
            by_dn[dn] = updated
            # Verified is a property of the whole document, not one DN, so it
            # is only ever set from the index screen.
            dimension_standard_service.save_standards(doc)
            flash(f"تم حفظ استاندرد DN{dn}", "success")
            return redirect(url_for("admin.dimension_standards"))
        except Exception as e:  # noqa: BLE001 - surfaced to the operator
            flash(f"خطأ: {str(e)}", "error")

    return render_template(
        "admin/edit_dimension_standard.html",
        dn=dn,
        symbols=doc.get("symbols", []),
        entries=dimension_standard_service.for_dn(dn, doc),
        source=doc.get("source", ""),
    )


@admin_bp.route("/settings/dimension-standards/verify", methods=["POST"])
@requires_permission("admin", "dimension_standards_list", "read")
def verify_dimension_standards():
    """Mark the transcribed table as checked by a human (or un-check it)."""
    doc = dimension_standard_service.load_standards()
    doc["verified"] = request.form.get("verified") == "1"
    dimension_standard_service.save_standards(doc)
    flash(
        "تم اعتماد الاستاندرد" if doc["verified"] else "تم إلغاء اعتماد الاستاندرد",
        "success",
    )
    return redirect(url_for("admin.dimension_standards"))


def _optional_float(raw):
    """Blank means "not defined by the standard", never zero."""
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


@admin_bp.route("/api/element-rules")
@requires_permission("admin", "settings_list", "read")
def api_get_element_rules():
    """API: Get all element rules"""
    rules = load_element_rules()
    return jsonify(rules)


@admin_bp.route("/api/element-rules/<element>", methods=["PUT"])
@requires_permission("admin", "element_rules_edit", "write")
def api_update_element_rule(element):
    """API: Update element rule"""
    rules = load_element_rules()

    # Find the element
    element_rule = None
    for rule in rules.get("rules", []):
        if rule["element"] == element:
            element_rule = rule
            break

    if not element_rule:
        return jsonify({"error": f"Element {element} not found"}), 404

    try:
        data = request.get_json()
        element_rule["ranges"] = data.get("ranges", [])
        save_element_rules(rules)
        return jsonify({"success": True, "message": f"{element} rules updated"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@admin_bp.route("/api/element-rules/add", methods=["POST"])
@csrf.exempt
@login_required
@requires_permission("admin", "element_rules_edit", "write")
def api_add_element():
    """API: Add new element"""
    rules = load_element_rules()
    data = request.get_json()

    element = data.get("element", "").upper()
    if not element:
        return jsonify({"error": "Element name required"}), 400

    # Check if already exists
    for rule in rules.get("rules", []):
        if rule["element"] == element:
            return jsonify({"error": f"Element {element} already exists"}), 400

    # Add new element
    rules["rules"].append(
        {
            "element": element,
            "ranges": data.get(
                "ranges",
                [
                    {"min": 0, "max": 0.5, "decision": "فحص أخيرة فقط"},
                    {"min": 0.51, "max": 1, "decision": "تالف"},
                ],
            ),
        }
    )

    save_element_rules(rules)
    return jsonify({"success": True, "message": f"Element {element} added"})


@admin_bp.route("/api/element-rules/<element>", methods=["DELETE"])
@requires_permission("admin", "element_rules_edit", "write")
def api_delete_element(element):
    """API: Delete element"""
    rules = load_element_rules()

    # Find and remove the element
    rules["rules"] = [r for r in rules.get("rules", []) if r["element"] != element]

    save_element_rules(rules)
    return jsonify({"success": True, "message": f"Element {element} deleted"})


def load_element_rules():
    """Load element rules from JSON file"""
    try:
        with open(ELEMENT_RULES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        return {"rules": [], "error": str(e)}


def save_element_rules(rules):
    """Save element rules to JSON file"""
    with open(ELEMENT_RULES_PATH, "w", encoding="utf-8") as f:
        json.dump(rules, f, ensure_ascii=False, indent=2)


def get_roles():
    """Roles selectable in the user form.

    Only the owner may hand out the owner role, so it is offered only when the
    actor already holds it.
    """
    roles = []
    if current_user.is_authenticated and current_user.is_super_admin:
        roles.append(
            {
                "value": User.ROLE_SUPER_ADMIN,
                "label": "Super Admin (Owner)",
                "label_ar": "المالك",
            }
        )
    roles += [
        {"value": User.ROLE_ADMIN, "label": "Admin", "label_ar": "مدير"},
        {"value": User.ROLE_SUPERVISOR, "label": "Supervisor", "label_ar": "مشرف"},
        {"value": User.ROLE_OPERATOR, "label": "Operator", "label_ar": "مشغل"},
        {"value": User.ROLE_WAREHOUSE, "label": "Warehouse", "label_ar": "أمين المخزن"},
        {"value": User.ROLE_VIEWER, "label": "Viewer", "label_ar": "مشاهد"},
    ]
    return roles


def assignable_roles():
    """Role values the current actor is allowed to assign."""
    return {r["value"] for r in get_roles()}


def owner_guard(target):
    """Reject a mutation of `target` that only the owner may perform, or that
    would leave the app with no active owner. Returns an error string or None.
    """
    if target.is_super_admin and not current_user.is_super_admin:
        return "Only the owner can modify the owner account"
    return None


def last_owner_standing(target):
    """True when `target` is the only remaining active owner."""
    if not target.is_super_admin:
        return False
    others = User.query.filter(
        User.role == User.ROLE_SUPER_ADMIN,
        User.is_active.is_(True),
        User.id != target.id,
    ).count()
    return others == 0


# ============================================================================
# Stage Defect Types Management
# ============================================================================


@admin_bp.route("/defect-types")
@requires_permission("admin", "defect_types_list", "read")
def defect_types():
    """Manage stage defect types"""
    stage_filter = request.args.get("stage", "")
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    if per_page not in (10, 20, 50, 100):
        per_page = 20

    query = StageDefectType.query
    if stage_filter:
        query = query.filter_by(stage_name=stage_filter)

    defect_types_list = query.order_by(
        StageDefectType.stage_name, StageDefectType.sort_order
    ).paginate(page=page, per_page=per_page, error_out=False)

    stages = Pipe.STAGES

    return render_template(
        "admin/defect_types.html",
        defect_types=defect_types_list,
        stages=stages,
        selected_stage=stage_filter,
        per_page=per_page,
        per_page_options=(10, 20, 50, 100),
    )


@admin_bp.route("/defect-types/add", methods=["GET", "POST"])
@requires_permission("admin", "defect_types_add", "create")
def add_defect_type():
    """Add new defect type"""
    if request.method == "POST":
        stage_name = request.form.get("stage_name", "").strip()
        defect_name_en = request.form.get("defect_name_en", "").strip()
        defect_name_ar = request.form.get("defect_name_ar", "").strip()
        if not stage_name or not defect_name_en:
            flash("Stage name and defect name (EN) are required", "error")
            return render_template(
                "admin/defect_type_form.html", defect_type=None, stages=Pipe.STAGES
            )

        defect_type = StageDefectType(
            stage_name=stage_name,
            defect_name_en=defect_name_en,
            defect_name_ar=defect_name_ar,
            is_active=request.form.get("is_active") == "on",
            sort_order=int(request.form.get("sort_order", 0)),
        )

        try:
            db.session.add(defect_type)
            db.session.commit()
            flash(
                "تم إضافة نوع العيب بنجاح / Defect type added successfully", "success"
            )
            return redirect(url_for("admin.defect_types"))
        except Exception as e:
            db.session.rollback()
            flash(f"خطأ: {str(e)}", "danger")

    stages = Pipe.STAGES
    return render_template(
        "admin/defect_type_form.html", defect_type=None, stages=stages
    )


@admin_bp.route("/defect-types/<int:id>/edit", methods=["GET", "POST"])
@requires_permission("admin", "defect_types_edit", "write")
def edit_defect_type(id):
    """Edit defect type"""
    defect_type = StageDefectType.query.get_or_404(id)

    if request.method == "POST":
        defect_type.stage_name = request.form.get("stage_name", defect_type.stage_name)
        defect_type.defect_name_en = request.form.get(
            "defect_name_en", defect_type.defect_name_en
        )
        defect_type.defect_name_ar = request.form.get(
            "defect_name_ar", defect_type.defect_name_ar
        )
        defect_type.is_active = request.form.get("is_active") == "on"
        defect_type.sort_order = int(request.form.get("sort_order", 0))

        try:
            db.session.commit()
            flash(
                "تم تحديث نوع العيب بنجاح / Defect type updated successfully", "success"
            )
            return redirect(url_for("admin.defect_types"))
        except Exception as e:
            db.session.rollback()
            flash(f"خطأ: {str(e)}", "danger")

    stages = Pipe.STAGES
    return render_template(
        "admin/defect_type_form.html", defect_type=defect_type, stages=stages
    )


@admin_bp.route("/defect-types/<int:id>/delete", methods=["POST"])
@requires_permission("admin", "defect_types_delete", "delete")
def delete_defect_type(id):
    """Delete defect type"""
    defect_type = StageDefectType.query.get_or_404(id)

    try:
        db.session.delete(defect_type)
        db.session.commit()
        flash("تم حذف نوع العيب بنجاح / Defect type deleted successfully", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"خطأ: {str(e)}", "danger")

    return redirect(url_for("admin.defect_types"))


# ============================================================================
# Stage Decision Types Management
# ============================================================================


@admin_bp.route("/decision-types")
@requires_permission("admin", "decision_types_list", "read")
def decision_types():
    """Manage stage decision types"""
    stage_filter = request.args.get("stage", "")
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    if per_page not in (10, 20, 50, 100):
        per_page = 20

    query = StageDecisionType.query
    if stage_filter:
        query = query.filter_by(stage_name=stage_filter)

    decision_types_list = query.order_by(
        StageDecisionType.stage_name, StageDecisionType.sort_order
    ).paginate(page=page, per_page=per_page, error_out=False)

    stages = Pipe.STAGES

    return render_template(
        "admin/decision_types.html",
        decision_types=decision_types_list,
        stages=stages,
        selected_stage=stage_filter,
        per_page=per_page,
        per_page_options=(10, 20, 50, 100),
    )


@admin_bp.route("/decision-types/add", methods=["GET", "POST"])
@requires_permission("admin", "decision_types_add", "create")
def add_decision_type():
    """Add new decision type"""
    if request.method == "POST":
        try:
            stage_name = request.form.get("stage_name", "").strip()
            decision_name_en = request.form.get("decision_name_en", "").strip()
            decision_name_ar = request.form.get("decision_name_ar", "").strip()

            if not stage_name or not decision_name_en or not decision_name_ar:
                flash(
                    "Stage name, decision name (EN), and decision name (AR) are required",
                    "error",
                )
                return render_template(
                    "admin/decision_type_form.html",
                    decision_type=None,
                    stages=Pipe.STAGES,
                )

            # Safe sort_order parsing (user may submit empty string)
            try:
                sort_order = int(request.form.get("sort_order") or 0)
            except (TypeError, ValueError):
                sort_order = 0

            # Check for duplicates (EN or AR) to give a clear message before commit
            dup = StageDecisionType.query.filter(
                StageDecisionType.stage_name == stage_name,
                db.or_(
                    StageDecisionType.decision_name_en == decision_name_en,
                    StageDecisionType.decision_name_ar == decision_name_ar,
                ),
            ).first()
            if dup:
                flash(
                    f'Decision already exists for stage "{stage_name}"',
                    "error",
                )
                return render_template(
                    "admin/decision_type_form.html",
                    decision_type=None,
                    stages=Pipe.STAGES,
                )

            decision_type = StageDecisionType(
                stage_name=stage_name,
                decision_name_en=decision_name_en,
                decision_name_ar=decision_name_ar,
                is_active=request.form.get("is_active") == "on",
                sort_order=sort_order,
            )

            db.session.add(decision_type)
            db.session.commit()
            flash("Decision type added successfully", "success")
            return redirect(url_for("admin.decision_types"))

        except IntegrityError:
            db.session.rollback()
            flash("A decision with the same name already exists for this stage", "error")
        except Exception as e:
            db.session.rollback()
            flash(f"Error adding decision type: {str(e)}", "error")

    stages = Pipe.STAGES
    return render_template(
        "admin/decision_type_form.html", decision_type=None, stages=stages
    )


@admin_bp.route("/decision-types/<int:id>/edit", methods=["GET", "POST"])
@requires_permission("admin", "decision_types_edit", "write")
def edit_decision_type(id):
    """Edit decision type"""
    decision_type = StageDecisionType.query.get_or_404(id)

    if request.method == "POST":
        try:
            decision_type.stage_name = (
                request.form.get("stage_name") or decision_type.stage_name
            ).strip()
            decision_type.decision_name_en = (
                request.form.get("decision_name_en") or decision_type.decision_name_en
            ).strip()
            decision_type.decision_name_ar = (
                request.form.get("decision_name_ar") or decision_type.decision_name_ar
            ).strip()
            decision_type.is_active = request.form.get("is_active") == "on"
            try:
                decision_type.sort_order = int(request.form.get("sort_order") or 0)
            except (TypeError, ValueError):
                decision_type.sort_order = 0

            db.session.commit()
            flash("Decision type updated successfully", "success")
            return redirect(url_for("admin.decision_types"))
        except IntegrityError:
            db.session.rollback()
            flash(
                "A decision with the same name already exists for this stage",
                "error",
            )
        except Exception as e:
            db.session.rollback()
            flash(f"Error updating decision type: {str(e)}", "error")

    stages = Pipe.STAGES
    return render_template(
        "admin/decision_type_form.html", decision_type=decision_type, stages=stages
    )


@admin_bp.route("/decision-types/<int:id>/delete", methods=["POST"])
@requires_permission("admin", "decision_types_delete", "delete")
def delete_decision_type(id):
    """Delete decision type"""
    decision_type = StageDecisionType.query.get_or_404(id)

    try:
        db.session.delete(decision_type)
        db.session.commit()
        flash("تم حذف نوع القرار بنجاح / Decision type deleted successfully", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"خطأ: {str(e)}", "danger")

    return redirect(url_for("admin.decision_types"))


# ============================================================================
# Reason Types Management (global decision/change reasons)
# ============================================================================


@admin_bp.route("/defect-reasons")
@requires_permission("admin", "defect_reasons_list", "read")
def defect_reasons():
    """Manage defect reasons (per-stage dropdown choices on the stages form)."""
    stage_filter = request.args.get("stage", "")
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    if per_page not in (10, 20, 50, 100):
        per_page = 20

    query = DefectReason.query
    if stage_filter:
        query = query.filter_by(stage_name=stage_filter)

    defect_reasons_list = query.order_by(
        DefectReason.stage_name, DefectReason.sort_order, DefectReason.id
    ).paginate(page=page, per_page=per_page, error_out=False)

    return render_template(
        "admin/defect_reasons.html",
        defect_reasons=defect_reasons_list,
        stages=_active_stage_names(),
        selected_stage=stage_filter,
        per_page=per_page,
        per_page_options=(10, 20, 50, 100),
    )


def _active_stage_names():
    from app.models.stage import ProductionStage

    return ProductionStage.active_names()


@admin_bp.route("/defect-reasons/add", methods=["GET", "POST"])
@requires_permission("admin", "defect_reasons_add", "create")
def add_defect_reason():
    """Add a new defect reason."""
    if request.method == "POST":
        try:
            stage_name = request.form.get("stage_name", "").strip()
            name_en = request.form.get("name_en", "").strip()
            name_ar = request.form.get("name_ar", "").strip()

            if not stage_name or not name_en or not name_ar:
                flash("Stage, defect reason (EN) and (AR) are required", "error")
                return render_template(
                    "admin/defect_reason_form.html",
                    defect_reason=None,
                    stages=_active_stage_names(),
                )

            try:
                sort_order = int(request.form.get("sort_order") or 0)
            except (TypeError, ValueError):
                sort_order = 0

            dup = DefectReason.query.filter(
                DefectReason.stage_name == stage_name,
                db.or_(
                    DefectReason.name_en == name_en,
                    DefectReason.name_ar == name_ar,
                ),
            ).first()
            if dup:
                flash("A defect reason with the same name already exists for this stage", "error")
                return render_template(
                    "admin/defect_reason_form.html",
                    defect_reason=None,
                    stages=_active_stage_names(),
                )

            defect_reason = DefectReason(
                stage_name=stage_name,
                name_en=name_en,
                name_ar=name_ar,
                is_active=request.form.get("is_active") == "on",
                sort_order=sort_order,
            )

            db.session.add(defect_reason)
            db.session.commit()
            flash("Defect reason added successfully", "success")
            return redirect(url_for("admin.defect_reasons"))

        except IntegrityError:
            db.session.rollback()
            flash("A defect reason with the same name already exists for this stage", "error")
        except Exception as e:
            db.session.rollback()
            flash(f"Error adding defect reason: {str(e)}", "error")

    return render_template(
        "admin/defect_reason_form.html",
        defect_reason=None,
        stages=_active_stage_names(),
    )


@admin_bp.route("/defect-reasons/<int:id>/edit", methods=["GET", "POST"])
@requires_permission("admin", "defect_reasons_edit", "write")
def edit_defect_reason(id):
    """Edit a defect reason."""
    defect_reason = DefectReason.query.get_or_404(id)

    if request.method == "POST":
        try:
            defect_reason.stage_name = (
                request.form.get("stage_name") or defect_reason.stage_name
            ).strip()
            defect_reason.name_en = (
                request.form.get("name_en") or defect_reason.name_en
            ).strip()
            defect_reason.name_ar = (
                request.form.get("name_ar") or defect_reason.name_ar
            ).strip()
            defect_reason.is_active = request.form.get("is_active") == "on"
            try:
                defect_reason.sort_order = int(request.form.get("sort_order") or 0)
            except (TypeError, ValueError):
                defect_reason.sort_order = 0

            db.session.commit()
            flash("Defect reason updated successfully", "success")
            return redirect(url_for("admin.defect_reasons"))
        except IntegrityError:
            db.session.rollback()
            flash("A defect reason with the same name already exists for this stage", "error")
        except Exception as e:
            db.session.rollback()
            flash(f"Error updating defect reason: {str(e)}", "error")

    return render_template(
        "admin/defect_reason_form.html",
        defect_reason=defect_reason,
        stages=_active_stage_names(),
    )


@admin_bp.route("/defect-reasons/<int:id>/delete", methods=["POST"])
@requires_permission("admin", "defect_reasons_delete", "delete")
def delete_defect_reason(id):
    """Delete a defect reason."""
    defect_reason = DefectReason.query.get_or_404(id)

    try:
        db.session.delete(defect_reason)
        db.session.commit()
        flash("تم حذف سبب العيب بنجاح / Defect reason deleted successfully", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"خطأ: {str(e)}", "danger")

    return redirect(url_for("admin.defect_reasons"))


@admin_bp.route("/reason-types")
@requires_permission("admin", "reason_types_list", "read")
def reason_types():
    """Manage the global list of decision/change reasons."""
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    if per_page not in (10, 20, 50, 100):
        per_page = 20

    reason_types_list = (
        ReasonType.query.order_by(ReasonType.sort_order, ReasonType.id)
        .paginate(page=page, per_page=per_page, error_out=False)
    )

    return render_template(
        "admin/reason_types.html",
        reason_types=reason_types_list,
        per_page=per_page,
        per_page_options=(10, 20, 50, 100),
    )


@admin_bp.route("/reason-types/add", methods=["GET", "POST"])
@requires_permission("admin", "reason_types_add", "create")
def add_reason_type():
    """Add a new reason."""
    if request.method == "POST":
        try:
            name_en = request.form.get("name_en", "").strip()
            name_ar = request.form.get("name_ar", "").strip()

            if not name_en or not name_ar:
                flash("Reason name (EN) and (AR) are required", "error")
                return render_template("admin/reason_type_form.html", reason_type=None)

            try:
                sort_order = int(request.form.get("sort_order") or 0)
            except (TypeError, ValueError):
                sort_order = 0

            dup = ReasonType.query.filter(
                db.or_(
                    ReasonType.name_en == name_en,
                    ReasonType.name_ar == name_ar,
                )
            ).first()
            if dup:
                flash("A reason with the same name already exists", "error")
                return render_template("admin/reason_type_form.html", reason_type=None)

            reason_type = ReasonType(
                name_en=name_en,
                name_ar=name_ar,
                is_active=request.form.get("is_active") == "on",
                sort_order=sort_order,
            )

            db.session.add(reason_type)
            db.session.commit()
            flash("Reason added successfully", "success")
            return redirect(url_for("admin.reason_types"))

        except IntegrityError:
            db.session.rollback()
            flash("A reason with the same name already exists", "error")
        except Exception as e:
            db.session.rollback()
            flash(f"Error adding reason: {str(e)}", "error")

    return render_template("admin/reason_type_form.html", reason_type=None)


@admin_bp.route("/reason-types/<int:id>/edit", methods=["GET", "POST"])
@requires_permission("admin", "reason_types_edit", "write")
def edit_reason_type(id):
    """Edit a reason."""
    reason_type = ReasonType.query.get_or_404(id)

    if request.method == "POST":
        try:
            reason_type.name_en = (
                request.form.get("name_en") or reason_type.name_en
            ).strip()
            reason_type.name_ar = (
                request.form.get("name_ar") or reason_type.name_ar
            ).strip()
            reason_type.is_active = request.form.get("is_active") == "on"
            try:
                reason_type.sort_order = int(request.form.get("sort_order") or 0)
            except (TypeError, ValueError):
                reason_type.sort_order = 0

            db.session.commit()
            flash("Reason updated successfully", "success")
            return redirect(url_for("admin.reason_types"))
        except IntegrityError:
            db.session.rollback()
            flash("A reason with the same name already exists", "error")
        except Exception as e:
            db.session.rollback()
            flash(f"Error updating reason: {str(e)}", "error")

    return render_template("admin/reason_type_form.html", reason_type=reason_type)


@admin_bp.route("/reason-types/<int:id>/delete", methods=["POST"])
@requires_permission("admin", "reason_types_delete", "delete")
def delete_reason_type(id):
    """Delete a reason."""
    reason_type = ReasonType.query.get_or_404(id)

    try:
        db.session.delete(reason_type)
        db.session.commit()
        flash("تم حذف السبب بنجاح / Reason deleted successfully", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"خطأ: {str(e)}", "danger")

    return redirect(url_for("admin.reason_types"))


# ============================================================================
# Email Settings Management
# ============================================================================


@admin_bp.route("/settings/email")
@requires_permission("admin", "email_settings_list", "read")
def email_settings():
    """Email configuration settings"""
    from flask import current_app

    settings = load_app_settings()
    email_settings = settings.get("email", {})

    return render_template("admin/email_settings.html", email_settings=email_settings)


@admin_bp.route("/settings/email", methods=["POST"])
@requires_permission("admin", "email_settings_edit", "write")
def update_email_settings():
    """Update email settings"""
    settings = load_app_settings()

    if "email" not in settings:
        settings["email"] = {}

    settings["email"]["mail_server"] = request.form.get("mail_server", "")
    settings["email"]["mail_port"] = int(request.form.get("mail_port", 587))
    settings["email"]["mail_use_tls"] = request.form.get("mail_use_tls") == "on"
    settings["email"]["mail_username"] = request.form.get("mail_username", "")
    settings["email"]["mail_default_sender"] = request.form.get(
        "mail_default_sender", ""
    )
    settings["email"]["recipients"] = request.form.get("recipients", "")

    # Also update config
    app = current_app
    app.config["MAIL_SERVER"] = settings["email"]["mail_server"]
    app.config["MAIL_PORT"] = settings["email"]["mail_port"]
    app.config["MAIL_USE_TLS"] = settings["email"]["mail_use_tls"]
    app.config["MAIL_USERNAME"] = settings["email"]["mail_username"]
    app.config["MAIL_DEFAULT_SENDER"] = settings["email"]["mail_default_sender"]
    app.config["DAILY_REPORT_RECIPIENTS"] = [
        r.strip() for r in settings["email"]["recipients"].split(",") if r.strip()
    ]

    try:
        save_app_settings(settings)
        flash("Email settings updated successfully", "success")
    except Exception as e:
        flash(f"Error: {str(e)}", "danger")

    return redirect(url_for("admin.email_settings"))


@admin_bp.route("/settings/email/test", methods=["POST"])
@requires_permission("admin", "email_settings_test", "execute")
def test_email():
    """Test email configuration"""
    from app.services.email_service import send_email

    settings = load_app_settings()
    email_cfg = settings.get("email", {})

    recipients = [email_cfg.get("mail_username", "")]
    if not recipients[0]:
        return jsonify({"success": False, "message": "No email configured"})

    result = send_email(
        subject="Test Email - GCP QC Pipes",
        body="This is a test email from GCP QC Pipes Traceability System.",
        recipients=recipients,
    )

    return jsonify(result)


# ============================================================================


@admin_bp.route("/machines")
@requires_permission("admin", "machines_list", "read")
def machines():
    """Manage machines"""
    stage_filter = request.args.get("stage", "")
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    if per_page not in (10, 20, 50, 100):
        per_page = 20

    query = Machine.query
    if stage_filter:
        query = query.filter_by(stage=stage_filter)

    machines_list = query.order_by(Machine.stage, Machine.machine_code).paginate(
        page=page, per_page=per_page, error_out=False
    )

    # Get unique stages from machines
    stages = db.session.query(Machine.stage).distinct().order_by(Machine.stage).all()
    stages = [s[0] for s in stages if s[0]]

    return render_template(
        "admin/machines.html",
        machines=machines_list,
        stages=stages,
        selected_stage=stage_filter,
        per_page=per_page,
        per_page_options=(10, 20, 50, 100),
    )


@admin_bp.route("/machines/add", methods=["GET", "POST"])
@requires_permission("admin", "machines_add", "create")
def add_machine():
    """Add new machine"""
    if request.method == "POST":
        machine = Machine(
            machine_code=request.form.get("machine_code", "").strip(),
            machine_name=request.form.get("machine_name", ""),
            stage=request.form.get("stage"),
            is_active=request.form.get("is_active") == "on",
        )

        try:
            db.session.add(machine)
            db.session.commit()
            flash("تم إضافة الماكينة بنجاح / Machine added successfully", "success")
            return redirect(url_for("admin.machines"))
        except Exception as e:
            db.session.rollback()
            flash(f"خطأ: {str(e)}", "danger")

    # Production stages that can have machines
    production_stages = Pipe.STAGES

    return render_template(
        "admin/machine_form.html", machine=None, stages=production_stages
    )


@admin_bp.route("/machines/<int:id>/edit", methods=["GET", "POST"])
@requires_permission("admin", "machines_edit", "write")
def edit_machine(id):
    """Edit machine"""
    machine = Machine.query.get_or_404(id)

    if request.method == "POST":
        machine.machine_code = request.form.get("machine_code", machine.machine_code)
        machine.machine_name = request.form.get("machine_name", "")
        machine.stage = request.form.get("stage")
        machine.is_active = request.form.get("is_active") == "on"

        try:
            db.session.commit()
            flash("تم تحديث الماكينة بنجاح / Machine updated successfully", "success")
            return redirect(url_for("admin.machines"))
        except Exception as e:
            db.session.rollback()
            flash(f"خطأ: {str(e)}", "danger")

    # Production stages that can have machines
    production_stages = Pipe.STAGES

    return render_template(
        "admin/machine_form.html", machine=machine, stages=production_stages
    )


@admin_bp.route("/machines/<int:id>/delete", methods=["POST"])
@requires_permission("admin", "machines_delete", "delete")
def delete_machine(id):
    """Delete machine"""
    machine = Machine.query.get_or_404(id)

    try:
        db.session.delete(machine)
        db.session.commit()
        flash("تم حذف الماكينة بنجاح / Machine deleted successfully", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"خطأ: {str(e)}", "danger")

    return redirect(url_for("admin.machines"))


# ============================================================================
# Furnace Management (Settings > Furnaces)
# ============================================================================


@admin_bp.route("/furnaces")
@requires_permission("admin", "furnaces_list", "read")
def furnaces():
    """Manage furnaces"""
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    if per_page not in (10, 20, 50, 100):
        per_page = 20

    furnaces_list = Furnace.query.order_by(Furnace.furnace_code).paginate(
        page=page, per_page=per_page, error_out=False
    )

    # Analysis count per furnace (to guard deletes)
    usage = dict(
        db.session.query(
            ChemicalAnalysis.furnace_id, db.func.count(ChemicalAnalysis.id)
        )
        .group_by(ChemicalAnalysis.furnace_id)
        .all()
    )

    return render_template(
        "admin/furnaces.html",
        furnaces=furnaces_list,
        usage=usage,
        per_page=per_page,
        per_page_options=(10, 20, 50, 100),
    )


@admin_bp.route("/furnaces/add", methods=["GET", "POST"])
@requires_permission("admin", "furnaces_add", "create")
def add_furnace():
    """Add new furnace"""
    if request.method == "POST":
        code = request.form.get("furnace_code", "").strip().upper()
        if not code:
            flash("رمز الفرن مطلوب / Furnace code is required", "danger")
            return render_template("admin/furnace_form.html", furnace=None)

        furnace = Furnace(
            furnace_code=code,
            furnace_name=request.form.get("furnace_name", "").strip(),
            is_active=request.form.get("is_active") == "on",
        )
        try:
            db.session.add(furnace)
            db.session.commit()
            flash("تم إضافة الفرن بنجاح / Furnace added successfully", "success")
            return redirect(url_for("admin.furnaces"))
        except IntegrityError:
            db.session.rollback()
            flash("رمز الفرن موجود بالفعل / Furnace code already exists", "danger")
        except Exception as e:
            db.session.rollback()
            flash(f"خطأ: {str(e)}", "danger")

    return render_template("admin/furnace_form.html", furnace=None)


@admin_bp.route("/furnaces/<int:id>/edit", methods=["GET", "POST"])
@requires_permission("admin", "furnaces_edit", "write")
def edit_furnace(id):
    """Edit furnace"""
    furnace = Furnace.query.get_or_404(id)

    if request.method == "POST":
        code = request.form.get("furnace_code", "").strip().upper()
        if code:
            furnace.furnace_code = code
        furnace.furnace_name = request.form.get("furnace_name", "").strip()
        furnace.is_active = request.form.get("is_active") == "on"

        try:
            db.session.commit()
            flash("تم تحديث الفرن بنجاح / Furnace updated successfully", "success")
            return redirect(url_for("admin.furnaces"))
        except IntegrityError:
            db.session.rollback()
            flash("رمز الفرن موجود بالفعل / Furnace code already exists", "danger")
        except Exception as e:
            db.session.rollback()
            flash(f"خطأ: {str(e)}", "danger")

    return render_template("admin/furnace_form.html", furnace=furnace)


@admin_bp.route("/furnaces/<int:id>/toggle", methods=["POST"])
@requires_permission("admin", "furnaces_toggle", "execute")
def toggle_furnace(id):
    """Activate/deactivate a furnace (hides it from the analysis form)"""
    furnace = Furnace.query.get_or_404(id)
    furnace.is_active = not furnace.is_active
    db.session.commit()
    flash("تم تحديث حالة الفرن / Furnace status updated", "success")
    return redirect(url_for("admin.furnaces"))


@admin_bp.route("/furnaces/<int:id>/delete", methods=["POST"])
@requires_permission("admin", "furnaces_delete", "delete")
def delete_furnace(id):
    """Delete a furnace (blocked when analyses reference it)"""
    furnace = Furnace.query.get_or_404(id)

    in_use = ChemicalAnalysis.query.filter_by(furnace_id=furnace.id).count()
    if in_use:
        flash(
            f"الفرن مستخدم في {in_use} تحليل — قم بإلغاء تفعيله بدلاً من الحذف / "
            f"Furnace is used by {in_use} analysis(es) — deactivate it instead",
            "danger",
        )
        return redirect(url_for("admin.furnaces"))

    try:
        db.session.delete(furnace)
        db.session.commit()
        flash("تم حذف الفرن بنجاح / Furnace deleted successfully", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"خطأ: {str(e)}", "danger")

    return redirect(url_for("admin.furnaces"))


# ============================================================================
# Stage Management (Settings > Production Stages)
# ============================================================================


@admin_bp.route("/stages")
@requires_permission("admin", "production_stages_list", "read")
def stages():
    """Manage production stages"""
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    if per_page not in (10, 20, 50, 100):
        per_page = 20

    stages_list = ProductionStage.query.order_by(
        ProductionStage.sort_order, ProductionStage.id
    ).paginate(page=page, per_page=per_page, error_out=False)

    # Pipe-stage usage count per stage (to guard deletes)
    usage = dict(
        db.session.query(PipeStage.stage_name, db.func.count(PipeStage.id))
        .group_by(PipeStage.stage_name)
        .all()
    )

    return render_template(
        "admin/stages.html",
        stages=stages_list,
        usage=usage,
        per_page=per_page,
        per_page_options=(10, 20, 50, 100),
    )


@admin_bp.route("/stages/add", methods=["GET", "POST"])
@requires_permission("admin", "production_stages_add", "create")
def add_stage():
    """Add new production stage"""
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("اسم المرحلة مطلوب / Stage name is required", "danger")
            return render_template("admin/stage_form.html", stage=None)

        max_order = (
            db.session.query(db.func.max(ProductionStage.sort_order)).scalar() or 0
        )
        stage = ProductionStage(
            name=name,
            name_ar=request.form.get("name_ar", "").strip(),
            sort_order=int(request.form.get("sort_order") or (max_order + 10)),
            is_active=request.form.get("is_active") == "on",
            is_builtin=False,
        )
        try:
            db.session.add(stage)
            db.session.commit()
            flash("تم إضافة المرحلة بنجاح / Stage added successfully", "success")
            return redirect(url_for("admin.stages"))
        except IntegrityError:
            db.session.rollback()
            flash("اسم المرحلة موجود بالفعل / Stage name already exists", "danger")
        except Exception as e:
            db.session.rollback()
            flash(f"خطأ: {str(e)}", "danger")

    return render_template("admin/stage_form.html", stage=None)


@admin_bp.route("/stages/<int:id>/edit", methods=["GET", "POST"])
@requires_permission("admin", "production_stages_edit", "write")
def edit_stage(id):
    """Edit production stage. Both built-in and custom stages can be renamed;
    built-in stages are looked up by their stable `code` in the rest of the
    app, so renaming the display `name` is safe."""
    stage = ProductionStage.query.get_or_404(id)

    if request.method == "POST":
        new_name = request.form.get("name", "").strip()
        if new_name and new_name != stage.name:
            old_name = stage.name
            # Cascade the rename to every table that stores the stage by name.
            # Built-in stages are still safe to rename because code paths use
            # `ProductionStage.by_code(stage.code).name` at runtime.
            PipeStage.query.filter_by(stage_name=old_name).update(
                {"stage_name": new_name}
            )
            StageDefectType.query.filter_by(stage_name=old_name).update(
                {"stage_name": new_name}
            )
            StageDecisionType.query.filter_by(stage_name=old_name).update(
                {"stage_name": new_name}
            )
            Machine.query.filter_by(stage=old_name).update({"stage": new_name})
            stage.name = new_name

        stage.name_ar = request.form.get("name_ar", "").strip()
        stage.sort_order = int(request.form.get("sort_order") or stage.sort_order)
        stage.is_active = request.form.get("is_active") == "on"

        try:
            db.session.commit()
            flash("تم تحديث المرحلة بنجاح / Stage updated successfully", "success")
            return redirect(url_for("admin.stages"))
        except IntegrityError:
            db.session.rollback()
            flash("اسم المرحلة موجود بالفعل / Stage name already exists", "danger")
        except Exception as e:
            db.session.rollback()
            flash(f"خطأ: {str(e)}", "danger")

    return render_template("admin/stage_form.html", stage=stage)


@admin_bp.route("/stages/<int:id>/toggle", methods=["POST"])
@requires_permission("admin", "production_stages_toggle", "execute")
def toggle_stage(id):
    """Activate/deactivate a stage (hides it from all stage lists)"""
    stage = ProductionStage.query.get_or_404(id)
    stage.is_active = not stage.is_active
    db.session.commit()
    flash("تم تحديث حالة المرحلة / Stage status updated", "success")
    return redirect(url_for("admin.stages"))


@admin_bp.route("/stages/<int:id>/delete", methods=["POST"])
@requires_permission("admin", "production_stages_delete", "delete")
def delete_stage(id):
    """Delete a custom stage (built-in or in-use stages cannot be deleted)"""
    stage = ProductionStage.query.get_or_404(id)

    if stage.is_builtin:
        flash(
            "لا يمكن حذف مرحلة أساسية — يمكن إلغاء تفعيلها فقط / "
            "Built-in stages cannot be deleted — deactivate instead",
            "danger",
        )
        return redirect(url_for("admin.stages"))

    in_use = PipeStage.query.filter_by(stage_name=stage.name).count()
    if in_use:
        flash(
            f"المرحلة مستخدمة في {in_use} أنبوب — قم بإلغاء تفعيلها بدلاً من الحذف / "
            f"Stage is used by {in_use} pipe(s) — deactivate it instead",
            "danger",
        )
        return redirect(url_for("admin.stages"))

    try:
        db.session.delete(stage)
        db.session.commit()
        flash("تم حذف المرحلة بنجاح / Stage deleted successfully", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"خطأ: {str(e)}", "danger")

    return redirect(url_for("admin.stages"))


@admin_bp.route("/stages/<int:id>/move/<direction>", methods=["POST"])
@requires_permission("admin", "production_stages_move", "execute")
def move_stage(id, direction):
    """Move a stage up or down in the order"""
    stage = ProductionStage.query.get_or_404(id)
    ordered = ProductionStage.query.order_by(
        ProductionStage.sort_order, ProductionStage.id
    ).all()
    idx = next((i for i, s in enumerate(ordered) if s.id == stage.id), None)

    swap_idx = idx - 1 if direction == "up" else idx + 1
    if idx is not None and 0 <= swap_idx < len(ordered):
        ordered[idx], ordered[swap_idx] = ordered[swap_idx], ordered[idx]
        # Renumber the whole list so the order is always unambiguous
        for i, s in enumerate(ordered):
            s.sort_order = (i + 1) * 10
        db.session.commit()

    return redirect(url_for("admin.stages"))


# ============================================================================
# Mechanical Rules Management
# ============================================================================


@admin_bp.route("/settings/mechanical-rules")
@requires_permission("admin", "mechanical_rules_list", "read")
def mechanical_rules():
    """Mechanical property rules configuration"""
    rules = load_mechanical_rules()
    return render_template("admin/mechanical_rules.html", rules=rules)


@admin_bp.route("/settings/mechanical-rules/update", methods=["POST"])
@requires_permission("admin", "mechanical_rules_edit", "write")
def update_mechanical_criterion():
    """Update a mechanical acceptance criterion"""
    rules = load_mechanical_rules()

    criterion_key = request.form.get("criterion_key")
    condition = request.form.get("condition")
    unit = request.form.get("unit")

    if not criterion_key or criterion_key not in rules.get("acceptance_criteria", {}):
        flash(f"المعيار {criterion_key} غير موجود", "error")
        return redirect(url_for("admin.mechanical_rules"))

    try:
        # Update the criterion
        rules["acceptance_criteria"][criterion_key]["condition"] = condition
        if unit:
            rules["acceptance_criteria"][criterion_key]["unit"] = unit

        # Save back to file
        save_mechanical_rules(rules)

        flash(f"تم تحديث المعيار بنجاح", "success")
    except Exception as e:
        flash(f"خطأ: {str(e)}", "error")

    return redirect(url_for("admin.mechanical_rules"))


@admin_bp.route("/api/mechanical-rules")
@requires_permission("admin", "settings_list", "read")
def api_get_mechanical_rules():
    """API: Get all mechanical rules"""
    rules = load_mechanical_rules()
    return jsonify(rules)


@admin_bp.route("/api/mechanical-rules/<property_code>", methods=["PUT"])
@requires_permission("admin", "mechanical_rules_edit", "write")
def api_update_mechanical_rule(property_code):
    """API: Update mechanical property rule"""
    rules = load_mechanical_rules()

    # Find the property
    property_rule = None
    for rule in rules.get("rules", []):
        if rule["property"] == property_code:
            property_rule = rule
            break

    if not property_rule:
        return jsonify({"error": f"Property {property_code} not found"}), 404

    try:
        data = request.get_json()
        property_rule["ranges"] = data.get("ranges", [])
        save_mechanical_rules(rules)
        return jsonify({"success": True, "message": f"{property_code} rules updated"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@admin_bp.route("/api/mechanical-rules/add", methods=["POST"])
@csrf.exempt
@login_required
@requires_permission("admin", "mechanical_rules_edit", "write")
def api_add_mechanical_property():
    """API: Add new mechanical property"""
    rules = load_mechanical_rules()
    data = request.get_json()

    property_code = data.get("property", "").lower()
    if not property_code:
        return jsonify({"error": "Property code required"}), 400

    # Check if already exists
    for rule in rules.get("rules", []):
        if rule["property"] == property_code:
            return jsonify({"error": f"Property {property_code} already exists"}), 400

    # Add new property
    rules["rules"].append(
        {
            "property": property_code,
            "name": data.get("name", property_code),
            "name_ar": data.get("name_ar", property_code),
            "unit": data.get("unit", ""),
            "ranges": data.get(
                "ranges",
                [
                    {"min": 0, "max": 50, "decision": "فحص أخيرة فقط"},
                    {"min": 50.01, "max": 100, "decision": "تالف"},
                ],
            ),
        }
    )

    save_mechanical_rules(rules)
    return jsonify({"success": True, "message": f"Property {property_code} added"})


@admin_bp.route("/api/mechanical-rules/<property_code>", methods=["DELETE"])
@requires_permission("admin", "mechanical_rules_edit", "write")
def api_delete_mechanical_property(property_code):
    """API: Delete mechanical property"""
    rules = load_mechanical_rules()

    # Find and remove the property
    rules["rules"] = [
        r for r in rules.get("rules", []) if r["property"] != property_code
    ]

    save_mechanical_rules(rules)
    return jsonify({"success": True, "message": f"Property {property_code} deleted"})


def load_mechanical_rules():
    """Load mechanical rules from JSON file"""
    try:
        with open(MECHANICAL_RULES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        return {"rules": [], "error": str(e)}


def save_mechanical_rules(rules):
    """Save mechanical rules to JSON file"""
    with open(MECHANICAL_RULES_PATH, "w", encoding="utf-8") as f:
        json.dump(rules, f, ensure_ascii=False, indent=2)


# ============================================================================
# AI Settings Management
# ============================================================================


def load_app_settings():
    """Load app settings from JSON file"""
    try:
        with open(APP_SETTINGS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "ai": {
                "gemini_api_key": "",
                "gemini_model": "gemini-2.5-flash",
                "enabled": True,
            }
        }


def save_app_settings(settings):
    """Save app settings to JSON file"""
    with open(APP_SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


@admin_bp.route("/settings/ai")
@requires_permission("admin", "ai_settings_list", "read")
def ai_settings():
    """AI/Gemini/OpenRouter settings configuration"""
    from app.services.ai_service import get_default_prompts

    settings = load_app_settings()
    ai_settings = settings.get("ai", {})

    # Mask the API keys for display (show last 4 chars only)
    gemini_key = ai_settings.get("gemini_api_key", "")
    masked_gemini = ""
    if gemini_key:
        masked_gemini = (
            "*" * (len(gemini_key) - 4) + gemini_key[-4:]
            if len(gemini_key) > 4
            else "****"
        )

    openrouter_key = ai_settings.get("openrouter_api_key", "")
    masked_openrouter = ""
    if openrouter_key:
        masked_openrouter = (
            "*" * (len(openrouter_key) - 4) + openrouter_key[-4:]
            if len(openrouter_key) > 4
            else "****"
        )

    default_prompts = get_default_prompts()
    current_prompts = ai_settings.get("prompts", {})

    # The exact system prompt the chatbot agent would receive for the
    # current admin right now (override + dynamic user/time/page sections),
    # so what is edited above can be checked against what is really sent.
    try:
        from app.services.agent_service import build_system_prompt
        chatbot_effective_prompt = build_system_prompt(current_user)
    except Exception as exc:  # pragma: no cover - display only
        chatbot_effective_prompt = f"(could not build prompt: {exc})"

    return render_template(
        "admin/ai_settings.html",
        ai_settings=ai_settings,
        masked_gemini=masked_gemini,
        masked_openrouter=masked_openrouter,
        default_prompts=default_prompts,
        current_prompts=current_prompts,
        chatbot_effective_prompt=chatbot_effective_prompt,
    )


@admin_bp.route("/settings/ai/update", methods=["POST"])
@requires_permission("admin", "ai_settings_edit", "write")
def update_ai_settings():
    """Update AI settings"""
    settings = load_app_settings()

    # Get form data
    provider = request.form.get("provider", "gemini")
    gemini_key = request.form.get("gemini_api_key", "").strip()
    gemini_model = request.form.get("gemini_model", "gemini-2.5-flash").strip()
    openrouter_key = request.form.get("openrouter_api_key", "").strip()
    openrouter_model = request.form.get(
        "openrouter_model", "qwen/qwen3-next-80b-a3b-instruct:free"
    ).strip()
    enabled = request.form.get("enabled") == "on"

    # Initialize ai settings if not exists
    if "ai" not in settings:
        settings["ai"] = {}

    # Set provider
    settings["ai"]["provider"] = provider

    # Only update API keys if new ones are provided (not the masked version)
    if gemini_key and not gemini_key.startswith("*"):
        settings["ai"]["gemini_api_key"] = gemini_key
    elif "gemini_api_key" not in settings["ai"]:
        settings["ai"]["gemini_api_key"] = ""

    if openrouter_key and not openrouter_key.startswith("*"):
        settings["ai"]["openrouter_api_key"] = openrouter_key
    elif not openrouter_key or openrouter_key.startswith("*"):
        pass  # Keep existing key if masked or empty
    else:
        settings["ai"]["openrouter_api_key"] = ""

    settings["ai"]["gemini_model"] = gemini_model
    settings["ai"]["openrouter_model"] = openrouter_model
    # Vision/OCR provider — chosen independently of the text provider.
    settings["ai"]["ocr_provider"] = request.form.get("ocr_provider", "gemini")
    settings["ai"]["ocr_openrouter_model"] = request.form.get(
        "ocr_openrouter_model", ""
    ).strip()
    settings["ai"]["openrouter_fallback_models"] = request.form.get(
        "openrouter_fallback_models", ""
    ).strip()
    settings["ai"]["enable_auto_fallback"] = (
        request.form.get("enable_auto_fallback") == "on"
    )
    settings["ai"]["enabled"] = enabled

    # Persist prompt overrides. Empty string — or text identical to the
    # built-in default (the form prefills defaults) — means "use default",
    # so future default updates still propagate.
    from app.services.ai_service import get_default_prompts
    defaults = get_default_prompts()
    if "prompts" not in settings["ai"]:
        settings["ai"]["prompts"] = {}
    for key in ("chatbot_system", "mechanical_decision", "chemical_decision", "auto_decision_summary", "microstructure_image"):
        form_key = f"prompt_{key}"
        value = request.form.get(form_key, "").strip()
        if value == (defaults.get(key) or "").strip():
            value = ""
        settings["ai"]["prompts"][key] = value

    try:
        save_app_settings(settings)
        flash(
            "تم تحديث إعدادات الذكاء الاصطناعي بنجاح / AI settings updated successfully",
            "success",
        )
    except Exception as e:
        flash(f"خطأ: {str(e)}", "danger")

    return redirect(url_for("admin.ai_settings"))


# ============================================================================
# Sticker Settings Management
# ============================================================================

STICKER_IMAGES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "static", "images"
)


@admin_bp.route("/settings/stickers")
@requires_permission("admin", "sticker_settings_list", "read")
def sticker_settings():
    """Sticker design settings"""
    settings = load_app_settings()
    sticker_settings = settings.get("sticker", {})

    # Check if logo and recycle images exist
    logo_exists = os.path.exists(os.path.join(STICKER_IMAGES_PATH, "gcp_logo.jpg"))
    recycle_exists = os.path.exists(os.path.join(STICKER_IMAGES_PATH, "recycle.jpg"))

    import time

    return render_template(
        "admin/sticker_settings.html",
        sticker_settings=sticker_settings,
        logo_exists=logo_exists,
        recycle_exists=recycle_exists,
        now=int(time.time()),
    )


@admin_bp.route("/settings/stickers", methods=["POST"])
@requires_permission("admin", "sticker_settings_edit", "write")
def update_sticker_settings():
    """Update sticker settings"""
    from werkzeug.utils import secure_filename

    settings = load_app_settings()

    # Initialize sticker settings if not exists
    if "sticker" not in settings:
        settings["sticker"] = {}

    # Handle logo upload
    if "logo" in request.files:
        logo_file = request.files["logo"]
        if logo_file and logo_file.filename:
            # Validate file type
            allowed_extensions = {"png", "jpg", "jpeg", "gif"}
            ext = logo_file.filename.rsplit(".", 1)[-1].lower()
            if ext in allowed_extensions:
                # Ensure images directory exists
                os.makedirs(STICKER_IMAGES_PATH, exist_ok=True)
                # Save as gcp_logo.jpg (convert if needed)
                from PIL import Image

                img = Image.open(logo_file)
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                logo_path = os.path.join(STICKER_IMAGES_PATH, "gcp_logo.jpg")
                img.save(logo_path, "JPEG", quality=95)
                flash("Logo uploaded successfully", "success")
            else:
                flash("Invalid logo file type. Use JPG, PNG, or GIF.", "warning")

    # Handle recycle image upload
    if "recycle" in request.files:
        recycle_file = request.files["recycle"]
        if recycle_file and recycle_file.filename:
            # Validate file type
            allowed_extensions = {"png", "jpg", "jpeg", "gif"}
            ext = recycle_file.filename.rsplit(".", 1)[-1].lower()
            if ext in allowed_extensions:
                # Ensure images directory exists
                os.makedirs(STICKER_IMAGES_PATH, exist_ok=True)
                # Save as recycle.jpg (convert if needed)
                from PIL import Image

                img = Image.open(recycle_file)
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                recycle_path = os.path.join(STICKER_IMAGES_PATH, "recycle.jpg")
                img.save(recycle_path, "JPEG", quality=95)
                flash("Recycler symbol uploaded successfully", "success")
            else:
                flash("Invalid recycle file type. Use JPG, PNG, or GIF.", "warning")

    # Update text settings
    settings["sticker"]["company_name"] = request.form.get(
        "company_name", "GCP Ductile Iron Pipes"
    )
    settings["sticker"]["website_url"] = request.form.get(
        "website_url", "www.gcpipes.com"
    )
    settings["sticker"]["website_color"] = request.form.get("website_color", "#0066CC")
    settings["sticker"]["text_color"] = request.form.get("text_color", "#333333")

    # Update sizes
    settings["sticker"]["sizes"] = {
        "small": [
            int(request.form.get("size_small_w", 80)),
            int(request.form.get("size_small_h", 50)),
        ],
        "medium": [
            int(request.form.get("size_medium_w", 100)),
            int(request.form.get("size_medium_h", 60)),
        ],
        "large": [
            int(request.form.get("size_large_w", 120)),
            int(request.form.get("size_large_h", 80)),
        ],
        "gcp": [
            int(request.form.get("size_gcp_w", 140)),
            int(request.form.get("size_gcp_h", 90)),
        ],
    }

    # Update layout options
    settings["sticker"]["show_logo"] = request.form.get("show_logo") == "on"
    settings["sticker"]["show_recycle"] = request.form.get("show_recycle") == "on"
    settings["sticker"]["show_qr"] = request.form.get("show_qr") == "on"
    code_type = (request.form.get("code_type") or "qr").strip().lower()
    settings["sticker"]["code_type"] = code_type if code_type in {"qr", "data_matrix"} else "qr"
    settings["sticker"]["show_website"] = request.form.get("show_website") == "on"
    settings["sticker"]["show_barcode"] = request.form.get("show_barcode") == "on"
    settings["sticker"]["show_warehouse_barcode"] = (
        request.form.get("show_warehouse_barcode") == "on"
    )
    settings["sticker"]["show_title"] = request.form.get("show_title") == "on"
    settings["sticker"]["show_product_code"] = (
        request.form.get("show_product_code") == "on"
    )
    settings["sticker"]["show_description"] = (
        request.form.get("show_description") == "on"
    )
    settings["sticker"]["title_text"] = (request.form.get("title_text") or "DIP").strip()
    settings["sticker"]["dpi"] = int(request.form.get("dpi", 300))

    # Per-field visibility for the sticker data grid
    settings["sticker"]["show_fields"] = {
        key: request.form.get(f"field_{key}") == "on"
        for key in (
            "pipe_no",
            "pipe_code",
            "diameter",
            "class",
            "production",
            "sales_order",
            "customer",
            "decision",
            "application",
        )
    }

    try:
        save_app_settings(settings)
        flash(
            "Sticker settings updated successfully / تم تحديث إعدادات الملصقات بنجاح",
            "success",
        )
    except Exception as e:
        flash(f"Error: {str(e)}", "danger")

    return redirect(url_for("admin.sticker_settings"))


@admin_bp.route("/api/sticker-settings")
@requires_permission("admin", "settings_list", "read")
def api_get_sticker_settings():
    """API: Get sticker settings"""
    settings = load_app_settings()
    return jsonify(settings.get("sticker", {}))


@admin_bp.route("/api/ai-settings/test", methods=["POST"])
@requires_permission("admin", "ai_settings_test", "execute")
def test_ai_connection():
    """Test AI API connection (Gemini or OpenRouter)"""
    import requests

    settings = load_app_settings()
    provider = settings.get("ai", {}).get("provider", "gemini")

    if provider == "openrouter":
        # Env first, then settings file — same precedence as get_api_key()
        # used by the real AI features, so the test reflects reality.
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            api_key = settings.get("ai", {}).get("openrouter_api_key", "")
        if not api_key:
            return jsonify({"success": False, "message": "OpenRouter API key not configured"})

        from app.services.ai_service import get_openrouter_model
        model = get_openrouter_model()

        try:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer": "https://gcpipes.com",
                "X-Title": "GCP QC Pipes Traceability",
            }
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": "Say Connection successful in Arabic"}],
                "max_tokens": 50,
                # No provider "only" restriction — all OpenRouter providers
                # (free and paid) are allowed; fallbacks stay enabled.
                "provider": {"allow_fallbacks": True},
            }
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=30,
            )

            if response.status_code == 200:
                try:
                    result = response.json() or {}
                    choices = result.get("choices") or []
                    text = ""
                    if choices and choices[0]:
                        msg = choices[0].get("message") or {}
                        content = msg.get("content")
                        # content can be None (reasoning models/refusals) or a
                        # list of parts on some providers — normalize to str
                        if isinstance(content, list):
                            text = "".join(
                                (p.get("text", "") if isinstance(p, dict) else str(p))
                                for p in content
                            )
                        else:
                            text = content or ""
                    if not text:
                        err = result.get("error") or {}
                        err_msg = err.get("message") if isinstance(err, dict) else str(err)
                        if err_msg:
                            return jsonify({"success": False, "message": f"OpenRouter Error: {err_msg[:200]}"})
                    used_model = result.get("model") or model
                    return jsonify({
                        "success": True,
                        "message": f"OpenRouter connected! Model: {used_model}. Response: {text[:100]}",
                    })
                except Exception as parse_err:
                    return jsonify({"success": False, "message": f"Parse error: {str(parse_err)}"})
            else:
                return jsonify({
                    "success": False,
                    "message": f"OpenRouter Error: {response.status_code} - {response.text[:200]}",
                })

        except requests.exceptions.Timeout:
            return jsonify({"success": False, "message": "Connection timeout"})
        except Exception as e:
            return jsonify({"success": False, "message": f"Error: {str(e)}"})

    else:
        # Env first, then settings file — mirrors get_api_key()
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            api_key = settings.get("ai", {}).get("gemini_api_key", "")
        if not api_key:
            return jsonify({"success": False, "message": "Gemini API key not configured"})

        from app.services.ai_service import get_gemini_model
        model = get_gemini_model()

        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            response = requests.post(
                url,
                headers={"Content-Type": "application/json"},
                json={
                    "contents": [
                        {"parts": [{"text": "Say Connection successful in Arabic"}]}
                    ],
                    "generationConfig": {"maxOutputTokens": 50},
                },
                timeout=10,
            )

            if response.status_code == 200:
                result = response.json() or {}
                text = ""
                candidates = result.get("candidates") or []
                if candidates and candidates[0]:
                    parts = (candidates[0].get("content") or {}).get("parts") or []
                    if parts and parts[0]:
                        text = parts[0].get("text") or ""
                return jsonify({
                    "success": True,
                    "message": f"Gemini connected! Response: {text[:100]}",
                })
            else:
                return jsonify({
                    "success": False,
                    "message": f"Gemini Error: {response.status_code} - {response.text[:200]}",
                })

        except requests.exceptions.Timeout:
            return jsonify({"success": False, "message": "Connection timeout"})
        except Exception as e:
            return jsonify({"success": False, "message": f"Error: {str(e)}"})



# ============================================================================
# Product Parameter Management
# ============================================================================


@admin_bp.route("/product-parameters")
@requires_permission("admin", "product_parameters_list", "read")
def product_parameters():
    """Manage product parameters - tabbed by type"""
    param_type = request.args.get("type", "CLASS")
    if param_type not in ProductParameter.PARAM_TYPES:
        param_type = "CLASS"

    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    if per_page not in (10, 20, 50, 100):
        per_page = 20

    params = (
        ProductParameter.query.filter_by(param_type=param_type)
        .order_by(ProductParameter.sort_order)
        .paginate(page=page, per_page=per_page, error_out=False)
    )

    return render_template(
        "admin/product_parameters.html",
        parameters=params,
        param_types=ProductParameter.PARAM_TYPES,
        selected_type=param_type,
        param_type_labels=ProductParameter.PARAM_TYPE_LABELS,
        per_page=per_page,
        per_page_options=(10, 20, 50, 100),
    )


@admin_bp.route("/product-parameters/add", methods=["GET", "POST"])
@requires_permission("admin", "product_parameters_add", "create")
def add_product_parameter():
    """Add new product parameter"""
    if request.method == "POST":
        param_type = request.form.get("param_type", "").strip()
        code = request.form.get("code", "").strip()
        name_en = request.form.get("name_en", "").strip()

        # Auto-increment code for PROJECT_TYPE to avoid duplicates
        if param_type == "PROJECT_TYPE":
            existing = ProductParameter.query.filter_by(param_type="PROJECT_TYPE").all()
            nums = []
            for e in existing:
                try:
                    nums.append(int(e.code))
                except ValueError:
                    pass
            next_num = max(nums) + 1 if nums else 1
            code = "%02d" % next_num

        if not param_type or not code or not name_en:
            flash("Type, code, and English name are required", "error")
            return render_template(
                "admin/product_parameter_form.html",
                param=None,
                param_types=ProductParameter.PARAM_TYPES,
                param_type_labels=ProductParameter.PARAM_TYPE_LABELS,
            )

        # Check for duplicate before inserting
        existing = ProductParameter.query.filter_by(
            param_type=param_type, code=code
        ).first()
        if existing:
            flash(
                f'A parameter with type "{param_type}" and code "{code}" already exists.',
                "error",
            )
            return render_template(
                "admin/product_parameter_form.html",
                param=None,
                param_types=ProductParameter.PARAM_TYPES,
                param_type_labels=ProductParameter.PARAM_TYPE_LABELS,
            )

        try:
            sort_order = int(request.form.get("sort_order") or 0)
        except (TypeError, ValueError):
            sort_order = 0

        param = ProductParameter(
            param_type=param_type,
            code=code,
            name_en=name_en,
            name_ar=request.form.get("name_ar", "").strip(),
            description_en=request.form.get("description_en", "").strip(),
            description_ar=request.form.get("description_ar", "").strip(),
            sort_order=sort_order,
            is_active=request.form.get("is_active") == "on",
            application_group=_read_application_group(param_type),
        )

        try:
            db.session.add(param)
            db.session.commit()
            flash("Parameter added successfully", "success")
            return redirect(url_for("admin.product_parameters", type=param_type))
        except IntegrityError:
            db.session.rollback()
            flash(
                f'A parameter with type "{param_type}" and code "{code}" already exists.',
                "error",
            )
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {str(e)}", "danger")

    return render_template(
        "admin/product_parameter_form.html",
        param=None,
        param_types=ProductParameter.PARAM_TYPES,
        param_type_labels=ProductParameter.PARAM_TYPE_LABELS,
    )


@admin_bp.route("/product-parameters/<int:id>/edit", methods=["GET", "POST"])
@requires_permission("admin", "product_parameters_edit", "write")
def edit_product_parameter(id):
    """Edit product parameter"""
    param = ProductParameter.query.get_or_404(id)

    if request.method == "POST":
        param.code = request.form.get("code", param.code).strip()
        param.name_en = request.form.get("name_en", param.name_en).strip()
        param.name_ar = request.form.get("name_ar", "").strip()
        param.description_en = request.form.get("description_en", "").strip()
        param.description_ar = request.form.get("description_ar", "").strip()
        try:
            param.sort_order = int(request.form.get("sort_order") or 0)
        except (TypeError, ValueError):
            param.sort_order = 0
        param.is_active = request.form.get("is_active") == "on"
        param.application_group = _read_application_group(param.param_type)

        try:
            db.session.commit()
            flash("Parameter updated successfully", "success")
            return redirect(url_for("admin.product_parameters", type=param.param_type))
        except IntegrityError:
            db.session.rollback()
            flash("A parameter with this type and code already exists.", "error")
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {str(e)}", "danger")

    return render_template(
        "admin/product_parameter_form.html",
        param=param,
        param_types=ProductParameter.PARAM_TYPES,
        param_type_labels=ProductParameter.PARAM_TYPE_LABELS,
    )


@admin_bp.route("/product-parameters/<int:id>/delete", methods=["POST"])
@requires_permission("admin", "product_parameters_delete", "delete")
def delete_product_parameter(id):
    """Delete product parameter"""
    param = ProductParameter.query.get_or_404(id)
    param_type = param.param_type
    try:
        db.session.delete(param)
        db.session.commit()
        flash("Parameter deleted successfully", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error: {str(e)}", "danger")
    return redirect(url_for("admin.product_parameters", type=param_type))


# ============================================================================
# Product Management
# ============================================================================


@admin_bp.route("/products")
@requires_permission("admin", "products_list", "read")
def products():
    """Manage products"""
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    if per_page not in (10, 20, 50, 100):
        per_page = 20

    products_list = Product.query.order_by(Product.product_code).paginate(
        page=page, per_page=per_page, error_out=False
    )
    return render_template(
        "admin/products.html",
        products=products_list,
        per_page=per_page,
        per_page_options=(10, 20, 50, 100),
    )


def _clean_barcode(raw):
    """Normalize a submitted product barcode. Returns (value, error_message).

    Thin alias: the rule now lives in barcode_service so the per-pipe
    warehouse barcode is normalised identically — both are read by the same
    scanners, so they cannot be allowed to disagree on what a valid code is.
    """
    return clean_barcode(raw)


def _read_application_group(param_type):
    """The standards group posted for an INTENT_USE parameter.

    Only INTENT_USE carries one — every other parameter type stores None, so a
    stray field on another form can never gate the product's standards.
    """
    if param_type != "INTENT_USE":
        return None
    group = (request.form.get("application_group") or "").strip()
    return group if group in application_spec_service.INTENT_GROUPS else None


def _product_param_fk_fields():
    """List of FK column names on Product, in the active configured order."""
    return [fk for _attr, fk, _pt in Product.get_param_order()]


def _build_product_form_context(product):
    """Build params_by_type + ordered param_fields for the product form template."""
    order = Product.get_param_order()
    params_by_type = {}
    for _attr, _fk, pt in order:
        params_by_type[pt] = (
            ProductParameter.query.filter_by(param_type=pt, is_active=True)
            .order_by(ProductParameter.sort_order)
            .all()
        )
    param_fields = []
    for _attr, fk, pt in order:
        if pt == 'PROJECT_TYPE':
            continue  # serial is auto-assigned server-side, not shown on form
        label_ar, label_en = ProductParameter.PARAM_TYPE_LABELS.get(pt, (pt, pt))
        param_fields.append((fk, pt, label_ar, label_en))
    return params_by_type, param_fields


def _save_application_profile(product):
    """Store the Application block posted with a product form.

    Blank keeps prior: a form that never rendered the block (or an older one)
    leaves whatever the product already had, same rule as the order form.
    """
    profile, has_any = application_spec_service.read_application_profile(request.form)
    if has_any:
        product.application_profile = profile


@admin_bp.route("/products/add", methods=["GET", "POST"])
@requires_permission("admin", "products_add", "create")
def add_product():
    """Add new product"""
    if request.method == "POST":
        barcode, error = _clean_barcode(request.form.get("barcode"))
        if error:
            flash(error, "danger")
            params_by_type, param_fields = _build_product_form_context(None)
            return render_template(
                "admin/product_form.html",
                product=None,
                params_by_type=params_by_type,
                param_fields=param_fields,
            )

        product = Product(
            barcode=barcode,
            weight_kg=float(request.form.get("weight_kg", 0) or 0),
            length_m=float(request.form.get("length_m", 0) or 0),
            is_active=request.form.get("is_active") == "on",
        )
        _save_application_profile(product)

        # Set parameter FKs (serial is auto-assigned below, not on form)
        for field in _product_param_fk_fields():
            if field == 'project_type_param_id':
                continue
            val = request.form.get(field, type=int)
            if val:
                setattr(product, field, val)

        # Auto-assign serial parameter (PROJECT_TYPE) — not shown on form
        serial_param = ProductParameter.query.filter_by(
            param_type='PROJECT_TYPE', is_active=True
        ).order_by(ProductParameter.sort_order).first()
        if serial_param:
            product.project_type_param_id = serial_param.id

        try:
            # Generate product code BEFORE inserting into DB
            # (product_code is NOT NULL, so we must set it before flush)
            # Eagerly load the relationship objects so generate_product_code works
            for attr, fk, _pt in Product.get_param_order():
                fk_val = getattr(product, fk)
                if fk_val:
                    setattr(product, attr, ProductParameter.query.get(fk_val))

            product.generate_product_code()
            product.generate_description()

            # Manual description overrides win over auto-generation
            desc_en = request.form.get("description_en", "").strip()
            desc_ar = request.form.get("description_ar", "").strip()
            if desc_en:
                product.description_en = desc_en
            if desc_ar:
                product.description_ar = desc_ar

            # Fallback if no params produced a code
            if not product.product_code:
                import uuid

                product.product_code = f"P-{uuid.uuid4().hex[:8].upper()}"

            db.session.add(product)
            db.session.commit()
            flash("Product added successfully", "success")
            return redirect(url_for("admin.products"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {str(e)}", "danger")

    params_by_type, param_fields = _build_product_form_context(None)
    return render_template(
        "admin/product_form.html",
        product=None,
        params_by_type=params_by_type,
        param_fields=param_fields,
    )


@admin_bp.route("/products/<int:id>/edit", methods=["GET", "POST"])
@requires_permission("admin", "products_edit", "write")
def edit_product(id):
    """Edit product"""
    product = Product.query.get_or_404(id)

    if request.method == "POST":
        barcode, error = _clean_barcode(request.form.get("barcode"))
        if error:
            flash(error, "danger")
            params_by_type, param_fields = _build_product_form_context(product)
            return render_template(
                "admin/product_form.html",
                product=product,
                params_by_type=params_by_type,
                param_fields=param_fields,
            )

        product.barcode = barcode
        product.weight_kg = float(request.form.get("weight_kg", 0) or 0)
        product.length_m = float(request.form.get("length_m", 0) or 0)
        product.is_active = request.form.get("is_active") == "on"
        _save_application_profile(product)

        for field in _product_param_fk_fields():
            if field == 'project_type_param_id':
                continue  # serial is auto-assigned, not on form
            val = request.form.get(field, type=int)
            setattr(product, field, val if val else None)

        # Auto-assign serial if not already set
        if not product.project_type_param_id:
            serial_param = ProductParameter.query.filter_by(
                param_type='PROJECT_TYPE', is_active=True
            ).order_by(ProductParameter.sort_order).first()
            if serial_param:
                product.project_type_param_id = serial_param.id

        # Eagerly load relationship objects so generate_product_code works
        for attr, fk, _pt in Product.get_param_order():
            fk_val = getattr(product, fk)
            if fk_val:
                setattr(product, attr, ProductParameter.query.get(fk_val))

        try:
            product.generate_product_code()
            product.generate_description()

            # Manual description overrides win over auto-generation
            desc_en = request.form.get("description_en", "").strip()
            desc_ar = request.form.get("description_ar", "").strip()
            if desc_en:
                product.description_en = desc_en
            if desc_ar:
                product.description_ar = desc_ar

            db.session.commit()
            flash("Product updated successfully", "success")
            return redirect(url_for("admin.products"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {str(e)}", "danger")

    params_by_type, param_fields = _build_product_form_context(product)
    return render_template(
        "admin/product_form.html",
        product=product,
        params_by_type=params_by_type,
        param_fields=param_fields,
    )


@admin_bp.route("/products/<int:id>/delete", methods=["POST"])
@requires_permission("admin", "products_delete", "delete")
def delete_product(id):
    """Delete product"""
    product = Product.query.get_or_404(id)
    try:
        db.session.delete(product)
        db.session.commit()
        flash("Product deleted successfully", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error: {str(e)}", "danger")
    return redirect(url_for("admin.products"))


@admin_bp.route("/settings/product-code-order", methods=["GET", "POST"])
@requires_permission("admin", "product_code_order_list", "read")
def product_code_order():
    """Admin: reorder the parameter fields used to build the product code."""
    settings = load_app_settings()
    if request.method == "POST":
        raw = request.form.get("order", "")
        incoming = [t.strip() for t in raw.split(",") if t.strip()]
        valid = [t for t in incoming if t in ProductParameter.PARAM_TYPES]
        # append any missing types so we always have the full set
        for t in ProductParameter.PARAM_TYPES:
            if t not in valid:
                valid.append(t)
        settings.setdefault("product_code", {})["param_order"] = valid
        try:
            save_app_settings(settings)
            flash("Product code order saved", "success")
        except Exception as e:
            flash(f"Error saving order: {e}", "danger")
        return redirect(url_for("admin.product_code_order"))

    order = [pt for _attr, _fk, pt in Product.get_param_order()]
    return render_template(
        "admin/product_code_order.html",
        order=order,
        labels=ProductParameter.PARAM_TYPE_LABELS,
        widths=Product.PARAM_CODE_WIDTH,
    )


@admin_bp.route("/api/preview-product-code", methods=["POST"])
@requires_permission("admin", "product_code_order_list", "read")
def api_preview_product_code():
    """API: Preview auto-generated product code from selected params"""
    data = request.get_json()

    # Create a transient product object to use its generation logic
    product = Product()

    # Set IDs from data
    for field in _product_param_fk_fields():
        val = data.get(field)
        if val:
            try:
                setattr(product, field, int(val))
            except (ValueError, TypeError):
                pass

    # Auto-assign serial for preview (hidden from form)
    serial_param = ProductParameter.query.filter_by(
        param_type='PROJECT_TYPE', is_active=True
    ).order_by(ProductParameter.sort_order).first()
    if serial_param:
        product.project_type_param_id = serial_param.id

    product.generate_product_code()
    product.generate_description()

    return jsonify(
        {
            "code": product.product_code or "N/A",
            "description_en": product.description_en or "",
            "description_ar": product.description_ar or "",
        }
    )


# ============================================================================
# Customer Management
# ============================================================================


@admin_bp.route("/customers")
@requires_permission("admin", "customers_list", "read")
def customers():
    """Manage customers"""
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    if per_page not in (10, 20, 50, 100):
        per_page = 20

    customers_list = Customer.query.order_by(Customer.name_en).paginate(
        page=page, per_page=per_page, error_out=False
    )
    return render_template(
        "admin/customers.html",
        customers=customers_list,
        per_page=per_page,
        per_page_options=(10, 20, 50, 100),
    )


@admin_bp.route("/customers/add", methods=["GET", "POST"])
@requires_permission("admin", "customers_add", "create")
def add_customer():
    """Add new customer"""
    if request.method == "POST":
        code = request.form.get("code", "").strip()
        name_en = request.form.get("name_en", "").strip()
        if not code or not name_en:
            flash("Code and English name are required", "error")
            return render_template("admin/customer_form.html", customer=None)

        customer = Customer(
            code=code,
            name_en=name_en,
            name_ar=request.form.get("name_ar", "").strip(),
            is_active=request.form.get("is_active") == "on",
        )
        try:
            db.session.add(customer)
            db.session.commit()
            flash("Customer added successfully", "success")
            return redirect(url_for("admin.customers"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {str(e)}", "danger")

    return render_template("admin/customer_form.html", customer=None)


@admin_bp.route("/customers/<int:id>/edit", methods=["GET", "POST"])
@requires_permission("admin", "customers_edit", "write")
def edit_customer(id):
    """Edit customer"""
    customer = Customer.query.get_or_404(id)

    if request.method == "POST":
        customer.code = request.form.get("code", customer.code).strip()
        customer.name_en = request.form.get("name_en", customer.name_en).strip()
        customer.name_ar = request.form.get("name_ar", "").strip()
        customer.is_active = request.form.get("is_active") == "on"

        try:
            db.session.commit()
            flash("Customer updated successfully", "success")
            return redirect(url_for("admin.customers"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {str(e)}", "danger")

    return render_template("admin/customer_form.html", customer=customer)


@admin_bp.route("/customers/<int:id>/delete", methods=["POST"])
@requires_permission("admin", "customers_delete", "delete")
def delete_customer(id):
    """Delete customer"""
    customer = Customer.query.get_or_404(id)
    try:
        db.session.delete(customer)
        db.session.commit()
        flash("Customer deleted successfully", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error: {str(e)}", "danger")
    return redirect(url_for("admin.customers"))


# ============================================================================
# Mold Management
# ============================================================================


@admin_bp.route("/molds")
@requires_permission("admin", "molds_list", "read")
def molds():
    """Manage molds"""
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    if per_page not in (10, 20, 50, 100):
        per_page = 20

    molds_list = Mold.query.order_by(Mold.diameter, Mold.mold_number).paginate(
        page=page, per_page=per_page, error_out=False
    )
    return render_template(
        "admin/molds.html",
        molds=molds_list,
        per_page=per_page,
        per_page_options=(10, 20, 50, 100),
    )


def _parse_mold_diameter(raw):
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


@admin_bp.route("/molds/add", methods=["GET", "POST"])
@requires_permission("admin", "molds_add", "create")
def add_mold():
    """Add new mold"""
    if request.method == "POST":
        mold_number = request.form.get("mold_number", "").strip()
        if not mold_number:
            flash("Mold number is required", "error")
            return render_template("admin/mold_form.html", mold=None)

        mold = Mold(
            mold_number=mold_number,
            diameter=_parse_mold_diameter(request.form.get("diameter")),
            description=request.form.get("description", "").strip() or None,
            is_active=request.form.get("is_active") == "on",
        )
        try:
            db.session.add(mold)
            db.session.commit()
            flash("Mold added successfully", "success")
            return redirect(url_for("admin.molds"))
        except IntegrityError:
            db.session.rollback()
            flash(f"A mold with number '{mold_number}' already exists", "error")
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {str(e)}", "danger")

    return render_template("admin/mold_form.html", mold=None)


@admin_bp.route("/molds/<int:id>/edit", methods=["GET", "POST"])
@requires_permission("admin", "molds_edit", "write")
def edit_mold(id):
    """Edit mold"""
    mold = Mold.query.get_or_404(id)

    if request.method == "POST":
        mold.mold_number = request.form.get("mold_number", mold.mold_number).strip()
        mold.diameter = _parse_mold_diameter(request.form.get("diameter"))
        mold.description = request.form.get("description", "").strip() or None
        mold.is_active = request.form.get("is_active") == "on"

        try:
            db.session.commit()
            flash("Mold updated successfully", "success")
            return redirect(url_for("admin.molds"))
        except IntegrityError:
            db.session.rollback()
            flash("A mold with this number already exists", "error")
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {str(e)}", "danger")

    return render_template("admin/mold_form.html", mold=mold)


@admin_bp.route("/molds/by-diameter/<int:diameter>")
@login_required
@requires_permission("admin", "molds_list", "read")
def molds_by_diameter(diameter):
    """JSON: active molds for a given DN, plus any with no diameter set"""
    q = Mold.query.filter(Mold.is_active.is_(True)).filter(
        db.or_(Mold.diameter == diameter, Mold.diameter.is_(None))
    ).order_by(Mold.mold_number)
    return jsonify([
        {"id": m.id, "mold_number": m.mold_number, "diameter": m.diameter}
        for m in q.all()
    ])


@admin_bp.route("/molds/<int:id>/delete", methods=["POST"])
@requires_permission("admin", "molds_delete", "delete")
def delete_mold(id):
    """Delete mold"""
    mold = Mold.query.get_or_404(id)
    try:
        db.session.delete(mold)
        db.session.commit()
        flash("Mold deleted successfully", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error: {str(e)}", "danger")
    return redirect(url_for("admin.molds"))


# ============================================================================
# Audit Log Viewer
# ============================================================================


@admin_bp.route("/audit")
@requires_permission("admin", "audit_list", "read")
def audit_log():
    """View audit trail"""
    page = request.args.get("page", 1, type=int)
    per_page = 50

    table_name = request.args.get("table_name", "")
    action = request.args.get("action", "")
    user_id = request.args.get("user_id", type=int)
    date_from = request.args.get("date_from", "")
    date_to = request.args.get("date_to", "")

    query = AuditLog.query

    if table_name:
        query = query.filter(AuditLog.table_name == table_name)
    if action:
        query = query.filter(AuditLog.action == action)
    if user_id:
        query = query.filter(AuditLog.user_id == user_id)
    if date_from:
        query = query.filter(AuditLog.timestamp >= date_from)
    if date_to:
        query = query.filter(AuditLog.timestamp <= date_to + " 23:59:59")

    logs = query.order_by(AuditLog.timestamp.desc()).paginate(
        page=page, per_page=per_page
    )

    from app.models.user import User

    users = User.query.order_by(User.username).all()

    tables = db.session.query(AuditLog.table_name).distinct().all()
    table_names = sorted([t[0] for t in tables]) if tables else []

    return render_template(
        "admin/audit_log.html",
        logs=logs,
        users=users,
        table_names=table_names,
        filters={
            "table_name": table_name,
            "action": action,
            "user_id": user_id,
            "date_from": date_from,
            "date_to": date_to,
        },
    )


# ============================================================================
# Permission Matrix
# ============================================================================


@admin_bp.route("/permissions")
@login_required
@super_admin_required
def permissions():
    """Permission matrix view"""
    from app.services.permission_service import get_permission_matrix

    matrix, roles = get_permission_matrix()
    return render_template("admin/permissions.html", matrix=matrix, roles=roles)


@admin_bp.route("/permissions/toggle", methods=["POST"])
@login_required
@super_admin_required
def toggle_permission():
    """Toggle a role's permission"""
    from app.models.permission import Permission as PermModel, RolePermission

    role = request.form.get("role")
    module = request.form.get("module")
    screen = request.form.get("screen")

    if role not in User.ROLES:
        return jsonify({"success": False, "message": "Unknown role"}), 400

    # The owner's access is absolute and is not stored in the matrix.
    if role == User.ROLE_SUPER_ADMIN:
        return (
            jsonify(
                {
                    "success": False,
                    "message": "The owner role always has full access and cannot be edited.",
                }
            ),
            400,
        )

    perm = PermModel.query.filter_by(module=module, screen=screen).first()
    if not perm:
        return jsonify({"success": False, "message": "Permission not found"}), 404

    existing = RolePermission.query.filter_by(role=role, permission_id=perm.id).first()
    if existing:
        db.session.delete(existing)
        granted = False
    else:
        db.session.add(RolePermission(role=role, permission_id=perm.id))
        granted = True

    db.session.commit()
    return jsonify({"success": True, "granted": granted})


@admin_bp.route("/permissions/restore-defaults", methods=["POST"])
@login_required
@super_admin_required
def restore_permission_defaults():
    """Reset the whole matrix to the code-defined ROLE_DEFAULTS.

    Owner-only like the rest of the matrix: if the matrix itself was tampered
    with, the owner is the one role whose access cannot be revoked, so the
    recovery path can never be locked out.
    """
    from app.models.permission import restore_default_grants

    granted = restore_default_grants()
    return jsonify({"success": True, "granted": granted})
