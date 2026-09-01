# GitHub → Render 公開部署指南

這個版本是給「手機直接掃分館 QR / 司機啟用 QR」測試用的公開部署版本。

## 1. 建立 GitHub Repository

1. 在 GitHub 建立一個新的 Repository，例如 `library-logistics`。
2. 建議先設為 **Private**。
3. 將本資料夾內所有檔案上傳到 Repository 根目錄。
4. 不要上傳 `.env`、`data/app.db` 或任何真實密碼。

Repository 根目錄應該直接看到：

- `Dockerfile`
- `render.yaml`
- `requirements.txt`
- `app/`
- `.github/`

## 2. 從 GitHub 部署到 Render

可使用 Render Blueprint / Web Service 連接 GitHub Repository。

本專案內的 `render.yaml` 已設定：

- Docker 部署
- `/health` 健康檢查
- Production 模式
- 公開 HTTPS 網站

部署時必須在 Render 私密環境變數中輸入：

- `ADMIN_INITIAL_PASSWORD`：管理者初始密碼
- `SECRETARY_INITIAL_PASSWORD`：總館秘書初始密碼
- `DEMO_BRANCH_PIN`：測試分館 4 位 PIN

建議不要把這些值寫入 GitHub。

預設帳號名稱仍為：

- 管理者：`moving`
- 總館秘書：`lib`

若要改帳號，可額外設定：

- `ADMIN_USERNAME`
- `SECRETARY_USERNAME`

## 3. 免費測試版與資料保存

`render.yaml` 是公開測試版設定，SQLite 放在暫存磁碟，因此服務重建 / 重新部署後資料可能重置。

這非常適合先測：

- 手機掃固定分館 QR
- 分館 4 位 PIN
- 司機 Activation QR
- 司機裝置啟用
- 分館簽名
- 唯一一次更正
- 即時 Dashboard

若要正式保存資料，專案另附：

`render-persistent.yaml`

它使用 Render Persistent Disk。正式環境也可進一步改 PostgreSQL。

## 4. 部署完成後

假設 Render 網址是：

`https://library-logistics-demo.onrender.com`

管理端：

`https://library-logistics-demo.onrender.com/`

管理者登入後，在「分館管理」查看 / 列印 QR。QR 會自動使用公開 HTTPS 網址，例如：

`https://library-logistics-demo.onrender.com/branch/<secure-token>`

因此 iPhone / Android 可以直接掃描。

司機啟用 QR 同樣會使用公開網址：

`https://library-logistics-demo.onrender.com/activate-driver/<secure-token>`

## 5. 司機 QR 的一次性規則

現在採嚴格一次性：

1. 管理者產生啟用 QR。
2. QR 最長 10 分鐘有效。
3. 若管理者在 10 分鐘內又產生新的 QR，舊的尚未使用 QR 立即撤銷。
4. 司機第一次成功輸入 4 位 PIN 並綁定裝置後，`used_at` 立即寫入資料庫。
5. 同一 QR 第二次掃描或第二次送出時，Backend 回覆 HTTP 410，不能再使用。

這不是只靠前端隱藏按鈕，而是 Server / Database 驗證。

## 6. GitHub Actions

`.github/workflows/ci.yml` 會在 Push / Pull Request 時：

- 安裝 Python dependencies
- 編譯檢查 `app/main.py`
- 載入 FastAPI application

## 7. 正式上線前仍建議處理

目前版本適合 UAT / 展示 / 流程測試。真正正式營運前，建議再完成：

- PostgreSQL
- 真正 SMTP / Email provider
- MFA
- PIN / Login Rate Limit
- Object Storage 保存簽名
- Backup / Restore
- 正式 Domain
- 公部門資安掃描與弱點修補
