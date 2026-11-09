import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,mkdirSync,copyFileSync,writeFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {spawnSync,execFileSync} from 'node:child_process';
test('finish gate rejects empty/partial evidence and limits attempts',()=>{
  const root=mkdtempSync(join(tmpdir(),'design-gate-test-'));
  try {
    mkdirSync(join(root,'scripts'));copyFileSync(new URL('./design-check.mjs',import.meta.url),join(root,'scripts/design-check.mjs'));
    execFileSync('git',['init','-q'],{cwd:root});
    execFileSync('git',['-c','user.name=Test','-c','user.email=test@example.invalid','commit','--allow-empty','-qm','fixture'],{cwd:root});
    const cli=join(root,'e2e/node_modules/@playwright/test');mkdirSync(cli,{recursive:true});
    const run=()=>spawnSync(process.execPath,[join(root,'scripts/design-check.mjs')],{encoding:'utf8'});
    writeFileSync(join(cli,'cli.js'),'');
    const empty=run(); assert.equal(empty.status,1); assert.match(empty.stderr,/no authenticated rendered evidence/);
    writeFileSync(join(cli,'cli.js'),`const fs=require('fs');fs.writeFileSync(process.env.VAUXR_DESIGN_CHECK+'/partial.json',JSON.stringify([{rule:'overflow',status:'PASS',label:'390/light/Agents'}]));`);
    for(let i=0;i<2;i++){const result=run();assert.equal(result.status,1);assert.match(result.stderr,/FAIL coverage: missing/);assert.match(result.stdout,/FAIL BLOCK DEPLOY/);}
    const exhausted=run();assert.equal(exhausted.status,1);assert.match(exhausted.stderr,/budget exhausted/);assert.doesNotMatch(exhausted.stderr,/FAIL coverage/);
  } finally {rmSync(root,{recursive:true,force:true});}
});
