// Real browser, actual scheduling controls/scripts, synthetic API responses.
// All network requests are blocked; no credentials or existing databases.
import assert from 'node:assert/strict';
import {readFileSync,writeFileSync,mkdirSync} from 'node:fs';
import {resolve,join} from 'node:path';
import {homedir,tmpdir} from 'node:os';
import {pathToFileURL} from 'node:url';
const root=resolve(import.meta.dirname,'..');
const html=readFileSync(join(root,'app/static/index.html'),'utf8');
const section=html.match(/<section id="schedule"[\s\S]*?<\/section>/)[0].replace('class="hidden"','');
const inlineStyles=[...html.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map(x=>x[1]).join('\n');
const css=readFileSync(join(root,'app/static/style.css'),'utf8')+'\n'+inlineStyles;
const source=html.slice(html.indexOf('let calDragging=false;'),html.indexOf('async function deleteException('));
const preview=readFileSync(join(root,'app/static/schedule-preview.js'),'utf8');
const {Browser}=await import(pathToFileURL(join(process.env.TASTE_SKILL_DIR||join(homedir(),'.codex/skills/design-taste-frontend'),'scripts/lib/cdp.mjs')));
const browser=await Browser.launch({extraArgs:['--disable-background-networking','--disable-sync','--disable-extensions','--disable-component-update','--no-pings']});
const results=[];
const screenshotDir=process.env.V195_SCREENSHOT_DIR||join(tmpdir(),'movingmatch-v195-ui');
try{
  for(const width of [320,390,768,1440]){
    const page=await browser.newPage({width,height:1000,mobile:width<700});
    await page.send('Fetch.enable',{patterns:[{urlPattern:'*',requestStage:'Request'}]});
    browser.on(page.sessionId,'Fetch.requestPaused',e=>{void page.send('Fetch.failRequest',{requestId:e.requestId,errorReason:'BlockedByClient'}).catch(()=>{});});
    await page.eval(({section,css,source,preview})=>{
      document.head.innerHTML='<meta name="viewport" content="width=device-width, initial-scale=1">';
      const style=document.createElement('style');style.textContent=css;document.head.append(style);
      document.body.innerHTML='<main class="wrap">'+section+'</main>';
      window.me={role:'ADMIN'};window.todayStr='2030-01-04';window.alert=()=>{};
      window.requests=[];
      window.api=async(path,options)=>{
        const body=JSON.parse(options.body);requests.push(body);
        return {days:1,plan_token:'synthetic-preview-token',has_conflicts:true,plan:[{
          branch_code:'SYNTHETIC',branch_name:'合成分館',sources:[{date:'2030-01-04',qty:5}],
          target_date:body.manual_target_date||'2030-01-11',existing_qty:8,total:13}]};
      };
      (0,eval)(preview+'\n'+source);
      window.loadSchedule=window.loadAll=window.loadNextDayDocs=async()=>{};
      exStart.value=exEnd.value='2030-01-04';exMonth.value='2030-01';
      scheduleTypeChanged();
    },{section,css,source,preview});
    const controls=await page.eval(()=>{
      const visible=el=>getComputedStyle(el).display!=='none';
      const auto=visible(exPostponeModeWrap)&&!visible(exManualDateWrap)&&exManualDate.disabled;
      exPostponeMode.value='manual';scheduleTypeChanged();
      const manual=visible(exManualDateWrap)&&!exManualDate.disabled&&exManualDate.required&&exManualDate.min==='2030-01-07';
      exType.value='STOP';scheduleTypeChanged();
      const hidden=!visible(exPostponeModeWrap)&&!visible(exManualDateWrap)&&exManualDate.disabled;
      exType.value='TYPHOON';scheduleTypeChanged();exManualDate.value='2030-01-07';
      return {auto,manual,hidden,fits:document.documentElement.scrollWidth<=innerWidth,
              dateTouch:exManualDate.getBoundingClientRect().height>=44};
    });
    assert.ok(Object.values(controls).every(Boolean),JSON.stringify({width,controls}));
    await page.eval(()=>{window.saving=saveExceptionRange()});
    const modal=await page.eval(()=>{
      const d=document.querySelector('dialog');const buttons=d.querySelectorAll('button');
      return {open:d.open,disabled:buttons[0].disabled,details:d.textContent.includes('2030-01-04 5件')&&d.textContent.includes('2030-01-07')&&d.textContent.includes('13 件'),
              fits:d.getBoundingClientRect().left>=0&&d.getBoundingClientRect().right<=innerWidth};
    });
    assert.ok(Object.values(modal).every(Boolean),JSON.stringify({width,modal}));
    {
      mkdirSync(screenshotDir,{recursive:true});
      const shot=await page.send('Page.captureScreenshot',{format:'png'});
      writeFileSync(join(screenshotDir,`v195-${width}.png`),Buffer.from(shot.data,'base64'));
    }
    await page.eval(()=>{const d=document.querySelector('dialog'),check=d.querySelector('input');check.checked=true;check.dispatchEvent(new Event('change'));d.querySelector('button').click()});
    await page.eval('saving');
    const bodies=await page.eval('requests');
    assert.equal(bodies.length,2);assert.equal(bodies[0].preview,true);
    assert.equal(bodies[1].manual_target_date,'2030-01-07');assert.equal(bodies[1].conflict_policy,'sum');
    assert.equal(bodies[1].plan_token,'synthetic-preview-token');
    // Non-typhoon requests must never accidentally submit the retained date.
    await page.eval(()=>{requests.length=0;exType.value='ADD';scheduleTypeChanged();void saveExceptionRange()});
    await page.eval(()=>document.querySelector('dialog button.btn.alt').click());
    assert.equal(await page.eval("'manual_target_date' in requests[0]"),false);
    results.push({width,controls:'PASS',preview_and_explicit_sum:'PASS',payload_and_cancel:'PASS'});
  }
}finally{await browser.close()}
console.log(JSON.stringify({passed:results.length*3,failed:0,results,screenshotDir,scope:'Offline browser; synthetic API; all network blocked'},null,2));
