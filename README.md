# SulitShelf PH — Flask Affiliate Mall

SulitShelf PH is an MIT-licensed affiliate mall for independent promoters. It
provides storefronts, product listings, outbound click analytics, and Shopee,
Lazada, and TikTok Shop affiliate-link support. The hosted offer has two plans:
permanent Free access with 50 products, and Pro at ₱49/month with unlimited
product uploads. There is no trial countdown.

The growth toolkit adds shareable campaign collections, Admin Picks, clearly
labeled sponsored shelves, real-click trending products, product and shop QR
codes, tracked social links, verification badges, private listing reports,
price-last-checked notices, privacy-friendly aggregate impression/click
analytics, storefront wishlists and recently viewed products, commission-report
imports, bulk listing imports, product-health checks, and Pro storefront
branding. The public catalog is installable as a PWA and exposes canonical SEO,
Open Graph, product structured data, robots, and sitemap endpoints.

The approved SulitShelf storefront-and-link artwork is included as browser,
Apple touch, PWA, maskable, social-sharing, and visible navigation icons. For
Google Search Console's optional HTML-tag verification method, copy only the
verification token into `GOOGLE_SITE_VERIFICATION`; the shared page head emits
the complete meta tag. Keep `PUBLIC_BASE_URL` set to the final HTTPS domain so
canonical, Open Graph, sitemap, and structured-data URLs use the production
host.

Pro can be purchased through a recurring Dodo checkout or renewed for 30 days
through an administrator-reviewed GCash, Maya, or other e-wallet receipt.
Optional one-time donations remain a separate flow and never activate or extend
Pro. The source remains MIT-licensed for self-hosting and customization.

Promoters can use password authentication or explicitly linked Google, GitHub,
and Facebook accounts. Uploaded product images, QR codes, and receipts are
validated, resized, orientation-corrected, and converted to WebP before local
or Cloudinary storage.

Optional email password recovery supports Resend and MailerSend with automatic
provider failover. Reset grants expire quickly, are stored only as SHA-256
digests, work once, and revoke existing sessions after use.

The administrator CRM adds a searchable promoter directory, direct and
broadcast in-app messages, optional one-to-one Resend/MailerSend email copies,
delivery history, unread tracking, and a private promoter inbox. Broadcasts
remain in-app to avoid web-request and email-provider timeouts. Email failure
never removes the in-app copy, and promoters can read only messages addressed
to their own account.

See [SECURITY.md](SECURITY.md) for the administrator trust boundary, supported
release policy, private reporting guidance, and production security baseline.

## Windows quick start with SQLite

Use Python 3.12 for this project:

```powershell
py -3.12 -m venv venv
venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48)); print(secrets.token_urlsafe(48))"
```

Put the two different generated values in `.env` as `SECRET_KEY` and
`TWO_FACTOR_ENCRYPTION_KEY`, then run:

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
shop when needed, writes a password-free audit event, and revokes every existing
session for that account. Rotate `SECRET_KEY` only when every user's session
must be invalidated, such as after an application-secret exposure.

`ADMIN_EMAILS` supplies the default email for these trusted CLI bootstrap and
recovery commands only. Public password registration and first-time OAuth
registration always create promoter accounts; an email match never grants
administrator access.

## Authenticator-app two-factor authentication

Every promoter and administrator can enable TOTP-based two-factor
authentication from **Promoter Studio → Shop settings → Account protection**.
Setup works with standard authenticator apps, requires a successful code before
activation, and creates ten one-time recovery codes. Authenticator secrets are
encrypted at rest; recovery codes are stored only as keyed digests. A used TOTP
counter or recovery code cannot be replayed.

Keep `TWO_FACTOR_ENCRYPTION_KEY` stable, private, and different from
`SECRET_KEY` and `COMMISSION_HASH_KEY`. Losing it makes existing authenticator
secrets undecryptable. If a user loses both the authenticator and recovery
codes, an operator with trusted host/database access can disable 2FA and revoke
all sessions:

```powershell
py -3.12 -m flask --app run.py reset-two-factor --email user@example.com
```

