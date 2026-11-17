import React, { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { mountRoot } from '../settings/root';
import { Button } from './Button';
import { Dialog } from './controls/Dialog';
import { SelectField } from './SelectField';
import { TextField } from './TextField';
import { BulkActionDialog, BulkActionRow, BulkFieldRow, BulkJobCard, BulkJobMessage, BulkJobProblems, BulkJobProgress, BulkJobSummary, BulkNote } from './BulkActions';
import { BulkJobDock } from './BulkJobDock';
import { TagBulkDialog } from './TagManagement';
import { rootStyle } from './rootStyle.web';

// Mounted once in the base layout. The toolbar (mountBulkToolbar.jsx) opens a
// short dialog with `bulk-actions-open`. Anything slow then runs as a "job" in a
// docked card, so the page stays usable. Add Plays jobs are remembered in
// localStorage and pick up again after a reload or a visit to another page.
const SCOPES = [
  { value: 'aired', label: 'All aired episodes' },
  { value: 'watched', label: 'Episodes my status says I watched' },
  { value: 'seasons', label: 'A range of seasons' },
];
const EXISTING = [
  { value: 'skip', label: 'Skip episodes that already have a play' },
  { value: 'add', label: 'Add another play anyway' },
];
const DATES = [
  { value: 'air_date', label: 'Episode air dates (estimate)' },
  { value: 'one_date', label: 'One date for every play' },
  { value: 'range', label: 'Spread across a date range' },
];
const SCOPE_HELP = {
  aired: 'Every episode that has aired. Specials are left out.',
  watched: 'Completed shows get every aired episode. Other shows get only their completed seasons.',
  seasons: 'Aired episodes in these seasons. Season 0 is specials.',
};
const DATE_HELP = {
  air_date: 'Each play is dated when the episode first aired. This is an estimate of when you watched, not a real watch time.',
  one_date: 'Every play lands on one day. Many plays on one day can distort activity, streaks and Statistics.',
  range: 'Plays are spread evenly in order across the dates. Still an estimate.',
};
const STORE = 'floppy-bulk-jobs';

function readPalette() {
  const css = rootStyle();
  const token = (name, fallback) => (css.getPropertyValue(name) || '').trim() || fallback;
  const accent = token('--color-accent', '#6366f1');
  return {
    background: token('--color-page-bg', '#18181b'),
    surface: token('--color-surface', '#27272a'),
    surfaceStrong: token('--color-surface-strong', '#3f3f46'),
    surfaceMuted: token('--color-surface-muted', '#52525b'),
    surfaceBorder: token('--color-surface-border', '#3f3f46'),
    surfaceHover: token('--color-surface-hover', '#52525b'),
    border: token('--color-surface-border', '#3f3f46'),
    link: token('--color-link', '#818cf8'),
    text: token('--color-text', '#fafafa'),
    textSecondary: token('--color-text-secondary', '#d4d4d8'),
    textMuted: token('--color-text-muted', '#a1a1aa'),
    accent,
    accentStrong: accent,
    success: token('--color-success-text', '#10b981'),
    warning: token('--color-warning-text', '#f59e0b'),
    danger: token('--color-error-text', '#ef4444'),
    overlay: 'rgba(0, 0, 0, 0.6)',
    radius: 10,
    focusStyle: {},
  };
}

const plural = (n, word) => `${n.toLocaleString()} ${word}${n === 1 ? '' : 's'}`;
const notify = (message, type = 'success') => {
  if (message && typeof window.showTrackToast === 'function') window.showTrackToast({ message, type });
};
const finished = () => window.dispatchEvent(new CustomEvent('bulk-job-finished'));

// ---- job store -------------------------------------------------------------
let jobs = [];
const listeners = new Set();
const remembered = job => job.kind === 'plays' && ['previewing', 'review', 'running'].includes(job.phase);
function setJobs(next) {
  jobs = next;
  try { localStorage.setItem(STORE, JSON.stringify(jobs.filter(remembered))); } catch { /* storage is optional */ }
  listeners.forEach(listener => listener());
}
const patchJob = (id, patch) => setJobs(jobs.map(job => job.id === id ? { ...job, ...patch } : job));
const dropJob = id => setJobs(jobs.filter(job => job.id !== id));
const useJobs = () => useSyncExternalStore(
  listener => { listeners.add(listener); return () => listeners.delete(listener); }, () => jobs);

async function post(url, csrf, body) {
  const response = await fetch(url, { method: 'POST', headers: { 'X-CSRFToken': csrf }, body });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.success === false) throw new Error(data.error || 'Something went wrong.');
  return data;
}

