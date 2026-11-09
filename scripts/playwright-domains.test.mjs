import { test } from 'node:test';
import assert from 'node:assert/strict';
import { domains, domainTestMatch, validateDomains } from './playwright-domains.mjs';

const files = Object.values(domains).flat().map(name => `${name}.spec.ts`);
test('every domain matches the current spec inventory', () => {
  for (const name of Object.keys(domains)) assert.equal(domainTestMatch(name).length, domains[name].length);
});
test('new or renamed specs cannot silently leave CI coverage', () => {
  assert.throws(() => validateDomains([...files, 'new.spec.ts']), /uncategorized=new.spec.ts/);
  assert.throws(() => validateDomains(files.slice(1)), /stale=agent-confirmation.spec.ts/);
  assert.throws(() => domainTestMatch('typo'), /Unknown Playwright domain/);
});
