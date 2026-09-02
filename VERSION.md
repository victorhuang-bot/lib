# V18.3.1 PostgreSQL Hotfix

基底：library-logistics-github-deploy-v18.2

## 主要變更
- 正式環境讀取 `DATABASE_URL` 並使用 PostgreSQL。
- Render Web Service 可維持 Free，不需要 Persistent Disk。
- 未設定 `DATABASE_URL` 時只供本機開發 fallback SQLite。
- 路線 A-F 改為路線 1-6，route id 仍維持 1..6。
- 匯入 74 個正式分館與指定順序。
- 「今日路線 / 司機指派」若選到名稱含「支援」的司機，會先跳出二次確認提醒。
- 保留 V18.2 既有功能，包括 V17 司機臨時交接。

## V18.3.1 Hotfix
- 修正 PostgreSQL 相容層啟動時缺少 `import re`，導致 Render `NameError: re is not defined`。