function playsBody(job, dryRun) {
  const data = new URLSearchParams({ ...job.form, dry_run: dryRun ? '1' : '0' });
  // One field keeps select-all sizes above Django's field-count limit postable.
  data.set('item_ids', job.itemIds.join(','));
  return data;
}

async function startPlays(config, job, dryRun) {
  try {
    const data = await post(config.playsUrl, config.csrfToken, playsBody(job, dryRun));
    patchJob(job.id, { operation: data.operation_id, phase: dryRun ? 'previewing' : 'running', error: '', progress: null });
  } catch (error) {
    patchJob(job.id, { phase: 'error', error: error.message });
  }
}

async function runStatus(config, job) {
  try {
    const body = new URLSearchParams({ status: job.status });
    body.set('item_ids', job.itemIds.join(','));
    const data = await post(config.statusUrl, config.csrfToken, body);
    patchJob(job.id, { phase: 'done', message: data.message || 'Status updated.' });
    notify(data.message || 'Status updated.');
    finished();
  } catch (error) {
    patchJob(job.id, { phase: 'error', error: error.message });
  }
}

function resultNotes(totals) {
  return [
    totals.existing && `${plural(totals.existing, 'episode')} skipped (already have a play)`,
    totals.unaired && `${plural(totals.unaired, 'episode')} skipped (not aired yet)`,
    totals.no_date && `${plural(totals.no_date, 'episode')} skipped (no air date)`,
  ].filter(Boolean);
}

// ---- docked job card -------------------------------------------------------
function JobCard({ config, job, palette }) {
  const polling = job.kind === 'plays' && ['previewing', 'running'].includes(job.phase) && job.operation;
  useEffect(() => {
    if (!polling) return undefined;
    let stopped = false;
    let timer;
    const url = config.playsStatusUrl.replace('OPERATION', job.operation);
    const tick = async () => {
      try {
        const response = await fetch(url);
        const data = await response.json();
        if (stopped) return;
        if (!response.ok) throw new Error(data.error || 'Progress is unavailable.');
        if (data.state === 'running') { patchJob(job.id, { progress: data }); timer = setTimeout(tick, 1500); return; }
        if (data.state === 'error') { patchJob(job.id, { phase: 'error', error: 'The job stopped unexpectedly.' }); return; }
        if (job.phase === 'previewing') { patchJob(job.id, { phase: 'review', result: data, progress: null }); return; }
        patchJob(job.id, { phase: 'done', result: data, progress: null });
        notify(`${plural(data.totals.plays, 'play')} added.`);
        finished();
      } catch (error) {
        if (!stopped) patchJob(job.id, { phase: 'error', error: error.message });
      }
    };
    timer = setTimeout(tick, 400);
    return () => { stopped = true; clearTimeout(timer); };
  }, [polling, job.operation, job.phase]);

  const title = job.kind === 'status' ? 'Update Status' : 'Add Plays to History';
  const result = job.result;
  const progress = job.progress || { done: 0, total: job.itemIds.length };
  const review = job.phase === 'review' && result;
  return <BulkJobCard title={title} palette={palette} onHide={() => dropJob(job.id)} footer={review ? <>
    {result.totals.plays > 0 && <Button type="button" onClick={() => { patchJob(job.id, { phase: 'starting' }); startPlays(config, job, false); }}>Add {plural(result.totals.plays, 'play')}</Button>}
    <Button type="button" secondary onClick={() => dropJob(job.id)}>Discard</Button></> : null}>
    {job.phase === 'working' && <BulkJobProgress label={`Setting ${plural(job.itemIds.length, 'item')} to ${job.statusLabel}`} palette={palette} />}
    {job.phase === 'starting' && <BulkJobProgress label="Starting" palette={palette} />}
    {job.phase === 'previewing' && <BulkJobProgress done={progress.done} total={progress.total} label="Checking" palette={palette} />}
    {job.phase === 'running' && <BulkJobProgress done={progress.done} total={progress.total} label="Adding plays" palette={palette} />}
    {review && <>
      <BulkJobSummary totals={result.totals} samples={result.samples} notes={resultNotes(result.totals)} palette={palette} />
      <BulkJobProblems count={result.problem_count} problems={result.problems} palette={palette} />
      {result.totals.plays === 0 && <BulkJobMessage palette={palette}>Nothing to add with these choices.</BulkJobMessage>}
    </>}
    {job.phase === 'done' && job.kind === 'status' && <BulkJobMessage palette={palette}>{job.message}</BulkJobMessage>}
    {job.phase === 'done' && job.kind === 'plays' && result && <>
      <BulkJobMessage palette={palette}>
        {plural(result.totals.plays, 'play')} added across {plural(result.totals.shows, 'show')}.
      </BulkJobMessage>
      <BulkJobProblems count={result.problem_count} problems={result.problems} palette={palette} />
    </>}
    {job.phase === 'error' && <BulkJobMessage tone="danger" palette={palette}>{job.error}</BulkJobMessage>}
  </BulkJobCard>;
}

