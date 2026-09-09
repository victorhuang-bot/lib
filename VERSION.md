# V19.2.15 Gmail API — 待補簽追蹤 / 協助修正 / 行政結案

1. 待補簽明細
- 管理者 / 總館可依日期、月份、路線查看待補簽分館。
- 顯示日期、路線、分館、四個數量與實際簽收時間。

2. 今日路線總表
- 新增「查看今日待補簽分館」入口。

3. 總館 / 管理者協助修正
- 僅限尚未取得分館簽名的 WAITING_BRANCH / LATE_BRANCH_PENDING。
- 可修正四個數量、備註、實際簽收時間。
- Audit Log：ASSIST_CORRECT_PENDING。

4. 管理者行政結案
- 僅 ADMIN。
- 必填原因。
- 狀態 ADMIN_CLOSED。
- 不冒充分館簽名。
- Audit Log：ADMIN_CLOSE_DELIVERY。
- ADMIN_CLOSED 視為 operational completed，不阻擋路線完成。

5. 報表
- 行政結案資料保留 admin_close_reason / status，供日報月報標示
  「管理者結案／未取得分館簽名」。

完整保留 V19.2.14、V19.2.13、V19.2.12、V19.2.10 與 Gmail API 功能。
