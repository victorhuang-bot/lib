# V19.2.14 Gmail API — 分館桌機簽名 500 修正

## 原因
V19.2.13 桌機分館畫面已顯示四個可修改數量欄位，
但送出時前端仍只傳公文收回 / 圖書收回。
後端 UPDATE 已改成四欄位，參數數量卻仍是舊版 tuple，
造成 PostgreSQL 執行時 500 Internal Server Error。
前端又直接呼叫 response.json()，因此使用者只看到：
Unexpected token 'I', "Internal S"... is not valid JSON

## V19.2.14
- 分館送出簽名時傳送四欄：
  公文送出 / 公文收回 / 圖書送出 / 圖書收回
- 後端 branch_sign 重新整理為正確 placeholder / tuple 數量
- 正常簽收與 LATE_BRANCH_PENDING 補簽皆支援四欄
- receipt_at 保留可修改
- branch_signed_at 仍由系統產生
- bapi 改成可處理 JSON 與 plain-text 500 錯誤，不再顯示 JSON parse error

完整保留 V19.2.13 桌機登入、responsive、FIFO、V19.2.12 路線辨識、
V19.2.10 migration-safe、VOID、隨車物品與 Gmail API。
