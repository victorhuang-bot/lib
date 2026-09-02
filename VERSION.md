# V18.3.7 PostgreSQL — 公文預填穩定性修正

基底：V18.3.6

## 修正
- 修正未來日期「公文預填清單讀取失敗」問題。
- `rebuild_service_date()` 改為 PostgreSQL concurrency-safe / idempotent：
  - daily_routes 使用 `(service_date, route_id)` conflict-safe 建立。
  - deliveries 使用 `(service_date, branch_id)` conflict-safe 建立。
  - 同一日期可重複整理，不會因重複建立而讓交易失敗。
- API 發生錯誤時會 rollback，不會留下 PostgreSQL aborted transaction。
- 前端會顯示實際後端錯誤，不再只顯示「讀取失敗」。
- Render Log 會留下 `PREFILL_ERROR` 方便快速定位。

## 沿用
- 公文預填日期可自由選擇。
- 固定配送 / CLOSED_ALL / STOP / ADD。
- 今日即時看板不切換日期。
- 路線1 許春芳、2 陳錦隆、3 彭運土、4 林聖原、5 張閔傑、6 陳錦隆。
- 支援司機二次確認、臨時改派、交接。
