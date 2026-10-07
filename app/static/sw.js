/* Bright Health — fallback HTML only when Render returns a gateway error. */
const CACHE_NAME = "bright-updating-v2";
const FALLBACK_URL = "/static/updating-fallback.html";

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll([FALLBACK_URL])).catch(() => undefined)
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
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
        // Brief network blips should not replace the app with the updating page.
        const cached = await caches.match(request);
        if (cached) return cached;
        throw new Error("network");
      }
    })()
  );
});
