import json
import os
import secrets
from datetime import datetime

from flask import Blueprint, abort, current_app, flash, jsonify, make_response, redirect, render_template, request, send_file, send_from_directory, session, url_for
from flask_babel import gettext as _
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from app.blueprints.placement import placement_test_is_ready
from app.maintenance_tools import (
    ACTIVE_SESSION_TOKEN_KEY,
    create_active_session,
    is_site_maintenance_enabled,
    revoke_active_session_by_token,
    revoke_sessions_for_user,
)
from app.notification_center import create_support_message, get_or_create_support_thread, notify_admins_about_support_thread
from app.storage import build_asset_url, delete_asset_file, resolve_asset_path

from . import db
from .extensions import csrf
from .authorization import admin_required, eleve_required
from .forms import (
    CreateUserForm,
    DeleteUserForm,
    LoginForm,
    ProfileForm,
    RegisterForm,
    RequestResetPasswordForm,
    ResetPasswordConfirmForm,
    UpdateUserForm,
)
from .i18n import normalize_language_code as normalize_supported_language
from .models import Cours, Log, PasswordResetGrant, ResultatPlacement, StudentOnboardingProfile, Utilisateur
from .utils import log_action
main = Blueprint("main", __name__)

PROFILE_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
SUPPORT_GUEST_THREAD_ID = "support_guest_thread_id"
SUPPORT_GUEST_STUDENT_ID = "support_guest_student_id"
PROTECTED_MANAGEMENT_ROLES = {"admin", "maintenance"}
STUDENT_ONBOARDING_EXEMPT_ENDPOINTS = {
    "main.changer_langue",
    "main.choix_langue",
    "main.onboarding_profile",
    "main.onboarding_profile_step2",
    "main.onboarding_profile_transition",
    "main.favicon",
    "main.legacy_service_worker",
    "main.logout",
    "main.media_file",
    "notifications.post_support_message",
    "notifications.support_thread_guest",
    "main.service_worker",
    "placement.complete",
    "placement.intro",
    "placement.start",
    "static",
}

ONBOARDING_GENRE_CHOICES = {"homme", "femme", "not_specified"}
ONBOARDING_AGE_RANGE_CHOICES = {"under_18", "18_25", "26_40", "40_plus"}
ONBOARDING_ARABIC_BACKGROUND_CHOICES = {"arab_origin", "new_language"}
ONBOARDING_STUDIED_BEFORE_CHOICES = {"first_time", "long_ago_refresh", "basic_notions"}
ONBOARDING_CURRENT_LEVEL_CHOICES = {"alphabet_only", "simple_words", "read_some_need_help"}
ONBOARDING_LEARNING_GOAL_CHOICES = {"work_or_school", "family_friends", "culture_passion"}
ONBOARDING_DAILY_COMMITMENT_CHOICES = {"5_min", "15_min", "30_plus"}
ONBOARDING_DRAFT_SESSION_KEY = "onboarding_profile_draft"


def _is_ajax_request():
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _normalize_language_code(code):
    normalized = normalize_supported_language(code)
    if normalized in current_app.config.get("LANGUAGES", {}):
        return normalized
    return None


def _saved_language_for_user(user):
    if user is None:
        return None
    return _normalize_language_code(getattr(user, "langue_preferee", None))


def _set_language_selection(code, user=None):
    normalized = _normalize_language_code(code)
    if normalized is None:
        return None

    session["langue"] = normalized
    session["lang"] = normalized
    if user is not None and getattr(user, "id", None) is not None:
        user.langue_preferee = normalized
    return normalized


def _selected_language_for_user(user=None):
    session_lang = _normalize_language_code(session.get("langue") or session.get("lang"))
    if session_lang is not None:
        session["langue"] = session_lang
        session["lang"] = session_lang
        return session_lang

    saved_lang = _saved_language_for_user(user)
    if saved_lang is not None:
        session["langue"] = saved_lang
        session["lang"] = saved_lang
        return saved_lang

    return None


def _required_student_onboarding_endpoint(user):
    if getattr(user, "role", None) != "eleve" or getattr(user, "id", None) is None:
        return None

    if _selected_language_for_user(user) is None:
        return "main.choix_langue"

    has_onboarding_profile = StudentOnboardingProfile.query.filter_by(eleve_id=user.id).first() is not None
    if not has_onboarding_profile:
        return "main.onboarding_profile"

    has_placement_result = ResultatPlacement.query.filter_by(eleve_id=user.id).first() is not None
    if not has_placement_result and placement_test_is_ready():
        return "placement.intro"
    return None


def _needs_language_choice(user):
    return _required_student_onboarding_endpoint(user) == "main.choix_langue"


