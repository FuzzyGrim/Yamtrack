import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ActivityIndicator, Alert, Pressable, ScrollView, StyleSheet, Text, TextInput, View, useWindowDimensions } from 'react-native';
import { Button } from '../../../frontend/ui/Button';
import { MediaCard } from '../../../frontend/ui/MediaCard';
import WindowedRail from './WindowedRail';
import { Icon } from '../../../frontend/ui/Icon';
import { actionIcons } from '../../../frontend/ui/actionIcons';
import { ProgressCard } from '../../../frontend/ui/ProgressCard.native';
import { ProgressDialog } from '../../../frontend/ui/ProgressDialog';
import { CollectionCopyDialog, collectionSeasonEpisodes } from '../../../frontend/ui/CollectionCopies';
import { ListsForm } from '../../../frontend/ui/ListsFormView';
import { AdaptivePresentationSheet } from '../components/AdaptivePresentationSheet';
import { RatingStarIcon } from '../../../frontend/ui/RatingStarIcon.native';
import { RatingChip } from '../../../frontend/ui/RatingChip';
import { RatingStars } from '../../../frontend/ui/RatingStars';
import { TagAssignmentDialog } from '../../../frontend/ui/TagManagement';
import { TrackDialog } from '../../../frontend/ui/TrackDialog';
import {
  applyListToggle,
  initialTrackValues,
  listsMembership,
  normalizeListsModel,
  normalizeTrackForm,
} from '../../../frontend/ui/mediaCardForms';
import {
  DetailActionBar,
  DetailBlock,
  DetailCollectionBlock,
  DetailEmpty,
  DetailEpisodeCards,
  DetailIssueCards,
  DetailLoading,
  DetailNotesBlock,
  DetailScoreRow,
} from '../../../frontend/ui/DetailPage';
import { DetailTrackingHistory } from '../../../frontend/ui/DetailSecondary';
import { DetailEpisodeHeader } from '../../../frontend/ui/DetailEpisode';
import { ArtworkPicker, EditionPicker } from '../../../frontend/ui/ArtworkPicker';
import { pickCoverImage } from '../data/imagePicker';
import { mergeScoreChips } from '../data/detailTransportCore';
import {
  collectionAddressFor,
  copyFieldsFor,
  copyValuesFromCopy,
  createCollectionTransport,
  emptyCopyValues,
} from '../data/collectionTransport';
import {
  deleteHistoryEntry,
  loadActivityTimeline,
  loadAnimeEpisodes,
  loadCheckIn,
  loadCollectionEntries,
  loadCoverCandidates,
  loadDetailTracking,
  loadEditions,
  loadIssues,
  loadProgressCard,
  loadScores,
  loadSeasonEpisodes,
  loadSeerr,
  requestSeerr,
  runCheckIn,
  saveCover,
  saveEdition,
  saveEntryNotes,
  syncMetadata,
  watchEpisode,
} from '../data/detailTransport';
import {
  createTag,
  deleteTrack,
  loadLists,
  loadTags,
  loadTrack,
  loadTrackTabs,
  saveArtwork,
  saveScore,
  saveTrack,
  setDiscoverHidden,
  setTags,
  toggleList,
  unwatchEpisode,
} from './mediaCardTransport';
import { badgePalette, mediaCardPalette, palette as themePalette, progressDetailPalette, ratingPalette, spacing } from '../theme';
import { loadDetailHeader } from '../data/loadDetailHeader.mjs';

// The detail page's own controls on top of the server page payload: the track
// dialog and check-in, tags, notes, lists, activity history, the progress card,
// collection copies, cover and edition pickers, provider scores with their
// series graphs, comic issues, anime episodes, Seerr and metadata sync. The
// screen owns the page model; this hook owns only what those controls read and
// write, and hands the screen the pieces to place in the shared detail view.

const accentContrast = badgePalette.indigo.color;

// One palette for the shared detail controls. Each colour comes from the theme
// tokens, so the iOS appearance and the web theme states still apply.
export const detailPagePalette = {
  ...themePalette,
  accentContrast,
  heroText: accentContrast,
  // The web score cards tint their fill and border with one accent per source.
  scoreAccents: {
    provider: { fill: 'rgba(34, 211, 238, 0.10)', border: 'rgba(34, 211, 238, 0.26)' },
    trakt: { fill: 'rgba(251, 113, 133, 0.10)', border: 'rgba(251, 113, 133, 0.26)' },
    imdb: { fill: 'rgba(251, 191, 36, 0.10)', border: 'rgba(251, 191, 36, 0.26)' },
    mal: { fill: 'rgba(129, 140, 248, 0.10)', border: 'rgba(129, 140, 248, 0.26)' },
    opencritic: { fill: 'rgba(251, 146, 60, 0.10)', border: 'rgba(251, 146, 60, 0.26)' },
  },
  surfaceBorder: themePalette.border,
  placeholder: themePalette.surfaceStrong,
  mediaCard: mediaCardPalette,
  rating: { ...ratingPalette, focusStyle: {} },
  button: {
    primary: { background: themePalette.accentStrong, pressed: themePalette.accent, hovered: themePalette.accent, text: accentContrast, border: themePalette.accentStrong },
    surface: { background: themePalette.surfaceStrong, pressed: themePalette.surfaceMuted, hovered: themePalette.surfaceMuted, text: themePalette.text, border: themePalette.border },
    'danger-solid': { background: themePalette.danger, pressed: themePalette.danger, hovered: themePalette.danger, text: accentContrast, border: themePalette.danger },
    'ghost-danger': { background: 'transparent', pressed: themePalette.surfaceStrong, hovered: themePalette.surfaceStrong, text: themePalette.danger, border: 'transparent' },
    radius: 8,
  },
};

const palette = detailPagePalette;
const formPalette = { ...palette, ...palette.mediaCard, radius: 8, focusStyle: {}, dateTime: palette.mediaCard };

const EMPTY_TRACK = { open: false, status: 'ready', model: null, tabs: [], values: {}, tracked: false, busy: false, serverError: '' };
const NO_DIALOG = { kind: '', busy: false, error: '', data: null };
const NO_TRACKING = { catalogItemId: null, tracked: false, tracking: null, score: null, plays: 0, notes: [], history: [], sourceUrl: '', sourceName: '' };

const listLabels = {
  title: 'Lists',
  close: 'Close',
  loading: 'Loading',
  loadFailed: "Couldn't load your lists.",
  retry: 'Try again',
  filterByTag: 'Filter by tag',
  allLists: 'All lists',
  countOne: 'On %s list',
  countMany: 'On %s lists',
  add: 'Add',
  remove: 'Remove',
  addTo: 'Add item to %s',
  removeFrom: 'Remove item from %s',
  emptyTag: 'No lists with this tag.',
  emptyLists: "You haven't created any lists yet.",
  toggleFailed: "Couldn't update this list. Please try again.",
};

// Web: "Add new entry" is offered once a finished entry exists for these types,
// and always for games (app/track_actions.py).
const ADD_ENTRY_ALWAYS = new Set(['game', 'boardgame']);
const ADD_ENTRY_WHEN_COMPLETED = new Set(['movie', 'anime', 'book', 'comic']);

function renderRating({ value, label, scaleMax, palette: ratingColors, onSelect }) {
  return <RatingStars scaleMax={scaleMax} value={value} label={label}
    renderStar={RatingStarIcon} onSelect={onSelect} palette={ratingColors} />;
}

/** The transports' identity from the page payload's identity. */
export function featureIdentity(model) {
  const key = model?.identity;
  const mediaType = key?.media_type || key?.mediaType;
  const mediaId = key?.media_id ?? key?.mediaId;
  if (!mediaType || !key?.source || mediaId == null) return null;
  const season = key.season_number ?? key.seasonNumber;
  const episode = key.episode_number ?? key.episodeNumber;
  return {
    mediaType,
    source: key.source,
    mediaId: String(mediaId),
    ...(season != null ? { seasonNumber: Number(season) } : {}),
    ...(episode != null ? { episodeNumber: Number(episode) } : {}),
    ...(key.library_media_type || key.libraryMediaType || key.parent_media_type === 'anime' ? { libraryMediaType: key.library_media_type || key.libraryMediaType || 'anime' } : {}),
  };
}

