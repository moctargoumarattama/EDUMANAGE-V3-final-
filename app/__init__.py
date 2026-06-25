import logging
import os
import shutil
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, current_app, g, render_template, request, session
from flask_login import current_user, logout_user
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError, OperationalError

try:
    from flask_dance.contrib.google import make_google_blueprint
except ImportError:  # pragma: no cover - optional dependency for local setups
    make_google_blueprint = None

from .config import Config
from .extensions import babel, csrf, db, login_manager, migrate
from .i18n import DEFAULT_LANGUAGE_CODE, build_language_options, get_language_direction, normalize_language_code
from .storage import build_asset_url


load_dotenv()

STRICT_NO_STORE_PREFIXES = (
    "/admin",
    "/dashboard",
    "/login",
    "/logout",
    "/maintenance",
    "/notifications",
    "/profile",
)
SOFT_PRIVATE_REVALIDATE_PREFIXES = (
    "/courses",
    "/lecons",
    "/placement",
    "/progression",
    "/quiz",
)
STRICT_NO_STORE_EXACT_PATHS = {
    "/placement/admin/questions",
}
STRICT_NO_STORE_SUBPREFIXES = (
    "/placement/admin/",
)
RUNTIME_CACHEABLE_EXACT_PATHS = {
    "/",
    "/offline",
    "/choix-langue",
    "/request_reset_password",
}
RUNTIME_CACHEABLE_PREFIXES = (
    "/courses",
    "/lecons",
    "/placement",
    "/progression",
    "/quiz",
    "/reset_password/",
)
MAINTENANCE_MODE_EXEMPT_ENDPOINTS = {
    "main.favicon",
    "main.legacy_service_worker",
    "main.login",
    "main.logout",
    "main.media_file",
    "main.service_worker",
    "static",
}


class RequestFormatter(logging.Formatter):
    def format(self, record):
        try:
            record.role = getattr(g, "role", "SYSTEM")
            record.user_id = getattr(g, "user_id", 0)
        except RuntimeError:
            record.role = "SYSTEM"
            record.user_id = 0
        return super().format(record)


def get_locale():
    lang = normalize_language_code(session.get("langue") or session.get("lang"))
    languages = current_app.config.get("LANGUAGES", {})
    if lang in languages:
        session["langue"] = lang
        session["lang"] = lang
        return lang
    if getattr(current_user, "is_authenticated", False):
        saved_lang = normalize_language_code(getattr(current_user, "langue_preferee", None))
        if saved_lang in languages:
            session["langue"] = saved_lang
            session["lang"] = saved_lang
            return saved_lang
    return request.accept_languages.best_match(tuple(languages.keys())) or current_app.config.get(
        "BABEL_DEFAULT_LOCALE",
        DEFAULT_LANGUAGE_CODE,
    )


