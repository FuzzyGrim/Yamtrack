#!/usr/bin/env node
// npm audit with dated exceptions, for the lint workflow's two npm audits.
//
// Usage (from the package directory): node ../scripts/npm_audit_gate.mjs [--audit-level=high]
// Fails on any advisory at or above the level unless it is listed in
// .github/npm-audit-exceptions.json with a reason and an expiry date that has
// not passed. An expired exception fails the build, so it gets looked at again.
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const LEVELS = ['info', 'low', 'moderate', 'high', 'critical'];

function advisoryId(via) {
  const match = String(via.url || '').match(/GHSA-[\w-]+/);
  return match ? match[0] : String(via.source || '');
}

// Pure decision: which advisories block, which are excused, which exceptions expired.
export function auditGate(report, exceptions, { level = 'low', today = new Date().toISOString().slice(0, 10) } = {}) {
  const threshold = LEVELS.indexOf(level);
  const active = new Map();
  const expired = [];
  for (const entry of exceptions || []) {
    if (!entry.advisory || !entry.expires || !entry.reason) continue;
    if (entry.expires < today) expired.push(entry);
    else active.set(entry.advisory, entry);
  }
  const blocking = new Map();
  const excused = new Map();
  for (const [name, vuln] of Object.entries(report?.vulnerabilities || {})) {
    for (const via of vuln.via || []) {
      if (typeof via !== 'object' || LEVELS.indexOf(via.severity) < threshold) continue;
      const id = advisoryId(via);
      const target = active.has(id) ? excused : blocking;
      target.set(`${id} ${name}`, { id, name, severity: via.severity, title: via.title });
    }
  }
  return { blocking: [...blocking.values()], excused: [...excused.values()], expired };
}

function main() {
  const level = (process.argv.find(arg => arg.startsWith('--audit-level=')) || '--audit-level=low').split('=')[1];
  const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
  const { exceptions } = JSON.parse(readFileSync(resolve(root, '.github/npm-audit-exceptions.json'), 'utf8'));
  let output;
  try {
    output = execFileSync('npm', ['audit', '--ignore-scripts', '--json'], { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
  } catch (error) {
    // npm audit exits non-zero when it finds anything; the JSON is still on stdout.
    output = error.stdout;
    if (!output) throw error;
  }
  const result = auditGate(JSON.parse(output), exceptions, { level });
  result.excused.forEach(item => console.log(`allowed until its listed expiry: ${item.id} (${item.name}, ${item.severity}) ${item.title}`));
  result.expired.forEach(entry => console.log(`EXPIRED exception: ${entry.advisory} (${entry.package}) expired ${entry.expires}; fix or renew it in .github/npm-audit-exceptions.json`));
  result.blocking.forEach(item => console.log(`BLOCKING: ${item.id} (${item.name}, ${item.severity}) ${item.title}`));
  if (result.blocking.length || result.expired.length) process.exit(1);
  console.log(`npm audit passed at level ${level}${result.excused.length ? ' with listed exceptions' : ''}.`);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main();
