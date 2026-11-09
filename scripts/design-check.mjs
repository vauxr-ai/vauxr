#!/usr/bin/env node
// Reuse owner-authenticated disposable browser flows; never accept a live URL.
import { mkdtempSync, readFileSync, readdirSync, rmSync, mkdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { spawnSync, execFileSync } from 'node:child_process';
const root = resolve(import.meta.dirname, '..');
const round = Number(process.argv.find(a => a.startsWith('--round='))?.split('=')[1] ?? 1);
if (process.argv.slice(2).some(a => !/^--round=[1-3]$/.test(a)) || ![1,2,3].includes(round)) {
  console.error('Usage: node scripts/design-check.mjs [--round=1|2|3] (at most three fix rounds)'); process.exit(2);
}
const head = execFileSync('git',['rev-parse','HEAD'],{cwd:root,encoding:'utf8'}).trim();
const statePath = resolve(root,execFileSync('git',['rev-parse','--git-path','design-check-attempts.json'],{cwd:root,encoding:'utf8'}).trim());
let prior; try {prior=JSON.parse(readFileSync(statePath,'utf8'));} catch {}
const attempt = prior?.head===head ? prior.attempt+1 : round;
if(attempt>3 || (process.argv.some(a=>a.startsWith('--round=')) && round!==attempt)) {
  console.error('FAIL BLOCK DEPLOY: three-round budget exhausted or round out of sequence. Report unresolved findings; start a new reviewed commit before another cycle.'); process.exit(1);
}
writeFileSync(statePath,JSON.stringify({head,attempt}));
const dir = mkdtempSync(join(tmpdir(), 'vauxr-design-check-'));
let failed = false;
try {
  const result = spawnSync(process.execPath, [join(root, 'e2e/node_modules/@playwright/test/cli.js'), 'test', 'design-audit.spec.ts', 'modal-layout.spec.ts', '--retries=0', '--reporter=line'], {
    cwd: join(root, 'e2e'), stdio: 'inherit',
    env: { ...process.env, VAUXR_DESIGN_CHECK: dir, PLAYWRIGHT_NO_COPY_PROMPT: '1' },
  });
  failed = result.status !== 0;
  const rows = readdirSync(dir).filter(f => f.endsWith('.json')).flatMap(f => JSON.parse(readFileSync(join(dir,f),'utf8')));
  for(const width of [390,768,1366]) for(const theme of ['light','dark']) {
    for(const state of ['Agents','Devices','Settings','Add Agent','Edit agent avatar','Voice pipeline','Voice pipeline-loading','Voice pipeline-error','Agent runtime settings','Delete agent','Device Diagnostics',...(width===390?['Voice companion']:[])]) {
      const matched=rows.some(r=>r.label===`${width}/${theme}/${state}` || (state==='Voice companion' && r.label?.startsWith(`${width}/${theme}/`) && /companion/i.test(r.label)));
      if(!matched){ console.error(`FAIL coverage: missing ${width}/${theme}/${state}`); failed=true; }
    }
  }
  for (const rule of ['overflow','token-colors','contrast','hover-motion','loading-text','page-headings','reduced-motion']) {
    const findings = rows.filter(r => r.rule === rule);
    const status = findings.some(r => r.status === 'FAIL') ? 'FAIL' : !findings.length || findings.some(r => r.status === 'WARN') ? 'WARN' : 'PASS';
    console.log(`${status} ${rule}: ${findings.length} rendered states checked`);
    for(const item of findings.filter(r => r.status !== 'PASS').slice(0,8)) console.log(`  ${item.label}: ${item.detail}`);
    if(status === 'FAIL' || !findings.length) failed = true;
  }
  for(const rule of ['hover-coverage','audit-coverage']) { const warnings=rows.filter(r=>r.rule===rule && r.status==='WARN'); if(warnings.length) console.log(`WARN ${rule}: ${warnings[0].detail}`); }
  mkdirSync(join(root,'e2e/test-results'),{recursive:true});
  writeFileSync(join(root,'e2e/test-results/design-check.json'),JSON.stringify({round:attempt, harnessExit:result.status, findings:rows},null,2));
  if (!rows.length) { console.error('FAIL harness: no authenticated rendered evidence'); failed = true; }
  console.log(`${failed ? 'FAIL BLOCK DEPLOY' : 'PASS FINISH GATE'} — round ${attempt}/3${failed && attempt === 3 ? '; stop and report unresolved findings, do not deploy' : ''}`);
} finally { rmSync(dir, {recursive:true, force:true}); }
process.exitCode = failed ? 1 : 0;
