# V19.2.9 Gmail API

## 1. 作廢配送
- 管理者可將未簽名、未由司機確認的錯建配送作廢。
- VOID 保留 Audit Log，不刪歷史。
- VOID 不列入司機路線有效站點總數，也不阻擋路線簽名。

## 2. 三種完成狀態分離
- 運送完成：STOP_COMPLETED + LATE_BRANCH_PENDING。
- 分館簽收完成：branch_signed_at / branch_signature。
- 總館簽核完成：daily_routes.secretary_signature / secretary_signed_at。

## 3. 總館隨車物品
- 新增 delivery_items。
- 海報、文宣、文具、其他可同時多筆填寫數量。
- 其他可自訂名稱。
- 總館路線簽核後鎖定。

## 4. 分館實際簽收時間
- 新增 deliveries.receipt_at。
- 分館畫面預設目前時間，可修改。
- branch_signed_at 仍由系統自動記錄且不可由分館修改。