def maybe_redirect_new_student_to_onboarding():
    if not current_user.is_authenticated:
        return None

    endpoint = request.endpoint
    required_endpoint = _required_student_onboarding_endpoint(current_user)
    if required_endpoint is None or endpoint is None or endpoint in STUDENT_ONBOARDING_EXEMPT_ENDPOINTS:
        return None
    return redirect(url_for(required_endpoint))


def _dashboard_endpoint_for_user(user):
    role = getattr(user, "role", None)
    if role == "admin":
        return "admin.dashboard"
    if role == "maintenance":
        return "maintenance.dashboard"
    if role == "enseignant":
        return "courses.liste"
    onboarding_endpoint = _required_student_onboarding_endpoint(user)
    if onboarding_endpoint is not None:
        return onboarding_endpoint
    return "progress.dashboard_eleve"


def _profile_images_folder():
    folder = current_app.config["PROFILE_IMAGE_FOLDER"]
    os.makedirs(folder, exist_ok=True)
    return folder


def _profile_images_index_path():
    os.makedirs(current_app.instance_path, exist_ok=True)
    return os.path.join(current_app.instance_path, "profile_images.json")


def _load_profile_images_index():
    path = _profile_images_index_path()
    if not os.path.exists(path):
        return {}

    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, OSError):
        return {}

    return data if isinstance(data, dict) else {}


def _save_profile_images_index(index_data):
    with open(_profile_images_index_path(), "w", encoding="utf-8") as handle:
        json.dump(index_data, handle, ensure_ascii=False, indent=2)


def _remove_profile_image_file(relative_path):
    delete_asset_file(relative_path, "uploads/profiles/")


def _profile_image_relative_path(user_id):
    return _load_profile_images_index().get(str(user_id))


def _profile_image_url(user):
    relative_path = _profile_image_relative_path(getattr(user, "id", None))
    if not relative_path:
        return None
    return build_asset_url(relative_path)


def _save_profile_image(user, file_storage):
    if not file_storage or not file_storage.filename:
        return _profile_image_relative_path(user.id)

    extension = file_storage.filename.rsplit(".", 1)[-1].lower() if "." in file_storage.filename else ""
    if extension not in PROFILE_IMAGE_EXTENSIONS:
        raise ValueError("La photo de profil doit etre au format png, jpg, jpeg, gif ou webp.")

    filename = secure_filename(file_storage.filename)
    final_name = f"profile_{user.id}_{secrets.token_hex(8)}.{extension}"
    destination = os.path.join(_profile_images_folder(), final_name)
    file_storage.save(destination)

    index_data = _load_profile_images_index()
    previous_path = index_data.get(str(user.id))
    relative_path = f"uploads/profiles/{final_name}"
    index_data[str(user.id)] = relative_path
    _save_profile_images_index(index_data)

    if previous_path and previous_path != relative_path:
        _remove_profile_image_file(previous_path)

    return relative_path


def _admin_would_be_missing_after_change(current_admin_count, current_role, requested_role=None, deleting=False):
    if current_role != "admin":
        return False

    remaining_admins = current_admin_count - 1 if deleting or requested_role != "admin" else current_admin_count
    return remaining_admins < 1


def _current_admin_count():
    return Utilisateur.query.filter_by(role="admin").count()


def _is_protected_management_role(role):
    return (role or "").strip().lower() in PROTECTED_MANAGEMENT_ROLES


def _is_protected_management_user(utilisateur):
    return _is_protected_management_role(getattr(utilisateur, "role", None))


def _inactive_account_message(utilisateur):
    if getattr(utilisateur, "statut", None) == "bloque":
        return _("Votre compte a ete bloque. Contactez l administrateur.")
    return _("Votre compte attend la validation de l administrateur.")


def _pending_student_query():
    return Utilisateur.query.filter_by(role="eleve", statut="en_attente")


def _apply_user_form_data(utilisateur, form):
    utilisateur.nom = form.nom.data.strip()
    utilisateur.prenom = form.prenom.data.strip() if form.prenom.data else None
    utilisateur.email = form.email.data.strip().lower()
    utilisateur.telephone = form.telephone.data.strip() if form.telephone.data else None
    utilisateur.role = form.role.data
    if form.password.data:
        utilisateur.set_mot_de_passe(form.password.data)


def _prefill_update_user_form(form, utilisateur):
    form.nom.data = utilisateur.nom
    form.prenom.data = utilisateur.prenom
    form.email.data = utilisateur.email
    form.telephone.data = utilisateur.telephone
    form.role.data = utilisateur.role


def _detach_user_relationships(utilisateur):
    Cours.query.filter_by(enseignant_id=utilisateur.id).update({"enseignant_id": None}, synchronize_session=False)
    Log.query.filter_by(utilisateur_id=utilisateur.id).update({"utilisateur_id": None}, synchronize_session=False)


@main.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for(_dashboard_endpoint_for_user(current_user)))
    return render_template("index.html")


@main.route("/offline")
def offline():
    return render_template("offline.html")


