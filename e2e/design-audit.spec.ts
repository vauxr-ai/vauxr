import { test, expect } from '@playwright/test';
import { auditPage } from '../scripts/design-audit.mjs';
const tokens = {colors:{ink:'#000000',paper:'#ffffff'}};
test('design auditor rejects deliberate violations and accepts their repair', async ({page}) => {
  await page.setViewportSize({width:390,height:844});
  await page.setContent(`<style>
    body {margin:0;background:white;color:black} button {background:white;color:black;border:0}
    button:hover {transform:translateY(-4px)}
    .low {color:#aaaaaa} .unmapped {background:rgb(255,0,0)}
    dialog {display:block;position:static;width:500px;height:50px;overflow:auto;margin:0;padding:0;border:0}
    .content {height:200px} @keyframes drift {to{transform:translateX(10px)}}
    .motion {animation:drift 1s infinite}
    </style><header><h1>Brand</h1></header><main><h1>Agents</h1><h1>Agents again</h1>
    <button>Action</button><p class="low">Weak contrast</p><p class="unmapped">Color</p>
    <p>Loading…</p><dialog open><div class="content">Content</div></dialog><div class="motion">Motion</div></main>`);
  const bad = await auditPage(page,tokens,'deliberate-bad');
  for(const rule of ['overflow','token-colors','contrast','hover-motion','loading-text','page-headings','reduced-motion']) {
    expect(bad.some(row => row.rule===rule && row.status==='FAIL'),rule).toBe(true);
  }
  await page.setContent(`<style>body{margin:0;background:white;color:black;background-image:linear-gradient(rgba(0,0,0,0),rgba(0,0,0,0))}button{background:white;color:black;border:0}</style><header><h1>Brand</h1></header><main><h1>Agents</h1><button>Action</button><p>Ready</p></main>`);
  const good = await auditPage(page,tokens,'repaired');
  expect(good.filter(row=>row.status==='FAIL')).toEqual([]);
  expect(good.filter(row=>row.rule==='page-headings')[0].status).toBe('PASS');
});

test('hover checks include card descendants, not only button boxes', async ({page}) => {
  await page.setContent(`<style>body{background:white;color:black}.card:hover svg{transform:translateY(-3px)}</style><main><h1>Agents</h1><div class="card" style="width:100px;height:50px"><svg width="40" height="40"><rect width="40" height="40"/></svg></div></main>`);
  const rows=await auditPage(page,tokens,'card-descendant');
  expect(rows.find(row=>row.rule==='hover-motion')?.status).toBe('FAIL');
});


test('token check validates settled transition endpoints without allowing raw colors',async({page})=>{
  await page.setContent(`<style>body{background:white;color:black}.sample{color:black;transition:color .15s}.valid{color:white}.invalid{color:red}</style><main><h1>Agents</h1><p class="sample">Status</p></main>`);
  const sample=page.locator('.sample');
  await sample.evaluate(el=>{getComputedStyle(el).color;el.classList.add('valid');});
  let rows=await auditPage(page,tokens,'valid-transition');
  expect(rows.find(r=>r.rule==='token-colors')?.status).toBe('PASS');
  await sample.evaluate(el=>el.classList.replace('valid','invalid'));
  rows=await auditPage(page,tokens,'invalid-transition');
  expect(rows.find(r=>r.rule==='token-colors')?.status).toBe('FAIL');
  expect(rows.find(r=>r.rule==='token-colors')?.evidence.some(e=>e.selector.includes('.sample')&&e.color==='rgb(255, 0, 0)')).toBe(true);
});


test('SVG container default fill is not paint, but a raw filled shape still fails',async({page})=>{
 const paper={colors:{paper:'#ffffff'}};
 await page.setContent(`<style>body{background:white;color:white}</style><svg width="40" height="40"><path d="M1 1L30 30" fill="none" stroke="white"/></svg>`);
 let rows=await auditPage(page,paper,'unpainted-container');
 expect(rows.find(r=>r.rule==='token-colors')?.status).toBe('PASS');
 await page.locator('svg').evaluate(el=>{el.innerHTML='<rect width="30" height="30" fill="red"/>';});
 rows=await auditPage(page,paper,'painted-shape');
 expect(rows.find(r=>r.rule==='token-colors')?.status).toBe('FAIL');
});

