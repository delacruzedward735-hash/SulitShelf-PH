# SulitShelf PH — Go-Live Checklist

This is the practical path from a working codebase to a live production site.
The application itself is complete (tests pass, blueprints load, migrations exist).
What remains is configuration, accounts, and deployment.

---

## 0. Quick status

| Area              | Status                                      |
|-------------------|---------------------------------------------|
| Application code  | Done                                        |
| Migrations        | 0010 (2FA) included                         |
| Tests             | 77 passed                                   |
| Docker / Caddy    | Ready                                       |
| Render templates  | Ready                                       |
| Production safety | Refuses to start if secrets/config are weak |

---

## 1. Generate secrets (do this first)

Run these on your machine (never commit the values):

```bash
python -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(48))"
python -c "import secrets; print('TWO_FACTOR_ENCRYPTION_KEY=' + secrets.token_urlsafe(48))"
python -c "import secrets; print('COMMISSION_HASH_KEY=' + secrets.token_urlsafe(48))"
python -c "import secrets; print('HEARTBEAT_TOKEN=' + secrets.token_urlsafe(32))"
python -c "import secrets; print('POSTGRES_PASSWORD=' + secrets.token_urlsafe(24))"
```

Keep `COMMISSION_HASH_KEY` stable forever. Changing it breaks commission duplicate detection.

---

## 2. Third-party accounts (minimum viable launch)

### Required for a real production site

| Service        | Why                                      | Notes |
|----------------|------------------------------------------|-------|
| **Domain**     | HTTPS + public URLs                      | Point A/AAAA (or CNAME) to your host |
| **PostgreSQL** | Primary database                         | Provided by Docker Compose or Render |
| **Redis**      | Shared rate-limit storage                | Provided by Docker Compose or Render |
| **Cloudinary** | Product images, QR codes, receipts       | Create a free/paid account → copy `CLOUDINARY_URL` |

### Strongly recommended

| Service              | Why |
|----------------------|-----|
| **Dodo Payments**    | Automatic ₱49/month Pro billing |
| **Resend or MailerSend** | Password-reset emails |

### Optional at launch

| Service     | Why |
|-------------|-----|
| Google OAuth  | “Continue with Google” |
| GitHub OAuth  | “Continue with GitHub” |
| Facebook OAuth| “Continue with Facebook” |

You can launch without OAuth and without Dodo. Manual GCash/Maya receipt review still works for Pro renewals and donations.

---

## 3. Choose a host

### Option A — Docker on a VPS (recommended control)

1. Point your domain DNS to the server (ports 80/443 open).
2. Copy the production env template:

```bash
cp .env.production.example .env.production
```

3. Edit `.env.production`:
   - Replace every `REPLACE_...` value
   - Set `DOMAIN`, `TRUSTED_HOSTS`, `PUBLIC_BASE_URL`, `OAUTH_REDIRECT_BASE_URL` to the same HTTPS host
   - URL-encode any special characters in the password used inside `DATABASE_URL`
   - Set `IMAGE_STORAGE_BACKEND=cloudinary` and paste `CLOUDINARY_URL`

4. Deploy:

```bash
docker compose --env-file .env.production config --quiet
docker compose --env-file .env.production up -d --build
docker compose --env-file .env.production ps
curl https://YOUR-DOMAIN/health/ready
```

Caddy handles automatic HTTPS. The `migrate` service runs `deploy-release` before the web process starts.

### Option B — Render (simpler managed host)

1. Create a Docker web service from this repository.
2. Use `Render.env.template` as the starting point for environment variables.
3. Attach a PostgreSQL database and a Redis instance.
4. Set the same secrets and `PUBLIC_BASE_URL` / `TRUSTED_HOSTS` as above.
5. Use the release command:

```text
python -m flask --app run.py deploy-release
```

6. Start command (or Dockerfile default):

```text
gunicorn -c gunicorn.conf.py run:app
```

On Render Free, the web service sleeps after idle time. Use an external uptime monitor that hits `/health/live` if you need it awake, or move to a paid always-on plan.

---

## 4. First-time admin account

Public registration **always** creates promoter accounts. Admins are created only via CLI.

After the app is up and migrations have run:

```bash
# Docker example
docker compose --env-file .env.production exec web \
  python -m flask --app run.py seed-admin --email admin@yourdomain.com

# Or with env vars
ADMIN_EMAIL=admin@yourdomain.com ADMIN_PASSWORD='your-strong-password' \
  python -m flask --app run.py seed-admin
```