@main.route("/choix-langue")
def choix_langue():
    if current_user.is_authenticated:
        if current_user.role != "eleve":
            return redirect(url_for(_dashboard_endpoint_for_user(current_user)))
        if _selected_language_for_user(current_user) is not None:
            return redirect(url_for(_dashboard_endpoint_for_user(current_user)))
    return render_template("choix_langue.html")


def _onboarding_step1_options():
    return {
        "genre": [
            ("homme", _("Homme")),
            ("femme", _("Femme")),
            ("not_specified", _("Je prefere ne pas le dire")),
        ],
        "age_range": [
            ("under_18", _("Moins de 18 ans")),
            ("18_25", _("18-25 ans")),
            ("26_40", _("26-40 ans")),
            ("40_plus", _("40 ans et plus")),
        ],
        "arabic_background": [
            ("arab_origin", _("Origine arabe (perfectionnement)")),
            ("new_language", _("Debut complet (nouvelle langue)")),
        ],
        "learning_language": [
            ("fr", _("Francais")),
            ("en", _("Anglais")),
            ("es", _("Espagnol")),
            ("ar", _("Arabe")),
        ],
    }


def _onboarding_step2_options():
    return {
        "studied_before": [
            ("first_time", _("Premiere fois")),
            ("long_ago_refresh", _("Etudie il y a longtemps")),
            ("basic_notions", _("Quelques bases")),
        ],
        "current_level": [
            ("alphabet_only", _("Je connais l alphabet")),
            ("simple_words", _("Je connais quelques mots")),
            ("read_some_need_help", _("Je lis un peu, mais j ai besoin d aide")),
        ],
        "learning_goal": [
            ("work_or_school", _("Travail ou etudes")),
            ("family_friends", _("Famille ou proches")),
            ("culture_passion", _("Passion et culture")),
        ],
        "daily_commitment": [
            ("5_min", _("5 min par jour")),
            ("15_min", _("15 min par jour")),
            ("30_plus", _("30 min ou plus par jour")),
        ],
    }


def _load_onboarding_draft():
    raw_draft = session.get(ONBOARDING_DRAFT_SESSION_KEY)
    if isinstance(raw_draft, dict):
        return raw_draft.copy()
    return {}


def _save_onboarding_draft(draft):
    session[ONBOARDING_DRAFT_SESSION_KEY] = draft
    session.modified = True


def _clear_onboarding_draft():
    session.pop(ONBOARDING_DRAFT_SESSION_KEY, None)
    session.modified = True


def _step1_is_complete(data):
    required_keys = ("preferred_name", "genre", "age_range", "arabic_background", "learning_language")
    return all((data.get(key) or "").strip() for key in required_keys)


def _onboarding_redirect_target_for_user(user):
    has_placement_result = ResultatPlacement.query.filter_by(eleve_id=user.id).first() is not None
    if not has_placement_result and placement_test_is_ready():
        return url_for("placement.intro")
    return url_for("progress.dashboard_eleve")


@main.route("/onboarding/profil", methods=["GET", "POST"])
@login_required
@eleve_required
def onboarding_profile():
    if _selected_language_for_user(current_user) is None:
        return redirect(url_for("main.choix_langue"))

    profile = StudentOnboardingProfile.query.filter_by(eleve_id=current_user.id).first()
    options = _onboarding_step1_options()
    draft = _load_onboarding_draft()

    form_data = {
        "preferred_name": (draft.get("preferred_name") or (profile.preferred_name if profile else current_user.prenom or current_user.nom or "")),
        "genre": draft.get("genre") or (profile.genre if profile else ""),
        "age_range": draft.get("age_range") or (profile.age_range if profile else ""),
        "arabic_background": draft.get("arabic_background") or (profile.arabic_background if profile else ""),
        "learning_language": draft.get("learning_language") or (profile.learning_language if profile else _selected_language_for_user(current_user) or "fr"),
    }

    if request.method == "POST":
        preferred_name = (request.form.get("preferred_name") or "").strip()
        genre = (request.form.get("genre") or "").strip()
        age_range = (request.form.get("age_range") or "").strip()
        arabic_background = (request.form.get("arabic_background") or "").strip()
        learning_language = _normalize_language_code(request.form.get("learning_language"))

        form_data = {
            "preferred_name": preferred_name,
            "genre": genre,
            "age_range": age_range,
            "arabic_background": arabic_background,
            "learning_language": learning_language or "",
        }

        has_error = False
        if not preferred_name:
            flash(_("Le prenom d usage est obligatoire."), "warning")
            has_error = True
        if genre not in ONBOARDING_GENRE_CHOICES:
            flash(_("Selectionnez votre genre."), "warning")
            has_error = True
        if age_range not in ONBOARDING_AGE_RANGE_CHOICES:
            flash(_("Selectionnez votre tranche d age."), "warning")
            has_error = True
        if arabic_background not in ONBOARDING_ARABIC_BACKGROUND_CHOICES:
            flash(_("Selectionnez votre lien avec la langue arabe."), "warning")
            has_error = True
        if learning_language is None:
            flash(_("Selectionnez votre langue d apprentissage."), "warning")
            has_error = True

        if has_error:
            return render_template("onboarding_profile.html", options=options, form_data=form_data, profile=profile)

        updated_draft = draft.copy()
        updated_draft.update(
            {
                "preferred_name": preferred_name,
                "genre": genre,
                "age_range": age_range,
                "arabic_background": arabic_background,
                "learning_language": learning_language,
            }
        )
        _save_onboarding_draft(updated_draft)
        _set_language_selection(learning_language, current_user)
        db.session.commit()
        return redirect(url_for("main.onboarding_profile_step2"))

    return render_template("onboarding_profile.html", options=options, form_data=form_data, profile=profile)