For Docker/Render, keep `RUN_MIGRATIONS_ON_START=true` and leave the service's
Docker Command blank. Startup applies migration `0010_two_factor_authentication`
and verifies every mapped table and column before Gunicorn starts. If setup is
temporarily unavailable, check the deploy log for `Verifying application
database schema...`, confirm `TWO_FACTOR_ENCRYPTION_KEY` is present, and confirm
`/health/ready` returns `status: ok` before trying again.

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
Migration `0005_session_invalidation` adds per-account session versions so an
administrator recovery or account-status change immediately revokes old login
cookies. Migration `0006_free_pro_subscriptions` creates the permanent Free and
monthly Pro plans, adds Dodo lifecycle fields, prevents reuse of an e-wallet
reference across donations and subscriptions, and keeps existing products.
Migration `0007_password_recovery` adds expiring, single-use reset grants; raw
reset tokens are never stored in the database.
Migration `0008_promoter_growth_center` adds aggregate hourly product metrics,
commission imports, product-health state, and Pro storefront branding. It does
not alter existing products, subscriptions, or authentication records.
Migration `0009_promoter_crm` adds private administrator-to-promoter inbox
messages and email-delivery status without modifying existing user accounts.

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
- Product impressions and clicks are additionally summarized by hour, source,
  and broad device class. The aggregate table stores no raw IP address, user
  agent, cookie identifier, or visitor identifier. These figures measure site
  engagement; they are never presented as marketplace sales.
- Pro promoters can import up to 250 normalized product rows at a time and up
  to 5,000 commission rows at a time. Download the exact CSV templates inside
  **Promoter Studio → Growth center**. All row limits and file-size limits are
  enforced by the server.
- Commission reports are the only source of displayed commission totals. Only
  imported approved/confirmed/completed/paid/validated rows contribute to the
  displayed earnings total; pending and rejected rows remain stored for audit.
  Marketplace order references are salted with the shop ID and stored only as
  keyed HMAC-SHA-256-derived row identities; the raw reference is discarded
  after parsing. Keep `COMMISSION_HASH_KEY` stable and separate from
  `SECRET_KEY` so session-key rotation does not affect duplicate detection.
  Import-file and row identities prevent accidental duplicates.
- Product health flags placeholder images, stale price checks, invalid supported
  marketplace URLs, and pending visitor broken-link reports. It deliberately
  does not ask the server to crawl arbitrary affiliate URLs.
- Pro storefront branding supports a theme, tagline, logo, banner, and optional
  removal of the platform footer. Visitors can keep a private browser-only
  wishlist and recent-product list without creating an account.
- Visitors can privately report broken, misleading, incorrect, unsafe, or
  rights-sensitive listings. Only an administrator can dismiss a report or
  pause the reported product.

Run a product-health scan for every shop from a trusted release/cron process:

```bash
python -m flask --app run.py scan-product-health
```

For Render, this can be a separate Cron Job using the same repository and
database environment. A daily schedule is enough; promoters can also run a
shop-specific scan from Promoter Studio.

The public **Promoter help** page lists optional one-time human services such
as storefront setup and listing optimization. To expose a public contact
button, configure:

```env
SERVICE_CONTACT_EMAIL=your-public-support-email@example.com
```

These service fees do not unlock features, buy verification, or create hidden
ranking. These optional services are separate from the Free and Pro plans.

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

## Production deployment with Docker

This deployment includes PostgreSQL, Redis-backed rate limits, a one-shot
migration service, Gunicorn, and Caddy automatic HTTPS. Point the domain's DNS
records to the server and allow inbound TCP 80/443 and UDP 443 first.

```bash
cp .env.production.example .env.production
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Edit `.env.production` and replace every required placeholder. The password in
`DATABASE_URL` must be URL-encoded, while `POSTGRES_PASSWORD` uses the original
password. The domain in `DOMAIN`, `TRUSTED_HOSTS`, `PUBLIC_BASE_URL`, and
`OAUTH_REDIRECT_BASE_URL` must agree. `Render.env.template` is the corresponding
copy-ready template for a single Render Docker web service. Then validate and
deploy:

```bash
docker compose --env-file .env.production config --quiet
docker compose --env-file .env.production up -d --build
docker compose --env-file .env.production ps
curl https://shelf.example.com/health/ready
```

The `web` service starts only after PostgreSQL and Redis are healthy and the
`migrate` service has successfully run the production safety check, all Alembic
migrations, and default-data seeding. Create the first administrator after the
site is healthy:

```bash
docker compose --env-file .env.production exec web \
  flask --app run.py seed-admin --email admin@example.com
