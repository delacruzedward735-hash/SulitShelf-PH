from functools import wraps

from flask import abort, current_app
from flask_login import current_user, login_required


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        allowed = current_user.is_admin or current_user.email.lower() in current_app.config["ADMIN_EMAILS"]
        if not allowed:
            abort(403)
        return view(*args, **kwargs)
    return wrapped