@main.route("/onboarding/profil/etape-2", methods=["GET", "POST"])
@login_required
@eleve_required
def onboarding_profile_step2():
    if _selected_language_for_user(current_user) is None:
        return redirect(url_for("main.choix_langue"))

    profile = StudentOnboardingProfile.query.filter_by(eleve_id=current_user.id).first()
    options = _onboarding_step2_options()
    draft = _load_onboarding_draft()
    step1_source = draft if _step1_is_complete(draft) else {}
    if not step1_source and profile is not None:
        step1_source = {
            "preferred_name": profile.preferred_name or "",
            "genre": profile.genre or "",
            "age_range": profile.age_range or "",
            "arabic_background": profile.arabic_background or "",
            "learning_language": profile.learning_language or "",
        }

    if not _step1_is_complete(step1_source):
        flash(_("Commencez par l etape 1."), "info")
        return redirect(url_for("main.onboarding_profile"))

    form_data = {
        "studied_before": draft.get("studied_before") or (profile.studied_before if profile else ""),
        "current_level": draft.get("current_level") or (profile.current_level if profile else ""),
        "learning_goal": draft.get("learning_goal") or (profile.learning_goal if profile else ""),
        "daily_commitment": draft.get("daily_commitment") or (profile.daily_commitment if profile else ""),
    }

    if request.method == "POST":
        studied_before = (request.form.get("studied_before") or "").strip()
        current_level = (request.form.get("current_level") or "").strip()
        learning_goal = (request.form.get("learning_goal") or "").strip()
        daily_commitment = (request.form.get("daily_commitment") or "").strip()

        form_data = {
            "studied_before": studied_before,
            "current_level": current_level,
            "learning_goal": learning_goal,
            "daily_commitment": daily_commitment,
        }

        has_error = False
        if studied_before not in ONBOARDING_STUDIED_BEFORE_CHOICES:
            flash(_("Selectionnez votre historique d apprentissage."), "warning")
            has_error = True
        if current_level not in ONBOARDING_CURRENT_LEVEL_CHOICES:
            flash(_("Selectionnez votre niveau actuel."), "warning")
            has_error = True
        if learning_goal not in ONBOARDING_LEARNING_GOAL_CHOICES:
            flash(_("Selectionnez votre objectif."), "warning")
            has_error = True
        if daily_commitment not in ONBOARDING_DAILY_COMMITMENT_CHOICES:
            flash(_("Selectionnez votre disponibilite quotidienne."), "warning")
            has_error = True

        if has_error:
            return render_template("onboarding_profile_step2.html", options=options, form_data=form_data)

        if profile is None:
            profile = StudentOnboardingProfile(eleve_id=current_user.id)
            db.session.add(profile)

        profile.preferred_name = step1_source["preferred_name"]
        profile.genre = step1_source["genre"]
        profile.age_range = step1_source["age_range"]
        profile.arabic_background = step1_source["arabic_background"]
        profile.learning_language = step1_source["learning_language"]
        profile.studied_before = studied_before
        profile.current_level = current_level
        profile.learning_goal = learning_goal
        profile.daily_commitment = daily_commitment

        _set_language_selection(step1_source["learning_language"], current_user)
        if not (current_user.prenom or "").strip():
            current_user.prenom = step1_source["preferred_name"]

        db.session.commit()
        _clear_onboarding_draft()
        flash(_("Profil enregistre. On prepare votre parcours."), "success")
        return redirect(url_for("main.onboarding_profile_transition"))

    return render_template("onboarding_profile_step2.html", options=options, form_data=form_data)


@main.route("/onboarding/profil/transition")
@login_required
@eleve_required
def onboarding_profile_transition():
    profile = StudentOnboardingProfile.query.filter_by(eleve_id=current_user.id).first()
    if profile is None:
        return redirect(url_for("main.onboarding_profile"))

    next_url = _onboarding_redirect_target_for_user(current_user)
    response = make_response(render_template("onboarding_profile_transition.html", next_url=next_url))
    response.headers["Refresh"] = f"2; url={next_url}"
    return response


