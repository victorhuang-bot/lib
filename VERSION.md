# V19.2.27 — 司機 QR 與隨車物品中文修正版

## 司機啟用 QR
- 修正「10分鐘一次性啟用 QR」無法正常產生 / 顯示。
- 點按鈕後立即開啟視窗，避免瀏覽器 popup blocker。
- 後端直接回傳 QR 的 PNG data URL。
- QR 圖片不再透過 `<img>` 直接呼叫受保護的 `/api/qr.png`。
- QR 視窗可直接列印。
- 仍維持：10 分鐘內有效、使用一次即失效、同司機只保留最新未使用 QR。

## 司機端隨車物品
所有系統 enum 統一轉成中文：
- DOCUMENT_BAG → 公文袋
- ATTACHMENT_BAG → 附件袋
- PRIZE → 獎品
- CONSUMABLE → 耗材
- POSTER → 海報
- NEWBORN_GIFT → 新生兒閱讀好禮
- BOOKCLUB_RESOURCE → 讀書會資源庫用書
- PROMOTION → 文宣（歷史相容）
- STATIONERY → 文具（歷史相容）
- OTHER → 自訂名稱 / 其他

未知舊類型也不直接顯示英文 enum，會顯示 item_name 或「其他物品」。

完整保留 V19.2.26 多路線單機作業功能。
