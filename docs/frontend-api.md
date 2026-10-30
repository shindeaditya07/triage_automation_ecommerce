# Frontend API access

The web app never hand-writes a request or response type.

- **Types** are generated from the FastAPI OpenAPI schema into `apps/web/src/api/schema.d.ts`
  (committed). After changing any route or model, run `make generate-types` and commit the result.
  CI's *API Types* job runs `make check-types` and fails if the committed file is stale.
- **Client** (`src/api/client.ts`): a typed `openapi-fetch` client. It sends the session cookies,
  adds the `X-CSRF-Token` header (from the `rd_csrf` cookie) to every unsafe request, and on a 401
  from any non-auth route refreshes the session once (shared by requests that fail together) and
  repeats the request. If the refresh fails the 401 is returned so the app shows the login page.
  Tokens are in cookies the script cannot read; nothing is kept in `localStorage`.
- **TanStack Query** (`src/api/queryClient.ts`): reads retry twice on a network failure or 5xx and
  never on a 4xx; writes are never retried automatically. Use `unwrap(api.GET(...))` in a
  `queryFn`/`mutationFn` so an error status becomes an `ApiError` carrying the server's message.
- **Auth hooks** (`src/api/auth.ts`): `useMe`, `useLogin`, `useLogout`.

## Pages

| Address | Page |
|---|---|
| `/` | Metrics (the home page). `/metrics` redirects here. |
| `/tickets` | The ticket queue. A "Tickets" link in every owner page's header opens it. |
| `/tickets/:id`, `/audit/:id?`, `/login` | A ticket, the audit log, sign-in |
| `/contact` | Public: the customer contact form (no sign-in) |

Every owner page shares one header (`TopBar`): Metrics, Tickets, the signed-in address and
Sign out.