def setup_logging(app):
    log_dir = os.path.dirname(app.config["LOG_FILE"])
    os.makedirs(log_dir, exist_ok=True)

    file_handler = RotatingFileHandler(
        app.config["LOG_FILE"],
        maxBytes=10 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    formatter = RequestFormatter(
        "%(asctime)s %(levelname)s [user_id=%(user_id)s role=%(role)s]: %(message)s [in %(pathname)s:%(lineno)d]"
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)
    app.logger.addHandler(file_handler)
    app.logger.setLevel(logging.INFO)
    app.logger.info("Demarrage de l'application")


def _warn_for_dev_secrets(app):
    if app.config.get("SECRET_KEY") == "dev-only-local-secret-key":
        app.logger.warning("SECRET_KEY par defaut detectee. Configurez SECRET_KEY avant le deploiement.")
    if app.config.get("SECURITY_PASSWORD_SALT") == "dev-only-local-password-salt":
        app.logger.warning(
            "SECURITY_PASSWORD_SALT par defaut detecte. Configurez SECURITY_PASSWORD_SALT avant le deploiement."
        )


def _migrate_legacy_uploads(app):
    legacy_root = Path(app.config.get("LEGACY_STATIC_UPLOADS_ROOT") or "")
    media_root = Path(app.config["MEDIA_ROOT"]) / "uploads"
    if not legacy_root.exists():
        return

    for source_path in legacy_root.rglob("*"):
        if not source_path.is_file():
            continue
        relative_path = source_path.relative_to(legacy_root)
        destination = media_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            shutil.copy2(source_path, destination)


def _get_existing_tables():
    return set(inspect(db.engine).get_table_names())


BOOTSTRAP_SCHEMA_REQUIREMENTS = {
    "utilisateur": {"id", "email", "role", "langue_preferee", "date_creation"},
    "cours": {"id", "nom", "niveau", "image_url", "est_publie", "date_creation", "enseignant_id"},
    "lecon": {
        "id",
        "cours_id",
        "titre",
        "ordre",
        "contenu_texte",
        "video_url",
        "pdf_url",
        "audio_url",
        "duree_minutes",
        "points_recompense",
        "est_publie",
        "date_publication",
        "date_creation",
    },
}


def _get_existing_columns(table_name):
    return {column["name"] for column in inspect(db.engine).get_columns(table_name)}


def _schema_ready():
    existing_tables = _get_existing_tables()
    for table_name, required_columns in BOOTSTRAP_SCHEMA_REQUIREMENTS.items():
        if table_name not in existing_tables:
            return False
        if not required_columns.issubset(_get_existing_columns(table_name)):
            return False
    return True


def _ensure_support_reset_schema():
    from .models import PasswordResetGrant, SupportMessage, SupportThread

    existing_tables = _get_existing_tables()
    required_tables = {
        "support_thread": SupportThread.__table__,
        "support_message": SupportMessage.__table__,
        "password_reset_grant": PasswordResetGrant.__table__,
    }
    missing_tables = [table for name, table in required_tables.items() if name not in existing_tables]
    if missing_tables:
        db.Model.metadata.create_all(bind=db.engine, tables=missing_tables)


def _ensure_user_language_schema(app):
    existing_tables = _get_existing_tables()
    if "utilisateur" not in existing_tables:
        return

    existing_columns = _get_existing_columns("utilisateur")
    if "langue_preferee" in existing_columns:
        return

    with db.engine.begin() as connection:
        connection.execute(text("ALTER TABLE utilisateur ADD COLUMN langue_preferee VARCHAR(5)"))
    app.logger.info("Colonne utilisateur.langue_preferee ajoutee automatiquement pour compatibilite locale.")


def _ensure_maintenance_ops_schema():
    from .models import ActiveSession, SystemSetting

    existing_tables = _get_existing_tables()
    required_tables = {
        "system_setting": SystemSetting.__table__,
        "active_session": ActiveSession.__table__,
    }
    missing_tables = [table for name, table in required_tables.items() if name not in existing_tables]
    if missing_tables:
        db.Model.metadata.create_all(bind=db.engine, tables=missing_tables)


def _ensure_learning_content_i18n_schema(app):
    from .models import Cours, CoursTraduction, Lecon, LeconTraduction

    existing_tables = _get_existing_tables()
    required_tables = {
        "cours_traduction": CoursTraduction.__table__,
        "lecon_traduction": LeconTraduction.__table__,
    }
    missing_tables = [table for name, table in required_tables.items() if name not in existing_tables]
    if missing_tables:
        db.Model.metadata.create_all(bind=db.engine, tables=missing_tables)

    try:
        all_courses = Cours.query.all()
        all_lessons = Lecon.query.all()
    except OperationalError:
        db.session.rollback()
        app.logger.info("Initialisation des traductions de contenu differee: schema en attente.")
        return

    has_pending_changes = False
    demo_course_ar_overrides = {
        "uploads/courses/course_demo_salutations.svg": {
            "nom": "مسار العربية A1 - الأساسيات",
            "description": "مسار للمبتدئين لتعلم التحية والأبجدية وأهم العبارات الأساسية.",
        },
        "uploads/courses/course_demo_quotidien.svg": {
            "nom": "مسار العربية A2 - الحياة اليومية",
            "description": "مسار تمهيدي لمفردات الحياة اليومية: البيت، المدرسة، والأشياء الأكثر استعمالا.",
        },
    }
    demo_lesson_ar_overrides = {
        ("uploads/courses/course_demo_salutations.svg", 1): "التحية باللغة العربية",
        ("uploads/courses/course_demo_salutations.svg", 2): "التعريف بالنفس ببساطة",
        ("uploads/courses/course_demo_quotidien.svg", 1): "أدوات الفصل",
        ("uploads/courses/course_demo_quotidien.svg", 2): "البيت والعائلة",
    }

    for cours in all_courses:
        course_title = (cours.nom or "").strip()
        if not course_title:
            continue
        course_description = (cours.description or "").strip() or None
        translations_by_language = {item.langue: item for item in cours.traductions}
        demo_override = demo_course_ar_overrides.get(cours.image_url or "")
        if demo_override:
            course_title = demo_override.get("nom") or course_title
            course_description = demo_override.get("description") or course_description
        if "ar" not in translations_by_language:
            db.session.add(
                CoursTraduction(
                    cours_id=cours.id,
                    langue="ar",
                    nom=course_title,
                    description=course_description,
                )
            )
            has_pending_changes = True
        elif demo_override:
            arabic_entry = translations_by_language["ar"]
            if arabic_entry.nom != course_title or arabic_entry.description != course_description:
                arabic_entry.nom = course_title
                arabic_entry.description = course_description
                has_pending_changes = True

    for lesson in all_lessons:
        lesson_title = (lesson.titre or "").strip()
        if not lesson_title:
            continue
        lesson_content = (lesson.contenu_texte or "").strip() or None
        translations_by_language = {item.langue: item for item in lesson.traductions}
        demo_key = ((lesson.cours.image_url if lesson.cours else ""), lesson.ordre or 0)
        demo_title = demo_lesson_ar_overrides.get(demo_key)
        if demo_title:
            lesson_title = demo_title
        if "ar" not in translations_by_language:
            db.session.add(
                LeconTraduction(
                    lecon_id=lesson.id,
                    langue="ar",
                    titre=lesson_title,
                    contenu_texte=lesson_content,
                )
            )
            has_pending_changes = True
        elif demo_title:
            arabic_entry = translations_by_language["ar"]
            if arabic_entry.titre != lesson_title:
                arabic_entry.titre = lesson_title
                has_pending_changes = True

    if has_pending_changes:
        db.session.commit()


def _ensure_lesson_images_schema(app):
    from .models import LeconImage

    existing_tables = _get_existing_tables()
    if "lecon_image" not in existing_tables:
        db.Model.metadata.create_all(bind=db.engine, tables=[LeconImage.__table__])
        return

    existing_columns = _get_existing_columns("lecon_image")
    if "audio_url" in existing_columns:
        return

    with db.engine.begin() as connection:
        connection.execute(text("ALTER TABLE lecon_image ADD COLUMN audio_url VARCHAR(500)"))
    app.logger.info("Colonne lecon_image.audio_url ajoutee automatiquement pour compatibilite locale.")


def _ensure_student_onboarding_schema():
    from .models import StudentOnboardingProfile

    existing_tables = _get_existing_tables()
    if "student_onboarding_profile" in existing_tables:
        return
    db.Model.metadata.create_all(bind=db.engine, tables=[StudentOnboardingProfile.__table__])


def _response_is_cache_policy_candidate(response):
    return (
        response.mimetype in {"text/html", "application/xhtml+xml"}
        or 300 <= response.status_code < 400
    )


def _path_matches_prefix(path, prefix):
    return path == prefix or path.startswith(f"{prefix}/")


def _is_strict_no_store_path(path):
    if path in STRICT_NO_STORE_EXACT_PATHS:
        return True
    if any(path.startswith(prefix) for prefix in STRICT_NO_STORE_SUBPREFIXES):
        return True
    return any(_path_matches_prefix(path, prefix) for prefix in STRICT_NO_STORE_PREFIXES)


def _is_soft_private_revalidate_path(path):
    return any(_path_matches_prefix(path, prefix) for prefix in SOFT_PRIVATE_REVALIDATE_PREFIXES)


def _is_runtime_navigation_cacheable_path(path):
    if _is_strict_no_store_path(path):
        return False
    if path in RUNTIME_CACHEABLE_EXACT_PATHS:
        return True
    return any(_path_matches_prefix(path, prefix) for prefix in RUNTIME_CACHEABLE_PREFIXES)


def apply_route_cache_policy(response, path):
    if not _response_is_cache_policy_candidate(response):
        response.headers.pop("X-Cacheable-Navigation", None)
        return response

    if _is_runtime_navigation_cacheable_path(path):
        response.headers["X-Cacheable-Navigation"] = "1"
    else:
        response.headers.pop("X-Cacheable-Navigation", None)

    if _is_strict_no_store_path(path):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0, private"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    if _is_soft_private_revalidate_path(path):
        response.headers["Cache-Control"] = "private, no-cache, max-age=0, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"

    return response


def _bootstrap_runtime_data(app):
    from .models import (
        Choix,
        Cours,
        Inscription,
        Lecon,
        PlacementChoix,
        PlacementQuestion,
        PlacementTest,
        ProgressionLecon,
        Question,
        ResultatPlacement,
        Utilisateur,
        Vocabulaire,
        XPEleve,
    )

    if not _schema_ready():
        app.logger.info("Schema absent ou incomplet: bootstrap differe jusqu'apres migration.")
        return

    try:
        Utilisateur.query.limit(1).all()
    except OperationalError:
        db.session.rollback()
        app.logger.info("Schema en cours de migration: bootstrap differe jusqu'a compatibilite complete.")
        return

    utilisateurs_a_creer = [
        {
            "email": "admin@ecole.ne",
            "nom": "Admin",
            "prenom": "Plateforme",
            "role": "admin",
            "telephone": "+22700000001",
            "mot_de_passe": "admin123",
        },
        {
            "email": "prof@ecole.ne",
            "nom": "Enseignant",
            "prenom": "Demo",
            "role": "enseignant",
            "telephone": "+22700000002",
            "mot_de_passe": "prof123",
        },
        {
            "email": "eleve@edumanage.local",
            "nom": "Apprenant",
            "prenom": "Demo",
            "role": "eleve",
            "telephone": "+22700000003",
            "mot_de_passe": "eleve123",
        },
        {
            "email": "maintenance@ecole.ne",
            "nom": "Maintenance",
            "prenom": "Technique",
            "role": "maintenance",
            "telephone": "+22700000004",
            "mot_de_passe": "maintenance123",
        },
    ]

    for data in utilisateurs_a_creer:
        if Utilisateur.query.filter_by(email=data["email"]).first():
            continue

        try:
            utilisateur = Utilisateur(
                nom=data["nom"],
                prenom=data["prenom"],
                email=data["email"],
                role=data["role"],
                telephone=data["telephone"],
            )
            utilisateur.set_mot_de_passe(data["mot_de_passe"])
            db.session.add(utilisateur)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()

    try:
        from .pedagogical_seed import seed_pedagogical_content

        if not PlacementTest.query.first():
            test = PlacementTest()
            db.session.add(test)
            db.session.flush()

            qcm = PlacementQuestion(
                test_id=test.id,
                texte="Comment dit-on bonjour en arabe ?",
                type_question="qcm",
                ordre=1,
            )
            vocabulaire = PlacementQuestion(
                test_id=test.id,
                texte="Quel mot correspond au mot arabe ?????",
                type_question="qcm",
                ordre=2,
            )
            phrase = PlacementQuestion(
                test_id=test.id,
                texte="Quelle phrase est correcte ?",
                type_question="qcm",
                ordre=3,
            )
            db.session.add_all([qcm, vocabulaire, phrase])
            db.session.flush()

            db.session.add_all(
                [
                    PlacementChoix(question_id=qcm.id, texte="?????", est_correct=True),
                    PlacementChoix(question_id=qcm.id, texte="????", est_correct=False),
                    PlacementChoix(question_id=qcm.id, texte="???", est_correct=False),
                    PlacementChoix(question_id=qcm.id, texte="???", est_correct=False),
                    PlacementChoix(question_id=vocabulaire.id, texte="École", est_correct=True),
                    PlacementChoix(question_id=vocabulaire.id, texte="Livre", est_correct=False),
                    PlacementChoix(question_id=vocabulaire.id, texte="Maison", est_correct=False),
                    PlacementChoix(question_id=vocabulaire.id, texte="Voiture", est_correct=False),
                    PlacementChoix(question_id=phrase.id, texte="أنا أحب العربية", est_correct=True),
                    PlacementChoix(question_id=phrase.id, texte="أنا العربية أحب", est_correct=False),
                    PlacementChoix(question_id=phrase.id, texte="أحب العربية أنا", est_correct=False),
                    PlacementChoix(question_id=phrase.id, texte="العربية أحب أنا", est_correct=False),
                ]
            )

        vocabulaire_demo = [
            {
                "mot_arabe": "\u0645\u0631\u062d\u0628\u0627",
                "translitteration": "marhaban",
                "traduction_fr": "Bonjour",
                "traduction_en": "Hello",
                "traduction_es": "Hola",
                "audio_url": "uploads/audios/demo_marhaba.wav",
                "exemple_phrase": "\u0645\u0631\u062d\u0628\u0627 \u064a\u0627 \u0635\u062f\u064a\u0642\u064a \u0627\u0644\u0639\u0632\u064a\u0632.",
            },
            {
                "mot_arabe": "\u0645\u062f\u0631\u0633\u0629",
                "translitteration": "madrasa",
                "traduction_fr": "École",
                "traduction_en": "School",
                "traduction_es": "Escuela",
                "audio_url": "uploads/audios/demo_madrasa.wav",
                "exemple_phrase": "\u0647\u0630\u0647 \u0645\u062f\u0631\u0633\u0629 \u062c\u0645\u064a\u0644\u0629.",
            },
            {
                "mot_arabe": "\u0643\u062a\u0627\u0628",
                "translitteration": "kitab",
                "traduction_fr": "Livre",
                "traduction_en": "Book",
                "traduction_es": "Libro",
                "audio_url": "uploads/audios/demo_kitab.wav",
                "exemple_phrase": "\u0643\u062a\u0627\u0628\u064a \u0639\u0644\u0649 \u0627\u0644\u0637\u0627\u0648\u0644\u0629.",
            },
            {
                "mot_arabe": "\u0623\u0646\u0627",
                "translitteration": "ana",
                "traduction_fr": "Je / Moi",
                "traduction_en": "I / Me",
                "traduction_es": "Yo",
                "audio_url": "uploads/audios/demo_ana_uhibbu.wav",
                "exemple_phrase": "\u0623\u0646\u0627 \u0623\u062d\u0628 \u0627\u0644\u0644\u063a\u0629 \u0627\u0644\u0639\u0631\u0628\u064a\u0629.",
            },
        ]
        for data in vocabulaire_demo:
            entree = Vocabulaire.query.filter_by(mot_arabe=data["mot_arabe"]).first()
            if entree is None:
                entree = Vocabulaire(mot_arabe=data["mot_arabe"])
                db.session.add(entree)
            entree.translitteration = data["translitteration"]
            entree.traduction_fr = data["traduction_fr"]
            entree.traduction_en = data["traduction_en"]
            entree.traduction_es = data["traduction_es"]
            entree.audio_url = data["audio_url"]
            entree.exemple_phrase = data["exemple_phrase"]

        enseignant_demo = Utilisateur.query.filter_by(email="prof@ecole.ne").first()
        eleve_demo = Utilisateur.query.filter_by(email="eleve@edumanage.local").first()
        seed_demo_content = not bool(Cours.query.first())

        demo_courses = [
            {
                "nom": "Démo A1 - Salutations et alphabet",
                "description": "Un cours de démonstration pour découvrir les salutations, l'alphabet arabe et les premières formules utiles.",
                "niveau": "A1",
                "image_url": "uploads/courses/course_demo_salutations.svg",
                "est_publie": True,
                "lessons": [
                    {
                        "titre": "Dire bonjour en arabe",
                        "ordre": 1,
                        "contenu_texte": (
                            '<div class="arabic-content">'
                            '<p>مرحباً! هذه أول كلمة نبدأ بها في العربية في هذه الوحدة القصيرة.</p>'
                            '<p>اسمع الكلمة جيداً ثم كررها حتى تحفظ نطقها بسهولة.</p>'
                            '</div>'
                            "<p>Objectif de la leçon : reconnaître un mot, l'écouter, puis l'écrire correctement.</p>"
                        ),
                        "video_url": "https://www.youtube.com/watch?v=JuKD4h0CIu8",
                        "pdf_url": "uploads/pdfs/support_salutations.pdf",
                        "audio_url": "uploads/audios/demo_marhaba.wav",
                        "duree_minutes": 8,
                        "points_recompense": 15,
                        "questions": [
                            {
                                "texte": "Quel mot arabe signifie bonjour ?",
                                "ordre": 1,
                                "type_exercice": "qcm",
                                "audio_url": "uploads/audios/demo_marhaba.wav",
                                "choix": [
                                    {"texte": "\u0645\u0631\u062d\u0628\u0627", "est_correct": True, "audio_url": "uploads/audios/demo_marhaba.wav"},
                                    {"texte": "\u0643\u062a\u0627\u0628", "est_correct": False, "audio_url": "uploads/audios/demo_kitab.wav"},
                                    {"texte": "\u0645\u062f\u0631\u0633\u0629", "est_correct": False, "audio_url": "uploads/audios/demo_madrasa.wav"},
                                    {"texte": "\u0623\u0646\u0627", "est_correct": False},
                                ],
                            },
                            {
                                "texte": "Écoutez et choisissez le bon mot",
                                "ordre": 2,
                                "type_exercice": "ecoute",
                                "audio_url": "uploads/audios/demo_marhaba.wav",
                                "choix": [
                                    {"texte": "\u0645\u0631\u062d\u0628\u0627", "est_correct": True},
                                    {"texte": "\u0628\u0627\u0628", "est_correct": False},
                                    {"texte": "\u0645\u062f\u0631\u0633\u0629", "est_correct": False},
                                    {"texte": "\u0623\u0646\u0627", "est_correct": False},
                                ],
                            },
                            {
                                "texte": "Remettez la phrase dans l'ordre",
                                "ordre": 3,
                                "type_exercice": "ordre_mots",
                                "bonne_reponse_texte": "أنا أحب العربية",
                                "choix": [
                                    {"texte": "أنا أحب العربية", "est_correct": True},
                                    {"texte": "أحب العربية", "est_correct": False},
                                    {"texte": "أنا العربية", "est_correct": False},
                                    {"texte": "العربية أنا", "est_correct": False},
                                ],
                            },
                            {
                                "texte": "Dictée : écoutez et écrivez le mot",
                                "ordre": 4,
                                "type_exercice": "dictee",
                                "audio_url": "uploads/audios/demo_dictee_marhaba.wav",
                                "bonne_reponse_texte": "مرحبا",
                                "choix": [],
                            },
                        ],
                    },
                    {
                        "titre": "Se présenter simplement",
                        "ordre": 2,
                        "contenu_texte": (
                            '<div class="arabic-content">'
                            '<p>أنا طالب. أنا سعيد. هذا درس بسيط للتعارف الأول.</p>'
                            '<p>تعلم كيف تقول من أنت وماذا تحب في جملة قصيرة.</p>'
                            '</div>'
                            "<p>Dans cette leçon, on combine texte, audio et écriture pour pratiquer une présentation simple.</p>"
                        ),
                        "video_url": None,
                        "pdf_url": "uploads/pdfs/support_salutations.pdf",
                        "audio_url": "uploads/audios/demo_ana_uhibbu.wav",
                        "duree_minutes": 10,
                        "points_recompense": 20,
                        "questions": [
                            {
                                "texte": "Quel mot signifie « je » en arabe ?",
                                "ordre": 1,
                                "type_exercice": "qcm",
                                "choix": [
                                    {"texte": "أنا", "est_correct": True},
                                    {"texte": "هو", "est_correct": False},
                                    {"texte": "هي", "est_correct": False},
                                    {"texte": "نحن", "est_correct": False},
                                ],
                            },
                            {
                                "texte": "Dictée du mot central de la présentation",
                                "ordre": 2,
                                "type_exercice": "dictee",
                                "audio_url": "uploads/audios/demo_ana_uhibbu.wav",
                                "bonne_reponse_texte": "أنا",
                                "choix": [],
                            },
                        ],
                    },
                ],
            },
            {
                "nom": "Démo A2 - Vocabulaire du quotidien",
                "description": "Un contenu de démonstration sur la maison, l'école, les objets et les petites phrases utiles du quotidien.",
                "niveau": "A2",
                "image_url": "uploads/courses/course_demo_quotidien.svg",
                "est_publie": True,
                "lessons": [
                    {
                        "titre": "Objets de la classe",
                        "ordre": 1,
                        "contenu_texte": (
                            '<div class="arabic-content">'
                            '<p>في الفصل أشياء كثيرة: كتاب وقلم وسبورة وحقيبة.</p>'
                            '<p>تعلم أسماء الأدوات الأساسية التي تراها كل يوم في المدرسة.</p>'
                            '</div>'
                            "<p>Commencez par repérer les mots les plus utiles de la classe, puis associez-les à leur sens.</p>"
                        ),
                        "video_url": None,
                        "pdf_url": "uploads/pdfs/support_vocabulaire.pdf",
                        "audio_url": "uploads/audios/demo_kitab.wav",
                        "duree_minutes": 9,
                        "points_recompense": 15,
                        "questions": [
                            {
                                "texte": "Quel mot arabe signifie livre ?",
                                "ordre": 1,
                                "type_exercice": "qcm",
                                "audio_url": "uploads/audios/demo_kitab.wav",
                                "choix": [
                                    {"texte": "\u0643\u062a\u0627\u0628", "est_correct": True, "audio_url": "uploads/audios/demo_kitab.wav"},
                                    {"texte": "\u0645\u062f\u0631\u0633\u0629", "est_correct": False},
                                    {"texte": "\u0642\u0644\u0645", "est_correct": False},
                                    {"texte": "\u0633\u0628\u0648\u0631\u0629", "est_correct": False},
                                ],
                            },
                            {
                                "texte": "Écoutez et choisissez le mot école",
                                "ordre": 2,
                                "type_exercice": "ecoute",
                                "audio_url": "uploads/audios/demo_madrasa.wav",
                                "choix": [
                                    {"texte": "\u0645\u062f\u0631\u0633\u0629", "est_correct": True},
                                    {"texte": "\u0633\u0628\u0648\u0631\u0629", "est_correct": False},
                                    {"texte": "\u0642\u0644\u0645", "est_correct": False},
                                    {"texte": "\u0628\u0627\u0628", "est_correct": False},
                                ],
                            },
                        ],
                    },
                    {
                        "titre": "Maison et famille",
                        "ordre": 2,
                        "contenu_texte": (
                            '<div class="arabic-content">'
                            '<p>هذه أمي. في البيت غرفة كبيرة ومطبخ صغير.</p>'
                            '<p>تعرف على كلمات الأسرة والبيت في عبارات سهلة وواضحة.</p>'
                            '</div>'
                            "<p>Cette leçon sert à pratiquer un vocabulaire concret sur la famille et la maison avec un rendu arabe lisible.</p>"
                        ),
                        "video_url": None,
                        "pdf_url": "uploads/pdfs/support_vocabulaire.pdf",
                        "audio_url": "uploads/audios/demo_madrasa.wav",
                        "duree_minutes": 7,
                        "points_recompense": 10,
                        "questions": [
                            {
                                "texte": "Remettez les mots dans l'ordre",
                                "ordre": 1,
                                "type_exercice": "ordre_mots",
                                "bonne_reponse_texte": "هذه أمي لطيفة",
                                "choix": [
                                    {"texte": "هذه أمي لطيفة", "est_correct": True},
                                    {"texte": "أمي لطيفة", "est_correct": False},
                                    {"texte": "هذه لطيفة", "est_correct": False},
                                    {"texte": "لطيفة أمي", "est_correct": False},
                                ],
                            },
                        ],
                    },
                ],
            },
        ]

        if not seed_demo_content:
            demo_courses = []

        obsolete_demo_courses = Cours.query.filter_by(
            image_url="uploads/courses/course_demo_dictee.svg"
        ).all()
        for obsolete_course in obsolete_demo_courses:
            db.session.delete(obsolete_course)

        for course_data in demo_courses:
            cours = Cours.query.filter_by(image_url=course_data["image_url"]).first()
            if cours is None:
                cours = Cours.query.filter_by(nom=course_data["nom"]).first()
            if cours is None:
                cours = Cours(
                    nom=course_data["nom"],
                    description=course_data["description"],
                    niveau=course_data["niveau"],
                    image_url=course_data["image_url"],
                    est_publie=course_data["est_publie"],
                    enseignant_id=enseignant_demo.id if enseignant_demo else None,
                )
                db.session.add(cours)
                db.session.flush()
            else:
                cours.description = course_data["description"]
                cours.niveau = course_data["niveau"]
                cours.image_url = course_data["image_url"]
                cours.est_publie = course_data["est_publie"]
                cours.enseignant_id = enseignant_demo.id if enseignant_demo else cours.enseignant_id

            for lesson_data in course_data["lessons"]:
                lecon = Lecon.query.filter_by(cours_id=cours.id, titre=lesson_data["titre"]).first()
                if lecon is None:
                    lecon = Lecon(cours_id=cours.id, titre=lesson_data["titre"])
                    db.session.add(lecon)
                    db.session.flush()

                lecon.ordre = lesson_data["ordre"]
                lecon.contenu_texte = lesson_data["contenu_texte"]
                lecon.video_url = lesson_data["video_url"]
                lecon.pdf_url = lesson_data["pdf_url"]
                lecon.audio_url = lesson_data["audio_url"]
                lecon.duree_minutes = lesson_data["duree_minutes"]
                lecon.points_recompense = lesson_data["points_recompense"]
                lecon.est_publie = lesson_data.get("est_publie", True)

                existing_questions = {question.ordre: question for question in lecon.questions}
                desired_orders = set()
                for question_data in lesson_data["questions"]:
                    desired_orders.add(question_data["ordre"])
                    question = existing_questions.get(question_data["ordre"])
                    if question is None:
                        question = Question(
                            lecon_id=lecon.id,
                            ordre=question_data["ordre"],
                            texte=question_data["texte"],
                        )
                        db.session.add(question)
                        db.session.flush()

                    question.texte = question_data["texte"]
                    question.type_exercice = question_data["type_exercice"]
                    question.audio_url = question_data.get("audio_url")
                    question.bonne_reponse_texte = question_data.get("bonne_reponse_texte")

                    existing_choices = {choice.texte: choice for choice in question.choix}
                    desired_choice_texts = set()
                    for choice_data in question_data.get("choix", []):
                        desired_choice_texts.add(choice_data["texte"])
                        choice = existing_choices.get(choice_data["texte"])
                        if choice is None:
                            choice = Choix(question_id=question.id, texte=choice_data["texte"])
                            db.session.add(choice)
                        choice.est_correct = choice_data["est_correct"]
                        choice.audio_url = choice_data.get("audio_url")

                    for choice in list(question.choix):
                        if choice.texte not in desired_choice_texts:
                            db.session.delete(choice)

                for question in list(lecon.questions):
                    if question.ordre not in desired_orders:
                        db.session.delete(question)

        def sync_choices(question, desired_choices):
            existing_choices = sorted(question.choix, key=lambda item: item.id)
            for index, choice_data in enumerate(desired_choices):
                if index < len(existing_choices):
                    choice = existing_choices[index]
                else:
                    choice = Choix(question_id=question.id)
                    db.session.add(choice)
                    existing_choices.append(choice)
                choice.texte = choice_data["texte"]
                choice.est_correct = choice_data["est_correct"]
                choice.audio_url = choice_data.get("audio_url")

            for extra_choice in existing_choices[len(desired_choices):]:
                db.session.delete(extra_choice)

        lesson_repairs = {
            "Salutations et alphabet arabe": {
                1: {
                    "titre": "Dire bonjour en arabe",
                    "contenu_texte": (
                        '<div class="arabic-content">'
                        '<p>\u0645\u0631\u062d\u0628\u0627\u064b! \u0647\u0630\u0647 \u0623\u0648\u0644 \u0643\u0644\u0645\u0629 \u0646\u0628\u062f\u0623 \u0628\u0647\u0627 \u0641\u064a \u0627\u0644\u0639\u0631\u0628\u064a\u0629 \u0641\u064a \u0647\u0630\u0647 \u0627\u0644\u0648\u062d\u062f\u0629 \u0627\u0644\u0642\u0635\u064a\u0631\u0629.</p>'
                        '<p>\u0627\u0633\u0645\u0639 \u0627\u0644\u0643\u0644\u0645\u0629 \u062c\u064a\u062f\u0627\u064b \u062b\u0645 \u0643\u0631\u0631\u0647\u0627 \u062d\u062a\u0649 \u062a\u062d\u0641\u0638 \u0646\u0637\u0642\u0647\u0627 \u0628\u0633\u0647\u0648\u0644\u0629.</p>'
                        '</div>'
                        "<p>Objectif de la le\u00e7on : reconna\u00eetre un mot, l'\u00e9couter, puis l'\u00e9crire correctement.</p>"
                    ),
                    "questions": {
                        1: {
                            "texte": "Quel mot arabe signifie bonjour ?",
                            "type_exercice": "qcm",
                            "audio_url": "uploads/audios/demo_marhaba.wav",
                            "bonne_reponse_texte": None,
                            "choix": [
                                {"texte": "\u0645\u0631\u062d\u0628\u0627", "est_correct": True, "audio_url": "uploads/audios/demo_marhaba.wav"},
                                {"texte": "\u0643\u062a\u0627\u0628", "est_correct": False, "audio_url": "uploads/audios/demo_kitab.wav"},
                                {"texte": "\u0645\u062f\u0631\u0633\u0629", "est_correct": False, "audio_url": "uploads/audios/demo_madrasa.wav"},
                                {"texte": "\u0623\u0646\u0627", "est_correct": False},
                            ],
                        },
                        2: {
                            "texte": "\u00c9coutez et choisissez le bon mot",
                            "type_exercice": "ecoute",
                            "audio_url": "uploads/audios/demo_marhaba.wav",
                            "bonne_reponse_texte": None,
                            "choix": [
                                {"texte": "\u0645\u0631\u062d\u0628\u0627", "est_correct": True},
                                {"texte": "\u0628\u0627\u0628", "est_correct": False},
                                {"texte": "\u0645\u062f\u0631\u0633\u0629", "est_correct": False},
                                {"texte": "\u0623\u0646\u0627", "est_correct": False},
                            ],
                        },
                        3: {
                            "texte": "Remettez la phrase dans l'ordre",
                            "type_exercice": "ordre_mots",
                            "audio_url": None,
                            "bonne_reponse_texte": "\u0623\u0646\u0627 \u0623\u062d\u0628 \u0627\u0644\u0639\u0631\u0628\u064a\u0629",
                            "choix": [
                                {"texte": "\u0623\u0646\u0627 \u0623\u062d\u0628 \u0627\u0644\u0639\u0631\u0628\u064a\u0629", "est_correct": True},
                                {"texte": "\u0623\u062d\u0628 \u0627\u0644\u0639\u0631\u0628\u064a\u0629", "est_correct": False},
                                {"texte": "\u0623\u0646\u0627 \u0627\u0644\u0639\u0631\u0628\u064a\u0629", "est_correct": False},
                                {"texte": "\u0627\u0644\u0639\u0631\u0628\u064a\u0629 \u0623\u0646\u0627", "est_correct": False},
                            ],
                        },
                        4: {
                            "texte": "Dict\u00e9e : \u00e9coutez et \u00e9crivez le mot",
                            "type_exercice": "dictee",
                            "audio_url": "uploads/audios/demo_dictee_marhaba.wav",
                            "bonne_reponse_texte": "\u0645\u0631\u062d\u0628\u0627",
                            "choix": [],
                        },
                    },
                },
                2: {
                    "titre": "Se pr\u00e9senter simplement",
                    "contenu_texte": (
                        '<div class="arabic-content">'
                        '<p>\u0623\u0646\u0627 \u0623\u062d\u0645\u062f. \u0623\u0646\u0627 \u0637\u0627\u0644\u0628. \u0623\u0646\u0627 \u0623\u062d\u0628 \u0627\u0644\u0639\u0631\u0628\u064a\u0629.</p>'
                        '<p>\u062a\u0639\u0644\u0645 \u0643\u064a\u0641 \u062a\u0642\u0648\u0644 \u0645\u0646 \u0623\u0646\u062a \u0648\u0645\u0627\u0630\u0627 \u062a\u062d\u0628 \u0641\u064a \u062c\u0645\u0644\u0629 \u0642\u0635\u064a\u0631\u0629.</p>'
                        '</div>'
                        "<p>Cette le\u00e7on combine texte, audio et \u00e9criture pour pratiquer une pr\u00e9sentation simple.</p>"
                    ),
                    "questions": {
                        1: {
                            "texte": "Quel mot signifie \u00ab je \u00bb en arabe ?",
                            "type_exercice": "qcm",
                            "audio_url": None,
                            "bonne_reponse_texte": None,
                            "choix": [
                                {"texte": "\u0623\u0646\u0627", "est_correct": True},
                                {"texte": "\u0647\u0648", "est_correct": False},
                                {"texte": "\u0647\u064a", "est_correct": False},
                                {"texte": "\u0646\u062d\u0646", "est_correct": False},
                            ],
                        },
                        2: {
                            "texte": "Dict\u00e9e du mot central de la pr\u00e9sentation",
                            "type_exercice": "dictee",
                            "audio_url": "uploads/audios/demo_ana_uhibbu.wav",
                            "bonne_reponse_texte": "\u0623\u0646\u0627",
                            "choix": [],
                        },
                    },
                },
            },
            "Vocabulaire du quotidien": {
                1: {
                    "titre": "Objets de la classe",
                    "contenu_texte": (
                        '<div class="arabic-content">'
                        '<p>\u0641\u064a \u0627\u0644\u0641\u0635\u0644 \u0623\u0634\u064a\u0627\u0621 \u0643\u062b\u064a\u0631\u0629: \u0643\u062a\u0627\u0628 \u0648\u0642\u0644\u0645 \u0648\u0633\u0628\u0648\u0631\u0629 \u0648\u062d\u0642\u064a\u0628\u0629.</p>'
                        '<p>\u062a\u0639\u0644\u0645 \u0623\u0633\u0645\u0627\u0621 \u0627\u0644\u0623\u062f\u0648\u0627\u062a \u0627\u0644\u0623\u0633\u0627\u0633\u064a\u0629 \u0627\u0644\u062a\u064a \u062a\u0631\u0627\u0647\u0627 \u0643\u0644 \u064a\u0648\u0645 \u0641\u064a \u0627\u0644\u0645\u062f\u0631\u0633\u0629.</p>'
                        '</div>'
                        "<p>Commencez par rep\u00e9rer les mots les plus utiles de la classe, puis associez-les \u00e0 leur sens.</p>"
                    ),
                    "questions": {
                        1: {
                            "texte": "Quel mot arabe signifie livre ?",
                            "type_exercice": "qcm",
                            "audio_url": "uploads/audios/demo_kitab.wav",
                            "bonne_reponse_texte": None,
                            "choix": [
                                {"texte": "\u0643\u062a\u0627\u0628", "est_correct": True, "audio_url": "uploads/audios/demo_kitab.wav"},
                                {"texte": "\u0645\u062f\u0631\u0633\u0629", "est_correct": False},
                                {"texte": "\u0642\u0644\u0645", "est_correct": False},
                                {"texte": "\u0633\u0628\u0648\u0631\u0629", "est_correct": False},
                            ],
                        },
                        2: {
                            "texte": "\u00c9coutez et choisissez le mot \u00e9cole",
                            "type_exercice": "ecoute",
                            "audio_url": "uploads/audios/demo_madrasa.wav",
                            "bonne_reponse_texte": None,
                            "choix": [
                                {"texte": "\u0645\u062f\u0631\u0633\u0629", "est_correct": True},
                                {"texte": "\u0633\u0628\u0648\u0631\u0629", "est_correct": False},
                                {"texte": "\u0642\u0644\u0645", "est_correct": False},
                                {"texte": "\u0628\u0627\u0628", "est_correct": False},
                            ],
                        },
                    },
                },
                2: {
                    "titre": "Maison et famille",
                    "contenu_texte": (
                        '<div class="arabic-content">'
                        '<p>\u0647\u0630\u0647 \u0623\u0645\u064a. \u0641\u064a \u0627\u0644\u0628\u064a\u062a \u063a\u0631\u0641\u0629 \u0643\u0628\u064a\u0631\u0629 \u0648\u0645\u0637\u0628\u062e \u0635\u063a\u064a\u0631.</p>'
                        '<p>\u062a\u0639\u0631\u0641 \u0639\u0644\u0649 \u0643\u0644\u0645\u0627\u062a \u0627\u0644\u0623\u0633\u0631\u0629 \u0648\u0627\u0644\u0628\u064a\u062a \u0641\u064a \u0639\u0628\u0627\u0631\u0627\u062a \u0633\u0647\u0644\u0629 \u0648\u0648\u0627\u0636\u062d\u0629.</p>'
                        '</div>'
                        "<p>Cette le\u00e7on sert \u00e0 pratiquer un vocabulaire concret sur la famille et la maison avec un rendu arabe lisible.</p>"
                    ),
                    "questions": {
                        1: {
                            "texte": "Remettez les mots dans l'ordre",
                            "type_exercice": "ordre_mots",
                            "audio_url": None,
                            "bonne_reponse_texte": "\u0647\u0630\u0647 \u0623\u0645\u064a \u0644\u0637\u064a\u0641\u0629",
                            "choix": [
                                {"texte": "\u0647\u0630\u0647 \u0623\u0645\u064a \u0644\u0637\u064a\u0641\u0629", "est_correct": True},
                                {"texte": "\u0623\u0645\u064a \u0644\u0637\u064a\u0641\u0629", "est_correct": False},
                                {"texte": "\u0647\u0630\u0647 \u0644\u0637\u064a\u0641\u0629", "est_correct": False},
                                {"texte": "\u0644\u0637\u064a\u0641\u0629 \u0623\u0645\u064a", "est_correct": False},
                            ],
                        },
                    },
                },
            },
        }

        for course_name, lessons_by_order in lesson_repairs.items():
            cours = Cours.query.filter_by(nom=course_name).first()
            if cours is None:
                continue

            lessons_by_existing_order = {lesson.ordre: lesson for lesson in cours.lecons}
            for lesson_order, lesson_data in lessons_by_order.items():
                lecon = lessons_by_existing_order.get(lesson_order)
                if lecon is None:
                    continue

                lecon.titre = lesson_data["titre"]
                lecon.contenu_texte = lesson_data["contenu_texte"]
                lecon.est_publie = True

                existing_questions = {question.ordre: question for question in lecon.questions}
                for question_order, question_data in lesson_data["questions"].items():
                    question = existing_questions.get(question_order)
                    if question is None:
                        continue

                    question.texte = question_data["texte"]
                    question.type_exercice = question_data["type_exercice"]
                    question.audio_url = question_data["audio_url"]
                    question.bonne_reponse_texte = question_data["bonne_reponse_texte"]
                    sync_choices(question, question_data["choix"])

        if eleve_demo:
            if not ResultatPlacement.query.filter_by(eleve_id=eleve_demo.id).first():
                db.session.add(
                    ResultatPlacement(
                        eleve_id=eleve_demo.id,
                        niveau_assigne="A1",
                        score=0.55,
                        a_passe=True,
                    )
                )
                eleve_demo.niveau_depart = "A1"

            demo_image_urls = [course["image_url"] for course in demo_courses]
            demo_courses_for_enrollment = Cours.query.filter(Cours.image_url.in_(demo_image_urls)).all()
            for cours in demo_courses_for_enrollment:
                inscription = Inscription.query.filter_by(
                    eleve_id=eleve_demo.id,
                    cours_id=cours.id,
                ).first()
                if inscription is None:
                    db.session.add(Inscription(eleve_id=eleve_demo.id, cours_id=cours.id, est_active=True))
                else:
                    inscription.est_active = True

            xp = XPEleve.get_or_create(eleve_demo.id)
            if xp.points == 0 and xp.total_lecons_terminees == 0:
                xp.points = 35
                xp.streak_jours = 2
                xp.total_lecons_terminees = 1

            first_course = Cours.query.filter_by(image_url="uploads/courses/course_demo_salutations.svg").first()
            if first_course and first_course.lecons:
                first_lesson = first_course.lecons[0]
                progression = ProgressionLecon.query.filter_by(
                    eleve_id=eleve_demo.id,
                    lecon_id=first_lesson.id,
                ).first()
                if progression is None:
                    progression = ProgressionLecon(
                        eleve_id=eleve_demo.id,
                        lecon_id=first_lesson.id,
                        vue=True,
                        date_vue=datetime.utcnow(),
                        score_quiz=0.75,
                    )
                    db.session.add(progression)
                else:
                    progression.vue = True
                    progression.date_vue = progression.date_vue or datetime.utcnow()
                    progression.score_quiz = progression.score_quiz if progression.score_quiz is not None else 0.75

        if seed_demo_content:
            seed_pedagogical_content(enseignant_demo=enseignant_demo)

        db.session.commit()
    except IntegrityError:
        db.session.rollback()

def create_app():
    app = Flask(__name__, instance_path=Config.INSTANCE_DIR)
    app.config.from_object(Config)
    app.config["STARTED_AT"] = datetime.utcnow()
    app.config["GOOGLE_OAUTH_ENABLED"] = bool(
        make_google_blueprint
        and app.config.get("GOOGLE_OAUTH_CLIENT_ID")
        and app.config.get("GOOGLE_OAUTH_CLIENT_SECRET")
    )

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "main.login"
    login_manager.login_message = "Veuillez vous connecter pour acceder a cette page."
    login_manager.login_message_category = "warning"
    migrate.init_app(app, db)
    csrf.init_app(app)
    babel.init_app(app, locale_selector=get_locale)

    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config["MEDIA_ROOT"], exist_ok=True)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    os.makedirs(app.config["COURSE_IMAGE_FOLDER"], exist_ok=True)
    os.makedirs(app.config["LESSON_IMAGE_FOLDER"], exist_ok=True)
    os.makedirs(app.config["AUDIO_FOLDER"], exist_ok=True)
    os.makedirs(app.config["PROFILE_IMAGE_FOLDER"], exist_ok=True)
    os.makedirs(app.config["PIPER_CACHE_FOLDER"], exist_ok=True)
    os.makedirs(app.config["BACKUP_FOLDER"], exist_ok=True)
    os.makedirs(app.config["CACHE_DIR"], exist_ok=True)
    _migrate_legacy_uploads(app)

    setup_logging(app)
    _warn_for_dev_secrets(app)

    from .models import Utilisateur

    @login_manager.user_loader
    def load_user(user_id):
        return Utilisateur.query.get(int(user_id))

    @app.before_request
    def inject_request_identity():
        from .maintenance_tools import (
            ACTIVE_SESSION_TOKEN_KEY,
            get_active_session_by_token,
            get_site_maintenance_payload,
            is_site_maintenance_enabled,
            touch_active_session,
        )

        has_session_user = bool(session.get("_user_id"))
        if getattr(current_user, "is_authenticated", False) and not has_session_user:
            logout_user()

        if has_session_user and getattr(current_user, "is_authenticated", False):
            if current_user.role not in Utilisateur.ROLES_VALIDES:
                app.logger.warning(
                    "Role utilisateur invalide detecte pour user_id=%s: %s",
                    current_user.id,
                    current_user.role,
                )
                logout_user()
                session.clear()
                return current_app.login_manager.unauthorized()

            stored_role = session.get("role")
            if stored_role and stored_role != current_user.role:
                app.logger.warning(
                    "Role de session corrige pour user_id=%s: %s -> %s",
                    current_user.id,
                    stored_role,
                    current_user.role,
                )
            session["role"] = current_user.role
            g.role = current_user.role
            g.user_id = current_user.id
            session_token = session.get(ACTIVE_SESSION_TOKEN_KEY)
            session_record = get_active_session_by_token(session_token)
            if session_record is None or session_record.user_id != current_user.id or not session_record.is_active:
                logout_user()
                session.clear()
                return current_app.login_manager.unauthorized()

            if touch_active_session(session_record):
                db.session.commit()

            if (
                is_site_maintenance_enabled()
                and current_user.role not in {"admin", "maintenance"}
                and (request.endpoint or "") not in MAINTENANCE_MODE_EXEMPT_ENDPOINTS
            ):
                payload = get_site_maintenance_payload()
                return render_template("maintenance/site_maintenance_public.html", maintenance_payload=payload), 503

            from .routes import maybe_redirect_new_student_to_onboarding

            redirect_response = maybe_redirect_new_student_to_onboarding()
            if redirect_response is not None:
                return redirect_response
        else:
            session.pop("role", None)
            g.role = "ANONYMOUS"
            g.user_id = 0

        if is_site_maintenance_enabled():
            endpoint = request.endpoint or ""
            user_role = getattr(current_user, "role", None) if getattr(current_user, "is_authenticated", False) else None
            if endpoint not in MAINTENANCE_MODE_EXEMPT_ENDPOINTS and user_role not in {"admin", "maintenance"}:
                payload = get_site_maintenance_payload()
                return render_template("maintenance/site_maintenance_public.html", maintenance_payload=payload), 503

    @app.after_request
    def add_no_cache_headers(response):
        if response.status_code >= 500:
            try:
                db.session.rollback()
                from .utils import log_action

                log_action(
                    module="http",
                    action=f"HTTP {response.status_code}",
                    level="ERROR",
                    details=f"{request.method} {request.path}",
                )
            except Exception:
                db.session.rollback()
        return apply_route_cache_policy(response, request.path)

    @app.context_processor
    def inject_language_context():
        from .maintenance_tools import get_site_maintenance_payload

        current_locale = get_locale()
        current_text_direction = get_language_direction(current_locale)
        language_options = build_language_options(current_locale)
        current_language = language_options[0]
        for option in language_options:
            if option["is_active"]:
                current_language = option
                break

        notification_summary = {"unread_count": 0, "items": []}
        notification_stream_enabled = False
        if getattr(current_user, "is_authenticated", False):
            try:
                from .notification_center import count_unread_notifications, list_notifications_for_user, serialize_notification

                items = list_notifications_for_user(current_user.id, limit=5)
                notification_summary = {
                    "unread_count": count_unread_notifications(current_user.id),
                    "items": [serialize_notification(item) for item in items],
                }
                notification_stream_enabled = getattr(current_user, "role", None) == "eleve"
            except Exception:
                db.session.rollback()

        return {
            "available_languages": app.config.get("LANGUAGES", {}),
            "current_locale": current_locale,
            "current_text_direction": current_text_direction,
            "current_language": current_language,
            "language_options": language_options,
            "google_oauth_enabled": app.config.get("GOOGLE_OAUTH_ENABLED", False),
            "asset_version": app.config.get("ASSET_VERSION", app.config.get("VERSION", "dev")),
            "asset_url": build_asset_url,
            "notification_summary": notification_summary,
            "notification_stream_enabled": notification_stream_enabled,
            "site_maintenance_mode": get_site_maintenance_payload(),
        }

    from .admin import admin_bp
    from .blueprints.courses import courses_bp
    from .blueprints.lessons import lessons_bp
    from .blueprints.maintenance import maintenance_bp
    from .blueprints.notifications import notifications_bp
    from .blueprints.placement import placement_bp
    from .blueprints.progress import progress_bp
    from .blueprints.quiz import quiz_bp
    from .routes import main
    from .scheduler import init_scheduler

    app.register_blueprint(main)
    if app.config["GOOGLE_OAUTH_ENABLED"]:
        google_bp = make_google_blueprint(
            client_id=app.config["GOOGLE_OAUTH_CLIENT_ID"],
            client_secret=app.config["GOOGLE_OAUTH_CLIENT_SECRET"],
            scope=["profile", "email"],
            redirect_to="main.google_callback",
        )
        app.register_blueprint(google_bp, url_prefix="/auth")
    app.register_blueprint(admin_bp)
    app.register_blueprint(courses_bp, url_prefix="/courses")
    app.register_blueprint(lessons_bp, url_prefix="/lecons")
    app.register_blueprint(maintenance_bp, url_prefix="/maintenance")
    app.register_blueprint(notifications_bp, url_prefix="/notifications")
    app.register_blueprint(placement_bp, url_prefix="/placement")
    app.register_blueprint(quiz_bp, url_prefix="/quiz")
    app.register_blueprint(progress_bp, url_prefix="/progression")

    with app.app_context():
        _ensure_user_language_schema(app)
        _ensure_support_reset_schema()
        _ensure_maintenance_ops_schema()
        _ensure_student_onboarding_schema()
        _ensure_learning_content_i18n_schema(app)
        _ensure_lesson_images_schema(app)
        _bootstrap_runtime_data(app)
        try:
            from .notification_center import ensure_notification_defaults

            ensure_notification_defaults()
        except OperationalError:
            db.session.rollback()
            app.logger.info("Initialisation des notifications differee: schema en attente.")

    init_scheduler(app)

    return app
