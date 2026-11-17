#!/usr/bin/env node
// Run `npm audit` and fail on advisories at or above a severity, except the
// ones listed in scripts/npm_audit_exceptions.json.
//
// An exception is only for an advisory with no fixed release yet. Each one
// names the advisory, why it does not reach users, and a date after which it
// stops applying, so the gate goes red again and someone looks at it.
//
// Usage (from the package directory): node <repo>/scripts/npm_audit_gate.mjs [--audit-level=high]
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const LEVELS = ['info', 'low', 'moderate', 'high', 'critical'];
const levelArg = process.argv.find(arg => arg.startsWith('--audit-level='));
const minLevel = levelArg ? levelArg.split('=')[1] : 'low';

const here = dirname(fileURLToPath(import.meta.url));
const today = new Date().toISOString().slice(0, 10);
const exceptions = JSON.parse(readFileSync(join(here, 'npm_audit_exceptions.json'), 'utf8'));
const active = new Map();
for (const entry of exceptions) {
  if (entry.expires >= today) active.set(entry.advisory, entry);
  else console.log(`Exception for ${entry.advisory} expired on ${entry.expires}; it no longer applies.`);
}

let raw;
try {
  raw = execFileSync('npm', ['audit', '--ignore-scripts', '--json'], {
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
  });
} catch (error) {
  // npm audit exits non-zero when it finds anything; the JSON is still on stdout.
  raw = error.stdout;
}
const report = JSON.parse(raw);
if (report.error) {
  console.error(`npm audit failed: ${JSON.stringify(report.error)}`);
  process.exit(1);
}

const blocking = new Map();
const excused = new Set();
for (const vulnerability of Object.values(report.vulnerabilities || {})) {
  for (const via of vulnerability.via) {
    if (typeof via !== 'object') continue; // inherited from another package, counted there
    if (LEVELS.indexOf(via.severity) < LEVELS.indexOf(minLevel)) continue;
    const id = (via.url || '').split('/').pop();
    if (active.has(id)) excused.add(id);
    else blocking.set(id || via.title, `${via.severity}: ${via.name} - ${via.title} ${via.url || ''}`);
  }
}

for (const id of excused) {
  const entry = active.get(id);
  console.log(`Allowed until ${entry.expires}: ${id} (${entry.reason})`);
}
if (blocking.size) {
  console.error(`${blocking.size} advisories at or above ${minLevel}:`);
  for (const line of blocking.values()) console.error(`  ${line}`);
  process.exit(1);
}
console.log(`No advisories at or above ${minLevel} outside the exceptions.`);