// ---- dialogs that start a job ---------------------------------------------
function StatusDialog({ request, close, start, palette }) {
  const count = request.itemIds.length;
  const description = request.status === 'no_status'
    ? `Clear the status for ${plural(count, 'item')}? They become Not set, leave the normal library view, and can be found again with the No Status filter. Nothing is removed from History or Statistics.`
    : `Set ${plural(count, 'item')} to ${request.statusLabel}? ${request.status === 'Completed'
      ? 'Completed also adds a play for every episode not yet played, dated today. To choose the dates, use Add Plays to History first.'
      : 'This changes status only. Nothing is added to History, Statistics or streaks. To record viewing activity, use Add Plays to History.'}`;
  return <BulkActionDialog open title="Update Status" description={description}
    confirmLabel="Update status" cancelLabel="Cancel" palette={palette}
    onConfirm={start} onClose={close} />;
}

function PlaysDialog({ request, close, start, palette }) {
  const [form, setForm] = useState({ scope: 'aired', season_from: '1', season_to: '1', existing: 'skip',
    date_mode: 'air_date', date: '', start_date: '', end_date: '' });
  const today = new Date().toISOString().slice(0, 10);
  const set = event => setForm(prev => ({ ...prev, [event.target.name]: event.target.value }));
  return <Dialog open title="Add Plays to History" closeLabel="Close" onClose={close} palette={palette}>
    <BulkNote>Adds viewing records for {plural(request.itemIds.length, 'selected item')}. They show in History and count in activity, Statistics and streaks.</BulkNote>
    <SelectField id="bulk-plays-scope" name="scope" label="Which episodes" value={form.scope} options={SCOPES} onChange={set} palette={palette} />
    <BulkNote>{SCOPE_HELP[form.scope]}</BulkNote>
    {form.scope === 'seasons' && <BulkFieldRow>
      <TextField id="bulk-plays-from" name="season_from" type="number" min="0" label="First season" value={form.season_from} onChange={set} palette={palette} />
      <TextField id="bulk-plays-to" name="season_to" type="number" min="0" label="Last season" value={form.season_to} onChange={set} palette={palette} /></BulkFieldRow>}
    <SelectField id="bulk-plays-existing" name="existing" label="Episodes already played" value={form.existing} options={EXISTING} onChange={set} palette={palette} />
    <SelectField id="bulk-plays-dates" name="date_mode" label="Play dates" value={form.date_mode} options={DATES} onChange={set} palette={palette} />
    <BulkNote>{DATE_HELP[form.date_mode]}</BulkNote>
    {form.date_mode === 'one_date' && <TextField id="bulk-plays-date" name="date" type="date" max={today} label="Date" value={form.date} onChange={set} palette={palette} />}
    {form.date_mode === 'range' && <BulkFieldRow>
      <TextField id="bulk-plays-start" name="start_date" type="date" max={today} label="From" value={form.start_date} onChange={set} palette={palette} />
      <TextField id="bulk-plays-end" name="end_date" type="date" max={today} label="To" value={form.end_date} onChange={set} palette={palette} /></BulkFieldRow>}
    <BulkNote>Review checks every show in the background and shows the counts. Nothing is added until you confirm, and you can keep browsing meanwhile.</BulkNote>
    <BulkActionRow><Button type="button" onClick={() => start(form)}>Review</Button>
      <Button type="button" secondary onClick={close}>Cancel</Button></BulkActionRow>
  </Dialog>;
}