```

Caddy is the only publicly exposed service. The application container runs as
a non-root user with a read-only filesystem, dropped Linux capabilities, and a
health check. Uploaded images and private receipts must use Cloudinary in
production, so application containers remain stateless.

### Render Docker deployment

Render deploys the `Dockerfile`; it does not start the services declared in
`docker-compose.yml`. This image therefore runs the idempotent
`deploy-release` command before Gunicorn. The command applies every Alembic
migration, seeds required defaults, and uses a PostgreSQL advisory lock so
overlapping container starts cannot migrate the same database concurrently.

Create a Render PostgreSQL database, Redis-compatible Key Value instance, and
Docker web service. Use these web-service settings:

```text
Language: Docker
Dockerfile Path: ./Dockerfile
Docker Build Context Directory: .
Docker Command: leave blank
Health Check Path: /health/ready
```

Set the following environment variables in the Render web service. Use the
PostgreSQL and Key Value **internal** connection URLs supplied by Render:

```env
APP_ENV=production
APP_VERSION=render
SECRET_KEY=replace-with-a-fresh-random-secret-of-at-least-32-characters
TWO_FACTOR_ENCRYPTION_KEY=generate-a-different-random-secret-of-at-least-32-characters
DATABASE_URL=postgresql://user:password@internal-host/database
ADMIN_EMAILS=your-login-email@example.com
SERVICE_CONTACT_EMAIL=
TRUSTED_HOSTS=sulitshelf-ph.onrender.com
PUBLIC_BASE_URL=https://sulitshelf-ph.onrender.com
OAUTH_REDIRECT_BASE_URL=https://sulitshelf-ph.onrender.com
SESSION_COOKIE_SECURE=true
REMEMBER_COOKIE_SECURE=true
PROXY_FIX_X_FOR=1
PROXY_FIX_X_PROTO=1
PROXY_FIX_X_HOST=1
PROXY_FIX_X_PORT=0
PROXY_FIX_X_PREFIX=0
FORWARDED_ALLOW_IPS=*
RATELIMIT_STORAGE_URI=redis://internal-key-value-host:6379/0
HEARTBEAT_TOKEN=generate-a-separate-random-token-of-at-least-32-characters
IMAGE_STORAGE_BACKEND=cloudinary
CLOUDINARY_URL=cloudinary://api-key:api-secret@cloud-name
CLOUDINARY_UPLOAD_TIMEOUT_SECONDS=20
CSV_IMPORT_MAX_BYTES=2000000
BULK_PRODUCT_MAX_ROWS=250
COMMISSION_MAX_ROWS=5000
PRODUCT_STALE_DAYS=30
COMMISSION_HASH_KEY=generate-a-separate-random-secret-of-at-least-32-characters
TWO_FACTOR_CHALLENGE_MINUTES=5
RUN_MIGRATIONS_ON_START=true
```

Add any OAuth or Dodo variables only when those integrations are enabled.
Add the email variables from the password-recovery section when that feature is
enabled.
After saving the environment, deploy with **Clear build cache & deploy**. The
logs should show `Database release completed successfully` followed by
`Starting SulitShelf with Gunicorn`. Then visit `/health/ready`.
For this release, `/health/live` must report
`"code_release": "2026.07.19-signout.28"`; otherwise the hosting service is still
running an older image or source revision.

Do not set a Render Docker Command for normal deployment; doing so replaces the
safe startup command in the Dockerfile. On a paid plan that uses Render's
pre-deploy command, use `python -m flask --app run.py deploy-release` there and
set `RUN_MIGRATIONS_ON_START=false` on the web service.

### Better Stack heartbeat and external self-ping

SulitShelf exposes three separate health endpoints:

- `/health/live` confirms that the Gunicorn worker is running without calling
  external dependencies.
- `/health/ready` verifies that the PostgreSQL migration schema is current,
  required default data exists, and Redis is reachable. Keep this as Render's
  health-check path.
- `/health/heartbeat` performs the same dependency checks and returns the
  keyword `SULITSHELF_OK` only when the complete application is ready.

Use a Better Stack **keyword monitor** as the external self-ping. This is more
reliable than a timer inside Flask: once Render stops a free container, code in
that container cannot run to wake itself.

1. Generate a separate heartbeat token:

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

2. Save it in Render as `HEARTBEAT_TOKEN` and redeploy.
3. In Better Stack, go to **Monitors → Create monitor** and use:

   ```text
   URL: https://sulitshelf-ph.onrender.com/health/heartbeat
   Alert condition: Doesn't contain a keyword
   Required keyword: SULITSHELF_OK
   Check frequency: 5 minutes (or any interval below 15 minutes)
   Request header: Authorization: Bearer YOUR_HEARTBEAT_TOKEN
   ```

4. Confirm that Better Stack reports the monitor as **Up**. A missing token,
   unavailable database, unavailable Redis service, or unseeded database will
   not return the success keyword.

Create an HTTP/keyword monitor, not a Better Stack cron-heartbeat monitor, for
this purpose. The HTTP monitor sends inbound traffic to SulitShelf and can wake
it; a cron-heartbeat monitor waits for SulitShelf to call Better Stack and
therefore cannot wake a sleeping container.

Keeping a Render Free web service awake continuously uses almost all of the
workspace's 750 monthly free instance hours. If several free web services share
the workspace, allow idle spin-down or move important services to a paid
always-on instance so they are not suspended after the monthly allowance is
used.

### Database backups

Take a PostgreSQL backup before every release and test restores periodically:

```bash
docker compose --env-file .env.production exec -T db \
  pg_dump -U sulitshelf -d sulitshelf -Fc > sulitshelf-backup.dump
