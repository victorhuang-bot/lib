// Run with tests/v20_preview.py. This accepts only the disposable loopback fixture.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright');
const base='http://127.0.0.1:58120';
const out=process.env.V20_BROWSER_OUTPUT||'/tmp/movingmatch-v20-browser-evidence';await fs.mkdir(out,{recursive:true});
const browser=await chromium.launch({executablePath:process.env.CHROME_PATH||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
const errors=[];const checks=[];
try{
  for(const width of [1440,768,390]){
    const page=await browser.newPage({viewport:{width,height:900}});page.on('pageerror',e=>errors.push(e.message));
    await page.addInitScript(()=>sessionStorage.setItem('admin_session','synthetic-v20-admin'));
    await page.goto(base);await page.locator('#ebNav').waitFor({state:'visible'});await page.locator('#ebNav').click();
    await page.getByRole('heading',{name:'空箱盤點與調度',exact:true}).waitFor();
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'Office horizontal overflow '+width);
    await page.screenshot({path:`${out}/office-${width}.png`,fullPage:true});
    await page.getByRole('button',{name:'安排調度',exact:true}).first().click();
    await page.getByRole('button',{name:'增加來源館',exact:true}).click();assert.equal(await page.locator('.eb-source').count(),2);
    await page.getByRole('button',{name:'建議送達日期',exact:true}).click();await page.locator('#ebArrivalDates').filter({hasText:/\d{4}/}).waitFor();
    await page.close();checks.push('Office '+width+'px: layout, multi-source form, date suggestions');
    const branch=await browser.newPage({viewport:{width,height:900}});branch.on('pageerror',e=>errors.push(e.message));
    await branch.goto(base+'/branch/synthetic');
    await branch.evaluate(async()=>{session='synthetic-v20-branch-2';document.querySelector('#verify').classList.add('hidden');document.querySelector('#desktopLogin').classList.add('hidden');document.querySelector('#work').classList.remove('hidden');await load()});
    await branch.getByRole('heading',{name:'空箱盤點與交接',exact:true}).waitFor();
    assert.equal(await branch.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'Branch horizontal overflow '+width);
    await branch.screenshot({path:`${out}/branch-${width}.png`,fullPage:true});
    await branch.getByText('新增盤點',{exact:true}).click();const f=branch.locator('#ebStock');await f.locator('[name=unused]').fill('15');await f.locator('[name=actor]').fill('合成瀏覽器盤點人');
    assert.match(await branch.locator('#ebStockTotal').innerText(),/18/);
    if(width===390){
      await f.getByRole('button',{name:'保存盤點'}).click();await branch.waitForFunction(()=>EB.state?.balance?.unused===15);
      await branch.getByRole('button',{name:'填寫實際交接',exact:true}).click();
      const linked=branch.locator('#ebLinked');await linked.locator('#ebSigner').selectOption({label:'合成測試人員'});
      const canvas=linked.locator('#ebSig'),rect=await canvas.boundingBox();await branch.mouse.move(rect.x+10,rect.y+50);await branch.mouse.down();await branch.mouse.move(rect.x+80,rect.y+20,{steps:6});await branch.mouse.up();
      await linked.getByRole('button',{name:'確認交接',exact:true}).click();await branch.waitForFunction(()=>EB.state?.handoffs?.length===1);
      assert.equal(await branch.evaluate(()=>EB.state.balance.unused),12);
      await branch.screenshot({path:`${out}/branch-confirmed-390.png`,fullPage:true});
      checks.push('Standalone handoff: existing roster, signature, stock counted once, no fake delivery');
    }
    await branch.close();checks.push('Branch '+width+'px: layout, inventory sum, pending handoff');
  }
  assert.deepEqual(errors,[]);
  await fs.writeFile(`${out}/result.json`,JSON.stringify({checks,console_errors:errors,production_connections:0},null,2));
  console.log(JSON.stringify({checks,console_errors:errors,output:out},null,2));
}finally{await browser.close()}