test('entrance opacity cannot hide an overflowing native modal from the audit',async({page})=>{
 await page.setViewportSize({width:390,height:844});
 await page.setContent(`<style>body{background:white;color:black}dialog{width:500px;max-width:none;animation:enter .15s}@keyframes enter{from{opacity:0}to{opacity:1}}</style><dialog><p>Content</p></dialog>`);
 await page.locator('dialog').evaluate(el=>(el as HTMLDialogElement).showModal());
 const rows=await auditPage(page,tokens,'entering-dialog');
 expect(rows.filter(r=>r.rule==='overflow')[1].status).toBe('FAIL');
 expect(rows.filter(r=>r.rule==='overflow')[1].detail).toContain('1 visible dialogs');
});


test('vertical management scrolling is allowed, including behind a modal', async ({page}) => {
  await page.setViewportSize({width:390,height:844});
  await page.setContent(`<style>body{margin:0;background:white;color:black}main{height:200px;overflow:auto}article{height:1200px}dialog{width:200px;border:0;background:white;color:black}</style><main><h1>Agents</h1><article>Long list</article></main><dialog><p>Modal content</p></dialog>`);
  let rows=await auditPage(page,tokens,'vertical-page');
  expect(rows.filter(r=>r.rule==='overflow').every(r=>r.status==='PASS')).toBe(true);
  await page.locator('dialog').evaluate(el=>(el as HTMLDialogElement).showModal());
  rows=await auditPage(page,tokens,'vertical-page-under-modal');
  expect(rows.filter(r=>r.rule==='overflow').every(r=>r.status==='PASS')).toBe(true);
  await page.locator('dialog').evaluate(el=>{el.style.height='50px';el.style.overflow='auto';el.firstElementChild!.setAttribute('style','height:300px');});
  rows=await auditPage(page,tokens,'scrolling-modal');
  expect(rows.filter(r=>r.rule==='overflow')[1].status).toBe('FAIL');
  await page.locator('dialog').evaluate(el=>el.style.overflow='hidden');
  rows=await auditPage(page,tokens,'clipped-modal');
  expect(rows.filter(r=>r.rule==='overflow')[1].status).toBe('FAIL');
});

test('horizontal page overflow remains forbidden even inside a viewport-sized scroller', async ({page}) => {
  await page.setViewportSize({width:390,height:844});
  await page.setContent(`<style>body{margin:0;background:white;color:black}main{width:300px;overflow:auto}article{width:600px}</style><main><h1>Agents</h1><article>Wide list</article></main>`);
  const rows=await auditPage(page,tokens,'horizontal-page');
  expect(rows.filter(r=>r.rule==='overflow')[0].status).toBe('FAIL');
  expect(rows.filter(r=>r.rule==='overflow')[0].detail).toContain('1 horizontal page scrolling containers');
});

test('pipeline ellipsis requires the complete name in its title', async ({page}) => {
  await page.setContent(`<style>body{margin:0;background:white;color:black}dialog{display:block;position:static;width:300px;height:140px} .pipeline-agent-name{display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden;width:60px;overflow-wrap:anywhere;line-height:16px}</style><main><h1>Agents</h1><dialog open><span class="pipeline-agent-name">${'P'.repeat(200)}</span></dialog></main>`);
  const bad=await auditPage(page,tokens,'missing-full-name');
  expect(bad.some(row=>row.rule==='overflow' && row.status==='FAIL')).toBe(true);
  await page.locator('.pipeline-agent-name').evaluate(el=>el.setAttribute('title',el.textContent!));
  const good=await auditPage(page,tokens,'accessible-ellipsis');
  expect(good.filter(row=>row.rule==='overflow').every(row=>row.status==='PASS')).toBe(true);
});
