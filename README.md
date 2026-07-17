# SulitShelf PH — Free & Open Source Flask Edition

SulitShelf PH is an MIT-licensed affiliate mall for independent promoters. It
provides free storefronts, product listings, outbound click analytics, and
Shopee, Lazada, and TikTok Shop affiliate-link support. There are no paid
plans, trials, subscription expiry dates, or donation-only features.

The growth toolkit adds shareable campaign collections, Admin Picks, clearly
labeled sponsored shelves, real-click trending products, product and shop QR
codes, tracked social links, verification badges, private listing reports,
price-last-checked notices, and privacy-friendly seven-day promoter analytics.

Optional one-time donations can support hosting and development through Dodo
Payments or an administrator-configured e-wallet QR code such as GCash or Maya.
Donations never change account access or product limits.

Promoters can use password authentication or explicitly linked Google, GitHub,
and Facebook accounts. Uploaded product images, QR codes, and receipts are
validated, resized, orientation-corrected, and converted to WebP before local
or Cloudinary storage.

## Windows quick start with SQLite

Use Python 3.12 for this project:

```powershell
py -3.12 -m venv venv
venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Put the generated value in `.env` as `SECRET_KEY`, then run:

```powershell
python -m flask --app run.py db upgrade
python -m flask --app run.py seed-defaults
$env:ADMIN_PASSWORD="choose-a-strong-password"
python -m flask --app run.py seed-admin --email delacruzedward0735@gmail.com
python -m flask --app run.py run --debug
```

Open `http://127.0.0.1:5000`. The administrator CMS is at `/admin/`.

The SQLite URL is `sqlite:///sulitshelf.db`; Flask stores that relative file
inside `instance`. Do not add `instance/` to the relative URL.

## Recover an administrator account

This is a local database recovery tool, not a public web password-reset page.
Stop the server, make sure the configured database is reachable, and run from
PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\recover-admin.ps1 -Email delacruzedward0735@gmail.com
```

The script uses `venv` or `.venv` when available and otherwise uses Python
3.12 through the Windows `py` launcher. It securely prompts for the new
password twice. You can also invoke the command directly:

```powershell
py -3.12 -m flask --app run.py recover-admin --email delacruzedward0735@gmail.com
```

Recovery requires a 12–128 character password. It creates the account if it is
missing, promotes it to administrator, reactivates it, restores its free admin
shop when needed, and writes a password-free audit event. If account compromise
is suspected, also rotate `SECRET_KEY` to invalidate existing login sessions.

## Upgrading an existing subscription build

Back up the database and uploads, install this release, then run:

```powershell
python -m flask --app run.py db upgrade
python -m flask --app run.py seed-defaults
```

Migration `0002_open_source_donations` preserves legacy subscription and
payment tables for compatibility, marks existing shops as free, and creates
separate donation records and donation tiers. Migration
`0003_oauth_identities` adds social identities without changing existing
password accounts. Migration `0004_growth_features` preserves existing
products and adds campaign, click-event, report, verification,
sponsored-label, and product trust fields. It also creates three empty starter
campaign collections; administrators choose which real products they contain.

## Growth and earning tools

- Add the administrator's own affiliate products through **Promoter Studio**;
  they appear in the transparent **SulitShelf Admin Picks** section.
- Create focused campaign URLs from **Admin CMS → Campaign shelves** and assign
  products using the IDs shown in the product directory.
- Mark a paid placement as sponsored only through the administrator CMS. Every
  public sponsored placement is visibly labeled.
- Verify a promoter manually from **Admin CMS → Promoters**. Verification is a
  trust signal, not proof of marketplace ownership or a product guarantee.
- Promoters can copy TikTok, Facebook, Instagram, YouTube, and Messenger shop
  links from **Promoter Studio → Growth tools**. Click events store only the
  product, shop, marketplace, source label, and time—never an IP address or
  visitor identifier.
- Visitors can privately report broken, misleading, incorrect, unsafe, or
  rights-sensitive listings. Only an administrator can dismiss a report or
  pause the reported product.

The public **Promoter help** page lists optional one-time human services such
as storefront setup and listing optimization. To expose a public contact
button, configure:

```env
SERVICE_CONTACT_EMAIL=your-public-support-email@example.com
```

These service fees do not unlock features, buy verification, or create hidden
ranking. SulitShelf itself remains free and open source.

## PostgreSQL

Set a PostgreSQL connection URL:

```env
DATABASE_URL=postgresql+psycopg://username:password@localhost:5432/sulitshelf
```

Provider URLs beginning with `postgres://` or plain `postgresql://` are
normalized automatically for Psycopg 3. Apply migrations with:

