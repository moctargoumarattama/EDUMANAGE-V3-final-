from flask import flash, redirect, render_template, request, url_for
from flask_login import login_required
from sqlalchemy import func

from app.authorization import admin_required
from app import db
from app.models import BadgeDefinition, Cours, Lecon, Utilisateur
from app.notification_center import BADGE_RULE_TYPES, ensure_notification_defaults, get_app_setting_int, set_app_setting
from app.utils import get_dashboard_stats

from . import admin_bp


BADGE_RULE_META = {
    "lessons_completed": {
        "label": "Lecons terminees",
        "description": "Le badge est donne quand un eleve termine assez de lecons.",
        "example": "Exemple : 3 = le badge arrive apres 3 lecons terminees.",
        "threshold_label": "Nombre de lecons a terminer",
        "effect": "L'eleve voit sa progression recompensee des qu'il finit ses lecons.",
    },
    "xp_total": {
        "label": "Points gagnes",
        "description": "Le badge est donne quand un eleve cumule assez de points XP.",
        "example": "Exemple : 100 = le badge arrive a partir de 100 points.",
        "threshold_label": "Nombre de points a atteindre",
        "effect": "L'eleve comprend qu'il avance grace aux points accumules.",
    },
}


def _badge_condition_summary(rule_type, threshold):
    if rule_type == "lessons_completed":
        return f"Le badge est donne apres {threshold} lecon(s) terminee(s)."
    if rule_type == "xp_total":
        return f"Le badge est donne apres {threshold} point(s) gagnes."
    return f"Condition actuelle : {threshold}"


@admin_bp.route("/admin")
@admin_bp.route("/admin/")
@admin_bp.route("/admin/dashboard")
@login_required
@admin_required
def dashboard():
    stats = get_dashboard_stats()
    utilisateurs = Utilisateur.query.order_by(Utilisateur.date_creation.desc()).limit(5).all()
    cours = Cours.query.order_by(Cours.date_creation.desc(), Cours.id.desc()).limit(5).all()
    lesson_counts = {}
    if cours:
        lesson_counts = dict(
            db.session.query(Lecon.cours_id, func.count(Lecon.id))
            .filter(Lecon.cours_id.in_([cours_item.id for cours_item in cours]))
            .group_by(Lecon.cours_id)
            .all()
        )
    return render_template(
        "admin_dashboard.html",
        stats=stats,
        utilisateurs=utilisateurs,
        cours=cours,
        lesson_counts=lesson_counts,
    )


@admin_bp.route("/admin/gamification", methods=["GET", "POST"])
@login_required
@admin_required
def gamification():
    ensure_notification_defaults()

    if request.method == "POST":
        form_name = (request.form.get("form_name") or "").strip()

        if form_name == "settings":
            streak_hours = max(request.form.get("streak_risk_hours", type=int) or 48, 1)
            badge_history_days = max(request.form.get("badge_history_days", type=int) or 30, 1)
            set_app_setting("streak_risk_hours", streak_hours)
            set_app_setting("badge_history_days", badge_history_days)
            flash("Parametres de gamification mis a jour.", "success")
            return redirect(url_for("admin.gamification"))

        if form_name == "badge":
            badge_id = request.form.get("badge_id", type=int)
            badge = BadgeDefinition.query.filter_by(id=badge_id).first() if badge_id else BadgeDefinition()
            if badge_id and badge is None:
                flash("Badge introuvable.", "danger")
                return redirect(url_for("admin.gamification"))

            slug = (request.form.get("slug") or "").strip().lower()
            name = (request.form.get("name") or "").strip()
            description = (request.form.get("description") or "").strip() or None
            image_url = (request.form.get("image_url") or "").strip() or None
            rule_type = (request.form.get("rule_type") or "").strip()
            threshold = max(request.form.get("threshold", type=int) or 1, 1)
            is_active = request.form.get("is_active") == "1"

            if not slug or not name or rule_type not in BADGE_RULE_TYPES:
                flash("Le badge doit avoir un slug, un nom et une regle valide.", "danger")
                return redirect(url_for("admin.gamification"))

            existing = BadgeDefinition.query.filter_by(slug=slug).first()
            if existing is not None and existing.id != badge.id:
                flash("Ce slug de badge existe deja.", "danger")
                return redirect(url_for("admin.gamification"))

            badge.slug = slug
            badge.name = name
            badge.description = description
            badge.image_url = image_url
            badge.rule_type = rule_type
            badge.threshold = threshold
            badge.is_active = is_active

            if badge.id is None:
                db.session.add(badge)

            db.session.commit()
            flash("Badge enregistre avec succes.", "success")
            return redirect(url_for("admin.gamification"))

    badges = (
        BadgeDefinition.query.filter(BadgeDefinition.rule_type.in_(BADGE_RULE_TYPES))
        .order_by(BadgeDefinition.created_at.asc(), BadgeDefinition.id.asc())
        .all()
    )
    badge_cards = [
        {
            "badge": badge,
            "rule_label": BADGE_RULE_META.get(badge.rule_type, {}).get("label", badge.rule_type),
            "condition_summary": _badge_condition_summary(badge.rule_type, badge.threshold),
            "effect_summary": BADGE_RULE_META.get(badge.rule_type, {}).get("effect", "L'eleve voit ce badge dans son espace."),
            "status_label": "Actif" if badge.is_active else "Inactif",
        }
        for badge in badges
    ]
    return render_template(
        "admin_gamification.html",
        badges=badges,
        badge_cards=badge_cards,
        badge_rule_types=BADGE_RULE_TYPES,
        badge_rule_meta=BADGE_RULE_META,
        streak_risk_hours=get_app_setting_int("streak_risk_hours", 48),
        badge_history_days=get_app_setting_int("badge_history_days", 30),
    )
