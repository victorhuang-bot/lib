# V18.1 — SMTP Hotfix

修正 V18 郵件功能，不改既有操作流程。

## 修正
- 移除 V17/V18 重複 `smtp_send()` 函式造成的參數衝突。
- `render.yaml` 明確宣告 SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASSWORD / SMTP_FROM / SMTP_TLS 為 Render Environment 管理值 (`sync: false`)。
- Email 後台新增 SMTP 診斷：顯示每個環境變數是否已設定，但永不顯示密碼內容。
- SMTP 未完整設定時，錯誤訊息直接列出缺少的 Environment Key。
- 保留 Google Workspace 設定：smtp.gmail.com:587 + STARTTLS。
- 保留正式網域：https://lib.moving-match.com

## Render 正式值
- SMTP_HOST = smtp.gmail.com
- SMTP_PORT = 587
- SMTP_USER = victor.huang@moving-match.com
- SMTP_PASSWORD = Google App Password（只放 Render）
- SMTP_FROM = lib@moving-match.com
- SMTP_TLS = true