@main.route("/langue/<code>")
def changer_langue(code):
    selected_lang = _set_language_selection(code, current_user if current_user.is_authenticated else None)
    if current_user.is_authenticated and selected_lang is not None:
        db.session.commit()
    next_url = request.args.get("next")
    if next_url:
        return redirect(next_url)
    if current_user.is_authenticated:
        return redirect(url_for(_dashboard_endpoint_for_user(current_user)))
    return redirect(request.referrer or url_for("main.index"))


@main.route("/sw.js")
def service_worker():
    response = send_from_directory("static", "sw.js", mimetype="application/javascript")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["Service-Worker-Allowed"] = "/"
    return response


@main.route("/service-worker.js")
def legacy_service_worker():
    response = send_from_directory("static", "sw.js", mimetype="application/javascript")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["Service-Worker-Allowed"] = "/"
    return response


@main.route("/cache-reset")
def cache_reset():
    response = make_response(render_template("cache_reset.html"))
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["Clear-Site-Data"] = '"cache", "storage", "cookies"'
    return response


@main.route("/media/<path:filename>")
def media_file(filename):
    if not (filename or "").startswith("uploads/"):
        abort(404)
    absolute_path = resolve_asset_path(filename)
    if absolute_path is None or not os.path.isfile(absolute_path):
        abort(404)
    return send_file(absolute_path)


@main.route("/health")
def health():
    database = "ok"
    try:
        db.session.execute(text("SELECT 1"))
    except Exception:
        database = "error"

    return jsonify(
        {
            "status": "ok" if database == "ok" else "degraded",
            "database": database,
            "version": current_app.config["VERSION"],
        }
    )


@main.route("/favicon.ico")
def favicon():
    return send_from_directory("static/images", "LOGO.jpeg", mimetype="image/jpeg")


@main.route("/login", methods=["GET", "POST"])
def login():
    if session.get("_user_id") and current_user.is_authenticated:
        redirect_url = url_for(_dashboard_endpoint_for_user(current_user))
        if _is_ajax_request():
            return jsonify({"success": True, "redirect_url": redirect_url})
        return redirect(redirect_url)

    form = LoginForm()
    if form.validate_on_submit():
        identifiant = form.email.data.strip().lower()
        utilisateur = Utilisateur.query.filter(Utilisateur.email.ilike(identifiant)).first()

        if utilisateur and check_password_hash(utilisateur.mot_de_passe, form.mot_de_passe.data):
            if utilisateur.statut != "actif":
                pending_message = _inactive_account_message(utilisateur)
                current_app.logger.info(
                    "Connexion bloquee pour %s: statut=%s",
                    utilisateur.email,
                    utilisateur.statut,
                )
                log_action(
                    module="auth",
                    action="Connexion bloquee",
                    level="WARNING",
                    user_id=utilisateur.id,
                    details=f"email={utilisateur.email}; statut={utilisateur.statut}",
                )
                if _is_ajax_request():
                    return jsonify({"success": False, "message": pending_message}), 403
                flash(pending_message, "warning")
                return render_template("login.html", form=form)

            if is_site_maintenance_enabled() and utilisateur.role not in {"admin", "maintenance"}:
                message = _("La plateforme est temporairement en maintenance.")
                log_action(
                    module="security",
                    action="Connexion refusee en mode maintenance",
                    level="WARNING",
                    user_id=utilisateur.id,
                    details=f"email={utilisateur.email}",
                )
                if _is_ajax_request():
                    return jsonify({"success": False, "message": message}), 503
                return render_template("maintenance/site_maintenance_public.html", maintenance_payload=None), 503

            previous_lang = _normalize_language_code(session.get("langue") or session.get("lang"))
            session.clear()
            login_user(utilisateur, remember=form.remember.data)
            active_session = create_active_session(
                utilisateur,
                ip_address=request.remote_addr or "",
                user_agent=request.user_agent.string or "",
            )
            session[ACTIVE_SESSION_TOKEN_KEY] = active_session.session_token
            selected_lang = previous_lang or _saved_language_for_user(utilisateur)
            if selected_lang is not None:
                _set_language_selection(selected_lang, utilisateur)
            utilisateur.dernier_acces = datetime.utcnow()
            db.session.commit()

            current_app.logger.info(
                "Connexion reussie pour %s depuis %s",
                utilisateur.email,
                request.remote_addr or "unknown",
            )
            log_action(
                module="auth",
                action="Connexion reussie",
                level="INFO",
                user_id=utilisateur.id,
                details=f"email={utilisateur.email}; role={utilisateur.role}",
            )
            flash(_("Connexion reussie."), "success")
            redirect_url = url_for(_dashboard_endpoint_for_user(utilisateur))
            if _is_ajax_request():
                return jsonify({"success": True, "redirect_url": redirect_url})
            return redirect(redirect_url)

        current_app.logger.warning("Tentative de connexion echouee pour %s", identifiant)
        log_action(
            module="auth",
            action="Connexion echouee",
            level="WARNING",
            details=f"email={identifiant}",
        )
        error_message = _("Identifiant ou mot de passe incorrect.")
        if _is_ajax_request():
            return jsonify({"success": False, "message": error_message}), 401
        flash(error_message, "danger")

    elif request.method == "POST" and _is_ajax_request():
        errors = []
        for field_errors in form.errors.values():
            errors.extend(field_errors)
        return jsonify(
            {
                "success": False,
                "message": errors[0] if errors else _("Le formulaire de connexion est invalide."),
            }
        ), 400

    return render_template("login.html", form=form)


