# Security Notes

This is a runnable MVP, not a production accreditation package.

Already implemented:
- PBKDF2 password/PIN hashes
- high-entropy QR/reset/activation tokens
- server-side 10-minute activation expiry
- one-time activation token usage
- branch-scoped session
- one correction per delivery enforced by DB uniqueness and state machine
- append-only audit API design
- HttpOnly admin/secretary session cookie
- SSE used only as refresh signal; database remains source of truth

Before production:
- HTTPS reverse proxy
- MFA for admin/secretary
- CSRF protection appropriate to deployment
- robust rate limits and lockouts
- formal Gmail API password reset delivery
- private object storage for signature images instead of DB data URLs
- backup/restore drills
- public-sector vulnerability scan / penetration test
- CSP/security headers
- secrets manager
- structured logging and monitoring

## GitHub deployment notes

- Do not commit production passwords, branch PINs, `.env`, or SQLite database files.
- Production initial passwords are read from `ADMIN_INITIAL_PASSWORD` and `SECRETARY_INITIAL_PASSWORD` environment variables.
- `DEMO_BRANCH_PIN` is also provided through the hosting provider secret environment settings.
- Driver activation tokens are stored as SHA-256 hashes; the raw token exists only in the generated activation URL.
- A successful activation writes `used_at` before returning success. Reuse is rejected by the backend.
- Generating a new activation QR revokes older unused activation tokens for the same driver.
