from pathlib import Path


def replace_once(path, old, new):
    file_path = Path(path)
    text = file_path.read_text()
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one anchor in {path}, found {count}: {old[:80]!r}")
    file_path.write_text(text.replace(old, new, 1))


def insert_before_last(path, anchor, content):
    file_path = Path(path)
    text = file_path.read_text()
    index = text.rfind(anchor)
    if index < 0:
        raise RuntimeError(f"Missing final anchor in {path}: {anchor!r}")
    file_path.write_text(text[:index] + content + text[index:])


replace_once(
    "app/services/health.py",
    "from app.models import PlatformSettings\n",
    "from app.models import PlatformSettings, Product, Shop, User\n",
)

health_monitor_code = '''

def admin_health_report():
    # Administrator-safe operational snapshot. Never return URLs with
    # credentials, API keys, bearer tokens, passwords, or provider secrets.
    report, readiness_status = readiness_report()
    checks = dict(report.get("checks", {}))
    counts = {
        "users_total": 0,
        "active_promoters": 0,
        "shops_total": 0,
        "products_total": 0,
        "public_products": 0,
        "paused_products": 0,
    }
    recommendations = []

    try:
        counts["users_total"] = db.session.scalar(db.select(db.func.count(User.id))) or 0
        counts["active_promoters"] = (
            db.session.scalar(
                db.select(db.func.count(User.id)).where(
                    User.role == "promoter",
                    User.is_active_account.is_(True),
                )
            )
            or 0
        )
        counts["shops_total"] = db.session.scalar(db.select(db.func.count(Shop.id))) or 0
        counts["products_total"] = db.session.scalar(db.select(db.func.count(Product.id))) or 0
        counts["paused_products"] = (
            db.session.scalar(db.select(db.func.count(Product.id)).where(Product.status != "active"))
            or 0
        )
        counts["public_products"] = (
            db.session.scalar(
                db.select(db.func.count(Product.id))
                .join(Shop, Product.shop_id == Shop.id)
                .join(User, Shop.owner_id == User.id)
                .where(
                    Product.status == "active",
                    User.is_active_account.is_(True),
                )
            )
            or 0
        )
        catalog_status = "ok" if counts["public_products"] else "warning"
        checks["catalog"] = {
            "status": catalog_status,
            "public_products": counts["public_products"],
            "products_total": counts["products_total"],
            "paused_products": counts["paused_products"],
        }
        if catalog_status == "warning":
            recommendations.append(
                "No public products are visible. Publish or restore genuine active listings before promoting the mall."
            )
    except Exception as error:
        db.session.rollback()
        checks["catalog"] = {"status": "failed", "reason": "catalog_query_failed"}
        recommendations.append(
            "Catalog diagnostics failed. Check the application logs and database schema before publishing changes."
        )
        current_app.logger.warning(
            "admin health catalog check failed error_type=%s",
            type(error).__name__,
        )

    requested_storage = current_app.config["IMAGE_STORAGE_BACKEND"]
    cloudinary_configured = bool(current_app.config["CLOUDINARY_URL"])
    if requested_storage == "auto":
        effective_storage = "cloudinary" if cloudinary_configured else "local"
    else:
        effective_storage = requested_storage

    storage_status = "ok"
    storage_reason = None
    if requested_storage == "cloudinary" and not cloudinary_configured:
        storage_status = "failed"
        storage_reason = "cloudinary_not_configured"
        recommendations.append(
            "Cloudinary is selected but not configured. Set CLOUDINARY_URL before accepting image uploads."
        )
    elif current_app.config["IS_PRODUCTION"] and effective_storage == "local":
        storage_status = "warning"
        storage_reason = "local_storage_in_production"
        recommendations.append(
            "Image storage is local in production. Use Cloudinary or verified persistent storage to prevent upload loss."
        )
    checks["storage"] = {
        "status": storage_status,
        "backend": effective_storage,
        "reason": storage_reason,
    }

    redis_status = checks.get("redis", {}).get("status")
    if current_app.config["IS_PRODUCTION"] and redis_status == "skipped":
        recommendations.append(
            "Redis is not configured. Use Redis for shared rate-limit state before scaling to multiple web instances."
        )

    heartbeat_configured = bool(current_app.config["HEARTBEAT_TOKEN"])
    if current_app.config["IS_PRODUCTION"] and not heartbeat_configured:
        recommendations.append(
            "HEARTBEAT_TOKEN is not configured. Add one for authenticated external uptime monitoring."
        )

    database_backend = db.engine.url.get_backend_name()
    if current_app.config["IS_PRODUCTION"] and database_backend != "postgresql":
        recommendations.append(
            "Production is not using PostgreSQL. Confirm DATABASE_URL points to the intended production database."
        )

    public_url = urlparse(current_app.config["PUBLIC_BASE_URL"])
    public_host = public_url.netloc or public_url.path or "local"
    email_configured = bool(
        current_app.config["RESEND_API_KEY"] or current_app.config["MAILERSEND_API_TOKEN"]
    )

    critical = readiness_status != 200 or any(
        checks.get(name, {}).get("status") == "failed"
        for name in ("database", "redis", "catalog", "storage")
    )
    warning = any(
        checks.get(name, {}).get("status") == "warning"
        for name in ("catalog", "storage")
    ) or (
        current_app.config["IS_PRODUCTION"]
        and (redis_status == "skipped" or not heartbeat_configured)
    )

    status = "critical" if critical else ("warning" if warning else "healthy")
    return {
        "status": status,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "app": {
            "version": report.get("version"),
            "code_release": report.get("code_release"),
            "started_at": report.get("started_at"),
            "uptime_seconds": report.get("uptime_seconds"),
        },
        "checks": checks,
        "counts": counts,
        "configuration": {
            "environment": current_app.config["APP_ENV"],
            "public_host": public_host,
            "database_backend": database_backend,
            "heartbeat_configured": heartbeat_configured,
            "password_reset_enabled": bool(current_app.config["PASSWORD_RESET_ENABLED"]),
            "email_configured": email_configured,
            "storage_backend": effective_storage,
        },
        "recommendations": recommendations,
    }
'''
replace_once(
    "app/services/health.py",
    "\n\ndef _elapsed_ms(started):\n",
    health_monitor_code + "\n\ndef _elapsed_ms(started):\n",
)

