# GitHub Deploy Edition

Build date: 2026-09-01

Deployment-specific changes:

- Public HTTPS-aware QR URL generation (`APP_BASE_URL` / reverse-proxy headers)
- Production credentials injected by environment variables instead of committed passwords
- Driver Activation QR remains valid for at most 10 minutes
- Driver Activation QR becomes permanently invalid immediately after the first successful device activation
- Creating a newer Driver Activation QR revokes any older unused activation QR for that driver
- `/health` endpoint for hosting platform health checks
- Secure session cookie in production
- Configurable SQLite data directory for hosted environments
- Dockerfile, Render Blueprint, GitHub Actions CI, `.gitignore`, `.dockerignore`
