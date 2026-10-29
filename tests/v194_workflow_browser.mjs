// Local static component checks. No backend imports, credentials, cookies or APIs.
// Chrome uses a throwaway profile. Browser requests are blocked before dispatch.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve, join } from 'node:path';
import { homedir } from 'node:os';
import { pathToFileURL } from 'node:url';
const root=resolve(import.meta.dirname,'..');
const { Browser }=await import(pathToFileURL(join(process.env.TASTE_SKILL_DIR||join(homedir(),'.codex/skills/design-taste-frontend'),'scripts/lib/cdp.mjs')));
const previewSource=readFileSync(join(root,'app/static/schedule-preview.js'),'utf8');
const signatureSource=readFileSync(join(root,'app/static/branch-history-signatures.js'),'utf8');
const css=readFileSync(join(root,'app/static/style.css'),'utf8');
const browser=await Browser.launch({extraArgs:['--disable-background-networking','--disable-sync','--disable-extensions','--disable-component-update','--no-pings']});
const results=[];
try {
  for (const width of [320,390,768,1440]) {
    const page=await browser.newPage({width,height:900,mobile:width<700});
    await page.send('Fetch.enable',{patterns:[{urlPattern:'*',requestStage:'Request'}]});
    browser.on(page.sessionId,'Fetch.requestPaused',event=>{void page.send('Fetch.failRequest',{requestId:event.requestId,errorReason:'BlockedByClient'}).catch(()=>{});});
    await page.eval(({css,previewSource,signatureSource})=>{
      document.head.innerHTML='<meta name="viewport" content="width=device-width, initial-scale=1">';
      const style=document.createElement('style');style.textContent=css;document.head.append(style);
      document.body.innerHTML='<button id="opener">檢查</button>';
      window.fmtDateTime=v=>v;window.alert=message=>{window.lastAlert=message};
      // Synthetic data only; bapi never performs a fetch.
      window.bapi=async()=>({service_date:'2030-01-04',signer_name:'<script>test</script>',branch_signed_at:'2030-01-04',branch_signature:'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=',correction_signature:'javascript:alert(1)'});
      (0,eval)(previewSource+'\n'+signatureSource);
      document.getElementById('opener').focus();
      const row={branch_code:'K13',branch_name:'士林',target_date:'2030-01-07',existing_qty:8,total:13,sources:[{date:'2030-01-04',qty:5}]};
      window.acceptance=confirmSchedulePreview({plan:Array.from({length:80},()=>row),has_conflicts:true},'颱風停送',{start_date:'2030-01-04',end_date:'2030-01-04'});
    },{css,previewSource,signatureSource});
    const preview=await page.eval(()=>{
      const d=document.querySelector('dialog'),buttons=[...d.querySelectorAll('button')];
      const metrics={open:d.open,rows:d.querySelectorAll('li').length,disabled:buttons[0].disabled,
        fits:d.getBoundingClientRect().right<=innerWidth&&d.getBoundingClientRect().left>=0,
        scrollable:d.scrollHeight>d.clientHeight,touch:buttons.every(b=>b.getBoundingClientRect().height>=44)};
      const check=d.querySelector('input');check.checked=true;check.dispatchEvent(new Event('change'));
      metrics.explicitEnabled=!buttons[0].disabled;buttons[0].click();return metrics;
    });
    assert.equal(preview.open,true);assert.equal(preview.rows,80);assert.equal(preview.disabled,true);
    assert.equal(preview.fits,true);assert.equal(preview.scrollable,true);assert.equal(preview.touch,true);assert.equal(preview.explicitEnabled,true);
    assert.equal(await page.eval('acceptance'),true);
    await page.eval('viewBranchSignatures(1)');
    const signature=await page.eval(()=>{
      const d=document.querySelector('dialog');return {open:d.open,images:d.querySelectorAll('img').length,
        fits:d.getBoundingClientRect().right<=innerWidth&&d.getBoundingClientRect().left>=0,
        noScript:d.querySelectorAll('script').length===0,invalidBlocked:d.textContent.includes('格式無法安全顯示'),
        escaped:d.textContent.includes('<script>test</script>')};
    });
    assert.deepEqual(signature,{open:true,images:1,fits:true,noScript:true,invalidBlocked:true,escaped:true});
    await page.eval(()=>document.querySelector('dialog button').click());
    assert.equal(await page.eval(()=>document.activeElement.id),'opener');
    results.push({width,preview:'passed',signature:'passed'});
  }
  console.log(JSON.stringify({passed:8,failed:0,results,scope:'Local component mocks only; no live website/API/DB, no screenshots'},null,2));
} finally {await browser.close();}
