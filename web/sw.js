"use strict";

const VERSION = "0.10.0-rc.1";
const CACHE = `dutralog-driver-shell-${VERSION}`;
const SHELL = [
  "/motorista",
  "/manifest.webmanifest",
  "/assets/app.css",
  "/assets/icon.svg",
  "/assets/apple-touch-icon.png",
  "/assets/icon-192.png",
  "/assets/icon-512.png",
  "/assets/icon-maskable-512.png",
  "/assets/delivery-form.js",
  "/assets/offline-queue.js",
  "/assets/motorista.js"
];

self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)).then(() => {
    // Primeira instalação ativa imediatamente. Atualizações aguardam o motorista
    // confirmar na tela para não interromper o preenchimento de uma ocorrência.
    if (!self.registration.active) return self.skipWaiting();
    return undefined;
  }));
});

self.addEventListener("message", event => {
  if (event.data && event.data.type === "SKIP_WAITING") self.skipWaiting();
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", event => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;

  if (request.mode === "navigate") {
    if (url.pathname !== "/motorista") return;
    event.respondWith(
      fetch(request)
        .then(response => {
          if (response.ok && url.pathname === "/motorista") {
            caches.open(CACHE).then(cache => cache.put("/motorista", response.clone()));
          }
          return response;
        })
        .catch(() => caches.match("/motorista"))
    );
    return;
  }

  event.respondWith(
    caches.match(request).then(cached => cached || fetch(request).then(response => {
      if (response.ok) caches.open(CACHE).then(cache => cache.put(request, response.clone()));
      return response;
    }))
  );
});
