# V19.2.22 Gmail API — 分館固定 QR 查看 / 列印修正

## 問題
原本「查看/列印」流程有兩個瀏覽器問題：

1. `qr()` 先 `await api(...)` 再 `window.open()`，
   Chrome / Safari 可能把後開的視窗判定為非使用者直接操作而阻擋。

2. 彈出視窗裡的 `<img src="/api/branches/{id}/qr.png">`
   會直接請求受保護 API，但圖片標籤無法帶後台 Bearer Authorization header，
   因此 QR 圖片可能無法載入。

## V19.2.22 修正
- 點「查看/列印」時立即同步開啟 QR 視窗，避免 popup blocker。
- `/api/branches/{id}/qr` 在已驗證的 JSON 回應內直接提供 `png_data_url`。
- QR 視窗使用 data URL 顯示圖片，不再讓 img 標籤直接打受保護 API。
- 顯示固定分館網址，方便管理者核對。
- QR 視窗內可直接「列印 QR Code」。
- 若瀏覽器仍阻擋 popup，會顯示清楚提示。
- 若 QR API 失敗，錯誤會顯示在新視窗中，不會無反應。

完整保留 V19.2.21 與前面所有功能。
