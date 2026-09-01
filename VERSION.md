# V16

正式子網域部署版本。

- `APP_BASE_URL` 預設為 `https://lib.moving-match.com`。
- 新產生的分館固定 QR Code 使用 `https://lib.moving-match.com/branch/...`。
- 新產生的司機裝置啟用 QR Code 使用 `https://lib.moving-match.com/activate-driver/...`。
- Render Blueprint 設定 `APP_BASE_URL=https://lib.moving-match.com`。
- 保留 V15：司機啟用 QR 10 分鐘有效、成功使用一次即失效、expiry 與 now() 統一使用 Asia/Taipei (+08:00)。
- 舊版已印出的固定 QR 若仍含 `onrender.com`，V16 部署後請重新產生 / 重新列印。