@main.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for(_dashboard_endpoint_for_user(current_user)))

    form = RegisterForm()
    if form.validate_on_submit():
        normalized_email = form.email.data.strip().lower()
        if Utilisateur.query.filter_by(email=normalized_email).first():
            flash(_("Cet email existe deja."), "danger")
            return render_template("register.html", form=form)

        utilisateur = Utilisateur(
            nom=form.nom.data.strip(),
            prenom=form.prenom.data.strip() if form.prenom.data else None,
            email=normalized_email,
            role="eleve",
            statut="actif",
            mot_de_passe="placeholder",
        )
        utilisateur.set_mot_de_passe(form.password.data)
        db.session.add(utilisateur)
        db.session.commit()
        flash(_("Compte cree avec succes. Vous pouvez vous connecter maintenant."), "success")
        return redirect(url_for("main.login"))

    return render_template("register.html", form=form)


@main.route("/auth/google/start")
def google_start():
    flash(_("La connexion Google n est plus disponible. Utilisez votre email et votre mot de passe."), "warning")
    return redirect(url_for("main.login"))


@main.route("/auth/google/callback")
def google_callback():
    flash(_("La connexion Google n est plus disponible. Utilisez votre email et votre mot de passe."), "warning")
    return redirect(url_for("main.login"))


@main.route("/logout")
@login_required
def logout():
    active_session_token = session.get(ACTIVE_SESSION_TOKEN_KEY)
    user_id = current_user.id
    user_email = current_user.email
    previous_lang = _selected_language_for_user(current_user)
    revoke_active_session_by_token(active_session_token, reason="logout")
    db.session.commit()
    logout_user()
    remember_action = session.get("_remember")
    session.clear()
    if remember_action is not None:
        session["_remember"] = remember_action
    if previous_lang in current_app.config["LANGUAGES"]:
        session["langue"] = previous_lang
        session["lang"] = previous_lang
    flash(_("Vous etes maintenant deconnecte."), "info")
    response = redirect(url_for("main.login"))
    response.delete_cookie(current_app.config.get("SESSION_COOKIE_NAME", "session"))
    response.delete_cookie(
        current_app.config.get("REMEMBER_COOKIE_NAME", "remember_token"),
        domain=current_app.config.get("REMEMBER_COOKIE_DOMAIN"),
        path=current_app.config.get("REMEMBER_COOKIE_PATH", "/"),
    )
    response.headers["Clear-Site-Data"] = '"cache"'
    log_action(
        module="auth",
        action="Deconnexion",
        level="INFO",
        user_id=user_id,
        details=f"email={user_email}",
    )
    return response


@main.route("/admin/vocabulaire")
def vocabulaire_admin():
    return abort(404)


@csrf.exempt
@main.route("/admin/vocabulaire/ajouter", methods=["POST"])
def ajouter_vocabulaire():
    return abort(404)


@csrf.exempt
@main.route("/admin/vocabulaire/supprimer/<int:vocabulaire_id>", methods=["POST"])
def supprimer_vocabulaire(vocabulaire_id):
    return abort(404)


@main.route("/dashboard")
@login_required
def dashboard():
    return redirect(url_for(_dashboard_endpoint_for_user(current_user)))


@main.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    form = ProfileForm()

    if request.method == "GET":
        form.nom.data = current_user.nom
        form.prenom.data = current_user.prenom
        form.telephone.data = current_user.telephone

    if form.validate_on_submit():
        current_user.nom = form.nom.data.strip()
        current_user.prenom = form.prenom.data.strip() if form.prenom.data else None
        current_user.telephone = form.telephone.data.strip() if form.telephone.data else None

        try:
            if form.photo.data and form.photo.data.filename:
                _save_profile_image(current_user, form.photo.data)
        except ValueError as exc:
            flash(str(exc), "danger")
            return render_template("profile.html", form=form, profile_image_url=_profile_image_url(current_user))

        db.session.commit()
        flash(_("Profil mis a jour avec succes."), "success")
        return redirect(url_for("main.profile"))

    return render_template("profile.html", form=form, profile_image_url=_profile_image_url(current_user))


