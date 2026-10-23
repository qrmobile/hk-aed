// hkaed Pages advanced-mode worker.
// Purpose: serve the Google Search Console verification file at its exact
// .html URL with HTTP 200. Cloudflare Pages' pretty-URL behaviour otherwise
// 308-redirects /google*.html to the extensionless path, and Search Console
// does not follow redirects when checking the verification file.
// Everything else is passed straight through to the static assets.
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/google1f94042d93f7e344.html") {
      return new Response("google-site-verification: google1f94042d93f7e344.html", {
        headers: { "content-type": "text/html; charset=utf-8" },
      });
    }
    return env.ASSETS.fetch(request);
  },
};
