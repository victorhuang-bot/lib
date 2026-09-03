# V18.3.16 PostgreSQL — 公文單筆存檔修正版

基底：V18.3.15

## 修正
Render Log 顯示：
`NameError: name '_prefill_save_db' is not defined`

V18.3.16 補回 `_prefill_save_db()`，單筆公文存檔會：
1. 取得/建立所選日期與分館的 delivery。
2. 檢查司機是否已輸入圖書送出。
3. 寫入 document_original / document_final。
4. WAITING_SECRETARY 轉為 WAITING_DRIVER。
5. 寫入 Audit Log。
6. commit PostgreSQL。
7. 清除該日期 prefill cache。

## 版本驗證
- APP_VERSION = V18.3.16
- Prefill GET API = /api/secretary/documents/prefill-v4
- common.js cache bust = v18.3.16

## 保留
- 公文日期自由選擇
- 高速批次查詢
- 45 秒 cache
- 選日期不大量建立 future delivery
- 存檔才建立 delivery
- 今日即時看板維持今天
- Neon pooled PostgreSQL
- 預設司機 1許春芳、2陳錦隆、3彭運土、4林聖原、5張閔傑、6陳錦隆
- 人工改派、支援司機二次確認、V17 臨時交接
- 502/503/504 友善處理
