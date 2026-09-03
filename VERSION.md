# V19 Gmail API Edition

基底：V18.3.16 PostgreSQL

## 唯一核心改造
- Email transport：Google Workspace SMTP → Google Gmail API `users.messages.send`。
- OAuth scope：`https://www.googleapis.com/auth/gmail.send`。
- MIME UTF-8 → base64URL → HTTPS 443 Gmail API。
- Access token 不落地；由 refresh token 自動取得。
- 不 fallback SMTP。

## 保留
配送、QR、4位碼、電子簽收、分館更正、路線/每日指派、司機裝置、休館/停送/非固定運送、Audit Log、PostgreSQL、公文預填與日/月報內容全部保留。

## Email
- 保留 `/api/email/settings`、`/recipients`、`/logs`、`/test`、`/send-report`、resend。
- 新增 `/api/email/health`。
- TO/CC/BCC。
- Email Log migration 新增 provider/message_id/sender/subject/to/cc/bcc/triggered_by/delivery_identifier。
- 日/月報 duplicate prevention。
- 429/500/502/503/504/timeout 最多 retry 3 次，backoff 1/2/4 秒。
