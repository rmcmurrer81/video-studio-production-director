"use strict";

const SHELL_CACHE = "video-studio-shell-v2";
const SHELL_ASSETS = [
  "/",
  "/manifest.webmanifest",
  "/icons/video-studio-icon-192.png",
  "/icons/video-studio-icon-512.png",
];

self.addEventListener("install", event => {
  event.waitUntil(caches.open(SHELL_CACHE).then(cache => cache.addAll(SHELL_ASSETS)));
  self.skipWaiting();
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(
      keys.filter(key => key !== SHELL_CACHE).map(key => caches.delete(key)),
    )).then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", event => {
  const requestUrl = new URL(event.request.url);
  if (event.request.method !== "GET" || requestUrl.origin !== self.location.origin) return;
  if (requestUrl.pathname.startsWith("/v1/") || requestUrl.pathname === "/health") return;
  event.respondWith(
    fetch(event.request).catch(() => caches.match(event.request).then(
      cached => cached || (event.request.mode === "navigate" ? caches.match("/") : undefined),
    )),
  );
});
