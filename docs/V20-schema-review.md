# V20 資料表與回歸範圍核對

基準：`d10743682405ec363290c85afe51f8430d2413d6`（本次取得的 main）。以該 commit 的 `app/` 在全新暫存 SQLite 初始化，再用 V20 程式初始化另一個全新 SQLite，比對所有既有表的欄位、型別、NULL、DEFAULT、PK。既有結構一致。完整獨立契約保存於 `tests/v20_schema_contract.json`，不是執行時從 migration 產生預期值。

## 原有 26 張表

| 範圍 | 表名 |
| --- | --- |
| 帳號與登入 | users、sessions、password_resets |
| 路線與分館 | routes、branches、branch_signers |
| 司機與裝置 | drivers、driver_devices、driver_activation_tokens、driver_sessions |
| 配送與簽收 | daily_routes、deliveries、corrections、branch_sessions、daily_reports、delivery_items |
| 班表與交接 | delivery_exceptions、global_closures、route_handoffs、delivery_driver_assignments、route_segment_signatures |
| Email | email_recipients、email_logs |
| 系統 | app_settings、audit_logs、schema_migrations |

## 新增 9 張表：全部僅供 V20 空箱流程使用

| 表名 | 用途與主要約束 |
| --- | --- |
| empty_box_stocktakes | 不可變盤點快照；branch FK；三項非負、版本為正；分館與版本唯一 |
| empty_box_requests | 補箱申請；branch FK；正數需求、補足量界線、狀態 CHECK |
| empty_box_transfers | 需求館與申請、送達日期、狀態；branch/request/route FK；箱數及狀態 CHECK |
| empty_box_transfer_sources | 多來源與各館預定／實際收取日；transfer/branch/route FK；同調度來源館唯一、箱數正數 |
| empty_box_handoffs | 交接與簽名快照；實際日期與確認時間分離；來源事件、業務事件唯一；可空的既有 delivery FK；kind／數量／來源對應 CHECK |
| empty_box_movements | 已確認交接的庫存流水；唯一 event PK 並 FK 至 handoff；branch FK |
| empty_box_reminders | 盤點提醒時間及操作人；branch FK |
| empty_box_balances | 目前庫存與樂觀版本；branch PK/FK；非負數量及正版本 CHECK；與不可變盤點快照分開 |
| empty_box_commands | 盤點、申請、調度的重送鍵與內容比對；event PK；operation／payload／result_id 必填，結果 ID 可對應不同業務表，因此無多型 FK |

後兩張是建議七張表以外的必要輔助表：前者讓交易內庫存更新與保留量檢查不必重算所有歷史，後者保證非交接請求也能安全重送。九張皆有 PK；涉及關係的表均有 FK；數量／狀態表有 CHECK；查詢用索引與業務唯一約束由自動化測試核對。

## 測試改法

`tests/v20_schema_checks.py` 取代單純的 `len(tables)==26`，兩個 PostgreSQL 執行器共用相同契約：

- 精確核對原有表名與 V20 新增表名集合，指出缺表或意外新增的表。
- 核對每張表的欄位名、整數／文字型別、NULL、既有預設值與主鍵。
- 額外核對 V20 外鍵指向、業務唯一鍵、必要 CHECK 與查詢索引。
- 原有空庫驗證保留：植入合成測試資料前，除 schema_migrations 外，所有業務表必須是零筆。

`tests/test_v195_manual_postponement.py`、V19.4 配送／簽收、秘書簽核測試檔均未修改。V19.5 的 16 個 PostgreSQL 案例仍直接繼承原有 `ManualPostponementCases`；只有執行器的 schema preflight、安全身分核對及結果統計擴充。涵蓋颱風人工順延、固定星期自動順延、停送／休館、開始配送與簽名阻擋、數量守恆、交易回滾及併發。

## PostgreSQL 目標安全

兩個 runner 只接受 PostgreSQL 執行檔目錄，不接受 DSN、host 或正式庫參數；先清除 DATABASE_URL、PG* 與 Render 連線環境，再以測試專屬暫存 SQLite 匯入應用。每次建立全新 /tmp PostgreSQL data/socket 目錄、關閉 TCP（listen_addresses 空字串），每案例另建 UUID 命名資料庫，只連自己建立的私有 Unix socket。驗證 server address 為 NULL、資料目錄與 runner 的暫存目錄一致。finally 停止自己的叢集後移除暫存資料。沒有 Neon 或 Render API 呼叫。
