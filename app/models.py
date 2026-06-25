import json
from datetime import date, datetime, timedelta

from flask_login import UserMixin
from sqlalchemy.orm import validates
from werkzeug.security import check_password_hash, generate_password_hash

from . import db
from .i18n import DEFAULT_LANGUAGE_CODE, SUPPORTED_LANGUAGE_CODES, normalize_language_code


def _unique_language_priority(*language_codes):
    ordered = []
    for code in language_codes:
        normalized = normalize_language_code(code)
        if normalized and normalized not in ordered:
            ordered.append(normalized)
    return ordered


class Utilisateur(db.Model, UserMixin):
    __tablename__ = "utilisateur"

    ROLES_VALIDES = ["admin", "enseignant", "eleve", "maintenance"]
    __table_args__ = (
        db.CheckConstraint(
            "role IN ('admin', 'enseignant', 'eleve', 'maintenance')",
            name="ck_utilisateur_role_valide",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(100), nullable=False)
    prenom = db.Column(db.String(100))
    email = db.Column(db.String(120), unique=True, nullable=False)
    mot_de_passe = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="eleve")
    telephone = db.Column(db.String(20))
    langue_preferee = db.Column(db.String(5))
    statut = db.Column(db.String(20), default="actif")
    niveau_depart = db.Column(db.String(5))
    date_creation = db.Column(db.DateTime, default=datetime.utcnow)
    dernier_acces = db.Column(db.DateTime)

    cours_enseignes = db.relationship(
        "Cours",
        back_populates="enseignant_utilisateur",
        lazy=True,
        foreign_keys="Cours.enseignant_id",
    )
    inscriptions_cours = db.relationship(
        "Inscription",
        back_populates="eleve",
        lazy=True,
        cascade="all, delete-orphan",
        foreign_keys="Inscription.eleve_id",
    )
    reponses_quiz = db.relationship(
        "ReponseEleve",
        back_populates="eleve",
        lazy=True,
        cascade="all, delete-orphan",
        foreign_keys="ReponseEleve.eleve_id",
    )
    progressions_lecons = db.relationship(
        "ProgressionLecon",
        back_populates="eleve",
        lazy=True,
        cascade="all, delete-orphan",
        foreign_keys="ProgressionLecon.eleve_id",
    )
    xp = db.relationship(
        "XPEleve",
        back_populates="eleve",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    push_subscriptions = db.relationship(
        "PushSubscription",
        back_populates="eleve",
        lazy=True,
        cascade="all, delete-orphan",
        foreign_keys="PushSubscription.eleve_id",
    )
    notifications = db.relationship(
        "Notification",
        back_populates="user",
        lazy=True,
        cascade="all, delete-orphan",
        foreign_keys="Notification.user_id",
    )
    notification_preferences = db.relationship(
        "NotificationPreference",
        back_populates="user",
        lazy=True,
        cascade="all, delete-orphan",
        foreign_keys="NotificationPreference.user_id",
    )
    support_thread = db.relationship(
        "SupportThread",
        back_populates="student",
        uselist=False,
        cascade="all, delete-orphan",
        foreign_keys="SupportThread.student_id",
    )
    support_messages = db.relationship(
        "SupportMessage",
        back_populates="author_user",
        lazy=True,
        foreign_keys="SupportMessage.author_user_id",
    )
    password_reset_grants = db.relationship(
        "PasswordResetGrant",
        back_populates="student",
        lazy=True,
        foreign_keys="PasswordResetGrant.student_id",
    )
    granted_password_reset_grants = db.relationship(
        "PasswordResetGrant",
        back_populates="granted_by_admin",
        lazy=True,
        foreign_keys="PasswordResetGrant.granted_by_admin_id",
    )
    earned_badges = db.relationship(
        "UserBadge",
        back_populates="user",
        lazy=True,
        cascade="all, delete-orphan",
        foreign_keys="UserBadge.user_id",
    )
    resultat_placement = db.relationship(
        "ResultatPlacement",
        back_populates="eleve",
        uselist=False,
        cascade="all, delete-orphan",
    )
    onboarding_profile = db.relationship(
        "StudentOnboardingProfile",
        back_populates="eleve",
        uselist=False,
        cascade="all, delete-orphan",
    )
    logs = db.relationship("Log", back_populates="utilisateur", lazy=True)
    active_sessions = db.relationship(
        "ActiveSession",
        back_populates="user",
        lazy=True,
        cascade="all, delete-orphan",
        foreign_keys="ActiveSession.user_id",
    )

    @validates("role")
    def validate_role(self, key, value):
        role = (value or "").strip().lower()
        if role not in self.ROLES_VALIDES:
            raise ValueError(f"Role invalide: {value}")
        return role

    @validates("langue_preferee")
    def validate_langue_preferee(self, key, value):
        if value is None:
            return None

        raw_value = (value or "").strip()
        if not raw_value:
            return None

        langue = normalize_language_code(raw_value)
        if langue is None:
            allowed = ", ".join(SUPPORTED_LANGUAGE_CODES)
            raise ValueError(f"Langue invalide: {value}. Autorisees: {allowed}")
        return langue

    @property
    def nom_complet(self):
        return f"{self.prenom or ''} {self.nom}".strip() or self.email

    def set_mot_de_passe(self, mot_de_passe_plain):
        self.mot_de_passe = generate_password_hash(mot_de_passe_plain)

    def check_mot_de_passe(self, mot_de_passe_plain):
        return check_password_hash(self.mot_de_passe, mot_de_passe_plain)

    def is_admin(self):
        return self.role == "admin"

    def __repr__(self):
        return f"<Utilisateur {self.email}>"

    def to_dict(self):
        return {
            "id": self.id,
            "nom": self.nom,
            "prenom": self.prenom,
            "email": self.email,
            "role": self.role,
            "telephone": self.telephone,
            "langue_preferee": self.langue_preferee,
            "statut": self.statut,
            "niveau_depart": self.niveau_depart,
            "date_creation": self.date_creation.isoformat() if self.date_creation else None,
            "dernier_acces": self.dernier_acces.isoformat() if self.dernier_acces else None,
        }


class StudentOnboardingProfile(db.Model):
    __tablename__ = "student_onboarding_profile"

    id = db.Column(db.Integer, primary_key=True)
    eleve_id = db.Column(
        db.Integer,
        db.ForeignKey("utilisateur.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    preferred_name = db.Column(db.String(120), nullable=False)
    genre = db.Column(db.String(30), nullable=False)
    age_range = db.Column(db.String(20), nullable=False)
    arabic_background = db.Column(db.String(40), nullable=False)
    learning_language = db.Column(db.String(5), nullable=False)
    studied_before = db.Column(db.String(40), nullable=False)
    current_level = db.Column(db.String(80), nullable=False)
    learning_goal = db.Column(db.String(40), nullable=False)
    daily_commitment = db.Column(db.String(30), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    eleve = db.relationship("Utilisateur", back_populates="onboarding_profile", foreign_keys=[eleve_id])

    @validates("learning_language")
    def validate_learning_language(self, key, value):
        normalized = normalize_language_code(value)
        if normalized is None:
            allowed = ", ".join(SUPPORTED_LANGUAGE_CODES)
            raise ValueError(f"Langue invalide: {value}. Autorisees: {allowed}")
        return normalized

    def __repr__(self):
        return f"<StudentOnboardingProfile eleve={self.eleve_id}>"


class Cours(db.Model):
    __tablename__ = "cours"

    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    coefficient = db.Column(db.Float, default=1.0)
    niveau = db.Column(db.String(10), nullable=False, default="A1")
    image_url = db.Column(db.String(255))
    est_publie = db.Column(db.Boolean, default=False)
    date_creation = db.Column(db.DateTime, default=datetime.utcnow)
    enseignant_id = db.Column(db.Integer, db.ForeignKey("utilisateur.id"))

    enseignant_utilisateur = db.relationship(
        "Utilisateur",
        back_populates="cours_enseignes",
        foreign_keys=[enseignant_id],
    )
    lecons = db.relationship(
        "Lecon",
        back_populates="cours",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="Lecon.ordre",
    )
    inscriptions = db.relationship(
        "Inscription",
        back_populates="cours",
        lazy=True,
        cascade="all, delete-orphan",
    )
    traductions = db.relationship(
        "CoursTraduction",
        back_populates="cours",
        lazy="selectin",
        cascade="all, delete-orphan",
    )

    @property
    def enseignant_nom(self):
        if self.enseignant_utilisateur:
            return self.enseignant_utilisateur.nom_complet
        return "Non assigne"

    def get_progression_eleve(self, eleve_id):
        lessons = list(self.lecons)
        total = len(lessons)
        if total == 0:
            return 0
        vues = (
            ProgressionLecon.query.filter_by(eleve_id=eleve_id, vue=True)
            .filter(ProgressionLecon.lecon_id.in_([lecon.id for lecon in lessons]))
            .count()
        )
        return round((vues / total) * 100)

    def __repr__(self):
        return f"<Cours {self.nom}>"

    def get_display_content(self, language_code=None, *, force_arabic=False):
        requested = "ar" if force_arabic else (normalize_language_code(language_code) or DEFAULT_LANGUAGE_CODE)
        priority = _unique_language_priority(requested, "ar", DEFAULT_LANGUAGE_CODE, "en", "es")
        translations_by_language = {entry.langue: entry for entry in (self.traductions or [])}

        for code in priority:
            entry = translations_by_language.get(code)
            if entry and (entry.nom or entry.description):
                return {
                    "nom": entry.nom or self.nom or "",
                    "description": entry.description,
                    "langue": code,
                }

        for entry in self.traductions or []:
            if entry.nom or entry.description:
                return {
                    "nom": entry.nom or self.nom or "",
                    "description": entry.description,
                    "langue": entry.langue,
                }

        return {
            "nom": self.nom or "",
            "description": self.description,
            "langue": None,
        }

    def get_gallery_cover_image_url(self):
        return self.image_url

    def get_gallery_image_urls(self):
        return [self.image_url] if self.image_url else []

    def to_dict(self):
        return {
            "id": self.id,
            "nom": self.nom,
            "description": self.description,
            "coefficient": self.coefficient,
            "niveau": self.niveau,
            "image_url": self.image_url,
            "est_publie": self.est_publie,
            "date_creation": self.date_creation.isoformat() if self.date_creation else None,
            "enseignant_id": self.enseignant_id,
        }


class Lecon(db.Model):
    __tablename__ = "lecon"

    id = db.Column(db.Integer, primary_key=True)
    cours_id = db.Column(db.Integer, db.ForeignKey("cours.id"), nullable=False)
    titre = db.Column(db.String(200), nullable=False)
    ordre = db.Column(db.Integer, default=0)
    contenu_texte = db.Column(db.Text)
    video_url = db.Column(db.String(500))
    pdf_url = db.Column(db.String(500))
    audio_url = db.Column(db.String(500))
    duree_minutes = db.Column(db.Integer)
    points_recompense = db.Column(db.Integer, default=10)
    est_publie = db.Column(db.Boolean, default=False, nullable=False)
    date_publication = db.Column(db.DateTime)
    date_creation = db.Column(db.DateTime, default=datetime.utcnow)

    cours = db.relationship("Cours", back_populates="lecons")
    questions = db.relationship(
        "Question",
        back_populates="lecon",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="Question.ordre",
    )
    progressions = db.relationship(
        "ProgressionLecon",
        back_populates="lecon",
        lazy=True,
        cascade="all, delete-orphan",
    )
    traductions = db.relationship(
        "LeconTraduction",
        back_populates="lecon",
        lazy="selectin",
        cascade="all, delete-orphan",
    )
    images = db.relationship(
        "LeconImage",
        back_populates="lecon",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="LeconImage.ordre",
    )

    def __repr__(self):
        return f"<Lecon {self.titre}>"

    def get_display_content(self, language_code=None, *, force_arabic=False):
        requested = "ar" if force_arabic else (normalize_language_code(language_code) or DEFAULT_LANGUAGE_CODE)
        priority = _unique_language_priority(requested, "ar", DEFAULT_LANGUAGE_CODE, "en", "es")
        translations_by_language = {entry.langue: entry for entry in (self.traductions or [])}

        for code in priority:
            entry = translations_by_language.get(code)
            if entry and (entry.titre or entry.contenu_texte):
                return {
                    "titre": entry.titre or self.titre or "",
                    "contenu_texte": entry.contenu_texte,
                    "langue": code,
                }

        for entry in self.traductions or []:
            if entry.titre or entry.contenu_texte:
                return {
                    "titre": entry.titre or self.titre or "",
                    "contenu_texte": entry.contenu_texte,
                    "langue": entry.langue,
                }

        return {
            "titre": self.titre or "",
            "contenu_texte": self.contenu_texte,
            "langue": None,
        }

    def get_ordered_images(self):
        return sorted(self.images or [], key=lambda image: (image.ordre or 0, image.id))

    def get_gallery_image_urls(self):
        return [image.image_url for image in self.get_ordered_images() if image.image_url]


class LeconImage(db.Model):
    __tablename__ = "lecon_image"

    id = db.Column(db.Integer, primary_key=True)
    lecon_id = db.Column(db.Integer, db.ForeignKey("lecon.id", ondelete="CASCADE"), nullable=False, index=True)
    image_url = db.Column(db.String(500), nullable=False)
    audio_url = db.Column(db.String(500))
    caption = db.Column(db.String(255))
    ordre = db.Column(db.Integer, default=0, nullable=False)
    date_creation = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    lecon = db.relationship("Lecon", back_populates="images")

    def __repr__(self):
        return f"<LeconImage lecon={self.lecon_id} ordre={self.ordre}>"


class CoursTraduction(db.Model):
    __tablename__ = "cours_traduction"

    id = db.Column(db.Integer, primary_key=True)
    cours_id = db.Column(db.Integer, db.ForeignKey("cours.id", ondelete="CASCADE"), nullable=False, index=True)
    langue = db.Column(db.String(5), nullable=False)
    nom = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    date_creation = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    date_mise_a_jour = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("cours_id", "langue", name="uq_cours_traduction_langue"),
    )

    cours = db.relationship("Cours", back_populates="traductions")

    @validates("langue")
    def validate_langue(self, key, value):
        normalized = normalize_language_code(value)
        if normalized is None:
            allowed = ", ".join(SUPPORTED_LANGUAGE_CODES)
            raise ValueError(f"Langue invalide: {value}. Autorisees: {allowed}")
        return normalized

    def __repr__(self):
        return f"<CoursTraduction cours={self.cours_id} langue={self.langue}>"


class LeconTraduction(db.Model):
    __tablename__ = "lecon_traduction"

    id = db.Column(db.Integer, primary_key=True)
    lecon_id = db.Column(db.Integer, db.ForeignKey("lecon.id", ondelete="CASCADE"), nullable=False, index=True)
    langue = db.Column(db.String(5), nullable=False)
    titre = db.Column(db.String(200), nullable=False)
    contenu_texte = db.Column(db.Text)
    date_creation = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    date_mise_a_jour = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("lecon_id", "langue", name="uq_lecon_traduction_langue"),
    )

    lecon = db.relationship("Lecon", back_populates="traductions")

    @validates("langue")
    def validate_langue(self, key, value):
        normalized = normalize_language_code(value)
        if normalized is None:
            allowed = ", ".join(SUPPORTED_LANGUAGE_CODES)
            raise ValueError(f"Langue invalide: {value}. Autorisees: {allowed}")
        return normalized

    def __repr__(self):
        return f"<LeconTraduction lecon={self.lecon_id} langue={self.langue}>"


class Question(db.Model):
    __tablename__ = "question"

    TYPES_EXERCICE = (
        "qcm",
        "ordre_mots",
        "ecoute",
        "dictee",
        "speaking",
    )

    id = db.Column(db.Integer, primary_key=True)
    lecon_id = db.Column(db.Integer, db.ForeignKey("lecon.id"), nullable=False)
    texte = db.Column(db.Text, nullable=False)
    ordre = db.Column(db.Integer, default=0)
    type_exercice = db.Column(db.String(30), default="qcm")
    audio_url = db.Column(db.String(500))
    bonne_reponse_texte = db.Column(db.String(500))

    lecon = db.relationship("Lecon", back_populates="questions")
    choix = db.relationship(
        "Choix",
        back_populates="question",
        lazy=True,
        cascade="all, delete-orphan",
    )
    reponses = db.relationship(
        "ReponseEleve",
        back_populates="question",
        lazy=True,
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<Question {self.id} lecon={self.lecon_id}>"


class Choix(db.Model):
    __tablename__ = "choix"

    id = db.Column(db.Integer, primary_key=True)
    question_id = db.Column(db.Integer, db.ForeignKey("question.id"), nullable=False)
    texte = db.Column(db.String(500), nullable=False)
    est_correct = db.Column(db.Boolean, default=False)
    audio_url = db.Column(db.String(500))

    question = db.relationship("Question", back_populates="choix")
    reponses = db.relationship(
        "ReponseEleve",
        back_populates="choix",
        lazy=True,
    )

    def __repr__(self):
        return f"<Choix {self.id} question={self.question_id}>"


class ReponseEleve(db.Model):
    __tablename__ = "reponse_eleve"

    id = db.Column(db.Integer, primary_key=True)
    eleve_id = db.Column(db.Integer, db.ForeignKey("utilisateur.id"), nullable=False)
    question_id = db.Column(db.Integer, db.ForeignKey("question.id"), nullable=False)
    choix_id = db.Column(db.Integer, db.ForeignKey("choix.id"), nullable=True)
    reponse_saisie = db.Column(db.String(500))
    est_correct = db.Column(db.Boolean, default=False)
    date_reponse = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("eleve_id", "question_id", name="uq_reponse_eleve_question"),
    )

    eleve = db.relationship("Utilisateur", back_populates="reponses_quiz", foreign_keys=[eleve_id])
    question = db.relationship("Question", back_populates="reponses")
    choix = db.relationship("Choix", back_populates="reponses")

    def __repr__(self):
        return f"<ReponseEleve eleve={self.eleve_id} question={self.question_id}>"


class Inscription(db.Model):
    __tablename__ = "inscription"

    id = db.Column(db.Integer, primary_key=True)
    eleve_id = db.Column(db.Integer, db.ForeignKey("utilisateur.id"), nullable=False)
    cours_id = db.Column(db.Integer, db.ForeignKey("cours.id"), nullable=False)
    date_inscription = db.Column(db.DateTime, default=datetime.utcnow)
    est_active = db.Column(db.Boolean, default=True)

    __table_args__ = (
        db.UniqueConstraint("eleve_id", "cours_id", name="uq_inscription_eleve_cours"),
    )

    eleve = db.relationship("Utilisateur", back_populates="inscriptions_cours", foreign_keys=[eleve_id])
    cours = db.relationship("Cours", back_populates="inscriptions")

    def __repr__(self):
        return f"<Inscription eleve={self.eleve_id} cours={self.cours_id}>"


class ProgressionLecon(db.Model):
    __tablename__ = "progression_lecon"

    id = db.Column(db.Integer, primary_key=True)
    eleve_id = db.Column(db.Integer, db.ForeignKey("utilisateur.id"), nullable=False)
    lecon_id = db.Column(db.Integer, db.ForeignKey("lecon.id"), nullable=False)
    vue = db.Column(db.Boolean, default=False)
    date_vue = db.Column(db.DateTime)
    score_quiz = db.Column(db.Float)

    __table_args__ = (
        db.UniqueConstraint("eleve_id", "lecon_id", name="uq_progression_eleve_lecon"),
    )

    eleve = db.relationship("Utilisateur", back_populates="progressions_lecons", foreign_keys=[eleve_id])
    lecon = db.relationship("Lecon", back_populates="progressions")

    @property
    def pourcentage_score(self):
        if self.score_quiz is None:
            return None
        return round(self.score_quiz * 100)

    def __repr__(self):
        return f"<ProgressionLecon eleve={self.eleve_id} lecon={self.lecon_id}>"


class XPEleve(db.Model):
    __tablename__ = "xp_eleve"

    id = db.Column(db.Integer, primary_key=True)
    eleve_id = db.Column(
        db.Integer,
        db.ForeignKey("utilisateur.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    points = db.Column(db.Integer, default=0)
    streak_jours = db.Column(db.Integer, default=0)
    derniere_activite = db.Column(db.DateTime)
    total_lecons_terminees = db.Column(db.Integer, default=0)

    eleve = db.relationship("Utilisateur", back_populates="xp", foreign_keys=[eleve_id])

    @staticmethod
    def get_or_create(eleve_id):
        xp = XPEleve.query.filter_by(eleve_id=eleve_id).first()
        if not xp:
            xp = XPEleve(eleve_id=eleve_id, points=0)
            db.session.add(xp)
            db.session.commit()
        return xp

    def ajouter_points(self, nb):
        now = datetime.utcnow()
        today = now.date()
        last_active_date = self.derniere_activite.date() if self.derniere_activite else None
        if last_active_date == today - timedelta(days=1):
            self.streak_jours += 1
        elif last_active_date != today:
            self.streak_jours = 1

        self.points += nb
        self.derniere_activite = now
        db.session.commit()

    def __repr__(self):
        return f"<XPEleve eleve={self.eleve_id} points={self.points}>"


class PushSubscription(db.Model):
    __tablename__ = "push_subscription"

    id = db.Column(db.Integer, primary_key=True)
    eleve_id = db.Column(db.Integer, db.ForeignKey("utilisateur.id"), nullable=False)
    endpoint = db.Column(db.Text, nullable=False)
    p256dh = db.Column(db.String(500), nullable=False)
    auth = db.Column(db.String(200), nullable=False)
    date_creation = db.Column(db.DateTime, default=datetime.utcnow)
    active = db.Column(db.Boolean, default=True)

    __table_args__ = (
        db.UniqueConstraint("eleve_id", "endpoint", name="uq_push_subscription_eleve_endpoint"),
    )

    eleve = db.relationship("Utilisateur", back_populates="push_subscriptions", foreign_keys=[eleve_id])

    def __repr__(self):
        return f"<PushSubscription eleve={self.eleve_id} active={self.active}>"


class Notification(db.Model):
    __tablename__ = "notification"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("utilisateur.id"), nullable=False)
    type = db.Column(db.String(50), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=False)
    cta_url = db.Column(db.String(500))
    icon = db.Column(db.String(50))
    event_key = db.Column(db.String(200))
    payload_json = db.Column(db.Text)
    is_read = db.Column(db.Boolean, default=False, nullable=False)
    read_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    pushed_at = db.Column(db.DateTime)

    __table_args__ = (
        db.UniqueConstraint("user_id", "event_key", name="uq_notification_user_event_key"),
    )

    user = db.relationship("Utilisateur", back_populates="notifications", foreign_keys=[user_id])

    def get_payload(self):
        if not self.payload_json:
            return {}
        try:
            return json.loads(self.payload_json)
        except (TypeError, ValueError):
            return {}

    def set_payload(self, payload):
        self.payload_json = json.dumps(payload or {}, ensure_ascii=False)

    def __repr__(self):
        return f"<Notification user={self.user_id} type={self.type} read={self.is_read}>"


class NotificationPreference(db.Model):
    __tablename__ = "notification_preference"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("utilisateur.id"), nullable=False)
    type = db.Column(db.String(50), nullable=False)
    inbox_enabled = db.Column(db.Boolean, default=True, nullable=False)
    push_enabled = db.Column(db.Boolean, default=True, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("user_id", "type", name="uq_notification_preference_user_type"),
    )

    user = db.relationship("Utilisateur", back_populates="notification_preferences", foreign_keys=[user_id])

    def __repr__(self):
        return f"<NotificationPreference user={self.user_id} type={self.type}>"


class SupportThread(db.Model):
    __tablename__ = "support_thread"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("utilisateur.id", ondelete="CASCADE"), nullable=False, unique=True)
    status = db.Column(db.String(20), default="open", nullable=False)
    last_activity_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_reset_requested_at = db.Column(db.DateTime)
    last_reset_granted_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    student = db.relationship("Utilisateur", back_populates="support_thread", foreign_keys=[student_id])
    messages = db.relationship(
        "SupportMessage",
        back_populates="thread",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="SupportMessage.created_at.asc()",
        foreign_keys="SupportMessage.thread_id",
    )
    reset_grants = db.relationship(
        "PasswordResetGrant",
        back_populates="thread",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="PasswordResetGrant.created_at.desc()",
        foreign_keys="PasswordResetGrant.thread_id",
    )

    @property
    def latest_message(self):
        if not self.messages:
            return None
        return self.messages[-1]

    def __repr__(self):
        return f"<SupportThread student={self.student_id} status={self.status}>"


class SupportMessage(db.Model):
    __tablename__ = "support_message"

    id = db.Column(db.Integer, primary_key=True)
    thread_id = db.Column(db.Integer, db.ForeignKey("support_thread.id", ondelete="CASCADE"), nullable=False)
    author_user_id = db.Column(db.Integer, db.ForeignKey("utilisateur.id", ondelete="SET NULL"))
    author_role = db.Column(db.String(20), nullable=False)
    message_type = db.Column(db.String(30), default="message", nullable=False)
    body = db.Column(db.Text, nullable=False)
    payload_json = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    thread = db.relationship("SupportThread", back_populates="messages", foreign_keys=[thread_id])
    author_user = db.relationship("Utilisateur", back_populates="support_messages", foreign_keys=[author_user_id])

    def get_payload(self):
        if not self.payload_json:
            return {}
        try:
            return json.loads(self.payload_json)
        except (TypeError, ValueError):
            return {}

    def set_payload(self, payload):
        self.payload_json = json.dumps(payload or {}, ensure_ascii=False)

    def __repr__(self):
        return f"<SupportMessage thread={self.thread_id} type={self.message_type} role={self.author_role}>"


class PasswordResetGrant(db.Model):
    __tablename__ = "password_reset_grant"

    id = db.Column(db.Integer, primary_key=True)
    thread_id = db.Column(db.Integer, db.ForeignKey("support_thread.id", ondelete="CASCADE"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("utilisateur.id", ondelete="CASCADE"), nullable=False)
    granted_by_admin_id = db.Column(db.Integer, db.ForeignKey("utilisateur.id", ondelete="SET NULL"))
    token = db.Column(db.String(255), nullable=False, unique=True)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime)
    request_ip = db.Column(db.String(80))
    request_user_agent = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    thread = db.relationship("SupportThread", back_populates="reset_grants", foreign_keys=[thread_id])
    student = db.relationship("Utilisateur", back_populates="password_reset_grants", foreign_keys=[student_id])
    granted_by_admin = db.relationship(
        "Utilisateur",
        back_populates="granted_password_reset_grants",
        foreign_keys=[granted_by_admin_id],
    )

    @property
    def is_expired(self):
        return self.expires_at <= datetime.utcnow()

    @property
    def is_active(self):
        return self.used_at is None and not self.is_expired

    def __repr__(self):
        return f"<PasswordResetGrant thread={self.thread_id} student={self.student_id}>"


class BadgeDefinition(db.Model):
    __tablename__ = "badge_definition"

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(80), nullable=False, unique=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text)
    image_url = db.Column(db.String(500))
    rule_type = db.Column(db.String(50), nullable=False)
    threshold = db.Column(db.Integer, nullable=False, default=1)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    earned_by = db.relationship(
        "UserBadge",
        back_populates="badge",
        lazy=True,
        cascade="all, delete-orphan",
        foreign_keys="UserBadge.badge_id",
    )

    def __repr__(self):
        return f"<BadgeDefinition slug={self.slug} rule={self.rule_type}>"