```

Store backups encrypted outside the server. Cloudinary assets need their own
retention/backup policy; the database stores only their identifiers.

### Other production platforms

The Docker image migrates, seeds, and starts Gunicorn automatically. If the
platform has a separate release phase, run `scripts/release.sh`, set
`RUN_MIGRATIONS_ON_START=false` for the web process, and start:

```bash
gunicorn -c gunicorn.conf.py run:app
```

`PORT` is honored automatically. Configure the exact number of trusted proxy
hops with the `PROXY_FIX_*` variables; never enable proxy trust for headers that
your hosting provider does not overwrite. Use `/health/live` for liveness,
`/health/ready` for platform readiness, and `/health/heartbeat` for an external
keyword monitor. The Flask development server intentionally refuses to start
when `APP_ENV=production`.

## Pro billing with Dodo

In the Dodo dashboard, create a **recurring monthly** product in PHP priced at
₱49 with no trial. In **Admin CMS → Pro plan settings**, confirm the
monthly price and paste that recurring product ID. Configure the API and
webhook secrets, then register this endpoint and enable all subscription
lifecycle events:

```text
https://your-domain.example/payments/dodo/webhook
```

The application grants Pro only from a signed webhook after verifying the
product ID, PHP amount, zero trial, subscription ID, and future Dodo billing
date. It stores Dodo's last event time to ignore stale lifecycle events. Active
customers can open Dodo's hosted customer portal to view invoices, update their
payment method, or cancel. Do not grant access from the browser return URL.

## Optional Dodo donations

Create **one-time** Dodo products for the suggested donation amounts. In
**Admin CMS → Donation amounts**, paste each matching product ID. Configure the
API and webhook secrets in `.env`, then register:

```text
https://your-domain.example/payments/dodo/webhook
```

The application verifies Standard Webhooks-compatible HMAC signatures,
deduplicates webhook IDs, and records `payment.succeeded`, `payment.failed`,
and `payment.cancelled` results. Donation product IDs must be one-time products,
not the recurring Pro product.

## Manual Pro renewal and optional e-wallet donations

In **Admin CMS → E-wallet & QR**, configure a provider name (for example,
GCash or Maya), account details, support message, and QR image. The same
destination appears in Pro billing and optional support. Every submission
requires a reference number and private receipt screenshot. Approving a Pro
payment grants or extends Pro for 30 days; approving a donation never changes
access. A transaction reference can be used only once across both flows.

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

## Forgot-password email with Resend or MailerSend

Verify a sending domain with either provider, create an API key, and set a
sender address on that verified domain. For automatic Resend-to-MailerSend
failover, configure both credentials:

```env
PASSWORD_RESET_ENABLED=true
EMAIL_PROVIDER=auto
EMAIL_FAILOVER_ENABLED=true
EMAIL_TIMEOUT_SECONDS=10
PASSWORD_RESET_TOKEN_MINUTES=30
MAIL_FROM_EMAIL=no-reply@your-domain.example
MAIL_FROM_NAME=SulitShelf PH
RESEND_API_KEY=re_...
MAILERSEND_API_TOKEN=mlsn....
```

You may configure only one provider. Set `EMAIL_PROVIDER=resend` or
`EMAIL_PROVIDER=mailersend` to prefer it; when failover is enabled, SulitShelf
uses the other configured provider if the first request fails. If email is not
ready, keep `PASSWORD_RESET_ENABLED=false`; the forgot-password link and routes
remain disabled.

The public reset URL comes from `PUBLIC_BASE_URL`. Keep it on your production
HTTPS origin. The application gives the same response for known and unknown
addresses, stores only a token digest, expires links after 30 minutes by
default, permits one use, and revokes all existing account sessions after a
successful reset.

## WebP optimization and Cloudinary storage

Every accepted JPG, PNG, or WebP upload is decoded by Pillow instead of being
trusted by its filename or header. Product images are resized and compressed;
QR codes and private payment/donation receipts use lossless WebP to preserve scannable
and readable detail.

For local development, `IMAGE_STORAGE_BACKEND=auto` uses local files when
Cloudinary is not configured. For deployment, copy the API environment value
from the Cloudinary console and use:

```env
IMAGE_STORAGE_BACKEND=cloudinary
CLOUDINARY_URL=cloudinary://API_KEY:API_SECRET@CLOUD_NAME
CLOUDINARY_FOLDER=sulitshelf
CLOUDINARY_UPLOAD_TIMEOUT_SECONDS=20
IMAGE_WEBP_QUALITY=82
IMAGE_MAX_DIMENSION=1600
```

Public product images and the e-wallet QR use Cloudinary CDN delivery.
Private receipts are uploaded as authenticated assets and exposed only to an
administrator through short-lived signed download URLs. See the
[Cloudinary Python SDK guide](https://cloudinary.com/documentation/python_quickstart).

### Reliable product publishing

The single-product form validates every field before uploading, converts the
selected image to WebP, and limits a Cloudinary request to
`CLOUDINARY_UPLOAD_TIMEOUT_SECONDS` (20 seconds by default). The PostgreSQL shop
row is locked only for the final capacity check and insert, so a slow image
provider does not hold a database transaction open. A repeated submission of
the exact affiliate URL keeps the existing listing instead of creating a
duplicate.

The browser keeps non-file product fields in session storage until the server
confirms a successful or duplicate submission. After a stale CSRF form or a
browser-back restore, the promoter must choose the image again—browsers do not
restore file inputs by design. A visible publishing state prevents accidental
double clicks while the image is being optimized.

## Production safety controls

With `APP_ENV=production`, startup fails unless all of these are configured:

- a unique random `SECRET_KEY` of at least 32 characters;
- a separate, stable random `TWO_FACTOR_ENCRYPTION_KEY` of at least 32 characters;
- a separate, stable random `COMMISSION_HASH_KEY` of at least 32 characters;
- PostgreSQL through Psycopg 3, shared Redis rate-limit storage, and Cloudinary;
- an HTTPS public origin, matching OAuth origin, trusted host, and secure cookies;
- explicit trusted TLS-proxy hop counts and at least one administrator email;
- Dodo live mode plus a webhook key whenever the Dodo API is enabled.

Responses include restrictive content, framing, referrer, permissions, and
cross-origin headers. HTTPS responses receive HSTS. Authenticated pages are
marked `private, no-store`; request logs omit query strings so OAuth callback
codes do not enter normal access logs. Do not set `FLASK_DEBUG` in production.
Keep database, Cloudinary, OAuth, payment, and Flask secrets only in the hosting
secret manager—never in source control or container images.

## Tests

```bash
python -m pytest -q
python -m pip check
python -m pip_audit -r requirements.txt
```

## License

Copyright © 2026 John Edward Q. Dela Cruz. Released under the [MIT License](LICENSE).
