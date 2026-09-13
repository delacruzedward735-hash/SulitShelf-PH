(() => {
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