```bash
python -m flask --app run.py db upgrade
python -m flask --app run.py seed-defaults
```

## Docker with PostgreSQL

Copy `.env.example` to `.env`, replace its placeholders, then:

```bash
docker compose up -d db
docker compose run --rm web flask --app run.py db upgrade
docker compose run --rm web flask --app run.py seed-defaults
docker compose run --rm web flask --app run.py seed-admin --email delacruzedward0735@gmail.com
docker compose up -d web
```

Open `http://localhost:8000`.

## Optional Dodo donations

Create **one-time** Dodo products for the suggested donation amounts. In
**Admin CMS → Donation amounts**, paste each matching product ID. Configure the
API and webhook secrets in `.env`, then register:

```text
https://your-domain.example/payments/dodo/webhook
```

The application verifies Standard Webhooks-compatible HMAC signatures,
deduplicates webhook IDs, and records `payment.succeeded`, `payment.failed`,
and `payment.cancelled` results. Do not use recurring subscription products.

## Optional e-wallet donations

In **Admin CMS → E-wallet & QR**, configure a provider name (for example,
GCash or Maya), account details, support message, and QR image. Supporters must
submit both a reference number and receipt. Administrators can confirm or
reject the submission without changing supporter access.

## Google, GitHub, and Facebook sign-in

Set the provider credentials in `.env`. Use the following exact callback URLs
when creating each provider application locally:

```text
http://localhost:5000/oauth/google/callback
http://localhost:5000/oauth/github/callback
http://localhost:5000/oauth/facebook/callback
```

In production, replace the base with your HTTPS domain and set both
`PUBLIC_BASE_URL` and `OAUTH_REDIRECT_BASE_URL` to that domain. Configure:

```env
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GITHUB_CLIENT_ID=
GITHUB_CLIENT_SECRET=
FACEBOOK_CLIENT_ID=
FACEBOOK_CLIENT_SECRET=
FACEBOOK_GRAPH_VERSION=v25.0
```

SulitShelf stores only the provider name and stable provider account ID. It
does not persist access tokens. An existing password account is never linked
only because its email matches; sign in first and connect the provider through
**Promoter Studio → Shop settings → Connected accounts**.

Provider setup references:

- [Google OpenID Connect](https://developers.google.com/identity/openid-connect/openid-connect)
- [GitHub OAuth Apps](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps)
- [Facebook Login](https://developers.facebook.com/docs/facebook-login/)

## WebP optimization and Cloudinary storage

Every accepted JPG, PNG, or WebP upload is decoded by Pillow instead of being
trusted by its filename or header. Product images are resized and compressed;
QR codes and private donation receipts use lossless WebP to preserve scannable
and readable detail.

For local development, `IMAGE_STORAGE_BACKEND=auto` uses local files when
Cloudinary is not configured. For deployment, copy the API environment value
from the Cloudinary console and use:

```env
IMAGE_STORAGE_BACKEND=cloudinary
CLOUDINARY_URL=cloudinary://API_KEY:API_SECRET@CLOUD_NAME
CLOUDINARY_FOLDER=sulitshelf
IMAGE_WEBP_QUALITY=82
IMAGE_MAX_DIMENSION=1600
```

Public product images and the donation QR use Cloudinary CDN delivery.
Donation receipts are uploaded as authenticated assets and exposed only to an
administrator through short-lived signed download URLs. See the
[Cloudinary Python SDK guide](https://cloudinary.com/documentation/python_quickstart).

## Production checklist

- Use PostgreSQL and a long random `SECRET_KEY`.
- Set `SESSION_COOKIE_SECURE=true` behind HTTPS.
- Store `instance/uploads` on a persistent volume or object storage.
- Use `IMAGE_STORAGE_BACKEND=cloudinary` on hosts with an ephemeral filesystem.
- Register the exact HTTPS OAuth callback URLs with every enabled provider.
- Run `python -m flask --app run.py db upgrade` during every release.
- Keep Dodo and database credentials only in environment variables.
- Put the app behind a TLS reverse proxy and back up PostgreSQL and uploads.
- Set `RATELIMIT_STORAGE_URI` to a shared backend such as Redis when running
  multiple workers.

## Tests

```bash
python -m pytest -q
```

## License

Copyright © 2026 John Edward Q. Dela Cruz. Released under the [MIT License](LICENSE).