replace_once(
    "app/admin/routes.py",
    "from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for\n",
    "from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, send_file, url_for\n",
)
replace_once(
    "app/admin/routes.py",
    "from app.services.email import EmailDeliveryError, send_crm_message_email\n",
    "from app.services.email import EmailDeliveryError, send_crm_message_email\nfrom app.services.health import admin_health_report\n",
)
replace_once(
    "app/admin/routes.py",
    "    return render_template(\n        \"admin.html\",\n",
    "    health = admin_health_report()\n    return render_template(\n        \"admin.html\",\n",
)
replace_once(
    "app/admin/routes.py",
    "        stats=stats,\n    )\n\n\n@bp.post(\"/crm/messages\")\n",
    "        stats=stats,\n        health=health,\n    )\n\n\n@bp.get(\"/health.json\")\n@admin_required\ndef health_monitor_json():\n    response = jsonify(admin_health_report())\n    response.headers[\"Cache-Control\"] = \"private, no-store\"\n    return response\n\n\n@bp.post(\"/crm/messages\")\n",
)

replace_once(
    "app/templates/admin.html",
    "{% block title %}Admin CMS · SulitShelf PH{% endblock %}\n",
    "{% block title %}Admin CMS · SulitShelf PH{% endblock %}\n{% block meta %}<meta name=\"description\" content=\"Private SulitShelf administrator operations console.\"><link rel=\"stylesheet\" href=\"{{ url_for('static', filename='css/admin-health.css', v='health-1') }}\">{% endblock %}\n",
)
replace_once(
    "app/templates/admin.html",
    '<nav><button data-tab-button="overview">Dashboard</button>',
    '<nav><button data-tab-button="overview">Dashboard</button><button data-tab-button="health">System health</button>',
)

