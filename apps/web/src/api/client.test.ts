import { describe, expect, it } from "vitest";

import { ApiError, createApiClient, readCookie, unwrap } from "./client";
import { shouldRetryQuery } from "./queryClient";

type Handler = (request: Request) => Response | Promise<Response>;

function harness(handler: Handler, cookies = "rd_csrf=tok1") {
  const seen: Request[] = [];
  let jar = cookies;
  const client = createApiClient({
    baseUrl: "http://api.test",
    cookies: () => jar,
    fetch: async (request) => {
      seen.push(request.clone());
      return handler(request);
    },
  });
  return { client, seen, setCookies: (value: string) => (jar = value) };
}

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

describe("readCookie", () => {
  it("finds a cookie among others and decodes it", () => {
    expect(readCookie("rd_csrf", "a=1; rd_csrf=x%2By; b=2")).toBe("x+y");
    expect(readCookie("rd_csrf", "a=1")).toBeUndefined();
    expect(readCookie("rd_csrf", "")).toBeUndefined();
  });
});

describe("CSRF header", () => {
  it("is sent on an unsafe request and not on a read", async () => {
    const { client, seen } = harness(() =>
      json(200, { items: [], total: 0, limit: 50, offset: 0 }),
    );
    await client.GET("/api/v1/tickets");
    await client.POST("/api/v1/auth/logout");
    expect(seen[0]!.headers.get("X-CSRF-Token")).toBeNull();
    expect(seen[1]!.headers.get("X-CSRF-Token")).toBe("tok1");
  });

  it("is left off when there is no token cookie", async () => {
    const { client, seen } = harness(() => new Response(null, { status: 204 }), "");
    await client.POST("/api/v1/auth/logout");
    expect(seen[0]!.headers.get("X-CSRF-Token")).toBeNull();
  });

  it("sends the session cookies with the request", async () => {
    const { client, seen } = harness(() => json(200, { email: "o@example.com" }));
    await client.GET("/api/v1/auth/me");
    expect(seen[0]!.credentials).toBe("same-origin");
  });
});

describe("an expired session", () => {
  it("is refreshed once and the request repeated with the new token", async () => {
    let first = true;
    const { client, seen, setCookies } = harness((request) => {
      const path = new URL(request.url).pathname;
      if (path === "/api/v1/auth/refresh") {
        setCookies("rd_csrf=tok2");
        return json(200, { csrf_token: "tok2", access_expires_in: 900 });
      }
      if (first) {
        first = false;
        return json(401, { detail: "expired" });
      }
      return json(200, { ticket_id: "t" });
    });
    const result = await client.POST("/api/v1/tickets/{ticket_id}/reject", {
      params: { path: { ticket_id: "3fa85f64-5717-4562-b3fc-2c963f66afa6" } },
      body: { reason: "a reason that is long enough" },
    });
    expect(result.response.status).toBe(200);
    const paths = seen.map((r) => new URL(r.url).pathname);
    expect(paths.filter((p) => p === "/api/v1/auth/refresh")).toHaveLength(1);
    expect(seen.at(-1)!.headers.get("X-CSRF-Token")).toBe("tok2");
    expect(await seen.at(-1)!.text()).toContain("a reason that is long enough"); // body kept
  });

  it("is refreshed once however many requests fail together", async () => {
    let refreshes = 0;
    const failed = new Set<string>();
    const { client } = harness(async (request) => {
      const path = new URL(request.url).pathname;
      if (path === "/api/v1/auth/refresh") {
        refreshes += 1;
        await new Promise((resolve) => setTimeout(resolve, 10));
        return json(200, { csrf_token: "t", access_expires_in: 900 });
      }
      if (!failed.has(request.url)) {
        failed.add(request.url);
        return json(401, { detail: "expired" });
      }
      return json(200, { items: [], total: 0, limit: 50, offset: 0 });
    });
    const results = await Promise.all([
      client.GET("/api/v1/tickets", { params: { query: { limit: 1 } } }),
      client.GET("/api/v1/tickets", { params: { query: { limit: 2 } } }),
      client.GET("/api/v1/tickets", { params: { query: { limit: 3 } } }),
    ]);
    expect(results.map((r) => r.response.status)).toEqual([200, 200, 200]);
    expect(refreshes).toBe(1);
  });

  it("returns the 401 when the refresh fails, and does not loop", async () => {
    let calls = 0;
    const { client } = harness(() => {
      calls += 1;
      return json(401, { detail: "nope" });
    });
    const result = await client.GET("/api/v1/tickets");
    expect(result.response.status).toBe(401);
    expect(calls).toBe(2); // the request, then the refresh
  });

  it("is not attempted for the auth routes themselves", async () => {
    let calls = 0;
    const { client } = harness(() => {
      calls += 1;
      return json(401, { detail: "bad credentials" });
    });
    const result = await client.POST("/api/v1/auth/login", {
      body: { email: "o@example.com", password: "x" },
    });
    expect(result.response.status).toBe(401);
    expect(calls).toBe(1);
  });
});

describe("unwrap", () => {
  it("returns the data of a good answer", async () => {
    const { client } = harness(() => json(200, { email: "o@example.com" }));
    expect(await unwrap(client.GET("/api/v1/auth/me"))).toEqual({ email: "o@example.com" });
  });

  it("throws an ApiError carrying the server's message", async () => {
    const { client } = harness(() => json(409, { detail: "already decided" }), "");
    const failure = unwrap(client.GET("/api/v1/auth/me")).catch((e: unknown) => e);
    const error = await failure;
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(409);
    expect((error as ApiError).message).toBe("already decided");
  });
});

describe("shouldRetryQuery", () => {
  it("retries a server failure twice and a client error never", () => {
    expect(shouldRetryQuery(0, new ApiError(503, "down"))).toBe(true);
    expect(shouldRetryQuery(2, new ApiError(503, "down"))).toBe(false);
    expect(shouldRetryQuery(0, new ApiError(401, "no"))).toBe(false);
    expect(shouldRetryQuery(0, new TypeError("network"))).toBe(true);
  });
});
