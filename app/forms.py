from flask_wtf import FlaskForm
from flask_wtf.file import FileField
from flask_babel import lazy_gettext as _l
from wtforms import BooleanField, IntegerField, PasswordField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Email, EqualTo, InputRequired, Length, NumberRange, Optional


CEFR_LEVEL_CHOICES = [
    ("A1", "A1"),
    ("A2", "A2"),
]

USER_ROLE_CHOICES = [
    ("admin", _l("Administrateur")),
    ("enseignant", _l("Enseignant")),
    ("eleve", _l("Eleve")),
    ("maintenance", _l("Maintenance technique")),
]


class LoginForm(FlaskForm):
    email = StringField(_l("Email"), validators=[DataRequired(), Length(max=120)])
    mot_de_passe = PasswordField(_l("Mot de passe"), validators=[DataRequired(), Length(max=128)])
    remember = BooleanField(_l("Se souvenir de moi"))
    submit = SubmitField(_l("Se connecter"))


class RegisterForm(FlaskForm):
    nom = StringField(_l("Nom"), validators=[DataRequired(), Length(min=2, max=100)])
    prenom = StringField(_l("Prenom"), validators=[Optional(), Length(max=100)])
    email = StringField(_l("Email"), validators=[DataRequired(), Length(max=120)])
    password = PasswordField(_l("Mot de passe"), validators=[DataRequired(), Length(min=6, max=128)])
    confirm_password = PasswordField(
        _l("Confirmer le mot de passe"),
        validators=[DataRequired(), EqualTo("password")],
    )
    submit = SubmitField(_l("Creer mon compte"))


class CreateUserForm(FlaskForm):
    nom = StringField(_l("Nom"), validators=[DataRequired(), Length(min=2, max=100)])
    prenom = StringField(_l("Prenom"), validators=[Length(max=100)])
    email = StringField(_l("Email"), validators=[DataRequired(), Email()])
    telephone = StringField(_l("Telephone"), validators=[Length(max=20)])
    password = PasswordField(_l("Mot de passe"), validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField(
        _l("Confirmer le mot de passe"),
        validators=[DataRequired(), EqualTo("password")],
    )
    role = SelectField(
        _l("Role"),
        choices=USER_ROLE_CHOICES,
        validators=[DataRequired()],
    )
    submit = SubmitField(_l("Creer utilisateur"))


class UpdateUserForm(FlaskForm):
    nom = StringField(_l("Nom"), validators=[DataRequired(), Length(min=2, max=100)])
    prenom = StringField(_l("Prenom"), validators=[Optional(), Length(max=100)])
    email = StringField(_l("Email"), validators=[DataRequired(), Email()])
    telephone = StringField(_l("Telephone"), validators=[Optional(), Length(max=20)])
    password = PasswordField(_l("Nouveau mot de passe"), validators=[Optional(), Length(min=6)])
    confirm_password = PasswordField(
        _l("Confirmer le mot de passe"),
        validators=[Optional(), EqualTo("password")],
    )
    role = SelectField(_l("Role"), choices=USER_ROLE_CHOICES, validators=[DataRequired()])
    submit = SubmitField(_l("Enregistrer les modifications"))


class DeleteUserForm(FlaskForm):
    submit = SubmitField(_l("Supprimer"))


class ProfileForm(FlaskForm):
    nom = StringField(_l("Nom"), validators=[DataRequired(), Length(min=2, max=100)])
    prenom = StringField(_l("Prenom"), validators=[Optional(), Length(max=100)])
    telephone = StringField(_l("Telephone"), validators=[Optional(), Length(max=20)])
    photo = FileField(_l("Photo de profil"))
    submit = SubmitField(_l("Enregistrer le profil"))


class CourseForm(FlaskForm):
    titre = StringField(_l("Titre"), validators=[DataRequired(), Length(min=1, max=100)])
    description = TextAreaField(_l("Description"), validators=[Optional(), Length(max=5000)])
    niveau = SelectField(_l("Niveau"), choices=CEFR_LEVEL_CHOICES, validators=[DataRequired()])
    image = FileField(_l("Image du cours"))
    est_publie = BooleanField(_l("Publier ce cours"))
    submit = SubmitField(_l("Enregistrer le cours"))


class LessonForm(FlaskForm):
    titre = StringField(_l("Titre"), validators=[DataRequired(), Length(min=1, max=200)])
    ordre = IntegerField(_l("Ordre"), validators=[InputRequired(), NumberRange(min=0)], default=0)
    contenu_texte = TextAreaField(_l("Contenu texte"), validators=[Optional(), Length(max=50000)])
    video_url = StringField(_l("URL video"), validators=[Optional(), Length(max=500)])
    pdf = FileField(_l("PDF de la lecon"))
    audio = FileField(_l("Audio de la lecon"))
    duree_minutes = IntegerField(_l("Duree (minutes)"), validators=[Optional(), NumberRange(min=1, max=10000)])
    est_publie = BooleanField(_l("Publier cette lecon"))
    submit = SubmitField(_l("Enregistrer la lecon"))


class RequestResetPasswordForm(FlaskForm):
    email = StringField(_l("Email"), validators=[DataRequired(), Email()])
    submit = SubmitField(_l("Contacter l administrateur"))


class ResetPasswordConfirmForm(FlaskForm):
    new_password = PasswordField(_l("Nouveau mot de passe"), validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField(
        _l("Confirmer le mot de passe"),
        validators=[DataRequired(), EqualTo("new_password")],
    )
    submit = SubmitField(_l("Reinitialiser"))
