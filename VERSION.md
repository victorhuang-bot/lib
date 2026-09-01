# V15

修正司機裝置啟用 QR 新產生後立即顯示失效的問題。

原因：V13/V14 將 now() 改為 Asia/Taipei (+08:00)，但 activation/session 的 expires_at
仍使用伺服器 naive datetime.now()，Render 為 UTC，造成字串比較時新 QR 被誤判為已過期。

V15：
- 所有新 expiry 統一使用 Asia/Taipei aware ISO timestamp。
- 司機啟用 QR 維持建立後 10 分鐘有效、成功使用一次即失效。
- 同步修正登入 session、密碼重設、司機 session、分館 session 的 expiry 時區一致性。