If you lose access later:

```bash
python -m flask --app run.py recover-admin --email admin@yourdomain.com
```

`ADMIN_EMAILS` in the environment is only a default target for these CLI commands. Matching that email on a public signup does **not** grant admin rights.

---

## 5. Configure the platform inside the app

Log in as admin and open **Admin CMS**:

1. **Payment settings**
   - Set e-wallet provider (GCash / Maya / etc.)
   - Account name + number
   - Upload the QR image customers will scan
2. **Pro plan settings**
   - Confirm ₱49 monthly price
   - If using Dodo: paste the recurring product ID
3. **Platform / growth**
   - Optional: Admin Picks products
   - Optional: Campaign shelves
4. **Promoter help / services**
   - Set `SERVICE_CONTACT_EMAIL` if you offer paid setup help

### Dodo Payments (automatic Pro)

1. In Dodo create a **recurring monthly** product in PHP priced at ₱49, **no trial**.
2. Copy the product ID into Admin → Pro plan settings.
3. Set `DODO_PAYMENTS_API_KEY` and `DODO_PAYMENTS_WEBHOOK_KEY`.
4. Register the webhook endpoint and enable subscription lifecycle events:

```text
https://YOUR-DOMAIN/payments/dodo/webhook
```

Pro is granted only from a verified webhook, never from the browser return URL.

---

## 6. Post-deploy verification checklist

Run through these once:

- [ ] `https://YOUR-DOMAIN/health/live` → 200
- [ ] `https://YOUR-DOMAIN/health/ready` → 200
- [ ] Home page loads, PWA manifest works
- [ ] Register a test promoter account
- [ ] Login / logout works
- [ ] Promoter can create a shop and add a product (Shopee/Lazada/TikTok link)
- [ ] Product appears on public storefront
- [ ] Click tracking increments (privacy-friendly aggregates)
- [ ] Admin can see the promoter in CRM directory
- [ ] Admin payment settings (QR) save correctly
- [ ] Manual Pro receipt flow works (or Dodo test webhook)
- [ ] Password reset is disabled until email is configured (`PASSWORD_RESET_ENABLED=false` by default)
- [ ] `python -m flask --app run.py check-production` passes (when `APP_ENV=production`)

Optional but recommended:

```bash
python -m pytest -q
python -m pip_audit -r requirements.txt
```

---

## 7. Local development (optional)

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env
# edit .env if needed — SQLite is fine for local work
python -m flask --app run.py db upgrade   # or just let tests create tables
python -m flask --app run.py seed-defaults
python -m flask --app run.py seed-admin --email admin@sulitshelf.local
python run.py
```

Open http://localhost:5000

---

## 8. Ongoing operations

| Task                    | How |
|-------------------------|-----|
| Database backup         | `pg_dump` (see README) — encrypt and store off-server |
| Product health scan     | `python -m flask --app run.py scan-product-health` (cron daily is enough) |
| Release / migrate       | `python -m flask --app run.py deploy-release` |
| Reset a user’s 2FA      | `python -m flask --app run.py reset-two-factor --email user@example.com` |
| Recover locked admin    | `recover-admin` CLI |
| Monitor                 | `/health/live`, `/health/ready`, optional `/health/heartbeat` |

---

## 9. What you still own after launch

The software is ready. These items stay on you:

1. **Legal** — Privacy policy, terms of use, affiliate disclosure language for the Philippines.
2. **Support** — Receipt review turnaround, promoter questions, abuse reports.
3. **Growth** — Getting the first promoters and traffic.
4. **Secrets hygiene** — Rotate only when necessary; never commit `.env.production`.
5. **Dependency updates** — Periodically run `pip-audit` and upgrade carefully.

---

## 10. Minimal “ship it today” path

If you want the shortest path to a live URL:

1. Buy/point a domain.
2. Create Cloudinary account → get `CLOUDINARY_URL`.
3. Generate the five secrets from section 1.
4. Fill `.env.production` (or Render env vars).
5. `docker compose --env-file .env.production up -d --build`
6. Seed admin.
7. Log in → set e-wallet QR + account details.
8. Create a test promoter and list one product.
9. Share the storefront.

Dodo, OAuth, and email can be added later without blocking launch.

---

**You’re done coding. Now ship.**

Questions on any step? Check the full `README.md` and `SECURITY.md` for deeper detail, or ask for help on a specific section (Dodo webhook, Render env map, admin recovery, etc.).
