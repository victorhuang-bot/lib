# V18.3.14 PostgreSQL — 版本驗證 + Prefill V2

基底：V18.3.13

## 為什麼做這版
V18.3.13 ZIP 實際檢查已確認公文預填 SQL 是：
`SELECT branch_id,exception_type,service_date FROM delivery_exceptions WHERE service_date=?`

但線上畫面仍回傳舊版：
`SELECT ... start_date,end_date ...`

因此判定線上服務或瀏覽器仍在使用舊程式。

## 本版
- 公文預填 GET API 改為全新：
  `/api/secretary/documents/prefill-v2`
- API response 直接回傳 `app_version=V18.3.14`
- UI 顯示「後端 V18.3.14」
- 新增 `/api/version`
- Render startup log 會印出 `APP_VERSION=V18.3.14`
- 首頁、static、prefill-v2 強制 `Cache-Control: no-store`
- Response header 加 `X-App-Version: V18.3.14`
- common.js cache bust 改成 `?v=18.3.14`

## 保留
- 公文日期自由選擇
- 高速批次查詢
- 45 秒同日期 cache
- 選日期不建立大量 future delivery
- 存檔才建立 delivery
- 今日即時看板維持今天
- Neon pooled PostgreSQL
- 預設司機 1許春芳、2陳錦隆、3彭運土、4林聖原、5張閔傑、6陳錦隆
- 人工改派、支援司機二次確認、V17臨時交接
- 502/503/504 友善處理
