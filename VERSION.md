# V19.2.3 Gmail API

修正：
- 修正查詢月份路線總計 JS 被放在 external script tag 內而未執行。
- 查詢月份路線總計改共用已正常運作的 /api/reports/monthly-route-summary。
- 月份欄位會自動帶入目前月份。
- 無資料顯示 0；錯誤顯示明確訊息。
- 本月日期/路線累積的 1/3/5 線套淡底色，2/4/6 線白底。
- 每條路線三欄後加垂直分隔線。
- common.js cache bust 更新為 v19.2.3。
