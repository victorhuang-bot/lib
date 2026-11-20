# Issue #45 分館公文點收更正

分館從本站點收頁面修正數量、填寫原因並手寫簽名。司機確認後，該數量才納入看板及報表合計。歷史簽收沿用最近 12 個月的查詢範圍，分館重新簽名後，派送該站的司機可在待確認清單處理。總館已簽核的受影響路線與日報須重新複核，並以新簽名完成。

## 資料庫與版本

- SQLite 與 PostgreSQL 沿用既有 schema，無 DDL、資料搬移或回填。部署無須執行 migration。
- `deliveries.document_original` 保存總館第一次預填的數量。待確認的分館點收量與簽名存於 `audit_logs`，司機確認後才更新 `deliveries.document_final`。看板和報表於等待期間顯示待確認，該站公文量暫不計入合計。
- 更正提交與司機確認共用現有 SQLite 寫入鎖及 PostgreSQL 交易級 advisory lock。送出須核對本站、配送版本與簽收人。司機確認時須提交所見的更正版本，總館重簽時亦須附送複核版本。
- 舊路線與日報簽名在同一筆交易存入稽核，再解除現行簽核。日報使用 `AMENDING` 標示待重新複核，維持其他編輯的鎖定，並停止提供正式日報下載，直到重簽鎖定。原始分館簽名、司機確認時間及既有更正證據留存。

## 驗收與回退

- SQLite 執行 `python -B -m unittest discover -s tests`。
- PostgreSQL 執行 `python -B tests/v20_postgres_runner.py --pg-bin /path/to/postgres/bin`。該 runner 建立新本機 Unix socket 叢集，再以合成配送跑同一組 API 更正案例與既有 V20 回歸。
- 使用全新合成配送走完 5→4 首簽、司機確認、總館簽核、4→3 重簽、司機再次確認、總館重新簽核，並比較看板、日報、月報以及舊簽名封存。不對現有驗收配送覆寫或重設。
- UAT 只使用 `movingmatch-v20-uat` 及獨立 Neon 的 `movingmatch_v20_uat`，依既有 UAT 守門程式確認固定目標。維持正式 Render 與 Neon 隔離。
- 若要回退應用版本，先確認沒有待司機確認的公文提案或 `AMENDING` 日報。舊版缺少更正版本核對與重簽入口。若仍有待處理資料，留在可以完成複核的版本，並保留已寫入的稽核證據。
