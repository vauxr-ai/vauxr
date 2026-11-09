import {mkdirSync} from 'node:fs';
import {test, expect, login, seedRealtimeAgent} from './auth-fixture';

for (const width of [390,768,1366]) for (const theme of ['light','dark']) {
  test(`agent action menu, tooltips and stationary primary ${width} ${theme}`, async ({page,server}) => {
    page.setDefaultTimeout(8000);
    await page.setViewportSize({width,height:width===390?844:768});
    // Exercise both disabled deletion reasons against real persisted agent records.
    if (theme==='dark') await seedRealtimeAgent(server);
    await login(page,server);
    await page.getByLabel('Appearance').selectOption(theme);
    const primary=page.getByRole('button',{name:'+ Add Agent',exact:true});
    await page.mouse.move(0,0);
    const resting=(await primary.boundingBox())!;
    await primary.hover();
    await page.waitForTimeout(240);
    expect(await primary.boundingBox()).toEqual(resting);
    await page.mouse.down();
    await page.waitForTimeout(200);
    expect(await primary.boundingBox()).toEqual(resting);
    expect(await primary.evaluate(el=>getComputedStyle(el).transform)).toBe('none');
    // Release outside the button rather than creating an agent.
    await page.mouse.move(0,0);await page.mouse.up();
    const row=page.locator('.agent-list > li').filter({has:page.getByRole('heading',{name:'Vauxr Support',exact:true,level:3})});
    const summary=row.getByLabel('Actions for Vauxr Support',{exact:true});
    await summary.focus();await page.keyboard.press('Enter');
    const menu=row.locator('.agent-actions-list');
    await expect(menu).toBeVisible();
    await expect(menu).toHaveJSProperty('inert',false);
    for (const name of ['Voice pipeline for Vauxr Support','Runtime settings for Vauxr Support','Delete agent']) {
      const item=menu.getByRole('button',{name,exact:true});
      expect((await item.boundingBox())!.height).toBeGreaterThanOrEqual(44);
      await expect(item.locator('svg')).toHaveCount(1);
    }
    const panel=(await menu.boundingBox())!;
    const trigger=(await summary.boundingBox())!;
    expect(panel.y).toBeGreaterThanOrEqual(trigger.y+trigger.height+5);
    expect(panel.y+panel.height).toBeLessThanOrEqual(page.viewportSize()!.height-16);
    const defaultRow=page.locator('.agent-list > li').filter({has:page.locator('.pill').getByText('Default',{exact:true})});
    await expect(defaultRow).toHaveCount(1);
    await expect(defaultRow.getByRole('button',{name:/^(Default|Set as default)$/})).toHaveCount(0);
    expect(panel.width).toBeGreaterThanOrEqual(240);
    expect(panel.x).toBeGreaterThanOrEqual(0);
    expect(panel.y).toBeGreaterThanOrEqual(0);
    expect(panel.x+panel.width).toBeLessThanOrEqual(width);
    const artifacts='/tmp/vauxr-menu-motion-screenshots';mkdirSync(artifacts,{recursive:true});
    await expect(row.locator('details')).toHaveCSS('opacity','1');
    await expect.poll(()=>row.locator('details').evaluate(el=>getComputedStyle(el,'::details-content').opacity)).toBe('1');
    await page.screenshot({path:`${artifacts}/menu-${width}-${theme}.png`});
    const tooltip=menu.getByRole('tooltip');
    await expect(tooltip).toBeHidden();
    await expect(menu.locator('small')).toHaveCount(0);
    await page.keyboard.press('Tab');
    await expect(menu.getByRole('button',{name:'Voice pipeline for Vauxr Support',exact:true})).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(menu.getByRole('button',{name:'Runtime settings for Vauxr Support',exact:true})).toBeFocused();
    await page.keyboard.press('Tab');
    const unavailable=menu.getByLabel('Delete Vauxr Support unavailable',{exact:true});
    await expect(unavailable).toBeFocused();
    await expect(tooltip).toBeVisible();
    await expect(tooltip).toHaveText(theme==='dark'?'Bundled agent cannot be deleted':'Set another agent as default first');
    await expect(menu.getByRole('button',{name:'Delete agent',exact:true})).toBeDisabled();
    await expect(tooltip).toHaveCSS('opacity','1');
    const tipBox=(await tooltip.boundingBox())!;
    for (const pill of await row.locator(".pill").all()) {
      const pillBox=(await pill.boundingBox())!;
      expect(tipBox.x >= pillBox.x+pillBox.width || tipBox.x+tipBox.width <= pillBox.x || tipBox.y >= pillBox.y+pillBox.height || tipBox.y+tipBox.height <= pillBox.y).toBe(true);
    }
    expect(tipBox.y).toBeGreaterThanOrEqual(0);
    expect(tipBox.y+tipBox.height).toBeLessThanOrEqual(width===390?844:768);
    // A rectangle may be in bounds yet hidden behind sticky navigation.
    expect(await tooltip.evaluate(el=>{
      const previous=el.style.pointerEvents;el.style.pointerEvents='auto';
      try {const r=el.getBoundingClientRect(); const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2); return el.contains(hit) ? true : {hit:hit?.tagName,classes:hit?.className,rect:r.toJSON()};}
      finally {el.style.pointerEvents=previous;}
    })).toBe(true);
    expect(tipBox.x).toBeGreaterThanOrEqual(0);
    const addBox=(await primary.boundingBox())!;
    expect(tipBox.x >= addBox.x+addBox.width || tipBox.x+tipBox.width <= addBox.x || tipBox.y >= addBox.y+addBox.height || tipBox.y+tipBox.height <= addBox.y).toBe(true);
    await page.screenshot({path:`${artifacts}/tooltip-${width}-${theme}.png`});
    const disabledBox=(await unavailable.boundingBox())!;
    const cursor={x:disabledBox.x+20,y:disabledBox.y+20};
    await page.mouse.move(cursor.x,cursor.y);
    let cursorBox=(await tooltip.boundingBox())!;
    const expectedX=cursor.x+12+cursorBox.width <= width-8 ? cursor.x+12 : Math.max(8,cursor.x-cursorBox.width-12);
    expect(Math.abs(cursorBox.x-expectedX)).toBeLessThanOrEqual(1);
    expect(Math.abs(cursorBox.y-(cursor.y-cursorBox.height/2))).toBeLessThanOrEqual(1);
    await page.mouse.move(cursor.x+10,cursor.y+3);
    const movedBox=(await tooltip.boundingBox())!;
    expect(movedBox.y-cursorBox.y).toBeCloseTo(3,0);
    await page.screenshot({path:`${artifacts}/tooltip-cursor-${width}-${theme}.png`});
    await unavailable.dispatchEvent('pointermove',{pointerType:'mouse',clientX:width-2,clientY:page.viewportSize()!.height-2});
    cursorBox=(await tooltip.boundingBox())!;
    expect(cursorBox.x).toBeGreaterThanOrEqual(8);
    expect(cursorBox.x+cursorBox.width).toBeLessThanOrEqual(width-14);
    expect(cursorBox.y+cursorBox.height).toBeLessThanOrEqual(page.viewportSize()!.height-7);
    await unavailable.dispatchEvent('pointerdown',{pointerType:'touch',clientX:0,clientY:0});
    const touchBox=(await tooltip.boundingBox())!;
    expect(touchBox.x).toBeGreaterThanOrEqual(8);
    expect(touchBox.y).toBeGreaterThanOrEqual(8);
    expect(touchBox.y+touchBox.height).toBeLessThanOrEqual(page.viewportSize()!.height-7);

    await page.keyboard.press('Escape');
    await expect(summary).toBeFocused();
    await expect(menu).toHaveJSProperty('inert',true);
    await page.keyboard.press('Tab');
    expect(await menu.evaluate(el=>el.contains(document.activeElement))).toBe(false);
    await expect(row.locator('details')).not.toHaveAttribute('open','');
    await expect(menu).toBeHidden();
    await summary.click();
    await unavailable.click(); // Touch/pointer users can focus the disabled action's explanation.
    await expect(tooltip).toBeVisible();
    await page.getByRole('heading',{name:'Agents',exact:true,level:1}).click();
    await expect(menu).toBeHidden();
    // Put the same trigger near the viewport bottom to force the collision branch.
    await row.evaluate(el => { el.style.position='fixed'; el.style.bottom='24px'; el.style.right='24px'; el.style.zIndex='30'; });
    await summary.click();
    await expect(row.locator('details')).toHaveAttribute('data-placement','above');
    await expect.poll(async () => {
      const flipped=(await menu.boundingBox())!;
      const lowTrigger=(await summary.boundingBox())!;
      return flipped.y >= 16 && flipped.y+flipped.height <= lowTrigger.y-5;
    }).toBe(true);
    await page.keyboard.press('Escape');
    await row.evaluate(el => {el.style.position='';el.style.bottom='';el.style.right='';el.style.zIndex='';});
    await primary.click();
    const dialog=page.getByRole('dialog',{name:'Add Agent',exact:true});
    await expect(dialog).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
    await expect(primary).toBeFocused();
    await summary.click();
    await menu.getByRole('button',{name:'Runtime settings for Vauxr Support',exact:true}).click();
    const runtime=page.getByRole('dialog',{name:'Agent runtime settings',exact:true});
    await expect(runtime).toBeVisible();
    await expect(row.locator('details')).not.toHaveAttribute('open','');
    await page.keyboard.press('Escape');
    await expect(summary).toBeFocused();
    await row.getByRole('button',{name:'Edit agent name',exact:true}).click();
    await expect(page.getByLabel('Agent name',{exact:true})).toBeVisible();
    await page.getByRole('button',{name:'Cancel name edit',exact:true}).click();
  });
}

test('reduced motion disables surface, tooltip and control effects',async({page,server})=>{
  await page.emulateMedia({reducedMotion:'reduce'});
  await login(page,server);
  await page.getByLabel('Actions for Vauxr Support',{exact:true}).click();
  const menu=page.locator('.agent-actions[open]');
  expect(await menu.evaluate(el=>getComputedStyle(el,'::details-content').transitionDuration)).toBe('0s');
  await menu.getByRole('button',{name:'Voice pipeline for Vauxr Support',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'Voice pipeline',exact:true});
  await expect(dialog.locator('.pipeline-graph')).toBeVisible();
  expect(await dialog.evaluate(el=>getComputedStyle(el).animationName)).toBe('none');
  expect(await dialog.evaluate(el=>getComputedStyle(el,'::backdrop').animationName)).toBe('none');
  const primary=dialog.getByRole('button',{name:'Save pipeline configuration',exact:true});
  expect(await primary.evaluate(el=>getComputedStyle(el).transitionDuration)).toBe('0s');
  await page.keyboard.press('Escape');
  await expect(page.getByLabel('Actions for Vauxr Support',{exact:true})).toBeFocused();
});
