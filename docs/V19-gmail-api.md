# V19 Gmail API Edition

## Render Environment
- GOOGLE_CLIENT_ID
- GOOGLE_CLIENT_SECRET
- GOOGLE_REFRESH_TOKEN
- GOOGLE_GMAIL_USER=victor.huang@moving-match.com
- EMAIL_FROM_ADDRESS=lib@moving-match.com
- EMAIL_FROM_NAME=圖書物流配送暨電子簽收管理系統

## Google Cloud / Workspace
1. 啟用 Gmail API。
2. OAuth consent 設定完成。
3. 建立 OAuth Client。
4. 以 `gmail.send` scope 完成一次授權取得 refresh token。
5. 確認 `lib@moving-match.com` 已是 `victor.huang@moving-match.com` 可合法 Send As / Alias。

程式不會建立或 commit credentials.json/token.json。
