import {mkdirSync} from 'node:fs';
import {test,expect,login} from './auth-fixture';
for(const width of [390,1366]) for(const theme of ['light','dark']) test(`Overflow action icons ${width} ${theme}`,async({page,server})=>{
 await page.setViewportSize({width,height:844});await login(page,server);
 await page.getByLabel('Appearance').selectOption(theme);
 const row=page.locator('.agent-list>li').first();
 await row.locator('details.agent-actions summary').click();
 for(const label of [/Voice pipeline for/,/Runtime settings for/]){
  const button=row.getByRole('button',{name:label});await expect(button).toBeVisible();
  const icon=button.locator('svg');await expect(icon).toHaveAttribute('aria-hidden','true');
  const box=(await icon.boundingBox())!;expect(box.width).toBe(20);expect(box.height).toBe(20);
  expect(box.x).toBeGreaterThanOrEqual(0);expect(box.x+box.width).toBeLessThanOrEqual(width);
 }
 mkdirSync('/tmp/icons-screens',{recursive:true});await page.screenshot({path:`/tmp/icons-screens/menu-${width}-${theme}.png`});
 await row.getByRole('button',{name:/Voice pipeline for/}).click();await expect(page.getByRole('dialog',{name:'Voice pipeline',exact:true})).toBeVisible();
 await page.keyboard.press('Escape');await row.locator('details.agent-actions summary').click();
 await row.getByRole('button',{name:/Runtime settings for/}).click();await expect(page.getByRole('dialog',{name:'Agent runtime settings',exact:true})).toBeVisible();
});