function collectionAddressForIdentity(target) {
  // A show or season collects every one of its episodes (PUT .../collect/).
  if (target.mediaType === 'tv' || target.mediaType === 'season') {
    const base = `/api/v1/media/tv/${encodeURIComponent(target.source)}/${encodeURIComponent(target.mediaId)}`;
    return { path: `${base}${target.mediaType === 'season' ? `/${target.seasonNumber}` : ''}/collect/` };
  }
  return collectionAddressFor({
    media_type: target.mediaType,
    source: target.source,
    media_id: target.mediaId,
    season_number: target.seasonNumber,
    episode_number: target.episodeNumber,
  });
}

function MediaCardActionHost({ open, action, palette: colors, onClose, children }) {
  return <AdaptivePresentationSheet isPresented={!!open} onDismiss={onClose} onClose={onClose}
    title={action?.title} titleColor={colors?.text} containerColor={themePalette.sidebar}>
    <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={{ paddingHorizontal: 16, paddingBottom: 24 }}>
      {children}
    </ScrollView>
  </AdaptivePresentationSheet>;
}

function NotesDialog({ open, note, busy, error, onSave, onClose }) {
  const [draft, setDraft] = useState('');
  useEffect(() => { if (open) setDraft(note?.text || ''); }, [open, note]);
  return <MediaCardActionHost open={open} action={{ title: 'Notes' }} palette={palette} onClose={onClose}>
    <TextInput accessibilityLabel="Notes" multiline value={draft} onChangeText={setDraft}
      placeholderTextColor={palette.textMuted} style={styles.notesInput} textAlignVertical="top" />
    {error ? <Text style={[styles.dialogText, { color: palette.danger }]}>{error}</Text> : null}
    <View style={styles.dialogActions}>
      <Button label="Cancel" tone="surface" palette={palette.button} onPress={onClose} />
      <Button label="Save" tone="primary" disabled={busy} busy={busy} palette={palette.button}
        onPress={() => onSave(draft)} />
    </View>
  </MediaCardActionHost>;
}

function PlaysDialog({ open, identity, entries, onRemove, onClose }) {
  const [timeline, setTimeline] = useState({ status: 'idle', days: [], multiple: false });
  useEffect(() => {
    if (!open || !identity) return undefined;
    let cancelled = false;
    setTimeline({ status: 'loading', days: [], multiple: false });
    loadActivityTimeline(identity)
      .then(next => { if (!cancelled) setTimeline({ status: 'ready', days: next.days, multiple: next.multipleInstances }); })
      .catch(() => { if (!cancelled) setTimeline({ status: 'error', days: [], multiple: false }); });
    return () => { cancelled = true; };
  }, [open, identity, entries.length]);
  return <MediaCardActionHost open={open} action={{ title: 'Activity History' }} palette={palette} onClose={onClose}>
    <ScrollView style={styles.dialogScroll}>
      {timeline.status === 'loading' ? <ActivityIndicator color={palette.accent} /> : null}
      {timeline.days.map(day => <View key={day.day} style={styles.dayGroup}>
        <Text accessibilityRole="header" style={[styles.dialogText, styles.dayHeading, { color: palette.text }]}>{day.label}</Text>
        {day.entries.map(entry => <View key={entry.id} style={[styles.dayEntry, { backgroundColor: palette.surfaceStrong }]}>
          <Text style={styles.playMeta}>{[entry.instance && timeline.multiple ? `Instance #${entry.instance}` : '', entry.date].filter(Boolean).join(' · ')}</Text>
          {entry.changes.map((change, index) => <Text key={index} style={[styles.dialogText, { color: palette.text }]}>{change}</Text>)}
        </View>)}
      </View>)}
      {timeline.status === 'ready' && !timeline.days.length ? <Text style={styles.dialogText}>{"You haven't started tracking it yet."}</Text> : null}
      {timeline.status === 'error' ? <Text style={styles.dialogText}>Activity history could not be loaded.</Text> : null}
      {entries.length ? <Text accessibilityRole="header" style={[styles.dialogText, styles.dayHeading, { color: palette.text }]}>Plays</Text> : null}
      {entries.map(entry => <View key={entry.id} style={styles.playRow}>
        <View style={styles.playBody}>
          <Text style={[styles.dialogText, { color: palette.text }]}>{entry.title}</Text>
          <Text style={styles.playMeta}>{[entry.date, entry.detail].filter(Boolean).join(' · ')}</Text>
        </View>
        <Button label="Remove" tone="ghost-danger" palette={palette.button} onPress={() => onRemove(entry)} />
      </View>)}
    </ScrollView>
  </MediaCardActionHost>;
}

function CoverDialog({ open, itemId, onSaved, onClose }) {
  const [state, setState] = useState({ status: 'loading', current: '', candidates: [] });
  const [selected, setSelected] = useState(null);
  const [url, setUrl] = useState('');
  const [showUrl, setShowUrl] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const load = useCallback(() => {
    setState(current => ({ ...current, status: 'loading' }));
    loadCoverCandidates(itemId)
      .then(next => setState({ status: 'ready', ...next }))
      .catch(() => setState(current => ({ ...current, status: 'error' })));
  }, [itemId]);
  useEffect(() => {
    if (!open) return;
    setSelected(null); setUrl(''); setShowUrl(false); setError('');
    load();
  }, [open, load]);
  const choose = async () => {
    try {
      const picked = await pickCoverImage();
      if (picked) setSelected({ kind: 'file', uri: picked.uri, base64: picked.base64 });
    } catch {
      setError('That image could not be read.');
    }
  };
  const save = async () => {
    setSaving(true); setError('');
    try {
      await saveCover(itemId, selected?.kind === 'file' ? { base64: selected.base64 }
        : { url: selected?.kind === 'candidate' ? selected.image : url.trim() });
      onSaved();
    } catch (cause) {
      setError(cause?.message || 'The cover could not be saved.');
    } finally {
      setSaving(false);
    }
  };
  return <MediaCardActionHost open={open} action={{ title: 'Cover' }} palette={formPalette} onClose={onClose}>
    <ArtworkPicker
      current={state.current} candidates={state.candidates} candidatesState={state.status}
      selected={selected} url={url} showUrl={showUrl} supportsDrag={false} saving={saving} error={error}
      palette={{ ...formPalette, radius: 8, radiusSmall: 6, button: formPalette.button, accentText: formPalette.accentContrast }}
      onChooseFile={choose} onToggleUrl={() => setShowUrl(value => !value)}
      onUrlChange={value => { setUrl(value); setSelected(value.trim() ? { kind: 'url', uri: value.trim() } : null); }}
      onPickCandidate={candidate => setSelected({ kind: 'candidate', id: candidate.id, uri: candidate.uri, image: candidate.image, title: candidate.title })}
      onReset={() => { setSelected(null); setUrl(''); }}
      onSave={save} onRetryCandidates={load} />
  </MediaCardActionHost>;
}

function EditionDialog({ open, itemId, onSaved, onClose }) {
  const [query, setQuery] = useState('');
  const [state, setState] = useState({ status: 'loading', rows: [], selected: '' });
  const load = useCallback(needle => {
    setState(current => ({ ...current, status: 'loading' }));
    loadEditions(itemId, needle)
      .then(next => setState({ status: 'ready', ...next }))
      .catch(() => setState(current => ({ ...current, status: 'error' })));
  }, [itemId]);
  useEffect(() => { if (open) { setQuery(''); load(''); } }, [open, load]);
  const pick = async row => {
    try {
      await saveEdition(itemId, row.id);
      onSaved();
    } catch {
      setState(current => ({ ...current, status: 'error' }));
    }
  };
  return <MediaCardActionHost open={open} action={{ title: 'Edition' }} palette={formPalette} onClose={onClose}>
    <EditionPicker
      description="Pick a specific Hardcover edition (e.g. a different language or cover) to use for this title's title and cover. Leave the search blank to see every edition."
      provider="Hardcover" placeholder="Search editions..." query={query} results={state.rows}
      selectedId={state.selected} state={state.status} open
      palette={{ ...formPalette, radius: 8, radiusSmall: 6, accentText: formPalette.accentContrast }}
      onQueryChange={value => { setQuery(value); load(value.trim()); }}
      onSelect={pick} onRetry={() => load(query.trim())} />
  </MediaCardActionHost>;
}

