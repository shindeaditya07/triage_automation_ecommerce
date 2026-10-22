import createClient from "openapi-fetch";

import type { paths } from "./schema";

const CSRF_COOKIE = "rd_csrf";
const CSRF_HEADER = "X-CSRF-Token";
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS", "TRACE"]);
const REFRESH_PATH = "/api/v1/auth/refresh";
const AUTH_PREFIX = "/api/v1/auth/";

/** A response the server answered with an error status. `detail` is the server's own message. */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;

  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : `request failed (${status})`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

export function readCookie(name: string, cookies: string): string | undefined {
  for (const part of cookies.split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === name) return decodeURIComponent(rest.join("="));
  }
  return undefined;
}

export interface ApiClientOptions {
  baseUrl?: string;
  fetch?: (request: Request) => Promise<Response>;
  /** The page's cookies (`document.cookie`); a function so a test can supply its own. */
  cookies?: () => string;
}

/**
 * The typed API client. Every request goes to the same origin with the session cookies, and an
 * unsafe request carries the CSRF token from the `rd_csrf` cookie. A 401 on anything but the auth
 * routes is answered by refreshing the session once and repeating the request; if the refresh
 * fails, the 401 is returned so the app can show the login page. Tokens are never read by script
 * and never stored: the cookies hold them.
 */
export function createApiClient(options: ApiClientOptions = {}) {
  const baseFetch = options.fetch ?? ((request: Request) => globalThis.fetch(request));
  const cookies =
    options.cookies ?? (() => (typeof document === "undefined" ? "" : document.cookie));
  let refreshing: Promise<boolean> | null = null;

  function withCsrf(request: Request): Request {
    if (SAFE_METHODS.has(request.method.toUpperCase())) return request;
    const token = readCookie(CSRF_COOKIE, cookies());
    if (!token) return request;
    const headers = new Headers(request.headers);
    headers.set(CSRF_HEADER, token);
    return new Request(request, { headers });
  }

  function refreshOnce(): Promise<boolean> {
    refreshing ??= (async () => {
      try {
        const request = new Request(`${options.baseUrl ?? ""}${REFRESH_PATH}`, {
          method: "POST",
          credentials: "same-origin",
        });
        return (await baseFetch(withCsrf(request))).ok;
      } catch {
        return false;
      } finally {
        refreshing = null;
      }
    })();
    return refreshing;
  }

  async function apiFetch(input: Request): Promise<Response> {
    const retry = input.clone();
    const response = await baseFetch(withCsrf(input));
    if (
      response.status !== 401 ||
      new URL(input.url, "http://x").pathname.startsWith(AUTH_PREFIX)
    ) {
      return response;
    }
    if (!(await refreshOnce())) return response;
    return baseFetch(withCsrf(retry));
  }

  return createClient<paths>({
    baseUrl: options.baseUrl ?? "",
    credentials: "same-origin",
    fetch: apiFetch,
  });
}

export const api = createApiClient();

/** Turn openapi-fetch's `{ data, error }` into a value or a thrown `ApiError`, for TanStack Query. */
export async function unwrap<T>(
  result: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  const { data, error, response } = await result;
  if (!response.ok) {
    const detail =
      error && typeof error === "object" && "detail" in error
        ? (error as { detail: unknown }).detail
        : error;
    throw new ApiError(response.status, detail);
  }
  return data as T;
}
