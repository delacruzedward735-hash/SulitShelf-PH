const CACHE = "sulitshelf-shell-v12";
const SHELL = ["/static/offline.html", "/static/css/app.css?v=signout-28", "/static/js/app.js?v=signout-28", "/static/images/brand-icon.webp", "/static/images/favicon.ico", "/static/images/favicon-48x48.png", "/static/images/apple-touch-icon.png", "/static/images/app-icon-192.png", "/static/images/app-icon-512.png", "/static/images/app-icon-maskable-512.png", "/static/images/product-placeholder.svg"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key)))));
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(caches.match(event.request).then((cached) => cached || fetch(event.request).then((response) => {
      if (response.ok) caches.open(CACHE).then((cache) => cache.put(event.request, response.clone()));
      return response;
    })));
    return;
  }
  if (event.request.mode === "navigate") {
    event.respondWith(fetch(event.request).catch(() => caches.match("/static/offline.html")));
  }
});
