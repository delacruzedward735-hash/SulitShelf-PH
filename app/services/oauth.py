from dataclasses import dataclass

from flask import current_app

from app.extensions import oauth


@dataclass(frozen=True)
class OAuthProvider:
    key: str
    name: str
    mark: str


PROVIDERS = {
    "google": OAuthProvider("google", "Google", "G"),
    "github": OAuthProvider("github", "GitHub", "GH"),
    "facebook": OAuthProvider("facebook", "Facebook", "f"),
}


class OAuthProfileError(ValueError):
    pass


def configure_oauth(app):
    oauth.register(
        name="google",
        client_id=app.config["GOOGLE_CLIENT_ID"],
        client_secret=app.config["GOOGLE_CLIENT_SECRET"],
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
        overwrite=True,
    )
    oauth.register(
        name="github",
        client_id=app.config["GITHUB_CLIENT_ID"],
        client_secret=app.config["GITHUB_CLIENT_SECRET"],
        access_token_url="https://github.com/login/oauth/access_token",
        authorize_url="https://github.com/login/oauth/authorize",
        api_base_url="https://api.github.com/",
        client_kwargs={"scope": "read:user user:email"},
        overwrite=True,
    )
    version = app.config["FACEBOOK_GRAPH_VERSION"]
    oauth.register(
        name="facebook",
        client_id=app.config["FACEBOOK_CLIENT_ID"],
        client_secret=app.config["FACEBOOK_CLIENT_SECRET"],
        access_token_url=f"https://graph.facebook.com/{version}/oauth/access_token",
        authorize_url=f"https://www.facebook.com/{version}/dialog/oauth",
        api_base_url=f"https://graph.facebook.com/{version}/",
        client_kwargs={"scope": "email public_profile"},
        overwrite=True,
    )


def provider_is_configured(provider):
    if provider not in PROVIDERS:
        return False
    prefix = provider.upper()
    return bool(current_app.config.get(f"{prefix}_CLIENT_ID") and current_app.config.get(f"{prefix}_CLIENT_SECRET"))


def provider_context():
    base_url = current_app.config["OAUTH_REDIRECT_BASE_URL"]
    return [
        {
            "key": item.key,
            "name": item.name,
            "mark": item.mark,
            "configured": provider_is_configured(item.key),
            "callback_url": f"{base_url}/oauth/{item.key}/callback",
        }
        for item in PROVIDERS.values()
    ]


def _json(response):
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, (dict, list)):
        raise OAuthProfileError("The identity provider returned an invalid profile.")
    return payload


def load_provider_profile(provider, client, token):
    if provider == "google":
        profile = token.get("userinfo") or _json(client.get("userinfo", token=token))
        result = {
            "provider_user_id": str(profile.get("sub", "")),
            "email": str(profile.get("email", "")).strip().lower(),
            "email_verified": profile.get("email_verified") is True,
            "display_name": str(profile.get("name", "")).strip(),
        }
    elif provider == "github":
        profile = _json(client.get("user", token=token))
        emails = _json(client.get("user/emails", token=token))
        verified = [row for row in emails if isinstance(row, dict) and row.get("verified") and row.get("email")]
        selected = next((row for row in verified if row.get("primary")), verified[0] if verified else None)
        result = {
            "provider_user_id": str(profile.get("id", "")),
            "email": str(selected.get("email", "")).strip().lower() if selected else "",
            "email_verified": bool(selected),
            "display_name": str(profile.get("name") or profile.get("login") or "").strip(),
        }
    elif provider == "facebook":
        profile = _json(client.get("me", params={"fields": "id,name,email"}, token=token))
        result = {
            "provider_user_id": str(profile.get("id", "")),
            "email": str(profile.get("email", "")).strip().lower(),
            "email_verified": bool(profile.get("email")),
            "display_name": str(profile.get("name", "")).strip(),
        }
    else:
        raise OAuthProfileError("Unsupported identity provider.")
    if not result["provider_user_id"]:
        raise OAuthProfileError("The identity provider did not return an account ID.")
    return result