health_section = '''
    <section data-tab-panel="health" hidden data-health-monitor data-health-endpoint="{{ url_for('admin.health_monitor_json') }}" data-health-state="{{ health.status }}">
      <div class="health-hero">
        <div><p>OPERATIONS &amp; RELIABILITY</p><h2>System health monitor</h2><span>Private runtime checks for the web app, PostgreSQL, Redis, image storage, and public catalog.</span></div>
        <div class="health-overall"><small>OVERALL STATUS</small><strong data-health-overall>{{ health.status|upper }}</strong><span data-health-checked>Checked {{ health.checked_at }}</span><button type="button" data-health-refresh>Run health check</button></div>
      </div>

      <div class="health-grid">
        {% for key, label in [('database','PostgreSQL / DB'),('redis','Redis / rate limit'),('storage','Image storage'),('catalog','Public catalog')] %}
        {% set item = health.checks.get(key, {}) %}
        <article class="health-card" data-health-check-card="{{ key }}" data-state="{{ item.get('status','unknown') }}"><div><span class="health-dot"></span><small>{{ label }}</small></div><strong data-health-check-status="{{ key }}">{{ item.get('status','unknown')|upper }}</strong><p>{% if item.get('latency_ms') is not none %}<span data-health-latency="{{ key }}">{{ item.get('latency_ms') }} ms</span>{% elif key == 'storage' %}<span data-health-latency="{{ key }}">{{ item.get('backend','unknown')|title }}</span>{% elif key == 'catalog' %}<span data-health-latency="{{ key }}">{{ item.get('public_products',0) }} public products</span>{% else %}<span data-health-latency="{{ key }}">No latency sample</span>{% endif %}</p></article>
        {% endfor %}
      </div>

      <div class="health-layout">
        <div class="panel health-runtime-panel"><div class="panel-title"><div><p>APPLICATION RUNTIME</p><h2>Release and uptime</h2></div><span>No secrets exposed</span></div><div class="health-facts"><article><small>Environment</small><strong>{{ health.configuration.environment|upper }}</strong></article><article><small>App version</small><strong>{{ health.app.version }}</strong></article><article><small>Code release</small><strong>{{ health.app.code_release }}</strong></article><article><small>Uptime</small><strong><span data-health-uptime>{{ health.app.uptime_seconds|int }}</span> sec</strong></article><article><small>Public host</small><strong>{{ health.configuration.public_host }}</strong></article><article><small>Database engine</small><strong>{{ health.configuration.database_backend|upper }}</strong></article></div></div>
        <div class="panel health-config-panel"><div class="panel-title"><div><p>PRODUCTION SIGNALS</p><h2>Configuration readiness</h2></div><span>Presence only</span></div><div class="health-config-list"><article><span>Heartbeat protection</span><strong class="{{ 'ok' if health.configuration.heartbeat_configured else 'warn' }}">{{ 'CONFIGURED' if health.configuration.heartbeat_configured else 'NOT SET' }}</strong></article><article><span>Password reset</span><strong class="{{ 'ok' if health.configuration.password_reset_enabled else 'warn' }}">{{ 'ENABLED' if health.configuration.password_reset_enabled else 'DISABLED' }}</strong></article><article><span>Email delivery</span><strong class="{{ 'ok' if health.configuration.email_configured else 'warn' }}">{{ 'CONFIGURED' if health.configuration.email_configured else 'NOT SET' }}</strong></article><article><span>Image backend</span><strong>{{ health.configuration.storage_backend|upper }}</strong></article></div></div>
      </div>

      <div class="panel health-catalog-panel"><div class="panel-title"><div><p>DATA VISIBILITY</p><h2>Catalog and account counts</h2></div><span>Read-only database totals</span></div><div class="health-counts"><article><small>Users</small><strong data-health-count="users_total">{{ health.counts.users_total }}</strong></article><article><small>Active promoters</small><strong data-health-count="active_promoters">{{ health.counts.active_promoters }}</strong></article><article><small>Shops</small><strong data-health-count="shops_total">{{ health.counts.shops_total }}</strong></article><article><small>All products</small><strong data-health-count="products_total">{{ health.counts.products_total }}</strong></article><article><small>Public products</small><strong data-health-count="public_products">{{ health.counts.public_products }}</strong></article><article><small>Paused products</small><strong data-health-count="paused_products">{{ health.counts.paused_products }}</strong></article></div></div>

      <div class="panel health-actions"><div class="panel-title"><div><p>OPERATOR ACTIONS</p><h2>Warnings and recommendations</h2></div><span data-health-recommendation-count>{{ health.recommendations|length }} item{{ '' if health.recommendations|length == 1 else 's' }}</span></div><div data-health-recommendations>{% if health.recommendations %}<ul>{% for recommendation in health.recommendations %}<li>{{ recommendation }}</li>{% endfor %}</ul>{% else %}<div class="health-clear">✓ Core checks are healthy. No immediate operator action is required.</div>{% endif %}</div><div class="health-links"><a href="{{ url_for('health_live') }}" target="_blank" rel="noopener">Liveness endpoint ↗</a><a href="{{ url_for('health_ready') }}" target="_blank" rel="noopener">Readiness endpoint ↗</a></div></div>
    </section>

'''
replace_once(
    "app/templates/admin.html",
    '    <section data-tab-panel="campaigns" hidden>\n',
    health_section + '    <section data-tab-panel="campaigns" hidden>\n',
)
insert_before_last(
    "app/templates/admin.html",
    "{% endblock %}",
    "  <script src=\"{{ url_for('static', filename='js/admin-health.js', v='health-1') }}\" defer></script>\n",
)

