/* Bright Health — gateway fallback plus family update push. */
const CACHE_NAME = "bright-updating-v3";
const FALLBACK_URL = "/static/updating-fallback.html";

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) => cache.addAll([FALLBACK_URL]))
      .catch(() => undefined)
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

function isGatewayStatus(status) {
  return status === 502 || status === 503 || status === 504;
}

async function fallbackResponse() {
  const cached = await caches.match(FALLBACK_URL);
  if (cached) return cached;
  return Response.redirect(FALLBACK_URL, 302);
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;

  const accept = request.headers.get("accept") || "";
  const isNavigate = request.mode === "navigate" || accept.includes("text/html");
  if (!isNavigate) return;

  event.respondWith(
    (async () => {
      try {
        const response = await fetch(request);
        if (isGatewayStatus(response.status)) {
          return (await fallbackResponse()) || response;
        }
        return response;
      } catch {
        const cached = await caches.match(request);
        if (cached) return cached;
        throw new Error("network");
      }
    })()
  );
});

function pushPayload(event) {
  if (!event.data) return {};
  try {
    return event.data.json() || {};
  } catch {
    try {
      const text = event.data.text();
      return text ? { body: text } : {};
    } catch {
      return {};
    }
  }
}

self.addEventListener("push", (event) => {
  const data = pushPayload(event);
  const title = data.title || "Bright Health";
  const options = {
    body: data.body || "There is a new update.",
    icon: "/static/icon-192.png",
    badge: "/static/favicon.png",
    data: { url: data.url || "/#timeline" },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = event.notification.data?.url || "/#timeline";
  event.waitUntil(
    (async () => {
      const windows = await clients.matchAll({ type: "window", includeUncontrolled: true });
      for (const client of windows) {
        try {
          const origin = new URL(client.url).origin;
          if (origin === self.location.origin && "focus" in client) {
            if ("navigate" in client) {
              try {
                await client.navigate(target);
              } catch {
                /* keep the existing tab */
              }
            }
            return client.focus();
          }
        } catch {
          /* ignore malformed client urls */
        }
      }
      if (clients.openWindow) return clients.openWindow(target);
      return undefined;
    })()
  );
});
