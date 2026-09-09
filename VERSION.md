# V19.2.23 Gmail API — 分館簽名送出停留修正

## 根因
V19.2.22 的分館頁仍有一個 V19.2.20 類似問題：
`fmtDateTime()` 被放在 `<script src="/static/common.js">...</script>` 裡。

第一次簽名其實可能已成功寫入資料庫，但接著前端 `load()` 要顯示完成畫面時，
因 `fmtDateTime is not defined` 中斷，畫面仍停留在簽名表單。

使用者再次按送出後，後端就會回覆：
`此筆資料已填寫或已簽名，不能再次修改`

## V19.2.23 修正
- 將分館頁 `fmtDateTime()` 移到真正執行的 inline script。
- 簽名成功後顯示明確「簽收已送出完成」畫面。
- 送出按鈕第一次按下後立即 disabled，文字改為「送出中…」，避免重複點擊。
- 後端 branch sign 改為 idempotent：
  同一 session 在第一筆已成功後若因網路重送再次 POST，不改資料，直接回傳成功。
- 已簽資料仍然不可修改；idempotent 只避免重複送出造成錯誤，不會覆寫簽名。
- 保留實際簽收時間與系統簽名時間分離。

完整保留 V19.2.22 QR 修正與前面所有功能。
