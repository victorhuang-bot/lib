async function api(url,opt={}){
  const t=sessionStorage.getItem('admin_session');
  const baseHeaders={...(opt.headers||{}),'Content-Type':'application/json',...(t?{'Authorization':'Bearer '+t}:{})};
  const method=(opt.method||'GET').toUpperCase();
  const safeRetry=(method==='GET'||method==='HEAD'||url.includes('/api/auth/login'));
  let lastStatus=0;
  for(let attempt=0;attempt<(safeRetry?2:1);attempt++){
    if(attempt>0) await new Promise(r=>setTimeout(r,1800));
    let r;
    try{
      r=await fetch(url,{...opt,headers:baseHeaders});
    }catch(e){
      if(attempt===0&&safeRetry) continue;
      throw new Error('目前服務連線不穩定，請稍候幾秒後再試。');
    }
    lastStatus=r.status;
    const txt=await r.text();
    let data;
    try{data=JSON.parse(txt)}catch{data=txt}
    if(r.ok)return data;
    if([502,503,504].includes(r.status)){
      if(attempt===0&&safeRetry)continue;
      throw new Error(safeRetry?'Render / PostgreSQL 正在喚醒或暫時連線不穩，系統已自動重試一次。請稍候 5～10 秒再試。':'存檔時服務暫時無法回應。為避免重複寫入，系統沒有自動重送；請稍候 5～10 秒後重新整理確認是否已存檔。');
    }
    if(r.status===401&&!url.includes('/api/auth/login')&&!url.includes('/api/auth/me')){
      sessionStorage.removeItem('admin_session');
      alert('登入狀態已失效，請重新登入。');
      location.reload();
      throw new Error('登入已失效');
    }
    if(r.status===403)throw new Error('目前登入角色沒有此操作權限；若同時開啟管理者與秘書頁籤，請重新整理此頁確認角色。');
    let msg=(data&&data.detail)||data||r.statusText;
    if(typeof msg==='string' && /<!DOCTYPE|<html|Bad Gateway/i.test(msg)){
      msg='服務暫時無法回應，請稍候幾秒後再試。';
    }
    throw new Error(msg);
  }
  throw new Error(`服務暫時無法回應（${lastStatus||'network'}）`);
}
function statusText(s){return ({WAITING_SECRETARY:'待秘書輸入',WAITING_DRIVER:'待司機輸入',WAITING_BRANCH:'待分館簽收',LATE_BRANCH_PENDING:'運送完成・待分館補簽',WAITING_DRIVER_CONFIRM:'分館已簽・待司機確認',WAITING_BRANCH_CORRECTION:'待分館更正',WAITING_DRIVER_RECONFIRM:'已更正・待司機確認',STOP_COMPLETED:'本站完成'})[s]||s}
function sigPad(canvas){const ctx=canvas.getContext('2d');ctx.lineWidth=3;ctx.lineCap='round';let down=false,last=null;function p(e){let r=canvas.getBoundingClientRect(),t=e.touches?e.touches[0]:e;return {x:(t.clientX-r.left)*canvas.width/r.width,y:(t.clientY-r.top)*canvas.height/r.height}}canvas.onpointerdown=e=>{down=true;last=p(e)};canvas.onpointermove=e=>{if(!down)return;let n=p(e);ctx.beginPath();ctx.moveTo(last.x,last.y);ctx.lineTo(n.x,n.y);ctx.stroke();last=n};canvas.onpointerup=()=>down=false;return()=>canvas.toDataURL('image/png')}
