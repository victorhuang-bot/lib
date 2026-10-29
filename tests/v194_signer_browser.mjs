// Full branch render/sign/correct functions with local DOM and synthetic API responses.
// No backend imports, credentials, network or screenshots. Throwaway Chrome profile.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { homedir } from 'node:os';
import { pathToFileURL } from 'node:url';
const root=resolve(import.meta.dirname,'..');
const {Browser}=await import(pathToFileURL(join(process.env.TASTE_SKILL_DIR||join(homedir(),'.codex/skills/design-taste-frontend'),'scripts/lib/cdp.mjs')));
const html=readFileSync(join(root,'app/static/branch.html'),'utf8');
const body=html.match(/<body>([\s\S]*?)<script src=/)[1];
const css=readFileSync(join(root,'app/static/style.css'),'utf8')+'\n'+html.match(/<style>([\s\S]*?)<\/style>/)[1];
const inline=[...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)].map(m=>m[1]).filter(Boolean).join('\n')
  .replace("const branchToken='__BRANCH_TOKEN__';let session=null,data=null,getSig=null;",'window.session=null;window.data=null;window.getSig=null;');
const scripts=['common.js','branch-signers.js','branch-signing-identity.js'].map(f=>readFileSync(join(root,'app/static',f),'utf8'));
const browser=await Browser.launch({extraArgs:['--disable-background-networking','--disable-sync','--disable-extensions','--disable-component-update','--no-pings']});
const results=[];
try {
  for(const width of [320,390,768,1440])for(const mode of ['normal','late','correct']){
    const page=await browser.newPage({width,height:1000,mobile:width<700});
    await page.send('Fetch.enable',{patterns:[{urlPattern:'*',requestStage:'Request'}]});
    browser.on(page.sessionId,'Fetch.requestPaused',e=>{void page.send('Fetch.failRequest',{requestId:e.requestId,errorReason:'BlockedByClient'}).catch(()=>{});});
    await page.eval(({body,css,scripts,inline,mode})=>{
      document.head.innerHTML='<meta name="viewport" content="width=device-width,initial-scale=1">';
      const style=document.createElement('style');style.textContent=css;document.head.append(style);
      document.body.innerHTML=body;
      scripts.forEach(source=>(0,eval)(source));(0,eval)(inline);
      window.confirm=()=>true;window.alert=message=>{window.lastAlert=message};
      window.rows=[];window.requests=[];window.failRoster=false;
      window.bapi=async(path,options={})=>{
        if(path.startsWith('/api/branch-session/signers')){
          if(failRoster)throw Error('synthetic roster failure');
          if(options.method==='POST'){
            const row={id:11,name:JSON.parse(options.body).name,active:1};rows.push(row);return row;
          }
          return rows;
        }
        if(path==='/api/branch-session/sign'||path==='/api/branch-session/correct'){
          requests.push({path,payload:JSON.parse(options.body)});return {ok:true};
        }
        throw Error('Unexpected mock request '+path);
      };
      window.load=async()=>{};
      window.data={branch_name:'合成分館',service_date:'2030-01-04',status:mode==='late'?'LATE_BRANCH_PENDING':'WAITING_BRANCH',
        document_final:11,document_return_final:22,outbound_final:33,inbound_final:44,items:[],correction:{driver_note:'合成要求'}};
      work.classList.remove('hidden');
      if(mode==='correct')renderCorrection();else renderSign();
    },{body,css,scripts,inline,mode});
    await page.eval('refreshBranchSigningChoices()');
    assert.deepEqual(await page.eval(()=>({value:signer.value,disabled:signer.disabled,placeholder:signer.options[0].textContent,empty:signerChoiceStatus.textContent.includes('先新增')})),
      {value:'',disabled:true,placeholder:'請選擇簽收人員',empty:true});
    await page.eval(()=>{document.querySelector('.signing-identity button').click();newBranchSigner.value='合成 <b>甲</b>';return addBranchSigner({preventDefault(){}});});
    assert.equal(await page.eval(()=>signer.value),'','Adding must not implicitly select a person');
    assert.equal(await page.eval(()=>signer.options[1].textContent),'合成 <b>甲</b>');
    assert.equal(await page.eval(()=>signer.querySelector('b')===null),true);
    await page.eval(()=>{signer.value='11';signer.dispatchEvent(new Event('change'));});
    assert.equal(await page.eval(()=>document.querySelector('#branchSignBtn,#branchCorrectBtn').disabled),true,'Blank signature must block');
    await page.eval(()=>{
      if(document.getElementById('reason'))reason.value='合成更正原因';
      const canvas=document.getElementById('sig');canvas.scrollIntoView({block:'center'});
      const r=canvas.getBoundingClientRect();
      for(const [type,x,y] of [['pointerdown',10,10],['pointermove',40,25],['pointerup',40,25]])
        canvas.dispatchEvent(new PointerEvent(type,{clientX:r.left+x,clientY:r.top+y,bubbles:true,pointerId:1}));
    });
    assert.equal(await page.eval(()=>document.querySelector('#branchSignBtn,#branchCorrectBtn').disabled),false);
    const metrics=await page.eval(()=>{
      const s=signer.getBoundingClientRect();return {width:innerWidth,fits:s.left>=0&&s.right<=innerWidth,height:s.height,overflow:document.documentElement.scrollWidth>innerWidth};
    });
    assert.equal(metrics.fits,true,JSON.stringify(metrics));assert.equal(metrics.overflow,false,JSON.stringify(metrics));assert.ok(metrics.height>=44);
    await page.eval(mode==='correct'?'correct()':'sign()');
    const sent=await page.eval(()=>requests[0]);
    assert.equal(sent.payload.signer_id,11);assert.equal(sent.payload.signer,'合成 <b>甲</b>');assert.match(sent.payload.signature,/^data:image\/png;base64,/);
    assert.equal(sent.path,mode==='correct'?'/api/branch-session/correct':'/api/branch-session/sign');
    if(mode!=='correct')assert.deepEqual([sent.payload.document,sent.payload.document_return,sent.payload.outbound,sent.payload.inbound],[11,22,33,44]);
    else assert.deepEqual([sent.payload.document_return,sent.payload.inbound],[null,null]);
    await page.eval(()=>{clearSig();rows[0].name='合成改名';return refreshBranchSigningChoices();});
    assert.equal(await page.eval(()=>signer.value),'','Renamed selection must reset');
    await page.eval(()=>{rows[0].active=0;return refreshBranchSigningChoices();});
    assert.equal(await page.eval(()=>signer.options.length),1,'Disabled person cannot remain selectable');
    await page.eval(()=>{failRoster=true;return refreshBranchSigningChoices();});
    assert.equal(await page.eval(()=>signer.disabled&&document.querySelector('#branchSignBtn,#branchCorrectBtn').disabled),true);
    results.push({width,mode,status:'PASS'});
  }
  console.log(JSON.stringify({passed:results.length,failed:0,results,scope:'Local synthetic branch DOM workflows; no live API/DB or screenshots'},null,2));
}finally{await browser.close();}
