# V19 Gmail API Edition — Change Report

## 1. 修改檔案
- `app/main.py`
- `app/static/index.html`
- `requirements.txt`
- `render.yaml`
- `render-persistent.yaml`
- `.env.example`
- `.gitignore`
- `.github/workflows/ci.yml`
- `README.md`
- `SECURITY.md`
- `DEPLOY_GITHUB.md`
- `docs/V18-account-mail-flow.md`
- `VERSION.md`

## 2. 新增檔案
- `app/services/__init__.py`
- `app/services/gmail_service.py`
- `tests/test_gmail_service.py`
- `docs/V19-gmail-api.md`
- `docs/V19_CHANGE_REPORT.md`

## 3. 移除
- `docs/V18.2-SMTP-hotfix.md`
- Runtime SMTP code：`smtplib`、SMTP host/port/login/send_message 全部移除。
- 不提供 SMTP fallback。

## 4. Gmail API 寄送流程
FastAPI → `gmail_service.send_email()` → MIME UTF-8 → base64URL → OAuth refresh → HTTPS 443 → Gmail API `users.messages.send`。

Scope：`https://www.googleapis.com/auth/gmail.send`。

## 5. Render Environment Variables
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_REFRESH_TOKEN`
- `GOOGLE_GMAIL_USER=victor.huang@moving-match.com`
- `EMAIL_FROM_ADDRESS=lib@moving-match.com`
- `EMAIL_FROM_NAME=圖書物流配送暨電子簽收管理系統`

Secret 不進 GitHub。

## 6. Database migration
有。只擴充既有 `email_logs`，新增：
- `email_type`
- `subject`
- `to_addresses`
- `cc_addresses`
- `bcc_addresses`
- `sender`
- `provider`
- `message_id`
- `triggered_by`
- `delivery_identifier`

不修改配送、QR、簽收等資料表結構。

## 7. API
保留：
- `GET /api/email/settings`
- `GET /api/email/recipients`
- `GET /api/email/logs`
- `POST /api/email/test`
- `POST /api/email/send-report`
- `POST /api/email/logs/{id}/resend`

新增：
- `GET /api/email/health`

收件人 API 增加 `BCC` 類型，相容原 TO/CC。

## 8. Email Log
成功：`provider=gmail_api`、`status=sent`、記錄 Gmail `message_id`。
失敗：`status=failed`、只記錄清理後的錯誤原因，不記 token/client secret/authorization header。

## 9. Unit Test
本地離線 mock：4/4 PASS。
- MIME + 中文 + TO/CC/BCC + attachment
- Gmail API success
- transient retry then success
- sender unauthorized no retry

CI 已加入 `python -m unittest discover -s tests -v`，CI 不會真的寄信。

## 10. Regression Smoke Test
PASS：
- `/health`
- login
- dashboard
- routes
- drivers
- branches
- daily XLSX
- daily PDF
- email settings/health/recipients/logs
- BCC recipient CRUD smoke
- email_logs migration

## 11–14. GitHub / Branch / Commit / Deployment
本 ZIP 未直接連接或推送 GitHub，因此尚未產生正式 remote commit SHA，也尚未觸發 Render deployment。
建議：
- Repository：你目前 Render 綁定的 `victorhuang-bot/lib`
- Branch：`main`
- Commit Message：`Replace SMTP email delivery with Gmail API`

## 15. Render 尚需人工設定
在 Environment 新增上述 6 個 Gmail API variables；舊 SMTP variables 可刪除。
`DATABASE_URL` 不需修改。

## 16. Google Cloud / Workspace 尚需人工設定
1. 啟用 Gmail API。
2. 設定 OAuth consent。
3. 建立 OAuth Client。
4. 以 `gmail.send` scope 授權，取得 refresh token。
5. 確認 `lib@moving-match.com` 是 `victor.huang@moving-match.com` 可合法使用的 Send As / Alias。
6. 若 alias 未授權，系統會寄送失敗並寫 Email Log，不會偷偷改 From。
