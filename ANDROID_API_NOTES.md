# Android app support — what changed in the backend

## New: JSON REST API at `/api/v1/...`

Added under `app/api/`, registered in `app/__init__.py`, exempted from
CSRF (it authenticates with a `Bearer` token, not a session cookie, so
there's no ambient browser credential for CSRF to protect against).

- `POST /api/v1/auth/login` — email+password → 90-day bearer token
  (accounts with 2FA enabled are refused with a clear error; the mobile
  client doesn't implement the 2FA challenge yet)
- `POST /api/v1/auth/logout`, `GET /api/v1/me`
- `GET /api/v1/shops/<slug>`, `GET /api/v1/shops/<slug>/products` (paginated)
- `GET /api/v1/products/<id>`, `POST /api/v1/products/<id>/click`
- `GET /api/v1/promoter/dashboard`, `GET /api/v1/promoter/tokens`

New `ApiToken` model + migration `0011_api_tokens` — only the SHA-256
digest of each token is stored (same pattern as `PasswordResetToken`).
That migration also adds a composite index
(`product.shop_id, status, created_at`) matching the mobile catalog
query's exact filter+sort shape, so it can be satisfied by one index
lookup instead of a per-column merge.

Tested end-to-end (login → browse → product detail → click tracking →
dashboard → logout) against a local SQLite DB before packaging.

**Before deploying:** run `flask db upgrade` against your production
Postgres to apply `0011_api_tokens`.

## Postgres — verified, not changed

Your existing config (`app/config.py` / wherever `SQLALCHEMY_DATABASE_URI`
is built) was already solid: `psycopg[binary]` v3 driver, `postgres://` is
normalized to `postgresql+psycopg://` (Render's connection strings still
use the old `postgres://` scheme, which SQLAlchemy 1.4+/2.x rejects
outright), and `pool_pre_ping=True` so a connection that Render's proxy
silently dropped gets replaced instead of raising mid-request. Nothing
needed fixing here — I only added the one composite index above.

If you ever *do* want to double check connectivity from a shell:

```bash
flask shell -c "from app.extensions import db; print(db.session.execute(db.text('select 1')).scalar())"
```
