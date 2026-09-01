# V13

- 報表簽收時間統一顯示台灣中原標準時間（UTC+8 / Asia/Taipei），格式 YYYY-MM-DD HH:MM:SS。
- 舊版 Render 無時區時間戳在報表中視為 UTC 並轉換為台灣時間。
- PDF 改用可嵌入的繁體中文字型，修正 Safari / Chrome / Preview 中文亂碼。
- Docker 部署安裝 fonts-arphic-uming，避免 Render 缺少中文字型。
