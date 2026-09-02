# V18.3.9 PostgreSQL — 連線穩定版

基底：V18.3.8

## 核心修正
- PostgreSQL 改用 `psycopg_pool.ConnectionPool`，不再每一個 API 都重新建立 Neon TCP/SSL 連線。
- `min_size=0`：閒置時仍允許 Neon scale-to-zero。
- `max_size=5`：限制免費方案連線壓力。
- PostgreSQL connect timeout：5 秒。
- SQL statement timeout：10 秒。
- Pool checkout timeout：8 秒。
- 登入、公文預填讀取/存檔遇到 502/503/504 時，前端自動重試一次。
- 不再把 Render 的 `<!DOCTYPE html>...502 Bad Gateway` 整段 HTML 顯示在 alert / 登入頁。
- 登入錯誤與公文預填錯誤使用純文字顯示，避免 HTML 被直接插入頁面。

## 公文預填
- 日期切換仍採唯讀清單。
- 真正存檔時才建立該日期/該分館配送資料。
- 今天即時看板不受未來日期公文預填影響。

## 預設司機
- 路線1：許春芳
- 路線2：陳錦隆
- 路線3：彭運土
- 路線4：林聖原
- 路線5：張閔傑
- 路線6：陳錦隆