Path("app/static/css/admin-health.css").write_text('''.health-hero{display:flex;justify-content:space-between;gap:24px;align-items:stretch;padding:28px;border-radius:24px;background:linear-gradient(135deg,#15151a,#23232b);color:#fff;margin-bottom:20px}.health-hero>div:first-child{max-width:720px}.health-hero p,.health-runtime-panel .panel-title p,.health-config-panel .panel-title p,.health-catalog-panel .panel-title p,.health-actions .panel-title p{margin:0 0 6px;font-size:.72rem;font-weight:800;letter-spacing:.14em}.health-hero h2{font-size:clamp(1.8rem,3vw,2.8rem);margin:0 0 8px}.health-hero>div:first-child span{color:#c8c8d0;line-height:1.6}.health-overall{min-width:230px;padding:18px;border:1px solid rgba(255,255,255,.12);border-radius:18px;background:rgba(255,255,255,.06);display:grid;gap:7px;align-content:center}.health-overall small{font-size:.68rem;letter-spacing:.12em;color:#bcbcc7}.health-overall strong{font-size:1.45rem}.health-overall span{font-size:.72rem;color:#a9a9b6;overflow-wrap:anywhere}.health-overall button{border:0;border-radius:12px;padding:10px 12px;background:#fff;color:#15151a;font-weight:800;cursor:pointer}.health-overall button:disabled{opacity:.55;cursor:wait}[data-health-state="healthy"] .health-overall strong{color:#54d889}[data-health-state="warning"] .health-overall strong{color:#ffd166}[data-health-state="critical"] .health-overall strong{color:#ff6b6b}.health-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin-bottom:20px}.health-card{padding:18px;border:1px solid #e6e6ec;border-radius:18px;background:#fff;box-shadow:0 8px 28px rgba(21,21,26,.05)}.health-card>div{display:flex;align-items:center;gap:8px}.health-card small{font-weight:800;color:#666674}.health-card strong{display:block;font-size:1.15rem;margin:14px 0 5px}.health-card p{margin:0;color:#7d7d88;font-size:.82rem}.health-dot{width:9px;height:9px;border-radius:99px;background:#a3a3ad;box-shadow:0 0 0 4px rgba(163,163,173,.13)}.health-card[data-state="ok"] .health-dot{background:#27ae60;box-shadow:0 0 0 4px rgba(39,174,96,.13)}.health-card[data-state="warning"] .health-dot,.health-card[data-state="skipped"] .health-dot{background:#e9a700;box-shadow:0 0 0 4px rgba(233,167,0,.13)}.health-card[data-state="failed"] .health-dot{background:#e74c3c;box-shadow:0 0 0 4px rgba(231,76,60,.13)}.health-layout{display:grid;grid-template-columns:1.35fr .8fr;gap:18px;margin-bottom:18px}.health-facts{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.health-facts article,.health-counts article{padding:14px;border-radius:14px;background:#f7f7fa;border:1px solid #ececf1}.health-facts small,.health-counts small{display:block;color:#767681;font-size:.72rem;margin-bottom:6px}.health-facts strong{font-size:.88rem;overflow-wrap:anywhere}.health-config-list{display:grid;gap:9px}.health-config-list article{display:flex;justify-content:space-between;gap:12px;padding:12px 0;border-bottom:1px solid #eeeeef}.health-config-list article:last-child{border-bottom:0}.health-config-list span{color:#64646f}.health-config-list strong{font-size:.76rem}.health-config-list strong.ok{color:#198754}.health-config-list strong.warn{color:#b87500}.health-counts{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px}.health-counts strong{font-size:1.4rem}.health-actions{margin-top:18px}.health-actions ul{margin:0;padding-left:20px;display:grid;gap:9px;color:#575762;line-height:1.5}.health-clear{padding:14px;border-radius:14px;background:#eefaf3;color:#1e7a46;font-weight:700}.health-links{display:flex;gap:14px;flex-wrap:wrap;margin-top:16px}.health-links a{font-weight:800;text-decoration:none}.health-monitor-refreshing{opacity:.82}.health-monitor-error{outline:2px solid rgba(231,76,60,.35)}@media(max-width:980px){.health-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.health-layout{grid-template-columns:1fr}.health-counts{grid-template-columns:repeat(3,minmax(0,1fr))}}@media(max-width:640px){.health-hero{flex-direction:column;padding:20px}.health-overall{min-width:0}.health-grid{grid-template-columns:1fr}.health-facts,.health-counts{grid-template-columns:repeat(2,minmax(0,1fr))}}
''')

