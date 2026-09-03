# V18.3.13 PostgreSQL — 公文預填高速查詢 Schema 修正版

基底：V18.3.12

## 修正
- 修正 `delivery_exceptions` 查詢欄位名稱。
- 實際 schema：
  - `service_date`
  - `branch_id`
  - `exception_type`
  - `reason`
- 不再使用不存在的 `start_date / end_date`。
- 高速公文預填改為：
  `SELECT branch_id, exception_type, service_date FROM delivery_exceptions WHERE service_date=?`
- 設定/刪除休館、停送、非固定運送後，清除公文預填 cache，避免短時間看到舊清單。

## 保留
- 公文日期自由選擇。
- 批次查詢 + Python 記憶體計算。
- 同日期 45 秒 cache。
- 選日期不大量建立 future delivery。
- 真正存檔才建立該日期/該分館 delivery。
- 今日即時看板維持今天。
- Neon pooled PostgreSQL。
- 預設司機：
  - 路線1 許春芳
  - 路線2 陳錦隆
  - 路線3 彭運土
  - 路線4 林聖原
  - 路線5 張閔傑
  - 路線6 陳錦隆
- 人工改派、支援司機二次確認、V17 臨時交接。
- 502/503/504 友善錯誤處理。
