/* Loaded only on branch and office pages. Driver has no new UI or permissions. */
const EB = {
  state:null, office:null, pending:null,
  escape(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))},
  date(){return new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Taipei'}).format(new Date())},
  key(){return crypto.randomUUID()},
  async on(){return (await fetch('/api/empty-boxes/config').then(r=>r.json())).enabled},
  async attempt(job){try{await job()}catch(e){alert(e.message)}},
  async send(path,body,branch=false){return (branch?bapi:api)(path,{method:'POST',body:JSON.stringify(body)})},
  input(label,name,value='',type='text',extra=''){return `<label>${label}<input name="${name}" type="${type}" value="${this.escape(value)}" ${extra}></label>`},
  async branch(){
    let host=document.getElementById('emptyBoxBranch');
    if(!await this.on().catch(()=>false)){host?.remove();return}
    if(!host){host=document.createElement('section');host.id='emptyBoxBranch';host.className='eb-panel';document.getElementById('work').after(host)}
    try{
      this.state=await bapi('/api/branch-session/empty-boxes');
      const x=this.state,b=x.balance;
      host.innerHTML=`<h2>空箱盤點與交接</h2><p>本站目前未使用 ${b?.unused??'—'} 箱・已裝書 ${b?.loaded??'—'} 箱・損壞 ${b?.damaged??'—'} 箱・持有總數 ${b?b.unused+b.loaded+b.damaged:'—'} 箱</p>
        ${x.reminders.length?`<p class="eb-status">最近盤點提醒：${this.escape(x.reminders[0].created_at)} ${this.escape(x.reminders[0].note)}</p>`:''}
        <details><summary>新增盤點</summary><form id="ebStock"><div class="eb-fields">${this.input('未使用空箱（箱）','unused',b?.unused??0,'number','min="0" required')}${this.input('已裝書用箱（箱）','loaded',b?.loaded??0,'number','min="0" required')}${this.input('損壞箱（箱）','damaged',b?.damaged??0,'number','min="0" required')}${this.input('盤點人','actor','','text','required maxlength="80"')}${this.input('備註','note')}</div><p id="ebStockTotal" aria-live="polite"></p><button class="btn">保存盤點</button></form></details>
        <details><summary>申請補箱</summary><form id="ebRequest"><div class="eb-fields">${this.input('申請箱數','quantity',1,'number','min="1" required')}${this.input('申請人','actor','','text','required maxlength="80"')}${this.input('備註','note')}</div><button class="btn">送出補箱申請</button></form></details>
        <h3>待交接空箱</h3><p>請核對實際數量與交接日期；可提前確認送達，原預定日期會保留。</p><div id="ebTasks"></div><div id="ebStandalone"></div>
        <details><summary>補箱申請紀錄（${x.requests.length}）</summary>${x.requests.map(r=>`<p>${this.escape(r.created_at)}・申請 ${r.quantity} 箱・尚缺 ${r.remaining} 箱・${this.escape(r.status)} ${r.status==='OPEN'?`<button type="button" data-cancel="${r.id}">取消申請</button>`:''}</p>`).join('')||'<p>尚無補箱申請。</p>'}</details>
        <details><summary>盤點歷史（${x.stocktakes.length}）</summary><div class="eb-scroll"><table><thead><tr><th>盤點時間／人員</th><th>未使用</th><th>裝書</th><th>損壞</th><th>總數</th><th>備註</th></tr></thead><tbody>${x.stocktakes.map(s=>`<tr><td>${this.escape(s.created_at)}<br>${this.escape(s.actor)}・版本 ${s.version}</td>${[s.unused,s.loaded,s.damaged,s.unused+s.loaded+s.damaged].map(n=>`<td class="eb-num">${n}</td>`).join('')}<td>${this.escape(s.note)}</td></tr>`).join('')||'<tr><td colspan="6">尚未盤點。</td></tr>'}</tbody></table></div></details>
        <details><summary>交接歷史（${x.handoffs.length}）</summary>${x.handoffs.map(h=>`<p>${h.kind==='PICKUP'?'交回':'收到'} ${h.quantity} 箱・實際 ${h.actual_date}<br>簽收 ${this.escape(h.confirmed_at)}・${this.escape(h.actor)} ${this.escape(h.reason)}</p>`).join('')||'<p>尚無交接紀錄。</p>'}</details>`;
      const stock=document.getElementById('ebStock');
      const total=()=>document.getElementById('ebStockTotal').textContent='持有總箱數：'+['unused','loaded','damaged'].reduce((n,k)=>n+Number(stock.elements[k].value),0)+' 箱';stock.oninput=total;total();
      stock.onsubmit=e=>{e.preventDefault();this.attempt(async()=>{const p=Object.fromEntries(new FormData(stock));['unused','loaded','damaged'].forEach(k=>p[k]=Number(p[k]));p.version=b?.version??0;p.event_key=stock.dataset.key||=this.key();await this.send('/api/branch-session/empty-boxes/stocktakes',p,true);await this.branch()})};
      const form=document.getElementById('ebRequest');form.onsubmit=e=>{e.preventDefault();this.attempt(async()=>{const p=Object.fromEntries(new FormData(form));p.quantity=Number(p.quantity);p.event_key=form.dataset.key||=this.key();await this.send('/api/branch-session/empty-boxes/requests',p,true);await this.branch()})};
      host.querySelectorAll('[data-cancel]').forEach(btn=>btn.onclick=()=>this.attempt(async()=>{if(!confirm('取消這筆補箱申請？'))return;await this.send('/api/branch-session/empty-boxes/cancel-request',{id:btn.dataset.cancel},true);await this.branch()}));
      document.getElementById('ebTasks').innerHTML=x.tasks.map(t=>`<div class="eb-task"><p><b>${t.kind==='PICKUP'?'預定交回':'預定收到'} ${t.quantity} 箱</b>・${t.planned_date}</p>${t.needs_replan?'<p class="eb-status">待總館重新安排</p>':t.kind==='RECEIVE'&&!t.ready?'<p>來源尚未全部交回</p>':`<button type="button" data-handoff="${t.task_id}">填寫實際交接</button>`}</div>`).join('')||'<p>目前沒有待交接空箱。</p>';
      host.querySelectorAll('[data-handoff]').forEach(btn=>btn.onclick=()=>this.openHandoff(x.tasks.find(t=>t.task_id===btn.dataset.handoff)));
      // Current delivery uses one existing signature; standalone uses the same roster validation.
      this.pending=null;document.getElementById('ebLinked')?.remove();
    }catch(e){host.textContent='空箱資料載入失敗：'+e.message}
  },
  openHandoff(t){
    const linked=['WAITING_BRANCH','LATE_BRANCH_PENDING'].includes(data?.status);
    const root=linked?document.getElementById('work'):document.getElementById('ebStandalone');
    document.getElementById('ebLinked')?.remove();
    const el=document.createElement('section');el.id='ebLinked';el.className='eb-panel';
    el.innerHTML=`<h3>${t.kind==='PICKUP'?'交回':'收到'}空箱確認</h3><p>原預定日期 ${t.planned_date}・預定 ${t.quantity} 箱</p><div class="eb-fields">${this.input('實際箱數','quantity',t.quantity,'number','min="0" required')}${this.input('實際交接日期','actual_date',this.date(),'date','required')}${this.input('差異原因','reason')}</div>${linked?'<label><input type="checkbox" id="ebInclude"> 與本次圖書簽收一起確認空箱</label><p>稍後補簽時，請填寫原實際交接日。</p>':'<label>本站簽收人員<select id="ebSigner"><option value="">請選擇</option></select></label><p>請簽名</p><canvas id="ebSig" width="600" height="180"></canvas><button type="button" id="ebClear">清除簽名</button><button type="button" class="btn" id="ebConfirm">確認交接</button>'}`;
    root.append(el);this.pending=t;
    if(!linked)this.attempt(async()=>{const rows=await bapi('/api/branch-session/signers');const select=el.querySelector('#ebSigner');rows.filter(s=>s.active).forEach(s=>{const o=document.createElement('option');o.value=s.id;o.textContent=s.name;select.append(o)});const canvas=el.querySelector('#ebSig'),read=sigPad(canvas);el.querySelector('#ebClear').onclick=()=>canvas.getContext('2d').clearRect(0,0,600,180);el.querySelector('#ebConfirm').onclick=()=>this.attempt(async()=>{if(!select.value||!branchSignatureHasInk(canvas))throw Error('請選擇本站人員並簽名');const p=this.handoffPayload();await this.send('/api/branch-session/empty-boxes/handoffs',{...p,signer_id:Number(select.value),signature:read()},true);await this.branch()})});
    el.scrollIntoView({block:'center'});
  },
  handoffPayload(){const el=document.getElementById('ebLinked');return {kind:this.pending.kind,task_id:this.pending.task_id,quantity:Number(el.querySelector('[name=quantity]').value),actual_date:el.querySelector('[name=actual_date]').value,reason:el.querySelector('[name=reason]').value}},
  linkedPayload(){return this.pending&&document.getElementById('ebInclude')?.checked?[this.handoffPayload()]:[]},
  async officeLoad(){
    const host=document.getElementById('emptyBoxes');
    try{
      this.office=await api('/api/empty-boxes');const x=this.office;
      host.innerHTML=`<div class="eb-panel"><h2>空箱盤點與調度</h2><div class="eb-summary"><a href="#ebCounted" onclick="document.getElementById('ebCounted').open=true">已盤點 ${x.counted} / ${x.total_branches} 館</a><span>最新盤點未使用空箱 ${x.unused_snapshot_total} 箱</span><a href="#ebShortage">缺空箱 ${x.shortage_branches} 館</a></div><p>涵蓋率以已盤點分館計算；可調出量會扣除已保留調度。</p>
        <details id="ebCounted"><summary>盤點清單與提醒</summary><div class="eb-scroll"><table><thead><tr><th>分館</th><th>盤點時間／盤點人</th><th>未用／裝書／損壞／總數</th><th>可調出</th><th>提醒</th></tr></thead><tbody>${x.branches.map(b=>{const s=b.stocktake;return `<tr><td>${this.escape(b.name)}</td><td>${s?this.escape(s.created_at)+'<br>'+this.escape(s.actor):'未盤點'}</td><td>${s?[s.unused,s.loaded,s.damaged,s.unused+s.loaded+s.damaged].join(' / '):'—'}</td><td class="eb-num">${b.available}</td><td>${b.overdue?`<button data-remind="${b.id}">提醒盤點</button>`:'盤點有效'}</td></tr>`}).join('')}</tbody></table></div></details>
        <h3 id="ebShortage">有效補箱申請</h3>${x.requests.map(r=>`<p>${this.escape(r.branch_name)}・${this.escape(r.created_at)}・尚缺 ${r.remaining} 箱（已安排 ${r.allocated} 箱） <button data-plan="${r.id}">安排調度</button></p>`).join('')||'<p>目前沒有未補足申請。</p>'}<div id="ebPlan"></div>
        <h3>待交接與在途空箱</h3>${x.tasks.map(t=>`<div class="eb-task"><p>需求館 ${this.escape(x.branches.find(b=>b.id===t.branch_id)?.name)}・送達 ${t.planned_date}・${t.quantity} 箱・${t.storage} ${t.in_transit} 箱 ${t.needs_replan?'<strong class="eb-status">待重新安排</strong>':''}</p>${t.sources.map(s=>`<p>來源 ${this.escape(x.branches.find(b=>b.id===s.branch_id)?.name)}・${s.quantity} 箱・預定收取 ${s.planned_date}・實際 ${s.actual_date||'待交回'}</p>`).join('')}<button data-replan="${t.id}">重新安排日期</button> <button data-stop="${t.id}">取消未收取調度</button></div>`).join('')||'<p>目前沒有進行中調度。</p>'}</div>`;
      host.querySelectorAll('[data-remind]').forEach(b=>b.onclick=()=>this.attempt(async()=>{await this.send('/api/empty-boxes/reminders',{branch_id:Number(b.dataset.remind)});b.textContent='已送出提醒';b.disabled=true}));
      host.querySelectorAll('[data-plan]').forEach(b=>b.onclick=()=>this.plan(x.requests.find(r=>r.id===b.dataset.plan)));
      host.querySelectorAll('[data-stop]').forEach(b=>b.onclick=()=>this.attempt(async()=>{const reason=prompt('取消調度原因');if(!reason)return;await this.send('/api/empty-boxes/cancel-transfer',{id:b.dataset.stop,reason});await this.officeLoad()}));
      host.querySelectorAll('[data-replan]').forEach(b=>b.onclick=()=>this.replan(x.tasks.find(t=>t.id===b.dataset.replan)));
    }catch(e){host.textContent='空箱資料載入失敗：'+e.message}
  },
  options(){return this.office.branches.map(b=>`<option value="${b.id}">${this.escape(b.name)}（可調 ${b.available} 箱）</option>`).join('')},
  plan(r){
    const el=document.getElementById('ebPlan');el.innerHTML=`<form class="eb-panel" id="ebPlanForm"><h3>${this.escape(r.branch_name)}補箱調度</h3><p>尚缺 ${r.remaining} 箱，可安排 ${r.remaining-r.allocated} 箱</p><div class="eb-fields">${this.input('預定送達日期','planned_date',this.date(),'date','required')}${this.input('調度說明','reason')}</div><button type="button" id="ebArrivalSuggest">建議送達日期</button><p id="ebArrivalDates"></p><div id="ebSources"></div><button type="button" id="ebAddSource">增加來源館</button> <button class="btn">建立調度</button></form>`;
    const form=el.querySelector('form');const add=()=>{const row=document.createElement('div');row.className='eb-fields eb-source';row.innerHTML=`<label>收取館<select name="branch_id">${this.options()}</select></label>${this.input('箱數','quantity',1,'number','min="1" required')}${this.input('預定收取日','source_date',this.date(),'date','required')}<button type="button" data-suggest>建議收取日</button><button type="button" data-remove>移除此來源</button><span data-dates></span>`;form.querySelector('#ebSources').append(row);row.querySelector('[data-remove]').onclick=()=>row.remove();row.querySelector('[data-suggest]').onclick=()=>this.attempt(async()=>{const dates=await api('/api/empty-boxes/suggestions?branch_id='+row.querySelector('select').value+'&start='+this.date());row.querySelector('[data-dates]').textContent=dates.dates.join('、')||'60 日內無有效班表';if(dates.dates[0])row.querySelector('[name=source_date]').value=dates.dates[0]})};add();form.querySelector('#ebAddSource').onclick=add;
    form.querySelector('#ebArrivalSuggest').onclick=()=>this.attempt(async()=>{const dates=await api('/api/empty-boxes/suggestions?branch_id='+r.branch_id+'&start='+this.date());form.querySelector('#ebArrivalDates').textContent=dates.dates.join('、')||'60 日內無有效班表';if(dates.dates[0])form.elements.planned_date.value=dates.dates[0]});
    form.onsubmit=e=>{e.preventDefault();this.attempt(async()=>{const p={request_id:r.id,planned_date:form.elements.planned_date.value,reason:form.elements.reason.value,event_key:form.dataset.key||=this.key(),sources:[...form.querySelectorAll('.eb-source')].map(row=>({branch_id:Number(row.querySelector('select').value),quantity:Number(row.querySelector('[name=quantity]').value),planned_date:row.querySelector('[name=source_date]').value}))};await this.send('/api/empty-boxes/transfers',p);await this.officeLoad()})};el.scrollIntoView({block:'center'});
  },
  replan(t){
    const el=document.getElementById('ebPlan');el.innerHTML=`<form class="eb-panel"><h3>重新安排調度日期</h3>${this.input('送達日','planned_date',t.planned_date,'date','required')}${t.sources.filter(s=>!s.actual_date).map(s=>this.input('來源 '+(this.office.branches.find(b=>b.id===s.branch_id)?.name||s.branch_id)+' 收取日',s.id,s.planned_date,'date','required')).join('')}${this.input('調整原因','reason','','text','required')}<button class="btn">保存重新安排</button></form>`;el.querySelector('form').onsubmit=e=>{e.preventDefault();this.attempt(async()=>{const f=e.target;await this.send('/api/empty-boxes/reschedule',{id:t.id,planned_date:f.elements.planned_date.value,reason:f.elements.reason.value,source_dates:Object.fromEntries(t.sources.filter(s=>!s.actual_date).map(s=>[s.id,f.elements[s.id].value]))});await this.officeLoad()})};el.scrollIntoView({block:'center'});
  }
};
