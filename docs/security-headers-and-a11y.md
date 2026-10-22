# Security headers and accessibility (plan 4.6)

## Headers

| Header | Value |
|---|---|
| `Content-Security-Policy` (web) | `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'` |
| `Content-Security-Policy` (API) | `default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'` |
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |

- **API:** `services/api/security_headers.py`, outermost middleware, so errors get them too. The
  interactive docs (`/docs`, `/redoc`) keep the three plain headers but not the API CSP, because
  they load their scripts from a CDN.
- **Web, in Docker or any nginx:** `apps/web/nginx.conf`.
- **Web on a Render Static Site:** Render reads no file for headers, so they are set in the
  dashboard (`guides/security-headers.md`). The values must match the table above.

The web CSP has no `unsafe-inline`: the built page has no inline script or style, and the app
sets its few computed widths through the DOM, which the policy allows. A new inline `<script>`,
`<style>` or `style=""` attribute in `index.html` would be blocked, so add none.

## Accessibility

- A skip link and an `id="main"` target on every page; focus moves to it after navigation.
- Every page sets its own title. Tabs use arrow keys, Home and End, with one tab stop.
- Dialogs trap focus, close on Escape and return focus. Charts list their numbers as text.
- `src/a11y.test.tsx` runs axe-core over each page, with an editor and a dialog open. It cannot
  check colour contrast (no layout in jsdom). Contrast was checked in Chromium, light and dark,
  with axe-core; the filled-accent text colour is the `--on-accent` token for that reason.