function SeerrDialog({ open, state, busy, error, onRequest, onClose }) {
  const seasons = state?.seasons || [];
  return <MediaCardActionHost open={open} action={{ title: 'Request in Seerr' }} palette={palette} onClose={onClose}>
    {error ? <Text style={[styles.dialogText, { color: palette.danger }]}>{error}</Text> : null}
    {!state && !error ? <ActivityIndicator color={palette.accent} /> : null}
    {state && seasons.length ? seasons.map(season => <View key={season.number} style={styles.playRow}>
      <Text style={[styles.dialogText, styles.playBody, { color: palette.text }]}>{season.name}</Text>
      {season.requestable
        ? <Button label="Request" disabled={busy} palette={palette.button} onPress={() => onRequest([season.number])} />
        : <Text style={styles.playMeta}>{season.status}</Text>}
    </View>) : null}
    {state && !seasons.length ? <Text style={[styles.dialogText, { color: palette.text }]}>{`Status: ${state.status}`}</Text> : null}
    {state?.requestable ? <Button
      label={seasons.length ? 'Request all missing seasons' : 'Request'} width="full" disabled={busy}
      palette={palette.button} onPress={() => onRequest([])} /> : null}
  </MediaCardActionHost>;
}

/**
 * @param model the page model the screen loaded (its `identity` names the item)
 * @param ready false while the page payload is loading or failed
 * @param version bumps when the screen reloaded the page model
 * @param refresh reloads the page model after a write
 */
