// UAT uses independent long secrets; production retains its existing PIN form.
(async () => {
  try {
    const response = await fetch('/health', {credentials: 'same-origin'});
    if (!response.ok || (await response.json()).uat !== true) return;
    const desktop = document.getElementById('desktopBranchPin');
    const driver = document.querySelector('#unlock #pin');
    for (const input of [desktop, driver]) {
      if (!input) continue;
      input.maxLength = 128;
      input.inputMode = 'text';
      input.type = 'password';
    }
    if (desktop) desktop.parentElement.firstChild.textContent = 'UAT 登入密碼';
    const hint = document.querySelector('#unlock p');
    if (driver && hint) hint.textContent = '已啟用的測試裝置請輸入獨立測試密碼。';
  } catch (_) {
    // A failed health request preserves the original form.
  }
})();