@main.route("/admin/utilisateurs")
@login_required
@admin_required
def gestion_utilisateurs():
    utilisateurs = Utilisateur.query.order_by(Utilisateur.nom.asc(), Utilisateur.prenom.asc()).all()
    delete_form = DeleteUserForm()
    return render_template(
        "gestion_utilisateurs.html",
        utilisateurs=utilisateurs,
        delete_form=delete_form,
    )


@main.route("/admin/creer_utilisateur", methods=["GET", "POST"])
@login_required
@admin_required
def creer_utilisateur():
    form = CreateUserForm()

    if form.validate_on_submit():
        if Utilisateur.query.filter_by(email=form.email.data.lower()).first():
            flash(_("Cet email existe deja."), "danger")
            return render_template("creer_utilisateur.html", form=form)

        utilisateur = Utilisateur(
            nom=form.nom.data.strip(),
            prenom=form.prenom.data.strip() if form.prenom.data else None,
            email=form.email.data.lower(),
            telephone=form.telephone.data.strip() if form.telephone.data else None,
            role=form.role.data,
            statut="actif",
            mot_de_passe=generate_password_hash(form.password.data),
        )
        db.session.add(utilisateur)
        db.session.commit()
        flash(_("Utilisateur cree avec succes."), "success")
        return redirect(url_for("main.gestion_utilisateurs"))

    return render_template("creer_utilisateur.html", form=form)


@main.route("/admin/utilisateurs/<int:user_id>/modifier", methods=["GET", "POST"])
@login_required
@admin_required
def modifier_utilisateur(user_id):
    utilisateur = Utilisateur.query.get_or_404(user_id)
    form = UpdateUserForm()

    if request.method == "GET":
        _prefill_update_user_form(form, utilisateur)

    if form.validate_on_submit():
        normalized_email = form.email.data.strip().lower()
        email_owner = Utilisateur.query.filter(Utilisateur.email == normalized_email, Utilisateur.id != utilisateur.id).first()
        if email_owner:
            flash(_("Cet email existe deja."), "danger")
            return render_template("modifier_utilisateur.html", form=form, utilisateur=utilisateur)

        requested_role = form.role.data
        admin_count = _current_admin_count()
        if _is_protected_management_user(utilisateur) and requested_role != utilisateur.role:
            flash(_("Le role des comptes admin et maintenance ne peut pas etre modifie."), "danger")
            return render_template("modifier_utilisateur.html", form=form, utilisateur=utilisateur)

        if utilisateur.id == current_user.id and utilisateur.role == "admin" and requested_role != "admin":
            flash(_("Vous ne pouvez pas retirer votre propre role administrateur."), "danger")
            return render_template("modifier_utilisateur.html", form=form, utilisateur=utilisateur)

        if _admin_would_be_missing_after_change(admin_count, utilisateur.role, requested_role=requested_role):
            flash(_("Impossible de retirer le role du dernier administrateur."), "danger")
            return render_template("modifier_utilisateur.html", form=form, utilisateur=utilisateur)

        _apply_user_form_data(utilisateur, form)
        db.session.commit()
        flash(_("Utilisateur modifie avec succes."), "success")
        return redirect(url_for("main.gestion_utilisateurs"))

    return render_template("modifier_utilisateur.html", form=form, utilisateur=utilisateur)


@main.route("/admin/utilisateurs/<int:user_id>/bloquer", methods=["POST"])
@login_required
@admin_required
def bloquer_utilisateur(user_id):
    utilisateur = Utilisateur.query.get_or_404(user_id)

    if _is_protected_management_user(utilisateur):
        flash(_("Les comptes admin et maintenance ne peuvent pas etre bloques."), "danger")
        return redirect(url_for("main.gestion_utilisateurs"))

    if utilisateur.statut == "bloque":
        flash(_("Cet utilisateur est deja bloque."), "info")
        return redirect(url_for("main.gestion_utilisateurs"))

    utilisateur.statut = "bloque"
    revoke_sessions_for_user(utilisateur.id, reason="user-blocked")
    db.session.commit()
    log_action(
        module="security",
        action="Utilisateur bloque",
        level="WARNING",
        user_id=current_user.id,
        details=f"target_user_id={utilisateur.id}; email={utilisateur.email}",
    )
    flash(_("Utilisateur bloque avec succes."), "success")
    return redirect(url_for("main.gestion_utilisateurs"))


@main.route("/admin/utilisateurs/<int:user_id>/debloquer", methods=["POST"])
@login_required
@admin_required
def debloquer_utilisateur(user_id):
    utilisateur = Utilisateur.query.get_or_404(user_id)

    if utilisateur.statut == "actif":
        flash(_("Cet utilisateur est deja actif."), "info")
        return redirect(url_for("main.gestion_utilisateurs"))

    utilisateur.statut = "actif"
    db.session.commit()
    log_action(
        module="security",
        action="Utilisateur debloque",
        level="INFO",
        user_id=current_user.id,
        details=f"target_user_id={utilisateur.id}; email={utilisateur.email}",
    )
    flash(_("Utilisateur debloque avec succes."), "success")
    return redirect(url_for("main.gestion_utilisateurs"))


