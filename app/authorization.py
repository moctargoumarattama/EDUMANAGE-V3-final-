from functools import wraps

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

from app.utils import log_action


def _deny_access(message, status_code=403):
    log_action(
        module="authorization",
        action="Acces refuse",
        level="WARNING",
        details=f"Tentative d'acces a {request.path} par {getattr(current_user, 'role', 'anonymous')}",
    )
    if request.is_json:
        return jsonify({"error": message}), status_code
    return render_template("403.html", message=message), status_code


def require_role(role_name):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                message = "Vous devez etre connecte pour acceder a cette ressource."
                if request.is_json:
                    return jsonify({"error": message}), 401
                flash(message, "warning")
                return redirect(url_for("main.login"))

            current_role = getattr(current_user, "role", None)
            if role_name == "admin":
                allowed = current_role == "admin"
            elif role_name == "enseignant":
                allowed = current_role in {"enseignant", "admin"}
            elif role_name == "eleve":
                allowed = current_role == "eleve"
            elif role_name == "maintenance":
                allowed = current_role == "maintenance"
            else:
                allowed = current_role == role_name

            if not allowed:
                return _deny_access(f"Acces non autorise pour le role '{current_role}'.")

            return view_func(*args, **kwargs)

        return wrapped

    return decorator


def admin_required(view_func):
    return require_role("admin")(view_func)


def enseignant_required(view_func):
    return require_role("enseignant")(view_func)


def eleve_required(view_func):
    return require_role("eleve")(view_func)


def maintenance_required(view_func):
    return require_role("maintenance")(view_func)
