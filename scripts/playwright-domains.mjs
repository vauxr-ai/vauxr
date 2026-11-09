import { readdirSync } from 'node:fs';
import { resolve } from 'node:path';

export const domains = {
  agents: ['agent-confirmation', 'agent-default-dedupe', 'agent-dialog', 'agent-editor', 'agent-menu-motion', 'agent-profiles', 'agent-voice', 'agents-priority', 'agents-menu-icons', 'agents-support-refinements', 'agents-third-round', 'registered-agents'],
  management: ['admin-workflows', 'auth-browser', 'auth-combined', 'devices-refinement', 'integration-metadata', 'plugins', 'settings-feedback', 'support'],
  voice: ['chat-only', 'companion', 'realtime-playback', 'speech-layout', 'voice-ui'],
  design: ['design-audit', 'design-triage', 'interface', 'modal-layout', 'redesign'],
};

export function validateDomains(files) {
  const assigned = Object.values(domains).flat().map(name => `${name}.spec.ts`);
  const duplicates = assigned.filter((name, i) => assigned.indexOf(name) !== i);
  const missing = files.filter(name => !assigned.includes(name));
  const stale = assigned.filter(name => !files.includes(name));
  if (duplicates.length || missing.length || stale.length) {
    throw new Error(`Invalid Playwright domains: duplicate=${duplicates}; uncategorized=${missing}; stale=${stale}`);
  }
}

export function domainTestMatch(domain, root = resolve(import.meta.dirname, '../e2e')) {
  const files = readdirSync(root, { recursive: true }).filter(name => !name.split('/').includes('node_modules') && name.endsWith('.spec.ts'));
  validateDomains(files);
  if (!Object.hasOwn(domains, domain)) throw new Error(`Unknown Playwright domain: ${domain}`);
  return domains[domain].map(name => `**/${name}.spec.ts`);
}
