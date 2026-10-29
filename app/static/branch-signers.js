// Names are always assigned through textContent/value, never interpolated into HTML.
let signerManagementBusy = false;
async function loadBranchSigners() {
  const panel = document.getElementById('branchSigners');
  panel.classList.remove('hidden');
  const status = document.getElementById('branchSignerMessage');
  const list = document.getElementById('branchSignerList');
  status.textContent = '載入中…';
  try {
    const include = document.getElementById('showInactiveSigners').checked;
    const rows = await bapi('/api/branch-session/signers?include_inactive=' + include);
    list.replaceChildren();
    for (const row of rows) {
      const item = document.createElement('div');
      item.className = 'signer-row';
      const name = document.createElement('input');
      name.value = row.name;
      name.maxLength = 80;
      name.setAttribute('aria-label', '修改簽收人姓名');
      const state = document.createElement('span');
      state.textContent = row.active ? '有效' : '已停用';
      const save = document.createElement('button');
      save.type = 'button'; save.className = 'btn alt'; save.textContent = '儲存姓名';
      save.onclick = () => changeBranchSigner(() => bapi('/api/branch-session/signers/' + row.id, {
        method: 'PATCH', body: JSON.stringify({ name: name.value })
      }));
      item.append(name, state, save);
      if (row.active) {
        const stop = document.createElement('button');
        stop.type = 'button'; stop.className = 'btn alt'; stop.textContent = '停用';
        stop.onclick = () => {
          if (confirm('確定停用「' + row.name + '」？歷史簽收紀錄不受影響。')) {
            void changeBranchSigner(() => bapi('/api/branch-session/signers/' + row.id, {
              method: 'PATCH', body: JSON.stringify({ active: false })
            }));
          }
        };
        item.append(stop);
      }
      list.append(item);
    }
    status.textContent = rows.length ? '' : '目前沒有' + (include ? '' : '有效的') + '常用簽收人員。';
    return true;
  } catch (error) { status.textContent = '名單載入失敗：' + error.message; return false; }
}
async function changeBranchSigner(action, afterSave) {
  if (signerManagementBusy) return;
  signerManagementBusy = true;
  const panel = document.getElementById('branchSigners');
  const controls = Array.from(panel.querySelectorAll('button, input'));
  controls.forEach(control => control.disabled = true);
  try {
    await action();
    if (afterSave) afterSave();
    if (await loadBranchSigners()) document.getElementById('branchSignerMessage').textContent = '名單已更新；歷史簽收姓名與簽名維持原樣。';
  } catch (error) {
    document.getElementById('branchSignerMessage').textContent = '儲存失敗：' + error.message;
  } finally {
    controls.forEach(control => control.disabled = false);
    signerManagementBusy = false;
  }
}
function addBranchSigner(event) {
  event.preventDefault();
  const input = document.getElementById('newBranchSigner');
  return changeBranchSigner(() => bapi('/api/branch-session/signers', {
    method: 'POST', body: JSON.stringify({ name: input.value })
  }), () => input.value = '');
}
