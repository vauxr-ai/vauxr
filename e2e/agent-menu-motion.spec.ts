import {mkdirSync} from 'node:fs';
import {test, expect, login, seedRealtimeAgent} from './auth-fixture';

for (const width of [390,1366]) for (const theme of ['light','dark']) {
  test(`agent action menu, tooltips and stationary primary ${width} ${theme}`, async ({page,server}) => {
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
    expect(tipBox.y+tipBox.height).toBeLessThanOrEqual(panel.y);
    expect(tipBox.x).toBeGreaterThanOrEqual(0);
    await page.screenshot({path:`${artifacts}/tooltip-${width}-${theme}.png`});
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
    await primary.click();
    const dialog=page.getByRole('dialog',{name:'Add Agent',exact:true});
    await expect(dialog).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
    await expect(primary).toBeFocused();
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
  await expect(page.getByRole('button',{name:'Voice pipeline for Vauxr Support',exact:true})).toBeFocused();
});
