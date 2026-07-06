interface Env {
  ASSETS: Fetcher;
  // default "https://stonks-api.ikusner.dev"; local dev: http://localhost:8000 via .dev.vars
  API_ORIGIN?: string;
  // service token; set as wrangler secrets in prod, absent locally
  CF_ACCESS_CLIENT_ID?: string;
  CF_ACCESS_CLIENT_SECRET?: string;
}

const DEFAULT_API_ORIGIN = "https://stonks-api.ikusner.dev";

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname.startsWith("/api/")) {
      return proxyToApi(request, url, env);
    }

    return env.ASSETS.fetch(request);
  },
} satisfies ExportedHandler<Env>;

async function proxyToApi(request: Request, url: URL, env: Env): Promise<Response> {
  const origin = env.API_ORIGIN ?? DEFAULT_API_ORIGIN;
  const target = new URL(url.pathname + url.search, origin);

  const headers = new Headers(request.headers);
  if (env.CF_ACCESS_CLIENT_ID && env.CF_ACCESS_CLIENT_SECRET) {
    headers.set("CF-Access-Client-Id", env.CF_ACCESS_CLIENT_ID);
    headers.set("CF-Access-Client-Secret", env.CF_ACCESS_CLIENT_SECRET);
  }

  let response: Response;
  try {
    // Stream the request/response through untouched so SSE and large bodies
    // are not buffered in memory.
    response = await fetch(target, {
      method: request.method,
      headers,
      body: request.body,
      redirect: "manual",
      // @ts-expect-error - required by workerd when streaming a request body through
      duplex: "half",
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return Response.json({ detail: { message: `API unreachable: ${message}` } }, { status: 502 });
  }

  if (response.status >= 300 && response.status < 400) {
    const location = response.headers.get("Location");
    if (location) {
      const resolved = new URL(location, target);
      if (resolved.hostname.endsWith("cloudflareaccess.com")) {
        return Response.json(
          { detail: { message: "Cloudflare Access rejected the request (service token missing/expired?)." } },
          { status: 502 },
        );
      }
      if (resolved.origin === target.origin) {
        const rewritten = new Headers(response.headers);
        rewritten.set("Location", resolved.pathname + resolved.search);
        return new Response(response.body, {
          status: response.status,
          statusText: response.statusText,
          headers: rewritten,
        });
      }
    }
  }

  return response;
}
