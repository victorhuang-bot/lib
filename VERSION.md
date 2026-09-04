# V19.2.4 Gmail API — Login 401 loop hotfix

基底：V19.2.3

## 問題
登入頁載入時，DOMContentLoaded 無條件呼叫：
`loadMonthlyRouteCumulative()`

該函式會呼叫需要 ADMIN/SECRETARY 權限的月統計 API。
使用者尚未登入時 API 回 401，common.js 因此顯示：
「登入狀態已失效，請重新登入。」
並 reload，形成循環。

## 修正
- 登入頁 DOMContentLoaded 只初始化月份，不呼叫受保護 API。
- `/api/auth/me` 確認已登入後，`loadAll()` 才並行載入：
  - 今日 Dashboard
  - 今日配送
  - 本月日期 / 路線累積
  - 查詢月份路線總計
- common.js cache bust 更新到 v19.2.4。
- APP_VERSION 固定為 V19.2.4。

## 保留
- V19 Gmail API
- 本月日期 / 路線累積
- 路線交錯底色
- 查詢月份路線總計
- 分館隔日補簽
- 配送 / QR / 角色權限 / 報表邏輯
