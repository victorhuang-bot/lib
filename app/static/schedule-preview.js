// Read-only preview before an explicit scheduling mutation.
function confirmSchedulePreview(preview, label, body) {
  return new Promise(resolve => {
    const dialog = document.createElement('dialog');
    dialog.style.cssText = 'width:min(94vw,760px);max-height:90dvh;overflow:auto;border:1px solid #aaa;border-radius:12px;padding:20px;box-sizing:border-box';
    const title = document.createElement('h2'); title.id = 'schedulePreviewTitle';
    title.textContent = label + '：順延預覽';
    dialog.setAttribute('aria-labelledby',title.id);
    const range = document.createElement('p');
    range.textContent = body.start_date + ' ～ ' + body.end_date;
    const hint = document.createElement('p');
    hint.textContent = body.manual_target_date ? '手動補送工作日：'+body.manual_target_date+'；不受分館固定配送星期限制。只補送公文，不搬移圖書；已有操作、簽名或鎖定資料會阻擋。' : '僅搬移尚未開始配送的預填公文。跳過休館、停送及非配送日；已操作、簽收或鎖定資料會阻擋整批變更。';
    dialog.append(title, range, hint);
    const list = document.createElement('ul'); list.style.cssText='padding-left:24px;overflow-wrap:anywhere';
    for (const row of preview.plan) {
      const li = document.createElement('li'); li.style.marginBottom='12px';
      li.textContent = `${row.branch_code} ${row.branch_name}：${row.sources.map(s=>s.date+' '+s.qty+'件').join('、')} → ${row.target_date}；目標原有 ${row.existing_qty??0} 件，結果 ${row.total} 件`;
      list.append(li);
    }
    if(!preview.plan.length) list.textContent='沒有需順延的預填公文。';
    dialog.append(list);
    const consent = document.createElement('input'); consent.type='checkbox';
    const agree = document.createElement('label'); agree.style.cssText='display:block;margin:16px 0';
    if(preview.has_conflicts) {
      agree.append(consent,document.createTextNode(' 我確認目標已有公文或多日匯入，依預覽數量相加，不覆蓋原數量。'));
      dialog.append(agree);
    }
    const submit = document.createElement('button'); submit.type='button';submit.className='btn';submit.textContent='確認儲存';
    submit.style.cssText='min-height:44px;margin:4px';
    submit.disabled=preview.has_conflicts;
    consent.onchange=()=>{submit.disabled=!consent.checked};
    const cancel = document.createElement('button');cancel.type='button';cancel.className='btn alt';cancel.textContent='取消';cancel.style.cssText='min-height:44px;margin:4px';
    let accepted=false;
    submit.onclick=()=>{accepted=true;dialog.close()}; cancel.onclick=()=>dialog.close();
    const opener=document.activeElement;
    dialog.addEventListener('close',()=>{dialog.remove();opener?.focus();resolve(accepted)},{once:true});
    dialog.append(submit,cancel);document.body.append(dialog);dialog.showModal();cancel.focus();
  });
}