Path("app/static/js/admin-health.js").write_text('''(() => {
  const root = document.querySelector('[data-health-monitor]');
  if (!root) return;
  const endpoint = root.dataset.healthEndpoint;
  const refreshButton = root.querySelector('[data-health-refresh]');

  const setText = (selector, value) => {
    const node = root.querySelector(selector);
    if (node && value !== undefined && value !== null) node.textContent = String(value);
  };

  const render = (data) => {
    root.dataset.healthState = data.status || 'unknown';
    setText('[data-health-overall]', String(data.status || 'unknown').toUpperCase());
    setText('[data-health-checked]', `Checked ${data.checked_at || 'just now'}`);
    if (data.app) setText('[data-health-uptime]', Math.floor(Number(data.app.uptime_seconds || 0)));

    ['database', 'redis', 'storage', 'catalog'].forEach((key) => {
      const check = (data.checks || {})[key] || {};
      const card = root.querySelector(`[data-health-check-card="${key}"]`);
      if (card) card.dataset.state = check.status || 'unknown';
      setText(`[data-health-check-status="${key}"]`, String(check.status || 'unknown').toUpperCase());
      let detail = 'No latency sample';
      if (check.latency_ms !== undefined) detail = `${check.latency_ms} ms`;
      else if (key === 'storage') detail = String(check.backend || 'unknown');
      else if (key === 'catalog') detail = `${check.public_products || 0} public products`;
      setText(`[data-health-latency="${key}"]`, detail);
    });

    Object.entries(data.counts || {}).forEach(([key, value]) => {
      setText(`[data-health-count="${key}"]`, value);
    });

    const recommendations = Array.isArray(data.recommendations) ? data.recommendations : [];
    setText('[data-health-recommendation-count]', `${recommendations.length} item${recommendations.length === 1 ? '' : 's'}`);
    const recommendationsRoot = root.querySelector('[data-health-recommendations]');
    if (recommendationsRoot) {
      recommendationsRoot.replaceChildren();
      if (recommendations.length) {
        const list = document.createElement('ul');
        recommendations.forEach((message) => {
          const item = document.createElement('li');
          item.textContent = message;
          list.appendChild(item);
        });
        recommendationsRoot.appendChild(list);
      } else {
        const clear = document.createElement('div');
        clear.className = 'health-clear';
        clear.textContent = '✓ Core checks are healthy. No immediate operator action is required.';
        recommendationsRoot.appendChild(clear);
      }
    }
    root.classList.remove('health-monitor-error');
  };

  const refresh = async () => {
    if (!endpoint || root.classList.contains('health-monitor-refreshing')) return;
    root.classList.add('health-monitor-refreshing');
    if (refreshButton) refreshButton.disabled = true;
    try {
      const response = await fetch(endpoint, {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
        cache: 'no-store',
      });
      if (!response.ok) throw new Error(`health check failed: ${response.status}`);
      render(await response.json());
    } catch (_) {
      root.classList.add('health-monitor-error');
      setText('[data-health-overall]', 'CHECK FAILED');
    } finally {
      root.classList.remove('health-monitor-refreshing');
      if (refreshButton) refreshButton.disabled = false;
    }
  };

  if (refreshButton) refreshButton.addEventListener('click', refresh);
  window.setInterval(() => {
    if (!document.hidden && !root.hidden) refresh();
  }, 30000);
})();
''')

