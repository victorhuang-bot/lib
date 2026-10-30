// Scoped localhost -> real test API -> PostgreSQL dropdown check. Never mocks API.
import assert from 'node:assert/strict';
import {homedir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
let browser;
try {
  const config=JSON.parse(process.env.V194_BROWSER_CONFIG||'{}');
  assert.match(config.base,/^http:\/\/127\.0\.0\.1:\d+$/);
  assert.ok(config.name.startsWith('V194_FUNC_')&&config.session&&config.qr);
  const helper=join(homedir(),'.codex/skills/design-taste-frontend/scripts/lib/cdp.mjs');
  const {Browser}=await import(pathToFileURL(helper));
  browser=await Browser.launch({extraArgs:['--disable-background-networking','--disable-sync','--disable-extensions','--disable-component-update','--no-pings']});
  const page=await browser.newPage({width:768,height:1000});
  await page.send('Fetch.enable',{patterns:[{urlPattern:'*',requestStage:'Request'}]});
  browser.on(page.sessionId,'Fetch.requestPaused',event=>{
    let approved=false;
    try {approved=new URL(event.request.url).origin===config.base;} catch {}
    void page.send(approved?'Fetch.continueRequest':'Fetch.failRequest',approved?{requestId:event.requestId}:{requestId:event.requestId,errorReason:'BlockedByClient'}).catch(()=>{});
  });
  await page.goto(config.base+'/branch/'+config.qr);
  await page.eval(({token})=>{
    session=token;document.getElementById('verify').classList.add('hidden');
    document.getElementById('work').classList.remove('hidden');
    return load();
  },{token:config.session});
  await page.eval('refreshBranchSigningChoices()');
  assert.equal(await page.eval(()=>document.getElementById('signer').value),'');
  const add=async name=>page.eval(async({name})=>{
    document.getElementById('newBranchSigner').value=name;
    await addBranchSigner({preventDefault(){}});
    const rows=await bapi('/api/branch-session/signers');
    return rows.find(row=>row.name===name)?.id;
  },{name});
  const first=await add(config.name);assert.ok(first>0);
  assert.equal(await page.eval(()=>signer.value),'');
  await page.eval(({id})=>{signer.value=String(id);signer.dispatchEvent(new Event('change'));},{id:first});
  assert.equal(await page.eval(()=>branchSignBtn.disabled),true);
  await page.eval(async({id,name})=>{
    await bapi('/api/branch-session/signers/'+id,{method:'PATCH',body:JSON.stringify({name})});
    await refreshBranchSigningChoices();
  },{id:first,name:config.name+'_renamed'});
  assert.equal(await page.eval(()=>signer.value),'');
  await page.eval(async({id})=>{
    await bapi('/api/branch-session/signers/'+id,{method:'PATCH',body:JSON.stringify({active:false})});
    await refreshBranchSigningChoices();
  },{id:first});
  assert.equal(await page.eval(({id})=>[...signer.options].some(o=>o.value===String(id)),{id:first}),false);
  const second=await add(config.name+'_selected');assert.ok(second>0);
  await page.eval(({id})=>{
    signer.value=String(id);signer.dispatchEvent(new Event('change'));
    const canvas=document.getElementById('sig');canvas.scrollIntoView({block:'center'});
    const rect=canvas.getBoundingClientRect();
    for(const [type,x,y] of [['pointerdown',5,5],['pointermove',40,20],['pointerup',40,20]])
      canvas.dispatchEvent(new PointerEvent(type,{clientX:rect.left+x,clientY:rect.top+y,bubbles:true,pointerId:1}));
  },{id:second});
  assert.equal(await page.eval(()=>branchSignBtn.disabled),false);
  await page.eval('sign()');
  assert.equal(await page.eval(()=>data.status),'WAITING_DRIVER_CONFIRM');
  assert.equal(await page.eval(()=>data.signer_name),config.name+'_selected');
  console.log(JSON.stringify({result:'PASS',real_api:true,automatic_preselection:false,rename_invalidated:true,inactive_removed:true,selected_signer_id:second}));
} catch {
  console.log(JSON.stringify({result:'BLOCKED',details:'Browser/API assertion failed; credentials and URLs withheld'}));
  process.exitCode=1;
} finally {
  if(browser)await browser.close();
}