export function useDetailFeatures({ model, ready, dataReady, version, refresh, navigate, onOpenItem, onOpenExternal, shelfWidth }) {
  const { width } = useWindowDimensions();
  const [ratingOpen, setRatingOpen] = useState(false);
  const identityKey = JSON.stringify(featureIdentity(model));
  const identity = useMemo(() => JSON.parse(identityKey), [identityKey]);
  const enabled = Boolean(identity && ready && !model?.publicView);
  const title = model?.title || '';
  const [tracking, setTracking] = useState(NO_TRACKING);
  const [track, setTrack] = useState(EMPTY_TRACK);
  const [dialog, setDialog] = useState(NO_DIALOG);
  const [notice, setNotice] = useState('');
  const [collection, setCollection] = useState({ status: 'loading', entries: [], counts: [], error: '' });
  const [copy, setCopy] = useState({ open: false, values: {}, errors: {}, busy: false });
  const [progress, setProgress] = useState({ data: null, open: false });
  const [scores, setScores] = useState({ chips: [], graphs: {}, gameLengths: null });
  const [checkin, setCheckin] = useState([]);
  const [headerReadyKey, setHeaderReadyKey] = useState(null);
  const [seerr, setSeerr] = useState({ available: false, state: null });
  const [episodes, setEpisodes] = useState({ rows: [], status: 'idle', error: '', order: 'asc' });
  const [issues, setIssues] = useState({ rows: [], total: 0, status: 'idle', error: '', layout: 'grid' });
  const [siblings, setSiblings] = useState([]);
  const [local, setLocal] = useState(0);

  const isEpisode = identity?.mediaType === 'episode';
  const isSeason = identity?.mediaType === 'season';
  const isAnime = identity?.mediaType === 'anime';
  const isComic = identity?.mediaType === 'comic';
  const canRequest = identity?.source === 'tmdb' && (identity.mediaType === 'movie' || identity.mediaType === 'tv');
  const reload = useCallback(() => { setLocal(value => value + 1); refresh?.(); }, [refresh]);
  const stamp = `${version}:${local}`;

  useEffect(() => {
    if (!enabled) return undefined;
    let cancelled = false;
    // The native page reveals its header together; web's server-rendered
    // header already arrives with its tracking state and uses separate slots.
    loadDetailHeader(identity, { tracking: loadDetailTracking, progress: loadProgressCard,
      scores: loadScores, checkin: loadCheckIn }, NO_TRACKING).then(next => {
      if (cancelled) return;
      setTracking(next.tracking);
      setProgress({ data: next.progress, open: false });
      setScores(next.scores);
      setCheckin(next.checkin);
      setHeaderReadyKey(identityKey);
    });
    return () => { cancelled = true; };
  }, [enabled, identity, identityKey, stamp, canRequest]);

  // Older servers lack the capability flag; keep their existing discovery path
  // without holding the detail page on an optional external service.
  useEffect(() => {
    if (!enabled || !dataReady || !canRequest || model?.seerrConfigured !== undefined) return undefined;
    let cancelled = false;
    loadSeerr(identity).then(state => { if (!cancelled) setSeerr({ available: Boolean(state), state }); })
      .catch(() => { if (!cancelled) setSeerr({ available: false, state: null }); });
    return () => { cancelled = true; };
  }, [enabled, identity, canRequest, dataReady, model?.seerrConfigured]);

  useEffect(() => {
    if (!enabled) return undefined;
    let cancelled = false;
    loadCollectionEntries(identity)
      .then(loaded => { if (!cancelled) setCollection({ status: 'ready', ...loaded, error: '' }); })
      .catch(cause => {
        if (!cancelled) setCollection({ status: 'error', entries: [], counts: [], error: cause?.message || 'Collection could not be loaded.' });
      });
    return () => { cancelled = true; };
  }, [enabled, identity, stamp]);

  // Anime episodes come from their own preview endpoint; the page payload lists none.
  const episodesRef = useRef(episodes);
  episodesRef.current = episodes;
  const loadEpisodes = useCallback(async (more = false) => {
    if (!enabled || (!isAnime && !isSeason)) return;
    if (more && episodesRef.current.status === 'loading') return;
    setEpisodes(current => ({ ...current, status: more || !current.rows.length ? 'loading' : 'ready', error: '' }));
    try {
      const next = isSeason ? await loadSeasonEpisodes(identity, identity.seasonNumber, { withPage: true, offset: more ? episodesRef.current.loadMore?.offset || 0 : 0 })
        : await loadAnimeEpisodes(identity, more ? episodesRef.current.loadMore?.querystring : '');
      setEpisodes(current => ({ ...current, rows: more ? [...current.rows, ...next.rows] : next.rows, loadMore: next.loadMore, total: next.total, status: 'ready', error: '' }));
    } catch (cause) {
      setEpisodes(current => ({ ...current, status: 'error', error: cause?.message || 'Episodes could not be loaded.' }));
    }
  }, [enabled, identity, isAnime, isSeason, stamp]);
  useEffect(() => { loadEpisodes(); }, [loadEpisodes]);

  // A comic volume lists its issues, twenty at a time.
  const issuesRef = useRef(issues);
  issuesRef.current = issues;
  const loadMoreIssues = useCallback(async (reset = false) => {
    if (!enabled || !isComic) return;
    setIssues(current => ({ ...current, status: 'loading', error: '', ...(reset ? { rows: [], total: 0 } : {}) }));
    try {
      const page = await loadIssues(identity, reset ? 0 : issuesRef.current.rows.length);
      setIssues(current => ({ ...current, rows: reset ? page.rows : [...current.rows, ...page.rows], total: page.total, status: 'ready' }));
    } catch (cause) {
      setIssues(current => ({ ...current, status: 'error', error: cause?.message || 'Issues could not be loaded.' }));
    }
  }, [enabled, identity, isComic]);
  useEffect(() => { loadMoreIssues(true); }, [loadMoreIssues]);

  // The episode page steps to the previous and next episode of its season.
  useEffect(() => {
    if (!enabled || !isEpisode) { setSiblings([]); return undefined; }
    let cancelled = false;
    loadSeasonEpisodes(identity, identity.seasonNumber)
      .then(rows => { if (!cancelled) setSiblings(rows); })
      .catch(() => { if (!cancelled) setSiblings([]); });
    return () => { cancelled = true; };
  }, [enabled, identity, isEpisode]);

  const closeDialog = useCallback(() => setDialog(NO_DIALOG), []);

  const openTrack = useCallback(async (fresh = false, target = identity) => {
    if (!target) return;
    setTrack({ ...EMPTY_TRACK, identity: target, open: true, status: 'loading' });
    try {
      const loaded = await loadTrack(target, { trackTime: true, fresh });
      const trackModel = normalizeTrackForm(loaded.model);
      setTrack({
        ...EMPTY_TRACK,
        identity: target,
        open: true,
        status: 'ready',
        model: trackModel,
        tabs: loaded.tabs,
        tracked: loaded.tracked,
        values: initialTrackValues(trackModel),
      });
    } catch {
      setTrack({ ...EMPTY_TRACK, identity: target, open: true, status: 'error' });
    }
  }, [identity]);

  const submitTrack = useCallback(async values => {
    if (!track.identity || !track.model) return;
    setTrack(current => ({ ...current, busy: true, serverError: '' }));
    try {
      await saveTrack(track.identity, track.model, values, track.tracked);
      setTrack(EMPTY_TRACK);
      reload();
    } catch (cause) {
      setTrack(current => ({ ...current, busy: false, serverError: cause?.message || 'The change could not be saved.' }));
    }
  }, [reload, track.identity, track.model, track.tracked]);

  const removeTrack = useCallback(async () => {
    if (!track.identity || !track.model) return;
    setTrack(current => ({ ...current, busy: true, serverError: '' }));
    try {
      await deleteTrack(track.identity, track.model);
      setTrack(EMPTY_TRACK);
      reload();
    } catch (cause) {
      setTrack(current => ({ ...current, busy: false, serverError: cause?.message || 'The entry could not be removed.' }));
    }
  }, [reload, track.identity, track.model]);

  const retryTabs = useCallback(async () => {
    if (!track.identity) return;
    try {
      const tabs = await loadTrackTabs(track.identity);
      setTrack(current => ({ ...current, tabs }));
    } catch {
      setTrack(current => ({
        ...current,
        tabs: current.tabs.map(tab => (tab.status === 'error' ? { ...tab, error: 'Tabs could not be reloaded.' } : tab)),
      }));
    }
  }, [track.identity]);

  // Tags: both the Tags dialog and the track dialog's Tags tab write through here.
  const applyTags = useCallback(async (tagList, toggledId, target = identity) => {
    const next = tagList.map(tag => (tag.id === toggledId ? { ...tag, hasTag: !tag.hasTag } : tag));
    await setTags(target, next.filter(tag => tag.hasTag).map(tag => tag.id));
    return next;
  }, [identity]);

  const toggleTrackTag = useCallback(async tag => {
    const tab = track.tabs.find(entry => entry.kind === 'tags');
    const mark = (pending, errorText) => setTrack(current => ({
      ...current,
      tabs: current.tabs.map(entry => (entry.kind !== 'tags' ? entry : {
        ...entry,
        tags: (entry.tags || []).map(row => (row.id === tag.id
          ? { ...row, pending, error: errorText, hasTag: errorText || pending ? row.hasTag : !row.hasTag }
          : row)),
      })),
    }));
    mark(true, '');
    try {
      await applyTags(tab?.tags || [], tag.id, track.identity);
      mark(false, '');
    } catch {
      mark(false, 'Could not update this tag.');
    }
  }, [applyTags, track.tabs, track.identity]);

  const createTrackTag = useCallback(async name => {
    try {
      const created = await createTag(name);
      const tab = track.tabs.find(entry => entry.kind === 'tags');
      const applied = (tab?.tags || []).filter(row => row.hasTag).map(row => row.id);
      await setTags(track.identity, [...applied, String(created.id)]);
      setTrack(current => ({
        ...current,
        tabs: current.tabs.map(entry => (entry.kind !== 'tags' ? entry : {
          ...entry,
          tags: [...(entry.tags || []), { id: String(created.id), name, hasTag: true, pending: false, error: '' }],
        })),
      }));
      return true;
    } catch {
      return false;
    }
  }, [track.identity, track.tabs]);

  const openTags = useCallback(async () => {
    setDialog({ kind: 'tags', busy: true, error: '', data: [] });
    try {
      setDialog({ kind: 'tags', busy: false, error: '', data: await loadTags(identity) });
    } catch (cause) {
      setDialog({ kind: 'tags', busy: false, error: cause?.message || "Couldn't load your tags.", data: [] });
    }
  }, [identity]);

  const toggleDialogTag = useCallback(async tag => {
    setDialog(current => ({ ...current, busy: true, error: '' }));
    try {
      const next = await applyTags(dialog.data, tag.id);
      setDialog({ kind: 'tags', busy: false, error: '', data: next });
    } catch {
      setDialog(current => ({ ...current, busy: false, error: 'Could not update this tag.' }));
    }
  }, [applyTags, dialog.data]);

  const createDialogTag = useCallback(async name => {
    try {
      const created = await createTag(name);
      const applied = (dialog.data || []).filter(row => row.hasTag).map(row => row.id);
      await setTags(identity, [...applied, String(created.id)]);
      setDialog(current => ({
        ...current,
        data: [...(current.data || []), { id: String(created.id), name, hasTag: true, pending: false, error: '' }],
      }));
      return true;
    } catch {
      return false;
    }
  }, [dialog.data, identity]);

  // Lists for the page item, or for an issue's own identity.
  const openLists = useCallback(async (target = identity, itemTitle = title) => {
    const blank = { membership: {}, model: null, pending: {}, errors: {} };
    setDialog({ kind: 'lists', busy: true, error: '', target, data: { status: 'loading', ...blank } });
    try {
      const data = normalizeListsModel(await loadLists(target, itemTitle));
      setDialog({ kind: 'lists', busy: false, error: '', target, data: { ...blank, status: 'ready', model: data, membership: listsMembership(data) } });
    } catch {
      setDialog({ kind: 'lists', busy: false, error: '', target, data: { status: 'error', ...blank } });
    }
  }, [identity, title]);

  const toggleListMembership = useCallback(async list => {
    const data = dialog.data;
    if (!data || data.pending[list.id]) return;
    const patch = value => setDialog(current => ({ ...current, data: { ...current.data, ...value(current.data) } }));
    patch(current => ({ pending: { ...current.pending, [list.id]: true }, errors: { ...current.errors, [list.id]: '' } }));
    try {
      const result = await toggleList(dialog.target || identity, list.id, Boolean(data.membership[list.id]));
      patch(current => ({ membership: applyListToggle(current.membership, list.id, result.hasItem), pending: { ...current.pending, [list.id]: false } }));
    } catch (cause) {
      patch(current => ({ pending: { ...current.pending, [list.id]: false }, errors: { ...current.errors, [list.id]: cause?.message || listLabels.toggleFailed } }));
    }
  }, [dialog.data, dialog.target, identity]);

  const openNotes = useCallback(note => {
    const target = note || (tracking.tracking ? { id: String(tracking.tracking.id), text: tracking.tracking.notes } : null);
    // Notes belong to an entry: an untracked item opens the track dialog instead.
    if (!target || target.id === 'null') { openTrack(); return; }
    setDialog({ kind: 'notes', busy: false, error: '', data: target });
  }, [tracking.tracking, openTrack]);

  const saveNotes = useCallback(async text => {
    setDialog(current => ({ ...current, busy: true, error: '' }));
    try {
      await saveEntryNotes(identity, dialog.data.id, text);
      closeDialog();
      reload();
    } catch (cause) {
      setDialog(current => ({ ...current, busy: false, error: cause?.message || 'The notes could not be saved.' }));
    }
  }, [closeDialog, dialog.data, identity, reload]);

  const removePlay = useCallback(async entry => {
    try {
      await deleteHistoryEntry(dialog.target || identity, entry.id);
      reload();
    } catch (cause) {
      setNotice(cause?.message || 'The play could not be removed.');
    }
  }, [dialog.target, identity, reload]);

  const sendSeerr = useCallback(async seasons => {
    setDialog(current => ({ ...current, busy: true, error: '' }));
    try {
      const state = await requestSeerr(identity, seasons);
      setSeerr({ available: true, state });
      setDialog({ kind: 'seerr', busy: false, error: '', data: state });
    } catch (cause) {
      setDialog(current => ({ ...current, busy: false, error: cause?.message || 'The request could not be sent.' }));
    }
  }, [identity]);

  const runSync = useCallback(async () => {
    setNotice('Syncing metadata…');
    try {
      await syncMetadata(identity);
      setNotice('Metadata synced successfully.');
      reload();
    } catch (cause) {
      setNotice(cause?.message || 'Metadata could not be synced.');
    }
  }, [identity, reload]);

  const rateEpisode = useCallback(async (child, score) => {
    try {
      await saveScore(child.identity, score == null ? null : score * 10 / (child.scaleMax || 10));
      loadEpisodes();
    } catch (cause) {
      setEpisodes(current => ({ ...current, error: cause?.message || 'The score could not be saved.' }));
    }
  }, [loadEpisodes]);

  function openCopyDialog(target = identity, itemTitle = title) {
    setCopy({ open: true, target, title: itemTitle, context: null, values: emptyCopyValues(copyFieldsFor(target.mediaType)), errors: {}, busy: false });
    loadCollectionEntries(target).then(loaded => {
      setCopy(current => current.open && current.target === target ? { ...current, context: loaded.context } : current);
    }).catch(() => {
      setCopy(current => current.open && current.target === target ? { ...current, context: { error: true } } : current);
    });
  }

  async function saveCopy() {
    if (copy.busy) return;
    setCopy(current => ({ ...current, busy: true, errors: {} }));
    try {
      const transport = createCollectionTransport();
      if (copy.editingId && copy.editingKind === 'season') {
        await transport.updateSeason({ seasonId: copy.editingId, values: copy.values, initial: copy.initialValues });
      } else if (copy.editingId) {
        await transport.updateCopy({ entryId: copy.editingId, values: copy.values, initial: copy.initialValues });
      } else {
        await transport.addCopy({ address: collectionAddressForIdentity(copy.target), values: copy.values });
      }
      const loaded = await loadCollectionEntries(identity);
      setCollection({ status: 'ready', ...loaded, error: '' });
      setCopy({ open: false, values: {}, errors: {}, busy: false });
      reload();
    } catch (cause) {
      setCopy(current => ({ ...current, busy: false, errors: { form: cause?.message || 'The copy could not be saved.' } }));
    }
  }

  function editCollectionCopy(entry) {
    const values = copyValuesFromCopy(entry);
    setCopy(current => ({ ...current, editingId: entry.id, editingKind: entry.kind, editingLabel: entry.label, values, initialValues: values, errors: {} }));
  }

  function removeCollectionCopy(entry) {
    Alert.alert(entry.kind === 'season' ? 'Remove season?' : 'Remove copy?', entry.kind === 'season' ? 'This removes all collected episode entries for this season.' : 'This removes the selected collection entry.', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Remove', style: 'destructive', onPress: async () => {
        setCopy(current => ({ ...current, busy: true, errors: {} }));
        try {
          const transport = createCollectionTransport();
          if (entry.kind === 'season') await transport.removeSeason({ seasonId: entry.id });
          else await transport.removeCopy({ entryId: entry.id });
          const [parent, target] = await Promise.all([loadCollectionEntries(identity), loadCollectionEntries(copy.target)]);
          setCollection({ status: 'ready', ...parent, error: '' });
          setCopy(current => ({ ...current, context: target.context, busy: false,
            ...(current.editingId === entry.id ? { editingId: null, values: emptyCopyValues(copyFieldsFor(current.target.mediaType)), initialValues: {} } : {}) }));
          reload();
        } catch (cause) {
          setCopy(current => ({ ...current, busy: false, errors: { form: cause?.message || 'The copy could not be removed.' } }));
        }
      } },
    ]);
  }

  const rawCopies = copy.context?.copies || [];
  const mapCopy = entry => ({
    id: String(entry.id),
    label: entry.item__season_number == null ? (entry.item__title || 'Copy')
      : `S${String(entry.item__season_number).padStart(2, '0')}${entry.item__episode_number == null ? '' : `E${String(entry.item__episode_number).padStart(2, '0')}`} · ${entry.item__title}`,
    mediaType: entry.media_type || '', resolution: entry.resolution || '', hdr: entry.hdr || '',
    is3d: !!entry.is_3d, audioCodec: entry.audio_codec || '', audioChannels: entry.audio_channels || '',
    bitrate: entry.bitrate == null ? '' : String(entry.bitrate),
    purchasePrice: entry.purchase_price == null ? '' : String(entry.purchase_price),
    purchaseLocation: entry.purchase_location || '', collectedAt: (entry.collected_at || '').slice(0, 10),
  });
  const auditKind = copy.context?.auditKind;
  const auditRows = copy.context?.auditEntries || [];
  const collectionCopies = auditKind === 'seasons' && auditRows.length
    ? auditRows.map(row => {
      const metadata = row.metadata || {};
      const episodes = collectionSeasonEpisodes(row.episodes || []);
      return { ...mapCopy(metadata), id: String(row.id), kind: 'season', label: row.title,
        sourceLabels: row.source_labels, qualityLabel: row.quality_label, collectedAt: '', episodes };
    })
    : auditKind === 'episodes' && auditRows.length
      ? auditRows.map(row => {
        const raw = rawCopies.find(entry => entry.id === row.id);
        return raw ? { ...mapCopy(raw), label: row.title, sourceLabels: row.source_labels, qualityLabel: row.quality_label } : null;
      }).filter(Boolean)
      : rawCopies.filter(entry => entry.item__media_type === copy.target?.mediaType).map(mapCopy);


  if (!enabled) return { enabled: false };

  const status = tracking.tracking?.status || '';
  const statusText = status.replace('In progress', 'In Progress');
  const offersNewEntry = tracking.tracked && (
    ADD_ENTRY_ALWAYS.has(identity.mediaType)
    || (ADD_ENTRY_WHEN_COMPLETED.has(identity.mediaType) && status === 'Completed'));
  const runCheckinAction = async action => {
    try {
      const next = await runCheckIn(identity, action.key);
      setCheckin(next?.actions || []);
      if (action.then === 'edit') openTrack(false);
      else reload();
    } catch (cause) {
      setNotice(cause?.message || 'The check-in could not be updated.');
    }
  };
  const trackActions = checkin.map(action => ({
    key: action.key,
    label: action.key === 'edit' ? (statusText || action.label) : action.label,
    icon: action.icon,
    onPress: () => {
      if (action.key === 'edit') openTrack(false);
      else if (action.key === 'create') openTrack(true);
      else runCheckinAction(action);
    },
  }));
  const trackRest = trackActions.slice(1);
  const openKind = kind => () => setDialog({ ...NO_DIALOG, kind });
  const historyItem = { key: 'history', label: 'View history', icon: 'history', onPress: openKind('history') };
  const canPickCover = tracking.tracked && tracking.catalogItemId && !isEpisode;
  // Keep cover/edition tools available without displacing the PWA's quick actions.
  const extraItems = [
    canPickCover ? { key: 'cover', label: 'Change cover', icon: 'image', onPress: openKind('cover') } : null,
    canPickCover && identity.source === 'hardcover' && identity.mediaType === 'book'
      ? { key: 'edition', label: 'Edition', icon: 'book', onPress: openKind('edition') } : null,
  ].filter(Boolean);
  const linkSections = model?.linkSections?.filter(section => section.entries?.some(entry => entry.url)) || [];
  const links = linkSections.length ? linkSections : tracking.sourceUrl
    ? [{ title: 'Source', entries: [{ label: tracking.sourceName || 'Source', url: tracking.sourceUrl }] }]
    : [];
  const sourceItem = links.length ? {
    key: 'source',
    label: 'Links',
    icon: 'link',
    onPress: openKind('links'),
  } : null;
  const quickItems = [
    sourceItem,
    { key: 'tags', label: 'Tags', icon: 'tag', onPress: () => openTags() },
    { key: 'notes', label: 'Notes', icon: 'page', onPress: () => openNotes() },
    { key: 'lists', label: 'Add to list', icon: 'listAdd', onPress: () => openLists() },
    (model?.seerrConfigured ?? seerr.available) ? { key: 'seerr', label: 'Request in Seerr', icon: 'download', onPress: () => {
      setDialog({ ...NO_DIALOG, kind: 'seerr', data: seerr.state });
      if (!seerr.state) loadSeerr(identity).then(state => {
        setSeerr({ available: Boolean(state), state });
        setDialog(current => current.kind === 'seerr' ? { ...current, data: state } : current);
      }).catch(cause => setDialog(current => current.kind === 'seerr'
        ? { ...current, error: cause?.message || 'Seerr could not be loaded.' } : current));
    } } : null,
    historyItem,
    identity.source !== 'manual' ? { key: 'sync', label: 'Sync metadata', icon: 'sync', onPress: runSync } : null,
  ].filter(Boolean);
  // PWA keeps four quick actions plus More below 400px; wider screens expose them.
  const overflowItems = width < 400 && quickItems.length > 5 ? quickItems.slice(4) : [];
  const moreItems = [...overflowItems, ...extraItems];

  const actions = isEpisode ? {
    primary: {
      label: tracking.unavailable ? 'Tracking unavailable' : tracking.plays > 0 ? 'Watched' : 'Mark Watched',
      disabled: Boolean(tracking.unavailable),
      icon: tracking.plays > 0 ? 'eye' : 'eyeClosed',
      onPress: () => openTrack(false),
    },
    quick: [
      { key: 'notes', label: 'Add or edit notes', icon: 'page', onPress: () => openNotes() },
      { key: 'lists', label: 'Add to custom lists', icon: 'listAdd', onPress: () => openLists() },
      { key: 'history', label: 'View your activity history', icon: 'history', onPress: openKind('history') },
    ],
  } : {
    primary: trackActions.length ? {
      label: trackActions[0].label,
      icon: trackActions[0].icon,
      onPress: trackActions[0].onPress,
    } : {
      label: tracking.unavailable ? 'Tracking unavailable' : !tracking.tracked ? 'Add to tracker' : (offersNewEntry ? 'Add new entry' : (statusText || 'Edit')),
      disabled: Boolean(tracking.unavailable),
      icon: tracking.tracked && !offersNewEntry ? 'edit' : 'plus',
      onPress: () => openTrack(Boolean(offersNewEntry)),
    },
    edit: trackActions.length
      ? (trackRest.length > 1
        ? { label: 'More tracking actions', icon: 'chevronDown', onPress: openKind('trackmenu') }
        : trackRest.length === 1
          ? { label: trackRest[0].key === 'edit' ? `Edit entry, ${status}` : trackRest[0].label, icon: trackRest[0].icon, onPress: trackRest[0].onPress }
          : null)
      : offersNewEntry ? { label: `Edit entry, ${status}`, onPress: () => openTrack(false) } : null,
    quick: [
      ...(overflowItems.length ? quickItems.slice(0, 4) : quickItems),
      moreItems.length ? { key: 'more', label: 'More', icon: 'ellipsis', onPress: openKind('more') } : null,
    ].filter(Boolean),
  };

  const chips = mergeScoreChips((scores.chips || []).filter(row => row.key === 'provider').map(row => ({
    key: 'provider',
    label: String(row.label || '').toUpperCase(),
    value: row.rating == null ? '-' : Number(row.rating).toFixed(1),
    count: row.count == null ? '' : `${Number(row.count).toLocaleString('en-US')} ${Number(row.count) === 1 ? 'vote' : 'votes'}`,
    graph: 'provider',
    logo: row.logo_src || row.logo || model?.scoreChips?.find(chip => chip.key === 'provider')?.logo_src || '',
  })), scores);
  const viewerRating = model?.viewerRating;
  const ratingSlot = viewerRating?.editable ? <RatingChip
    value={viewerRating.value}
    scaleMax={viewerRating.scaleMax || 10}
    displayValue={viewerRating.displayValue}
    cardDisplayValue={viewerRating.cardDisplayValue ?? (viewerRating.value == null ? null : Number(viewerRating.value).toFixed(1))}
    derived={viewerRating.derived}
    layout={isEpisode ? 'pill' : 'card'}
    palette={ratingPalette}
    renderStar={RatingStarIcon}
    open={ratingOpen}
    onOpenChange={setRatingOpen}
    onSelect={async score => {
      try {
        await saveScore(identity, score == null ? null : score * 10 / (viewerRating.scaleMax || 10));
        reload();
      } catch (cause) {
        setNotice(cause?.message || 'The rating could not be saved.');
      }
    }} /> : null;
  const progressSlot = progress.data ? <ProgressCard
    model={progress.data.card}
    variant="detail"
    labels={progress.data.model?.labels}
    palette={progressDetailPalette}
    onOpen={() => setProgress(current => ({ ...current, open: true }))} /> : null;

  const at = isEpisode ? siblings.findIndex(row => Number(row.number) === Number(identity.episodeNumber)) : -1;
  // Seasons and episodes open as the show's own detail types, as the page payload names them.
  const openChild = child => {
    const target = child.identity || {};
    const seasoned = target.mediaType === 'season' || target.mediaType === 'episode';
    onOpenItem?.({ identity: seasoned ? { ...target, mediaType: target.libraryMediaType === 'anime' ? 'anime' : 'tv', detailType: target.mediaType } : target });
  };

  const collectionAddress = collectionAddressForIdentity(identity);
  // The episode page (web episode_details): its title line and episode picker.
  const currentEpisode = at >= 0 ? siblings[at] : null;
  const headerSlot = isEpisode ? <DetailEpisodeHeader
    showStill={false}
    episode={{
      title,
      seasonTitle: `Season ${identity.seasonNumber}`,
      numberLabel: `Ep ${identity.episodeNumber}`,
      airDate: currentEpisode?.dateLabel || '',
      runtime: currentEpisode?.runtime || '',
      watchedText: tracking.plays ? (tracking.plays === 1 ? 'Watched once' : `Watched ${tracking.plays} times`) : '',
    }}
    selector={siblings.length ? {
      items: siblings.map(row => ({
        key: row.key,
        label: `${row.number}. ${row.title}`,
        current: Number(row.number) === Number(identity.episodeNumber),
        identity: row.identity,
      })),
      previous: siblings[at - 1],
      next: siblings[at + 1],
      onOpen: openChild,
    } : null}
    labels={{ selectEpisode: 'Select episode', previousEpisode: 'Previous episode', nextEpisode: 'Next episode' }}
    palette={palette}
    onOpenSeason={() => onOpenItem?.({ identity: { mediaType: identity.libraryMediaType === 'anime' ? 'anime' : 'tv', detailType: 'season', source: identity.source, mediaId: identity.mediaId, seasonNumber: identity.seasonNumber } })}
  /> : null;
  const collectionSlot = isEpisode ? null : <DetailBlock title="Collection" palette={palette}
      action={collectionAddress ? { label: 'Add to collection', onPress: () => openCopyDialog() } : null}>
      {collection.status === 'loading' ? <DetailLoading text="Loading" palette={palette} /> : null}
      {collection.status === 'error' ? <DetailEmpty text={collection.error} palette={palette} /> : null}
      {collection.status === 'ready' ? <DetailCollectionBlock counts={collection.counts}
        entries={collection.entries} empty="No collection data available" palette={palette} /> : null}
    </DetailBlock>;
  const notesSlot = tracking.notes.length ? <DetailNotesBlock title="Your Notes" notes={tracking.notes} editLabel="Edit notes"
    onEdit={openNotes} palette={palette} /> : null;
  const noticeSlot = notice ? <Text accessibilityLiveRegion="polite" style={styles.notice}>{notice}</Text> : null;
  const historySlot = ['tv', 'season'].includes(identity.mediaType) && model.trackingEntries?.count > 1
    ? <DetailTrackingHistory entries={model.trackingEntries.entries.slice(1)} count={model.trackingEntries.count}
      hasMore={model.trackingEntries.hasMore} title="History" repeatsLabel="Repeats"
      onEdit={entry => openTrack(false, entry.identity)}
      onOpenHistory={() => setDialog({ ...NO_DIALOG, kind: 'history' })} palette={palette} /> : null;
  const sectionSlots = { collection: collectionSlot, notes: notesSlot, history: historySlot };
  const sectionsSlot = <>{collectionSlot}{notesSlot}{noticeSlot}</>;

  const sortedEpisodes = [...episodes.rows]
    .sort((left, right) => (episodes.order === 'desc' ? -1 : 1) * ((Number(left.number) || 0) - (Number(right.number) || 0)))
    .map(row => ({ ...row, actions: isAnime ? [{ key: 'track', label: 'Track Episode' }] : [
      row.canWatch ? { key: row.watched ? 'unwatch' : 'watch', label: row.watched ? 'Mark unwatched' : 'Mark watched' } : null,
      row.canCollect ? { key: 'collection', label: row.collected ? 'Collected' : 'Uncollected' } : null,
      row.canList ? { key: 'lists', label: 'Add to list' } : null,
      { key: 'history', label: 'View history' },
    ].filter(Boolean) }));
  // The web show page lists its seasons as titled poster cards, like its related grids.
  const seasonCards = identity.mediaType === 'tv' ? (model?.children?.seasons || []).map(season => ({
    key: season.key,
    title: season.number === 0 ? 'Specials' : `Season ${season.number}`,
    image: season.image?.src || '',
    date: season.dateLabel,
    card: season.card,
    identity: season.identity,
  })) : [];
  const seasonsSlot = seasonCards.length ? <WindowedRail title="Seasons" items={seasonCards}
    itemWidth={shelfWidth} palette={palette}
    renderItem={season => <MediaCard width={shelfWidth} palette={palette.mediaCard || palette}
      model={{ ...season.card, title: season.title, image: { src: season.image },
        lines: season.date ? [{ parts: [{ text: String(season.date).slice(0, 4) }] }] : [] }}
      onOpen={() => openChild(season)} onOpenLink={href => onOpenItem?.({ href })} />} /> : null;
  const issuesSlot = isComic && (issues.rows.length || issues.status === 'loading') ? <DetailIssueCards
    rows={issues.rows} total={issues.total} loading={issues.status === 'loading'} error={issues.error}
    layout={issues.layout} onLayout={layout => setIssues(current => ({ ...current, layout }))}
    onOpen={openChild} onTrack={issue => openTrack(false, issue.identity)}
    onLists={issue => openLists(issue.identity, issue.title)}
    onMore={() => loadMoreIssues(false)} palette={palette} />
    : null;
  const episodesSlot = (isAnime || isSeason) && (episodes.rows.length || episodes.error) ? <View style={styles.children}>
      {episodes.error ? <View style={styles.errorRow}>
        <Text style={[styles.dialogText, { color: palette.danger }]}>{episodes.error}</Text>
        <Button label="Try again" tone="surface" palette={palette.button} onPress={() => loadEpisodes()} />
      </View> : null}
      <DetailEpisodeCards
        nativeAnime={isAnime}
        loadMore={episodes.loadMore}
        onMore={() => loadEpisodes(true)}
        loadingMore={episodes.status === 'loading'}
        title="Episodes"
        count={episodes.total || sortedEpisodes.length}
        order={episodes.order}
        orderLabel={episodes.order === 'desc' ? 'Newest first' : 'Oldest first'}
        onOrderChange={() => setEpisodes(current => ({ ...current, order: current.order === 'asc' ? 'desc' : 'asc' }))}
        episodes={sortedEpisodes}
        labels={{ rate: 'Rate' }}
        palette={palette}
        renderRating={renderRating}
        onOpen={openChild}
        onAction={async (key, child) => {
          if (key === 'track') { openTrack(false, child.identity); return; }
          if (key === 'lists') { openLists(child.identity, child.title); return; }
          if (key === 'collection') { openCopyDialog(child.identity, child.title); return; }
          if (key === 'history') {
            setDialog({ ...NO_DIALOG, kind: 'history', target: child.identity });
            return;
          }
          if (!['watch', 'unwatch'].includes(key)) return;
          try {
            await (key === 'unwatch' ? unwatchEpisode(child.identity) : watchEpisode(child.identity));
            reload();
          } catch (cause) {
            setEpisodes(current => ({ ...current, error: cause?.message || 'The episode could not be updated.' }));
          }
        }}
        onRate={rateEpisode} />
    </View> : null;

  const childSlots = { seasons: seasonsSlot, issues: issuesSlot, episodes: episodesSlot };
  const childrenSlot = seasonsSlot || issuesSlot || episodesSlot;

  const overlays = <>
    <AdaptivePresentationSheet isPresented={track.open} onDismiss={() => setTrack(EMPTY_TRACK)}
      onClose={() => setTrack(EMPTY_TRACK)} title="Tracking" titleColor={formPalette.text} containerColor={themePalette.sidebar}>
    <View style={{ flex: 1, paddingHorizontal: 16 }}>
    <TrackDialog
      open={track.open}
      embedded
      model={track.status === 'ready' ? track.model : null}
      values={track.values}
      status={track.status}
      serverError={track.serverError}
      busy={track.busy}
      palette={formPalette}
      tabs={track.tabs}
      onChange={(name, value) => setTrack(current => ({ ...current, values: { ...current.values, [name]: value } }))}
      onSubmit={submitTrack}
      onDelete={track.tracked ? removeTrack : undefined}
      onClose={() => setTrack(EMPTY_TRACK)}
      onRetry={() => openTrack(false, track.identity)}
      onRetryTabs={retryTabs}
      onTagToggle={toggleTrackTag}
      onTagCreate={createTrackTag}
      onArtworkSave={async imageUrl => {
        if (!track.identity || !imageUrl) return;
        await saveArtwork(track.identity, imageUrl);
        reload();
      }}
      onDiscoverToggle={(itemId, hidden) => setDiscoverHidden(itemId, hidden)} />
    </View>
    </AdaptivePresentationSheet>
    <AdaptivePresentationSheet isPresented={dialog.kind === 'tags'} onDismiss={closeDialog}
      onClose={closeDialog} title="Tags" titleColor={formPalette.text} containerColor={themePalette.sidebar}>
    <View style={{ flex: 1, paddingHorizontal: 16 }}>
    <TagAssignmentDialog
      open={dialog.kind === 'tags'}
      embedded showClose={false}
      item={{ title }}
      tags={dialog.kind === 'tags' ? dialog.data || [] : []}
      palette={formPalette}
      busy={dialog.busy}
      error={dialog.kind === 'tags' ? dialog.error : ''}
      onToggle={toggleDialogTag}
      onCreate={createDialogTag}
      onClose={closeDialog} />
    </View>
    </AdaptivePresentationSheet>
    <MediaCardActionHost open={dialog.kind === 'links'} action={{ title: 'Links' }} palette={formPalette} onClose={closeDialog}>
      {links.map(section => <View key={section.title} style={{ marginBottom: 16 }}>
        <Text style={{ color: formPalette.textMuted, fontSize: 12, fontWeight: '600', marginBottom: 8 }}>{section.title}</Text>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
          {section.entries.filter(entry => entry.url).map(entry => <Pressable key={entry.url} accessibilityRole="link"
            onPress={() => { closeDialog(); onOpenExternal?.(entry.url); }}
            style={{ backgroundColor: palette.surfaceStrong, borderColor: palette.border, borderWidth: 1, borderRadius: 20, paddingHorizontal: 14, paddingVertical: 8 }}>
            <Text style={{ color: formPalette.text }}>{entry.label}</Text>
          </Pressable>)}
        </View>
      </View>)}
    </MediaCardActionHost>
    <MediaCardActionHost open={dialog.kind === 'lists'} action={{ title: 'Lists' }} palette={formPalette} onClose={closeDialog}>
      {dialog.kind === 'lists' && dialog.data ? <ListsForm
        model={dialog.data.model}
        membership={dialog.data.membership}
        tag=""
        status={dialog.data.status}
        pending={dialog.data.pending}
        errors={dialog.data.errors}
        labels={listLabels}
        palette={formPalette}
        showHeader={false}
        onTagChange={() => {}}
        onRetry={() => openLists(dialog.target || identity)}
        onClose={closeDialog}
        onToggle={toggleListMembership} /> : null}
    </MediaCardActionHost>
    {progress.data ? <ProgressDialog
      visible={progress.open}
      title={title}
      model={progress.data.model}
      labels={progress.data.model?.labels}
      palette={progressDetailPalette}
      onClose={() => setProgress(current => ({ ...current, open: false }))}
      onOpenSeason={href => { setProgress(current => ({ ...current, open: false })); navigate?.('Detail', { href }); }}
      onOpenEpisode={href => { setProgress(current => ({ ...current, open: false })); navigate?.('Detail', { href }); }} /> : null}
    <AdaptivePresentationSheet isPresented={copy.open}
      onDismiss={() => setCopy(current => ({ ...current, open: false }))}
      onClose={() => setCopy(current => ({ ...current, open: false }))}
      title={copy.editingId ? `Edit ${copy.editingLabel || copy.title}` : copy.title} titleColor={formPalette.text} containerColor={themePalette.sidebar}>
    <CollectionCopyDialog
      embedded
      visible={copy.open}
      item={copy.open ? { title: copy.title, mediaType: copy.target?.mediaType } : null}
      context={copy.editingId ? undefined : copy.context}
      showExisting={collectionCopies.length > 0 && !copy.editingId}
      labels={{ existing: auditKind === 'seasons' && auditRows.length ? 'Collected seasons' : auditKind === 'episodes' && auditRows.length ? 'Collected episodes' : 'Collected items' }}
      copies={collectionCopies}
      copyLimit={5}
      editingId={copy.editingId}
      onEdit={editCollectionCopy}
      onRemove={removeCollectionCopy}
      fields={copyFieldsFor(copy.target?.mediaType).filter(field => copy.editingKind !== 'season' || !['purchasePrice', 'purchaseLocation', 'collectedAt'].includes(field.name))}
      values={copy.values}
      errors={copy.errors}
      palette={palette}
      canMutate
      busy={copy.busy}
      onClose={() => setCopy(current => ({ ...current, open: false }))}
      onChange={(name, value) => setCopy(current => ({
        ...current,
        values: { ...current.values, [name]: value },
        errors: { ...current.errors, [name]: '' },
      }))}
      onSave={saveCopy} />
    </AdaptivePresentationSheet>
    <MediaCardActionHost open={dialog.kind === 'more'} action={{ title }} palette={palette} onClose={closeDialog}>
      {moreItems.map(item => <Pressable key={item.key} accessibilityRole="button"
        onPress={() => { closeDialog(); item.onPress(); }} style={styles.moreRow}>
        <Icon spec={actionIcons[item.icon]} size={16} color={palette.textSecondary} />
        <Text style={[styles.dialogText, { color: palette.text }]}>{item.label}</Text>
      </Pressable>)}
    </MediaCardActionHost>
    <MediaCardActionHost open={dialog.kind === 'trackmenu'} action={{ title }} palette={palette} onClose={closeDialog}>
      {trackRest.map(item => <Pressable key={item.key} accessibilityRole="button"
        onPress={() => { closeDialog(); item.onPress(); }} style={styles.moreRow}>
        <Text style={[styles.dialogText, { color: palette.text }]}>{item.label}</Text>
      </Pressable>)}
    </MediaCardActionHost>
    <CoverDialog open={dialog.kind === 'cover'} itemId={tracking.catalogItemId}
      onSaved={() => { closeDialog(); reload(); }} onClose={closeDialog} />
    <EditionDialog open={dialog.kind === 'edition'} itemId={tracking.catalogItemId}
      onSaved={() => { closeDialog(); reload(); }} onClose={closeDialog} />
    <NotesDialog open={dialog.kind === 'notes'} note={dialog.data} busy={dialog.busy}
      error={dialog.kind === 'notes' ? dialog.error : ''} onSave={saveNotes} onClose={closeDialog} />
    <PlaysDialog open={dialog.kind === 'history'} identity={dialog.target || identity} entries={dialog.target ? [] : tracking.history}
      onRemove={removePlay} onClose={closeDialog} />
    <SeerrDialog open={dialog.kind === 'seerr'} state={dialog.kind === 'seerr' ? dialog.data : null}
      busy={dialog.busy} error={dialog.kind === 'seerr' ? dialog.error : ''}
      onRequest={sendSeerr} onClose={closeDialog} />
  </>;

  return {
    enabled: true,
    headerReady: headerReadyKey === identityKey,
    pageType: isSeason ? 'tv' : isEpisode ? null : identity.mediaType,
    headerSlot,
    actionsSlot: <DetailActionBar primary={actions.primary} edit={actions.edit} quick={actions.quick} palette={palette} inlineAt={isEpisode ? 640 : 768} />,
    ratingInScoreRow: Boolean(ratingSlot),
    scoreSlot: chips.length || progressSlot || ratingSlot
      ? <DetailScoreRow chips={chips} graphs={scores.graphs} onOpenLink={onOpenExternal}
        progressSlot={progressSlot} palette={palette} compact={isEpisode}>{ratingSlot}</DetailScoreRow> : null,
    sectionSlots,
    childSlots,
    noticeSlot,
    sectionsSlot,
    childrenSlot,
    overlays,
    // The page payload's own Collection and notes sections give way to the editable ones.
    replacedSections: ['collection', 'notes'],
    replacesChildren: Boolean(childrenSlot),
  };
}

const styles = StyleSheet.create({
  children: { gap: spacing.sm },
  moreRow: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 12 },
  errorRow: { gap: spacing.sm },
  dialogText: { color: palette.textSecondary, fontSize: 14, lineHeight: 20 },
  dialogActions: { flexDirection: 'row', justifyContent: 'flex-end', gap: spacing.sm },
  dialogScroll: { maxHeight: 360 },
  notesInput: {
    minHeight: 140,
    borderWidth: 1,
    borderRadius: 8,
    borderColor: palette.border,
    backgroundColor: palette.surfaceStrong,
    color: palette.text,
    padding: spacing.md,
    fontSize: 15,
  },
  dayGroup: { gap: spacing.sm, marginBottom: spacing.md },
  dayHeading: { fontWeight: '700', fontSize: 16 },
  dayEntry: { borderRadius: 8, padding: spacing.sm, gap: 2 },
  playRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingVertical: spacing.sm },
  playBody: { flex: 1, minWidth: 0 },
  playMeta: { color: palette.textMuted, fontSize: 12 },
  notice: { color: palette.textMuted, fontSize: 13 },
});
