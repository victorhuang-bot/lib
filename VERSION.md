# V19.2.21 Gmail API — V19.2.20 顯示回歸修正版

## 原因
V19.2.20 將 `fmtDateTime()` 放進：

`<script src="/static/common.js"> ... </script>`

瀏覽器對具有 `src` 的 script tag 會忽略其中 inline JavaScript，
因此實際執行時出現：

`fmtDateTime is not defined`

這會讓歷史查詢、即時配送列表等 JavaScript 在渲染途中中斷，
所以看起來像「隨車物品」與「公文預填」也一起消失。

## V19.2.21 修正
- `fmtDateTime()` 移到真正會執行的 inline script。
- 保留 V19.2.20 的 `YYYY-MM-DD HH:mm` 顯示格式。
- `loadAll()` 改用 `Promise.allSettled()`，單一卡片載入失敗不再拖垮整個後台。
- 總館登入後主動初始化並載入「公文預填」。
- 確認今日配送表的「隨車物品」按鈕仍保留。
- 完整保留歷史路線 / 簽收、PNG、待補簽追蹤、行政結案、Gmail API 等功能。