class UserBadge(db.Model):
    __tablename__ = "user_badge"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("utilisateur.id"), nullable=False)
    badge_id = db.Column(db.Integer, db.ForeignKey("badge_definition.id"), nullable=False)
    source_notification_id = db.Column(db.Integer, db.ForeignKey("notification.id"))
    date_earned = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("user_id", "badge_id", name="uq_user_badge_user_badge"),
    )

    user = db.relationship("Utilisateur", back_populates="earned_badges", foreign_keys=[user_id])
    badge = db.relationship("BadgeDefinition", back_populates="earned_by", foreign_keys=[badge_id])
    source_notification = db.relationship("Notification", foreign_keys=[source_notification_id])

    def __repr__(self):
        return f"<UserBadge user={self.user_id} badge={self.badge_id}>"


class AppSetting(db.Model):
    __tablename__ = "app_setting"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(120), nullable=False, unique=True)
    value = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<AppSetting {self.key}>"


class PlacementTest(db.Model):
    __tablename__ = "placement_test"

    id = db.Column(db.Integer, primary_key=True)

    questions = db.relationship(
        "PlacementQuestion",
        back_populates="test",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="PlacementQuestion.ordre",
    )

    def __repr__(self):
        return f"<PlacementTest {self.id}>"


class PlacementQuestion(db.Model):
    __tablename__ = "placement_question"

    id = db.Column(db.Integer, primary_key=True)
    test_id = db.Column(db.Integer, db.ForeignKey("placement_test.id"), nullable=False)
    texte = db.Column(db.String(500), nullable=False)
    type_question = db.Column(db.String(30), default="qcm")
    image_url = db.Column(db.String(500))
    audio_url = db.Column(db.String(500))
    ordre = db.Column(db.Integer, default=0)

    test = db.relationship("PlacementTest", back_populates="questions")
    choix = db.relationship(
        "PlacementChoix",
        back_populates="question",
        lazy=True,
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<PlacementQuestion {self.id} test={self.test_id}>"


class PlacementChoix(db.Model):
    __tablename__ = "placement_choix"

    id = db.Column(db.Integer, primary_key=True)
    question_id = db.Column(db.Integer, db.ForeignKey("placement_question.id"), nullable=False)
    texte = db.Column(db.String(300), nullable=False)
    image_url = db.Column(db.String(500))
    est_correct = db.Column(db.Boolean, default=False)

    question = db.relationship("PlacementQuestion", back_populates="choix")

    def __repr__(self):
        return f"<PlacementChoix {self.id} question={self.question_id}>"


class ResultatPlacement(db.Model):
    __tablename__ = "resultat_placement"

    id = db.Column(db.Integer, primary_key=True)
    eleve_id = db.Column(
        db.Integer,
        db.ForeignKey("utilisateur.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    niveau_assigne = db.Column(db.String(5), nullable=False)
    score = db.Column(db.Float, nullable=False)
    date_passage = db.Column(db.DateTime, default=datetime.utcnow)
    a_passe = db.Column(db.Boolean, default=True)

    eleve = db.relationship("Utilisateur", back_populates="resultat_placement", foreign_keys=[eleve_id])

    def __repr__(self):
        return f"<ResultatPlacement eleve={self.eleve_id} niveau={self.niveau_assigne}>"


class Vocabulaire(db.Model):
    __tablename__ = "vocabulaire"

    id = db.Column(db.Integer, primary_key=True)
    mot_arabe = db.Column(db.String(200), nullable=False, unique=True)
    translitteration = db.Column(db.String(200))
    traduction_fr = db.Column(db.String(300))
    traduction_en = db.Column(db.String(300))
    traduction_es = db.Column(db.String(300))
    audio_url = db.Column(db.String(500))
    exemple_phrase = db.Column(db.Text)
    niveau_cefr = db.Column(db.String(5))
    theme = db.Column(db.String(100))
    est_pedagogique = db.Column(db.Boolean, default=False, nullable=False)
    date_ajout = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Vocabulaire {self.mot_arabe}>"


class Log(db.Model):
    __tablename__ = "log"

    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    level = db.Column(db.String(20), nullable=False)
    module = db.Column(db.String(100), nullable=False)
    action = db.Column(db.String(200), nullable=False)
    details = db.Column(db.Text)
    utilisateur_id = db.Column(db.Integer, db.ForeignKey("utilisateur.id"), nullable=True)
    ip_address = db.Column(db.String(45))

    utilisateur = db.relationship("Utilisateur", back_populates="logs")

    def __repr__(self):
        return f"<Log {self.timestamp} {self.level}>"

    def to_dict(self):
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "level": self.level,
            "module": self.module,
            "action": self.action,
            "details": self.details,
            "utilisateur_id": self.utilisateur_id,
            "ip_address": self.ip_address,
        }


class SystemSetting(db.Model):
    __tablename__ = "system_setting"

    key = db.Column(db.String(100), primary_key=True)
    value_json = db.Column(db.Text, nullable=False, default="null")
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def get_value(self):
        if not self.value_json:
            return None
        try:
            return json.loads(self.value_json)
        except (TypeError, ValueError):
            return None

    def set_value(self, value):
        self.value_json = json.dumps(value, ensure_ascii=False)

    def __repr__(self):
        return f"<SystemSetting {self.key}>"


class ActiveSession(db.Model):
    __tablename__ = "active_session"

    id = db.Column(db.Integer, primary_key=True)
    session_token = db.Column(db.String(255), unique=True, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("utilisateur.id", ondelete="CASCADE"), nullable=False)
    role_snapshot = db.Column(db.String(20), nullable=False)
    ip_address = db.Column(db.String(45))
    user_agent = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_seen_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    revoked_at = db.Column(db.DateTime)
    revoked_reason = db.Column(db.String(120))

    user = db.relationship("Utilisateur", back_populates="active_sessions", foreign_keys=[user_id])

    @property
    def is_active(self):
        return self.revoked_at is None

    def __repr__(self):
        return f"<ActiveSession user={self.user_id} active={self.is_active}>"