function TagDialog({ request, close, palette }) {
  const [selectedTag, setSelectedTag] = useState('');
  const [action, setAction] = useState('add');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function apply() {
    if (!selectedTag || busy) return;
    setBusy(true);
    setError('');
    try {
      const data = new URLSearchParams({ tag_name: selectedTag, action });
      data.set('item_ids', request.itemIds.join(','));
      await post(request.tagUrl, request.csrfToken, data);
      notify(`Tag "${selectedTag}" ${action === 'add' ? 'added to' : 'removed from'} ${plural(request.itemIds.length, 'item')}.`);
      finished();
      close();
    } catch (failure) {
      setError(failure.message || 'The bulk tag action failed.');
    } finally {
      setBusy(false);
    }
  }

  return <TagBulkDialog
    open
    itemCount={request.itemIds.length}
    tags={request.tags || []}
    selectedTag={selectedTag}
    action={action}
    busy={busy}
    error={error}
    palette={palette}
    onSelectTag={setSelectedTag}
    onActionChange={setAction}
    onApply={apply}
    onClose={close} />;
}

function Adapter({ config }) {
  const [request, setRequest] = useState(null);
  const [palette] = useState(readPalette);
  const all = useJobs();
  const seq = useRef(0);
  useEffect(() => {
    const open = event => setRequest(event.detail);
    window.addEventListener('bulk-actions-open', open);
    return () => window.removeEventListener('bulk-actions-open', open);
  }, []);
  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(STORE) || '[]');
      if (Array.isArray(saved) && saved.length && !jobs.length) setJobs(saved);
    } catch { /* ignore unreadable storage */ }
  }, []);

  const newJob = fields => {
    seq.current += 1;
    const job = { id: `${Date.now()}-${seq.current}`, itemIds: request.itemIds, ...fields };
    setJobs([...jobs, job]);
    setRequest(null);
    window.dispatchEvent(new CustomEvent('bulk-actions-done'));
    return job;
  };
  return <>
    {request?.kind === 'status' && <StatusDialog request={request} palette={palette} close={() => setRequest(null)}
      start={() => runStatus(config, newJob({ kind: 'status', phase: 'working', status: request.status, statusLabel: request.statusLabel }))} />}
    {request?.kind === 'tag' && <TagDialog request={request} palette={palette}
      close={() => setRequest(null)} />}
    {request?.kind === 'plays' && <PlaysDialog request={request} close={() => setRequest(null)} palette={palette}
      start={form => startPlays(config, newJob({ kind: 'plays', phase: 'starting', form }), true)} />}
    {all.length > 0 && <BulkJobDock label="Bulk jobs">{all.map(job => <JobCard key={job.id} config={config} job={job} palette={palette} />)}</BulkJobDock>}
  </>;
}

export function mountBulkActions(host, props) {
  return mountRoot(host, <Adapter config={props} />);
}
