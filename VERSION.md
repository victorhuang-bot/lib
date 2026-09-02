# V18.2 — SMTP + Email UI Hotfix

## SMTP 修正
- 不再於 `render.yaml` / `render-persistent.yaml` 宣告 SMTP secret keys，避免 Blueprint 更新把使用者在 Render 手動設定的 SMTP 值清空或變成未同步狀態。
- Google Workspace 非機密項目加入安全預設：
  - SMTP_HOST=smtp.gmail.com
  - SMTP_PORT=587
  - SMTP_USER=victor.huang@moving-match.com
  - SMTP_FROM=lib@moving-match.com
  - SMTP_TLS=true
- `SMTP_PASSWORD` 仍必須只存在 Render Environment，不寫入 GitHub。
- 後台 SMTP 診斷顯示每個欄位來自「Render / 系統預設 / 未設定」，但永不回傳密碼內容。
- 因此即使 Blueprint 沒有帶入 Host/User/From/Port/TLS，系統仍可正常使用；只要 Render 的 SMTP_PASSWORD 存在即可。

## Email UI 修正
- 報表寄送紀錄改為固定欄寬＋橫向捲動容器。
- 收件人與錯誤訊息允許自動換行，不再把整個 Email 頁面撐出視窗。
- 錯誤訊息列表只顯示前 72 字，完整內容放在滑鼠提示 title。
- SENT / FAILED 使用狀態標籤。
- 手機版保留 Email Log 所有必要欄位，不再被全站表格 CSS 隱藏。

## 重要部署步驟
V18.1 已可能因 Blueprint 把 SMTP 欄位清空。部署 V18.2 後，請回 Render → Environment 確認至少 `SMTP_PASSWORD` 還存在；若沒有，重新貼上 Google App Password 並 Save Changes。其他 SMTP 欄位即使沒有也有正式 Workspace 預設值。