tests_path = Path("tests/test_app.py")
tests_text = tests_path.read_text()
marker = "def test_superadmin_health_monitor_is_private_and_safe"
if marker in tests_text:
    raise RuntimeError("Health monitor tests already exist")

tests_text += '''


def test_superadmin_health_monitor_is_private_and_safe(client, app):
    anonymous = client.get("/admin/health.json")
    assert anonymous.status_code in {302, 401}

    register(client, email="health-admin@example.com")
    promoter = client.get("/admin/health.json")
    assert promoter.status_code == 403

    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.email == "health-admin@example.com"))
        user.role = "admin"
        db.session.commit()

    page = client.get("/admin/?tab=health")
    assert page.status_code == 200
    assert b"System health monitor" in page.data
    assert b"PostgreSQL / DB" in page.data
    assert b"Public catalog" in page.data
    assert b"Run health check" in page.data

    response = client.get("/admin/health.json")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    payload = response.get_json()
    assert payload["checks"]["database"]["status"] == "ok"
    assert payload["checks"]["catalog"]["status"] == "warning"
    assert payload["counts"]["public_products"] == 0
    assert payload["configuration"]["database_backend"] == "sqlite"
    serialized = json.dumps(payload).lower()
    assert "database_url" not in serialized
    assert "secret_key" not in serialized
    assert "cloudinary_url" not in serialized
    assert "api_key" not in serialized


def test_superadmin_health_monitor_marks_real_public_catalog_healthy(client, app):
    register(client, email="health-catalog-admin@example.com")
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.email == "health-catalog-admin@example.com"))
        user.role = "admin"
        product = Product(
            shop=user.shop,
            name="Health Monitor Test Product",
            description="A real-looking test listing used only inside the isolated test database.",
            why_sulit="It verifies that active visible products are counted correctly by the health monitor.",
            best_for="automated regression tests",
            department="Tech & Gadgets",
            marketplace="shopee",
            affiliate_url="https://shopee.ph/health-monitor-test",
            price_cents=19900,
            image_name="missing.webp",
            status="active",
        )
        db.session.add(product)
        db.session.commit()

    payload = client.get("/admin/health.json").get_json()
    assert payload["checks"]["catalog"]["status"] == "ok"
    assert payload["counts"]["public_products"] == 1
    assert payload["counts"]["products_total"] == 1
'''
tests_path.write_text(tests_text)

print("Superadmin health monitor patch applied successfully.")
