import React, { useEffect, useState } from 'react';
import { AccessibilityInfo, Text, TextInput, View } from 'react-native';
import { BulkActionDialog, BulkOptionRow, BulkToolbar } from '../../../frontend/ui/BulkActions';
import { palette, radius, spacing } from '../theme';

const STATUSES = [
  ['0', 'Planning'], ['1', 'In progress'], ['2', 'Paused'],
  ['3', 'Completed'], ['4', 'Dropped'],
];

export default function LibraryBulkActions({ rows, selectedRows, transport, selecting, onToggleSelect, onClearSelection, onDone }) {
  const [dialog, setDialog] = useState('');
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('3');
  const [listId, setListId] = useState('');
  const [tagId, setTagId] = useState('');
  const [tags, setTags] = useState([]);
  const [lists, setLists] = useState([]);
  const [error, setError] = useState('');
  const [playScope, setPlayScope] = useState('aired');
  const [existing, setExisting] = useState('skip');
  const [dateMode, setDateMode] = useState('air_date');
  const [seasonFrom, setSeasonFrom] = useState('1');
  const [seasonTo, setSeasonTo] = useState('1');
  const [playDate, setPlayDate] = useState('');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [preview, setPreview] = useState(null);
  const selected = rows.filter(row => selectedRows.includes(String(row.id)));

  useEffect(() => {
    let active = true;
    transport.loadLists().then(payload => {
      if (active) setLists((payload?.results || []).filter(list => !list.is_smart));
    }).catch(() => { if (active) setLists([]); });
    transport.loadTags().then(payload => {
      if (active) setTags(payload?.results || []);
    }).catch(() => { if (active) setTags([]); });
    return () => { active = false; };
  }, [transport]);

  async function confirm() {
    if (!selected.length || busy) return;
    setBusy(true);
    setError('');
    const mediaRows = selected.map(row => ({ ...row.media, identity: row.identity }));
    try {
      if (dialog === 'plays') {
        const result = await transport.startBulkPlays({ rows: mediaRows,
          options: { scope: playScope, existing, date_mode: dateMode, season_from: seasonFrom,
            season_to: seasonTo, date: playDate, start_date: startDate, end_date: endDate }, dryRun: !preview });
        let state = null;
        for (let attempt = 0; attempt < 120; attempt += 1) {
          await new Promise(resolve => setTimeout(resolve, 500));
          state = await transport.bulkPlaysStatus({ operationId: result.operation_id });
          if (state.state !== 'running') break;
        }
        if (!state || state.state === 'running' || state.state === 'error') {
          throw new Error(state?.error || 'The history job did not finish. Check History before retrying.');
        }
        if (!preview) {
          setPreview(state);
          return;
        }
      }
      else if (dialog === 'status') await transport.bulkStatus({ rows: mediaRows, status });
      else if (dialog === 'list') await transport.bulkAddToList({ listId, rows: mediaRows });
      else if (dialog === 'collection') await transport.bulkCollection({ rows: mediaRows });
      else if (dialog === 'tag') await transport.bulkTag({ rows: mediaRows, tagId: Number(tagId), mode: 'add' });
      if (dialog !== 'plays' || preview) {
        AccessibilityInfo.announceForAccessibility?.('Selected library items updated.');
        setDialog('');
        setPreview(null);
        onDone?.();
      }
    } catch (cause) {
      setError(cause?.message || 'The selected items could not be updated.');
    } finally {
      setBusy(false);
    }
  }

  // Select Items now lives in the screen's settings gear menu.
  if (!selecting) return null;

  const title = dialog === 'status' ? 'Update status'
    : dialog === 'list' ? 'Add to list'
      : dialog === 'collection' ? 'Add to collection'
        : dialog === 'tag' ? 'Apply tag' : dialog === 'plays' ? 'Add plays to history' : '';
  const description = `Apply this action to ${selected.length} selected item${selected.length === 1 ? '' : 's'}?`;
  return <View style={{ gap: spacing.sm }}>
    <BulkToolbar count={selected.length} palette={palette}
      onClear={onClearSelection} onCancel={onToggleSelect}
      onUpdateStatus={() => setDialog('status')} onAddToList={() => setDialog('list')}
      onAddToCollection={() => setDialog('collection')} onApplyTag={() => setDialog('tag')}
      onAddPlays={['tv', 'anime'].includes(rows[0]?.identity?.mediaType) && selected.every(row => ['tv', 'anime'].includes(row.identity.mediaType))
        ? () => { setPreview(null); setDialog('plays'); } : undefined} />
    {error ? <Text accessibilityRole="alert" style={{ color: palette.danger }}>{error}</Text> : null}
    <BulkActionDialog open={Boolean(dialog)} title={title} description={description}
      confirmLabel={dialog === 'plays' && preview ? 'Add plays' : dialog === 'plays' ? 'Preview' : 'Apply'}
      busy={busy} confirmDisabled={dialog === 'list' ? !listId : dialog === 'tag' ? !tagId : false}
      palette={palette} onConfirm={confirm} onClose={() => setDialog('')}>
      {dialog === 'status' ? STATUSES.map(([value, label]) => <BulkOptionRow key={value} label={label}
        selected={status === value} palette={palette} onPress={() => setStatus(value)} />) : null}
      {dialog === 'list' ? lists.map(list => <BulkOptionRow key={list.id} label={list.name}
        selected={String(listId) === String(list.id)} detail={list.is_public ? 'Shared' : 'Private'}
        palette={palette} onPress={() => setListId(String(list.id))} />) : null}
      {dialog === 'tag' ? tags.map(tag => <BulkOptionRow key={tag.id} label={tag.name}
        selected={String(tagId) === String(tag.id)} palette={palette}
        onPress={() => setTagId(String(tag.id))} />) : null}
      {dialog === 'plays' && !preview ? <>
        <Text style={{ color: palette.textSecondary }}>Choose which episodes to include.</Text>
        {['aired', 'watched', 'seasons'].map(value => <BulkOptionRow key={value}
          label={value === 'aired' ? 'Aired episodes' : value === 'watched' ? 'Watched seasons' : 'Season range'} selected={playScope === value}
          palette={palette} onPress={() => setPlayScope(value)} />)}
        {playScope === 'seasons' ? <View style={{ flexDirection: 'row', gap: spacing.sm }}>
          <TextInput accessibilityLabel="First season" keyboardType="number-pad" value={seasonFrom}
            onChangeText={setSeasonFrom} placeholder="First season" style={styles.input} />
          <TextInput accessibilityLabel="Last season" keyboardType="number-pad" value={seasonTo}
            onChangeText={setSeasonTo} placeholder="Last season" style={styles.input} />
        </View> : null}
        {['skip', 'add'].map(value => <BulkOptionRow key={value}
          label={value === 'skip' ? 'Skip episodes with existing plays' : 'Add repeats for existing plays'}
          selected={existing === value} palette={palette} onPress={() => setExisting(value)} />)}
        {['air_date', 'one_date', 'range'].map(value => <BulkOptionRow key={value}
          label={value === 'air_date' ? 'Use episode air dates' : value === 'one_date' ? 'Use one date' : 'Use a date range'}
          selected={dateMode === value} palette={palette} onPress={() => setDateMode(value)} />)}
        {dateMode === 'one_date' ? <TextInput accessibilityLabel="Play date" value={playDate} onChangeText={setPlayDate}
          placeholder="YYYY-MM-DD" style={styles.input} /> : null}
        {dateMode === 'range' ? <View style={{ flexDirection: 'row', gap: spacing.sm }}>
          <TextInput accessibilityLabel="Start date" value={startDate} onChangeText={setStartDate}
            placeholder="Start YYYY-MM-DD" style={styles.input} />
          <TextInput accessibilityLabel="End date" value={endDate} onChangeText={setEndDate}
            placeholder="End YYYY-MM-DD" style={styles.input} />
        </View> : null}
      </> : null}
      {dialog === 'plays' && preview ? <Text style={{ color: palette.textSecondary }}>
        Preview: {preview.totals?.plays || 0} play{preview.totals?.plays === 1 ? '' : 's'} across {preview.totals?.shows || 0} show{preview.totals?.shows === 1 ? '' : 's'}.
        {preview.problem_count ? ` ${preview.problem_count} show${preview.problem_count === 1 ? '' : 's'} need review.` : ''}
      </Text> : null}
      {dialog === 'collection' ? <Text style={{ color: palette.textSecondary }}>
        Existing collection copies may be rejected by the server and will be reported as an error.
      </Text> : null}
    </BulkActionDialog>
  </View>;
}

const styles = {
  input: { flex: 1, minWidth: 100, color: palette.text, backgroundColor: palette.surface,
    borderColor: palette.border, borderWidth: 1, borderRadius: radius.sm, minHeight: 40,
    paddingHorizontal: spacing.md, fontSize: 14 },
};
