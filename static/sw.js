// Minimal service worker — exists only to satisfy the browser's
// installability requirement (Android Chrome requires an active
// service worker with a fetch handler before it will offer to install
// a PWA). This app is dynamic/authenticated, so it deliberately does
// NOT cache or intercept anything — every request just passes straight
// through to the network, unchanged.
self.addEventListener("fetch", function () {
  // no-op: let the browser handle every request normally
});
