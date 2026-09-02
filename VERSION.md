# V18.3.10 PostgreSQL — Neon Pooler 修正版

基底：V18.3.9 PostgreSQL 連線穩定版

## Neon Pooler 修正
- 移除 PostgreSQL startup package 裡的 `options=-c statement_timeout=10000`。
- 原因：Neon pooled connection 不接受 `statement_timeout` startup parameter。
- 取得連線後才嘗試執行 `SET statement_timeout TO '10s'`；即使設定失敗也不阻止系統啟動。
- 啟動連線最多重試 3 次，每次間隔 2 秒，降低 Neon scale-to-zero / Render cold start 導致部署失敗的機率。
- ConnectionPool 保留：
  - min_size=0
  - max_size=5
  - pool checkout timeout=8 秒
  - connect timeout=5 秒
- 歸還 pool 前會清理非 idle transaction，避免 aborted transaction 被下一個 API 重用。

## 保留功能
- 公文預填日期自由選擇。
- 未按存檔時不建立大量未來配送資料；真正存檔時才建立該日期/該分館資料。
- 今日即時看板維持今天，不受未來日期公文預填影響。
- 每日預設司機：
  - 路線1 許春芳
  - 路線2 陳錦隆
  - 路線3 彭運土
  - 路線4 林聖原
  - 路線5 張閔傑
  - 路線6 陳錦隆
- 管理者人工臨時改派。
- 支援司機二次確認。
- V17 司機臨時交接 / segment signing。
- 502 / 503 / 504 前端自動重試一次。
- 不再把 Render 502 HTML 整段顯示在 alert 或登入畫面。
- 路線1～6與74個正式分館保留。
