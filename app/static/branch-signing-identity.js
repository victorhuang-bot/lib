// Shared by first signing, historical late signing and corrections.
// Roster names are DOM text only; submitted IDs are validated again by the server.
let branchSigningChoiceEpoch = 0;
function branchSigningIdentityMarkup() {
  return `<section class="signing-identity" aria-label="選擇本站簽收人員">
    <p>請選擇本站人員、核對四項配送數量，再完成手寫簽名。</p>
    <label for="signer">簽收人員</label>
    <select id="signer" required disabled><option value="">請選擇簽收人員</option></select>
    <p id="signerChoiceStatus" role="status" aria-live="polite">載入人員名單…</p>
    <button type="button" class="btn alt" onclick="focusNewBranchSigner()">新增簽收人員</button>
    <button type="button" class="btn alt" onclick="refreshBranchSigningChoices()">重新載入人員</button>
  </section>`;
}
function focusNewBranchSigner() {
  const input = document.getElementById('newBranchSigner');
  if (input) { input.scrollIntoView({block:'center',behavior:'auto'}); input.focus(); }
}
function selectedBranchSigningPerson() {
  const select = document.getElementById('signer');
  if (!select || select.disabled || !select.value) return null;
  const option = select.selectedOptions[0];
  return {signer_id:Number(select.value), signer:option.textContent};
}
function branchSignatureHasInk(canvas) {
  if (!canvas) return false;
  const pixels = canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;
  for (let i=0;i<pixels.length;i+=4) {
    if(pixels[i+3] && (pixels[i]<255 || pixels[i+1]<255 || pixels[i+2]<255))return true;
  }
  return false;
}
function updateBranchSigningReady() {
  const person = selectedBranchSigningPerson();
  const canvas = document.querySelector('#sig');
  if(canvas)canvas.style.pointerEvents=person?'auto':'none';
  const button = document.getElementById('branchSignBtn') || document.getElementById('branchCorrectBtn');
  const reason = document.getElementById('reason');
  if(button)button.disabled=!person || !branchSignatureHasInk(canvas) || Boolean(reason&&!reason.value.trim());
}
async function refreshBranchSigningChoices() {
  const select=document.getElementById('signer');
  if(!select || select.tagName!=='SELECT')return;
  const epoch=++branchSigningChoiceEpoch;
  const previous=selectedBranchSigningPerson();
  const status=document.getElementById('signerChoiceStatus');
  select.disabled=true; updateBranchSigningReady();
  try {
    const rows=await bapi('/api/branch-session/signers');
    if(epoch!==branchSigningChoiceEpoch || document.getElementById('signer')!==select)return;
    const placeholder=document.createElement('option');placeholder.value='';placeholder.textContent='請選擇簽收人員';
    select.replaceChildren(placeholder);
    for(const row of rows.filter(row=>row.active)) {
      const option=document.createElement('option');option.value=String(row.id);option.textContent=row.name;select.append(option);
    }
    select.value='';
    // Never pick the first person; invalidate stale names after rename/deactivation.
    if(previous && rows.some(row=>row.active&&row.id===previous.signer_id&&row.name===previous.signer))select.value=String(previous.signer_id);
    select.disabled=select.options.length===1;
    status.textContent=select.disabled?'沒有可用人員，請先新增簽收人員，再從名單選擇。':'請自行選擇簽收人員；新增或改名後請再次核對。';
  } catch(error) {
    if(epoch!==branchSigningChoiceEpoch || document.getElementById('signer')!==select)return;
    select.value='';select.disabled=true;status.textContent='人員名單載入失敗：'+error.message+'；請重新載入。';
  }
  updateBranchSigningReady();
}
function initBranchSigningIdentity() {
  const select=document.getElementById('signer'), canvas=document.querySelector('#sig');
  const readSignature=sigPad(canvas);
  getSig=()=>branchSignatureHasInk(canvas)?readSignature():'';
  select.addEventListener('change',updateBranchSigningReady);
  canvas.addEventListener('pointermove',()=>queueMicrotask(updateBranchSigningReady));
  canvas.addEventListener('pointerup',()=>queueMicrotask(updateBranchSigningReady));
  document.getElementById('reason')?.addEventListener('input',updateBranchSigningReady);
  updateBranchSigningReady();
  void refreshBranchSigningChoices();
}