@main.route("/admin/utilisateurs/<int:user_id>/supprimer", methods=["POST"])
@login_required
@admin_required
def supprimer_utilisateur(user_id):
    utilisateur = Utilisateur.query.get_or_404(user_id)

    if utilisateur.id == current_user.id:
        flash(_("Vous ne pouvez pas supprimer votre propre compte."), "danger")
        return redirect(url_for("main.gestion_utilisateurs"))

    if _is_protected_management_user(utilisateur):
        flash(_("Les comptes admin et maintenance ne peuvent pas etre supprimes."), "danger")
        return redirect(url_for("main.gestion_utilisateurs"))

    if _admin_would_be_missing_after_change(_current_admin_count(), utilisateur.role, deleting=True):
        flash(_("Impossible de supprimer le dernier administrateur."), "danger")
        return redirect(url_for("main.gestion_utilisateurs"))

    _detach_user_relationships(utilisateur)
    db.session.delete(utilisateur)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash(_("Cet utilisateur ne peut pas etre supprime car des donnees liees existent encore."), "danger")
        return redirect(url_for("main.gestion_utilisateurs"))

    flash(_("Utilisateur supprime definitivement."), "success")
    return redirect(url_for("main.gestion_utilisateurs"))


@main.route("/admin/valider-comptes")
@login_required
@admin_required
def valider_comptes():
    return redirect(url_for("main.gestion_utilisateurs"))


@main.route("/admin/utilisateurs/<int:user_id>/valider", methods=["POST"])
@login_required
@admin_required
def valider_utilisateur(user_id):
    return redirect(url_for("main.gestion_utilisateurs"))


@main.route("/request_reset_password", methods=["GET", "POST"])
def request_reset_password():
    form = RequestResetPasswordForm()
    if request.method == "POST" and isinstance(form.email.data, str):
        form.email.data = form.email.data.strip()

    if form.validate_on_submit():
        email = form.email.data.lower()
        utilisateur = Utilisateur.query.filter(Utilisateur.email.ilike(email), Utilisateur.role == "eleve").first()

        if utilisateur:
            thread = get_or_create_support_thread(utilisateur)
            session[SUPPORT_GUEST_THREAD_ID] = thread.id
            session[SUPPORT_GUEST_STUDENT_ID] = utilisateur.id

            reset_message = create_support_message(
                thread,
                body="Demande de reinitialisation du mot de passe.",
                author_role="system",
                message_type="reset_requested",
                payload={
                    "request_ip": request.remote_addr or "",
                    "request_user_agent": request.user_agent.string or "",
                    "requested_email": utilisateur.email,
                },
            )
            notify_admins_about_support_thread(
                thread,
                title="Nouvelle demande de reinitialisation",
                message=f"{utilisateur.nom_complet} a demande un changement de mot de passe.",
                event_key=f"support-reset:{reset_message.id}",
                payload={"message_id": reset_message.id},
                app=current_app._get_current_object(),
            )
            log_action(
                module="security",
                action="Demande reset mot de passe",
                level="WARNING",
                user_id=utilisateur.id,
                details=f"thread_id={thread.id}; email={utilisateur.email}",
            )
            flash(_("Votre demande a ete transmise a l administrateur."), "info")
            return redirect(url_for("notifications.support_thread_guest"))

        flash(_("Si un compte existe pour cet email, la demande a ete prise en compte."), "info")
        return redirect(url_for("main.login"))

    return render_template("request_reset_password.html", form=form)


@main.route("/reset_password/<token>", methods=["GET", "POST"])
def reset_password_token(token):
    grant = PasswordResetGrant.query.filter_by(token=token).first()
    if grant is None or grant.used_at is not None or grant.is_expired:
        flash(_("Le lien de reinitialisation est invalide ou expire."), "danger")
        return redirect(url_for("main.login"))

    form = ResetPasswordConfirmForm()
    if form.validate_on_submit():
        utilisateur = Utilisateur.query.filter_by(id=grant.student_id).first()
        if not utilisateur:
            flash(_("Utilisateur introuvable."), "danger")
            return redirect(url_for("main.login"))

        utilisateur.set_mot_de_passe(form.new_password.data)
        grant.used_at = datetime.utcnow()
        db.session.commit()
        log_action(
            module="security",
            action="Mot de passe reinitialise",
            level="INFO",
            user_id=utilisateur.id,
            details=f"grant_id={grant.id}",
        )
        flash(_("Mot de passe mis a jour. Vous pouvez vous reconnecter."), "success")
        return redirect(url_for("main.login"))

    return render_template("reset_password.html", form=form)
