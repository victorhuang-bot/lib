// Read-only local UI checks. Requires Node 22+ and the reviewed Taste-Skill CDP helper.
// No child_process/execFileSync, backend import, database, account, password or cookie injection.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { createServer } from 'node:http';
import { readFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { resolve, join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { homedir } from 'node:os';

assert.ok(Number(process.versions.node.split('.')[0]) >= 22, 'Node 22+ required');
const root = resolve(import.meta.dirname, '..');
const skill = process.env.TASTE_SKILL_DIR || join(homedir(), '.codex/skills/design-taste-frontend');
const { Browser } = await import(pathToFileURL(join(skill, 'scripts/lib/cdp.mjs')));
const out = join(root, 'docs/ui-audit/evidence');
assert.match(readFileSync(join(root, '.gitignore'), 'utf8'), /^docs\/ui-audit\/evidence\/$/m);
mkdirSync(out, { recursive: true });
const sha = value => createHash('sha256').update(value).digest('hex');
const html = readFileSync(join(root, 'app/static/driver.html'), 'utf8');
const scripts = Array.from(html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi), m => m[1]);
// Pinned fingerprints of the reviewed PR #25 base 05d05ca; no Git/subprocess call needed.
assert.equal(sha(JSON.stringify(scripts)), 'cf702210388ab1a8783ad8a238a6626a591e55193afc257618b555d52f57e77e', 'Driver behavior changed');
assert.equal(sha(readFileSync(join(root, 'app/static/common.js'))), 'a772bcf5efa34ee876fde6e11e65e0da75ce54bbd7b343db76382495c940685b', 'Shared behavior changed');
const allowed = new Map([
  ['/static/driver.html', ['app/static/driver.html', 'text/html; charset=utf-8']],
  ['/static/style.css', ['app/static/style.css', 'text/css']],
  ['/static/driver-ui.css', ['app/static/driver-ui.css', 'text/css']],
  ['/static/common.js', ['app/static/common.js', 'text/javascript']],
  ['/static/moving-match-favicon.png', ['app/static/moving-match-favicon.png', 'image/png']],
]);
const requests = [], blocked = [], results = [];
const server = createServer((req, res) => {
  requests.push({ method: req.method, path: req.url });
  const target = allowed.get(new URL(req.url, 'http://localhost').pathname);
  if (!['GET', 'HEAD'].includes(req.method) || !target) {
    res.writeHead(403); res.end(); return;
  }
  res.writeHead(200, { 'content-type': target[1] });
  res.end(req.method === 'HEAD' ? undefined : readFileSync(join(root, target[0])));
});
await new Promise(r => server.listen(0, '127.0.0.1', r));
const origin = `http://127.0.0.1:${server.address().port}`;
let browser;
try {
  // Browser.launch creates a new throwaway profile, never the user's Chrome profile.
  browser = await Browser.launch({ extraArgs: [
    '--disable-background-networking', '--disable-sync', '--disable-extensions',
    '--disable-component-update', '--no-pings',
  ] });
  for (const [width, height] of [[320, 812], [390, 844], [768, 1024], [1440, 900]]) {
    const page = await browser.newPage({ width, height, mobile: width < 700 });
    await page.send('Fetch.enable', { patterns: [{ urlPattern: '*', requestStage: 'Request' }] });
    browser.on(page.sessionId, 'Fetch.requestPaused', event => {
      let permitted = false;
      try {
        const url = new URL(event.request.url);
        permitted = url.origin === origin && allowed.has(url.pathname)
          && ['GET', 'HEAD'].includes(event.request.method);
      } catch {}
      if (!permitted) blocked.push({ width, method: event.request.method });
      void page.send(permitted ? 'Fetch.continueRequest' : 'Fetch.failRequest', permitted
        ? { requestId: event.requestId }
        : { requestId: event.requestId, errorReason: 'BlockedByClient' }).catch(() => {});
    });
    await page.goto(origin + '/static/driver.html', { settleMs: 100 });
    const metrics = await page.eval(() => {
      const pin = document.getElementById('pin'), button = document.querySelector('#unlock button');
      return {
        width: innerWidth, overflow: document.documentElement.scrollWidth > innerWidth,
        h1: parseFloat(getComputedStyle(document.querySelector('h1')).fontSize),
        buttonHeight: button.getBoundingClientRect().height,
        inputHeight: pin.getBoundingClientRect().height,
        label: pin.labels?.[0]?.textContent,
        cssLoaded: Array.from(document.styleSheets).some(s => s.href?.endsWith('/driver-ui.css')),
      };
    });
    assert.equal(metrics.overflow, false, `Horizontal overflow at ${width}`);
    assert.equal(metrics.cssLoaded, true, 'Pilot CSS must load');
    assert.equal(metrics.h1, 24);
    assert.ok(metrics.buttonHeight >= 44); assert.ok(metrics.inputHeight >= 44);
    assert.equal(metrics.label, '請輸入司機 4 位 PIN');
    await page.eval(() => document.querySelector('label[for="pin"]').click());
    assert.equal(await page.eval(() => document.activeElement.id), 'pin');
    await page.send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Tab', code: 'Tab' });
    await page.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Tab', code: 'Tab' });
    assert.equal(await page.eval(() => document.activeElement.textContent), '進入今日路線');
    // Input remains blank. Screenshots and all raw output stay in the ignored local directory.
    await page.screenshot({ path: join(out, `driver-pilot-${width}.jpg`) });
    await page.eval(() => document.querySelector('#unlock button').click());
    assert.equal(await page.eval(() => document.getElementById('msg').getAttribute('aria-live')), 'polite');
    assert.match(await page.eval(() => document.getElementById('msg').textContent), /此手機尚未啟用/);
    assert.equal(page.consoleErrors.length, 0, JSON.stringify(page.consoleErrors));
    results.push({ ...metrics, keyboard: true, existingErrorPath: true });
    await page.close();
  }
  assert.equal(requests.filter(r => !['GET', 'HEAD'].includes(r.method)).length, 0);
  assert.equal(requests.filter(r => r.path.startsWith('/api/')).length, 0);
  const report = {
    passed: 8, failed: 0, cases: '2 source fingerprints + 4 viewport cases + 2 network guards',
    inlineBehaviorIdentical: true, apiRequests: 0, blockedRequests: blocked, viewports: results,
    limitations: ['Unauthenticated local UI only', 'No real authorization or deliveries', 'No real signature gesture'],
  };
  writeFileSync(join(out, 'driver-pilot-browser.json'), JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify(report, null, 2));
} catch (error) {
  const report = { failed: 1, completedViewports: results, error: error.message };
  writeFileSync(join(out, 'driver-pilot-browser-failure.json'), JSON.stringify(report, null, 2) + '\n');
  console.error(JSON.stringify(report)); process.exitCode = 1;
} finally {
  if (browser) await browser.close();
  await new Promise(r => server.close(r));
}
