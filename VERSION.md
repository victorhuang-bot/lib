# V18.3.12 PostgreSQL — 公文預填高速查詢版

基底：V18.3.11 PostgreSQL

## 高速查詢
- 選日期時改成少量批次查詢，不再逐館多次查 PostgreSQL。
- 一次取得：分館/路線、所選日期 STOP/ADD、既有 deliveries/實際司機、active drivers、預設司機設定。
- 固定配送星期在 Python 記憶體內計算。
- 選日期仍然只讀，不建立大量未來 delivery。

## Cache
- 同一日期快取 45 秒。
- 單筆或批次公文存檔後清除該日期 cache。

## 保留
- 今日即時看板維持今天。
- 真正存檔才建立該日期/該分館 delivery。
- 預設司機：1許春芳、2陳錦隆、3彭運土、4林聖原、5張閔傑、6陳錦隆。
- 人工改派、支援司機二次確認、V17 臨時交接。
- Neon pooled PostgreSQL 與 502/503/504 友善錯誤處理。
