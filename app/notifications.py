import json
import logging
import os

from flask import current_app, has_app_context

try:
    from pywebpush import WebPushException, webpush
except ImportError:  # pragma: no cover - optional dependency for local setups
    WebPushException = Exception
    webpush = None


logger = logging.getLogger(__name__)


def envoyer_notification_push(subscription, payload, app=None):
    target_app = app
    if target_app is None and has_app_context():
        target_app = current_app._get_current_object()

    if target_app is None:
        logger.warning("Application Flask absente: notification push ignoree.")
        return False

    if webpush is None:
        logger.warning("pywebpush indisponible: notification push ignoree.")
        return False

    private_key = target_app.config.get("VAPID_PRIVATE_KEY")
    vapid_claims = target_app.config.get("VAPID_CLAIMS", {})
    if not private_key or not os.path.exists(private_key):
        logger.warning("Cle VAPID introuvable: notification push ignoree.")
        return False

    try:
        webpush(
            subscription_info={
                "endpoint": subscription.endpoint,
                "keys": {
                    "p256dh": subscription.p256dh,
                    "auth": subscription.auth,
                },
            },
            data=json.dumps(payload),
            vapid_private_key=private_key,
            vapid_claims=vapid_claims,
        )
        return True
    except WebPushException as exc:
        logger.warning("Erreur push pour %s: %s", subscription.endpoint, exc)
        return False
