# V19 Gmail API 郵件流程

FastAPI → Gmail Service → OAuth 2.0 refresh token → HTTPS 443 → Gmail API `users.messages.send`.

Scope 僅使用 `https://www.googleapis.com/auth/gmail.send`。

不使用 SMTP、不使用 App Password、不 fallback SMTP。
