# V18.3.15 PostgreSQL — Prefill psycopg placeholder 修正版

基底：V18.3.14

## 修正
V18.3.14 公文高速查詢使用：
`WHERE key LIKE 'DEFAULT_ROUTE_DRIVER_%'`

psycopg3 會把 `%` 當成 placeholder 語法，因此報錯：
`ProgrammingError: only '%s', '%b', '%t' are allowed as placeholders, got '%'`

V18.3.15 改成參數化：
`WHERE key LIKE ?`
參數：
`DEFAULT_ROUTE_DRIVER_%`

## 版本驗證
- APP_VERSION = V18.3.15
- 公文預填 API 改為 `/api/secretary/documents/prefill-v3`
- `/api/version` 回傳 prefill_api=v3
- common.js cache bust = v18.3.15

## 保留
- 公文日期自由選擇
- 高速批次查詢
- 45 秒同日期 cache
- 選日期不大量建立 future delivery
- 存檔才建立 delivery
- 今日即時看板維持今天
- Neon pooled PostgreSQL
- 預設司機：1許春芳、2陳錦隆、3彭運土、4林聖原、5張閔傑、6陳錦隆
- 人工改派、支援司機二次確認、V17 臨時交接
- 502/503/504 友善錯誤處理
