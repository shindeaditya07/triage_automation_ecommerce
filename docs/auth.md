# Owner authentication

One owner account, configured by environment variables. There is no sign-up and no user table.

| Variable | Meaning |
|---|---|
| `OWNER_EMAIL` | The owner's login email (compared case-insensitively). |
| `OWNER_PASSWORD_HASH` | Argon2id hash of the password. Make one with `make owner-hash`. |
| `SECRET_KEY` | Signs the session tokens. 32+ random characters in staging, nonprod and production. |
| `TRUSTED_PROXY_HOPS` | Proxies in front of the API whose `X-Forwarded-For` entry is trusted. `1` on Render, `0` when the API faces clients directly. Too high lets a caller choose their own IP. |

In a local `.env`, wrap the hash in single quotes (`OWNER_PASSWORD_HASH='$argon2id$…'`) so Docker
Compose does not read the `$` signs as variables. Hosts such as Render take the value as is.

Other knobs (`ACCESS_TOKEN_MINUTES`, `REFRESH_TOKEN_DAYS`, `LOGIN_*`) are in `.env.example`.
If the owner variables are missing or the hash is unreadable, the API still starts and the auth
routes answer 503.

## Endpoints

| Route | Does | Needs CSRF header |
|---|---|---|
| `POST /api/v1/auth/login` `{email, password}` | Checks credentials, sets the three cookies. 401 for any bad credential (same body), 429 with `Retry-After` when rate-limited or locked. | No (no session yet) |
| `POST /api/v1/auth/refresh` | Rotates the refresh token, issues a new access token. 401 and cleared cookies if the token is bad, expired or reused. | Yes |
| `POST /api/v1/auth/logout` | Revokes the session and clears the cookies. 204. | Yes |
| `GET /api/v1/auth/me` | `{email}` for a valid access cookie, else 401. | No (read) |

Owner-only routes depend on `services.api.auth.routes.require_owner`.

## Cookies

| Cookie | Path | HttpOnly | Lifetime | Holds |
|---|---|---|---|---|
| `rd_access` | `/api` | yes | 15 min | JWT, `typ=access` |
| `rd_refresh` | `/api/v1/auth` | yes | 7 days | JWT, `typ=refresh`, recorded in the database |
| `rd_csrf` | `/` | **no** | 7 days | CSRF token, read by the page's JavaScript |

All are `SameSite=Strict`, and `Secure` outside local development. Nothing is stored in
`localStorage`. The login response body also returns the CSRF token.

## CSRF

Every unsafe request (POST, PUT, PATCH, DELETE) under `/api/` that carries a valid session cookie
must send `X-CSRF-Token` equal to the `rd_csrf` cookie. The token is an HMAC of the session id, so
a value planted by an attacker (even in both places) is rejected. This is middleware, so routes
added later are covered without doing anything. The frontend reads `rd_csrf` and copies it into
the header on every mutation.

## Brute force

- Per address: 10 login attempts per 5 minutes (in memory; resets on restart).
- Per account: the 5th consecutive attempt locks the account for 60 s, and each later failure after
  a lock doubles it, up to 1 hour. State is in the database, so a restart does not clear it. A
  correct password during a lock is refused. A success clears the count.
- Attempts are counted before the password is checked, so parallel guesses cannot beat the limit.

## Sessions

Refresh tokens rotate on every use. Presenting one that was already used ends the whole session,
so a stolen token is detected the first time the thief or the owner uses it. Two tabs refreshing
at the same instant can trip this; the frontend should refresh from one place.

## Known limits

- Logout revokes the refresh token at once, but an access token already issued stays valid until it
  expires (at most 15 minutes) because access tokens are not checked against the database.
- Account lockout lets someone who knows the owner's email keep the owner locked out by trying
  wrong passwords. The cap is one hour per lock and the per-address limit slows one source; a
  distributed attacker could still do it.
- A session slides: each refresh extends it, with no absolute maximum age.
