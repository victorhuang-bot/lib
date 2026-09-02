# V18 — 帳號安全＋郵件管理

## 帳號安全
- 管理者 moving 與秘書 lib 可在後台「帳號與安全性」修改自己的密碼。
- 管理者可重設秘書密碼，且須再次驗證管理者目前密碼。
- 新密碼至少 8 碼；不提供查看目前密碼。
- 密碼繼續使用 PBKDF2-HMAC-SHA256 + salt。
- Render 的 ADMIN_INITIAL_PASSWORD / SECRETARY_INITIAL_PASSWORD 僅在資料庫第一次初始化時使用，不會在一般重新部署時覆寫後台已修改密碼。
- 忘記密碼正式透過 SMTP 寄到 lib@moving-match.com；Token 30 分鐘有效且單次使用。

## Google Workspace SMTP
- SMTP_HOST=smtp.gmail.com
- SMTP_PORT=587
- SMTP_USER=victor.huang@moving-match.com
- SMTP_FROM=lib@moving-match.com
- SMTP_TLS=true
- SMTP_PASSWORD 僅存 Render Environment。
- 管理者後台可查看 SMTP 是否已設定，並寄送測試信。

## Email / 報表
- 管理者管理 TO / CC 收件人、啟用停用及刪除。
- 管理者與秘書可寄送每日報表。
- 郵件附件包含 Excel + PDF。
- Email Log 保存 SENDING / SENT / FAILED、錯誤訊息、寄送時間。
- 可從 Email Log 重新寄送。

## 延續 V17
- 司機臨時交接、接手通知、分段簽名、Audit Log、報表「實際配送司機」。
- 正式網址 https://lib.moving-match.com。
