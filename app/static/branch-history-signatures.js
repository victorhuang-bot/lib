// Read-only, branch-scoped history. Never interpolate stored image/identity HTML.
async function viewBranchSignatures(id) {
  const opener = document.activeElement;
  let dialog;
  try {
    const row = await bapi('/api/branch-session/history/' + Number(id) + '/signatures');
    dialog = document.createElement('dialog');
    dialog.style.cssText = 'width:min(92vw,640px);max-height:90dvh;overflow:auto;border:1px solid #aaa;border-radius:12px;padding:20px';
    const heading = document.createElement('h2');
    heading.id = 'historySignatureTitle';
    heading.textContent = row.service_date + ' 歷史簽名（唯讀）';
    dialog.setAttribute('aria-labelledby', heading.id);
    dialog.append(heading);
    for (const [title, image, name, time] of [
      ['原始簽收', row.branch_signature, row.signer_name, row.branch_signed_at],
      ['更正簽收', row.correction_signature, row.correction_signer_name, row.corrected_at]
    ]) {
      const section = document.createElement('section');
      const label = document.createElement('h3');
      label.textContent = title;
      const info = document.createElement('p');
      info.textContent = (name || '未記錄姓名') + '｜' + (time ? fmtDateTime(time) : '未記錄時間');
      section.append(label, info);
      if (typeof image === 'string' && /^data:image\/(?:png|jpeg);base64,[A-Za-z0-9+/=\r\n]+$/.test(image)) {
        const img = document.createElement('img');
        img.alt = title + '手寫簽名';
        img.style.cssText = 'display:block;max-width:100%;height:auto;background:white;border:1px solid #ccc';
        img.src = image;
        section.append(img);
      } else {
        const missing = document.createElement('p');
        missing.textContent = image ? '簽名格式無法安全顯示，請洽管理者查詢原始紀錄。' : '沒有此項簽名紀錄。';
        section.append(missing);
      }
      dialog.append(section);
    }
    const close = document.createElement('button');
    close.type = 'button'; close.className = 'btn'; close.textContent = '關閉';
    close.onclick = () => dialog.close();
    dialog.append(close);
    dialog.addEventListener('close', () => { dialog.remove(); opener?.focus(); }, {once:true});
    document.body.append(dialog);
    dialog.showModal(); close.focus();
  } catch (error) {
    dialog?.remove();
    alert('簽名查詢失敗：' + error.message);
  }
}
