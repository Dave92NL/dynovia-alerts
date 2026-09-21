"use strict";

/* Service worker: stale-while-revalidate for the app, network-first for data.
 *
 * The app is served from cache so it opens instantly and works on the train,
 * and refreshed in the background so the next open has the new version. Plain
 * cache-first would need the cache name bumped on every deploy, and forgetting
 * once means the phone runs last month's code for ever.
 *
 * The JSON is fetched fresh and only falls back to cache offline: a stale
 * score is worse than an honest "no connection".
 */

const SHELL = "dynovia-shell-v2";
const DATA = "dynovia-data-v1";
const FILES = [
  "./",
  "index.html",
  "app.js",
  "manifest.json",
  "icons/icon-192.png",
  "icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL).then((cache) => cache.addAll(FILES)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((names) =>
        Promise.all(
          names.filter((n) => n !== SHELL && n !== DATA).map((n) => caches.delete(n))
        )
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;

  if (url.pathname.includes("/data/")) {
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          const copy = response.clone();
          caches.open(DATA).then((cache) => cache.put(event.request, copy));
          return response;
        })
        .catch(() => caches.match(event.request))
    );
    return;
  }

  event.respondWith(
    caches.open(SHELL).then(async (cache) => {
      const hit = await cache.match(event.request);
      const fresh = fetch(event.request)
        .then((response) => {
          if (response.ok) cache.put(event.request, response.clone());
          return response;
        })
        .catch(() => hit);
      return hit || fresh;
    })
  );
});

self.addEventListener("push", (event) => {
  let payload = { title: "Dynovia", body: "" };
  try {
    payload = { ...payload, ...event.data.json() };
  } catch {
    payload.body = event.data ? event.data.text() : "";
  }
  event.waitUntil(
    self.registration.showNotification(payload.title, {
      body: payload.body,
      icon: "icons/icon-192.png",
      badge: "icons/icon-192.png",
      tag: payload.tag || "dynovia",
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      for (const client of list) if ("focus" in client) return client.focus();
      return self.clients.openWindow("./");
    })
  );
});
