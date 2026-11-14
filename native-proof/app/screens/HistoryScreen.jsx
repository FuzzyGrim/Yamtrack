import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useWindowDimensions } from 'react-native';
import {
  HistoryActivityDialog,
  HistoryDeleteDialog,
  HistoryEditDialog,
  HistoryFeed,
  HistoryFilterFab,
} from '../../../frontend/ui/HistoryFeed.native.jsx';
import { HistoryView } from '../../../frontend/pages/HistoryView';
import { Screen } from '../components/Screen';
import {
  createHistoryTransport,
  historyDaysFromPayload,
  historyMonthNav,
  historySavedViewQuery,
  monthBounds,
  monthKey,
  parseHistoryFilterUrl,
  shiftMonth,
} from '../data/historyTransport';
import { useAccountDisplay } from '../data/accountDisplay';
import { getActiveAuthSession } from '../data/authSession';
import { createListTransport } from '../data/listTransport';
import { listsFromPayload } from '../data/listTransportCore';
import { DiscoverListPicker } from '../surfaces/DiscoverListPicker';
import { formatUserDate } from '../data/userDate';
import { historyPalette } from '../theme';

export const surface = {
  id: 'HISTORY',
  status: 'in_progress',
  title: 'History feed and activity',
  where: 'History screen and per-item activity, edit and delete dialogs',
  note: 'The shared HistoryView owns the heading, notice order and feed; the native feed renders the same compact day groups and three-column card grid as the web page, with Activity, Edit and Delete as the card actions. Everything goes through /api/v1: the month window and filters load /api/v1/history/, release-date mode loads paged days from /api/v1/history/release/, days over the per-day cap load more from /api/v1/history/days/<day>/, the activity dialog loads every session from /api/v1/history/sessions/, genre options come from /api/v1/history/genres/, edit and delete use /api/v1/history/entries/<type>/<instance_id>/ and Save view posts to /api/v1/history/saved-views/. Signed-out shows a sign-in prompt. Browser, iOS and Android runtime interaction remain unverified, so the surface stays in progress.',
};

const SERVER_PAGE_DAYS = 31;
const SERVER_DAY_ENTRIES = 30;

const labels = {
  mediaType: 'Media type',
  filters: 'Filter history',
  impliedGenre: 'Implied Genre',
  edit: 'Edit',
  delete: 'Delete',
};

const signedOutLabels = {
  noHistory: 'Sign in to see your history',
  noHistoryCopy: 'Connect your Floppy server to load your watch history.',
};

// A phone-width compact grid prints the title only, like the web card; wider
// layouts add the verb and date line the web card shows under the title.
function decorate(days, mode, captions) {
  return days.map(day => ({
    ...day,
    dateLabel: formatUserDate(day.date) || day.dateLabel,
    entries: day.entries.map(entry => {
      const base = mode === 'release'
        ? { ...entry, verb: 'Released', when: formatUserDate(entry.date), canViewActivity: false, canEdit: false, canDelete: false }
        : { ...entry, when: formatUserDate(entry.whenValue || entry.date) };
      return captions ? base : { ...base, verb: '', when: '' };
    }),
  }));
}

function filterParams(filters, queryFrom, queryTo, scope) {
  const params = {};
  if (scope && Object.keys(scope).length) params.scope = scope;
  if (queryFrom) params.from = queryFrom;
  if (queryTo) params.to = queryTo;
  if (filters.mediaTypes?.length) params.mediaTypes = filters.mediaTypes;
  if (filters.genres?.length) params.genre = filters.genres.join(',');
  if (filters.impliedGenres?.length) params.impliedGenre = filters.impliedGenres.join(',');
  return params;
}

// The web page body starts 24px below the header.
const pageTop = { paddingTop: 24 };

// The last History pages loaded for the signed-in session. Coming back to History shows
// them at once while the fresh copy loads, instead of an empty screen each visit.
const historyCaches = new WeakMap();
const NO_SESSION = {};
function sessionCache() {
  const key = getActiveAuthSession() || NO_SESSION;
  if (!historyCaches.has(key)) historyCaches.set(key, new Map());
  return historyCaches.get(key);
}

export default function HistoryScreen({ navigate, onBack, params, transport: injectedTransport }) {
  const [transport] = useState(() => injectedTransport || createHistoryTransport());
  // A link into History (a person's plays, an album) scopes the whole feed.
  const [scope] = useState(() => parseHistoryFilterUrl(params?.filters));
  const server = transport.mode === 'server';
  const [transportError, setTransportError] = useState('');
  const [loadError, setLoadError] = useState('');
  const [rawDays, setRawDays] = useState([]);
  const [monthCursor, setMonthCursor] = useState(() => monthKey());
  const [filters, setFilters] = useState({ mode: 'activity', mediaTypes: [], genres: [], impliedGenres: [], activeCount: 0 });
  const [genreOptions, setGenreOptions] = useState({ genres: [], impliedGenres: [] });
  const [genreOptionsLoaded, setGenreOptionsLoaded] = useState(false);
  const [refreshing, setRefreshing] = useState(server);
  const [refreshError, setRefreshError] = useState(false);
  const [loading, setLoading] = useState(server);
  const [reloadToken, setReloadToken] = useState(0);
  const [serverOffset, setServerOffset] = useState(0);
  const [serverTotal, setServerTotal] = useState(null);
  const [activityEntry, setActivityEntry] = useState(null);
  const [editingEntry, setEditingEntry] = useState(null);
  const [deletingEntry, setDeletingEntry] = useState(null);
  const [loadingDays, setLoadingDays] = useState({});
  const [listPicker, setListPicker] = useState(null);
  const daysRef = useRef([]);
  const [cache] = useState(sessionCache);

  useEffect(() => { daysRef.current = rawDays; }, [rawDays]);

  const mode = filters.mode || 'activity';
  const from = filters.from || '';
  const to = filters.to || '';
  const bounds = useMemo(() => monthBounds(monthCursor), [monthCursor]);
  const queryFrom = from || (to ? '' : bounds.from);
  const queryTo = to || (from ? '' : bounds.to);
  const mediaTypeKey = (filters.mediaTypes || []).join(',');
  const genreKey = (filters.genres || []).join(',');
  const impliedGenreKey = (filters.impliedGenres || []).join(',');
  const { width } = useWindowDimensions();
  const display = useAccountDisplay();
  // The server-side card hides the caption line on a compact phone grid.
  const captions = !(width <= 768 && display.compact);
  const [filtersOpen, setFiltersOpen] = useState(false);

  const queryKey = JSON.stringify([mode, mediaTypeKey, genreKey, impliedGenreKey, queryFrom, queryTo, scope, serverOffset]);

  // Before paint, show the saved copy of this query if there is one; the fetch below refreshes it.
  useLayoutEffect(() => {
    const saved = server ? cache.get(queryKey) : null;
    if (!saved) return;
    setRawDays(saved.days);
    setServerTotal(saved.total);
    setLoading(false);
    setRefreshing(true);
  }, [server, cache, queryKey]);

  useEffect(() => {
    if (!server) return undefined;
    let active = true;
    if (!cache.has(queryKey)) setLoading(true);
    setLoadError('');
    setRefreshError(false);
    setTransportError('');
    setServerTotal(null);
    const params = filterParams(
      { mediaTypes: mediaTypeKey ? mediaTypeKey.split(',') : [], genres: genreKey ? genreKey.split(',') : [], impliedGenres: impliedGenreKey ? impliedGenreKey.split(',') : [] },
      queryFrom,
      queryTo,
      scope,
    );
    const request = mode === 'release'
      ? transport.loadRelease({ ...params, limit: SERVER_PAGE_DAYS, offset: serverOffset })
      : transport.load({ ...params, limit: SERVER_PAGE_DAYS, offset: serverOffset, maxEntriesPerDay: SERVER_DAY_ENTRIES });
    request
      .then(payload => {
        if (!active) return;
        const loadedDays = historyDaysFromPayload(payload);
        const total = Number(payload?.pagination?.total);
        const loadedTotal = Number.isFinite(total) ? total : null;
        cache.set(queryKey, { days: loadedDays, total: loadedTotal });
        setRawDays(loadedDays);
        setServerTotal(loadedTotal);
        setLoading(false);
        setRefreshing(false);
      })
      .catch(error => {
        if (!active) return;
        if (daysRef.current.length) {
          setRefreshError(true);
        } else {
          setLoadError(error?.message || 'History could not be loaded.');
        }
        setLoading(false);
        setRefreshing(false);
      });
    return () => { active = false; };
  }, [transport, cache, queryKey, reloadToken, serverOffset, queryFrom, queryTo, mode, mediaTypeKey, genreKey, impliedGenreKey]);

  function beginRefresh() {
    if (!server) return;
    setRefreshError(false);
    setRefreshing(true);
    setReloadToken(current => current + 1);
  }

  function requestOptions() {
    if (!server || genreOptionsLoaded) return;
    transport.loadGenreOptions()
      .then(options => {
        setGenreOptions(options);
        setGenreOptionsLoaded(true);
      })
      .catch(() => {
        // Leave the flag unset so the next panel open retries, matching the
        // web dropdown which clears its lists when this request fails.
      });
  }

  const days = useMemo(() => decorate(
    rawDays.map(day => ({ ...day, loadingMore: Boolean(loadingDays[day.key]) })),
    mode,
    captions,
  ), [rawDays, loadingDays, mode, captions, display.loaded]);

  const pagination = useMemo(() => {
    if (!server || !serverTotal || serverTotal <= SERVER_PAGE_DAYS) return null;
    const pageCount = Math.ceil(serverTotal / SERVER_PAGE_DAYS);
    const page = Math.floor(serverOffset / SERVER_PAGE_DAYS) + 1;
    return {
      page,
      pageCount,
      previousUrl: page > 1 ? `?offset=${Math.max(0, serverOffset - SERVER_PAGE_DAYS)}` : null,
      nextUrl: page < pageCount ? `?offset=${serverOffset + SERVER_PAGE_DAYS}` : null,
    };
  }, [server, serverTotal, serverOffset]);

  const model = useMemo(() => {
    if (!server) {
      // Signed out: no month chrome, just the sign-in prompt.
      return {
        mode: 'activity',
        loading: false,
        error: false,
        refreshing: false,
        refreshError: false,
        summary: [],
        days: [],
        filters,
        options: {},
        nav: {},
      };
    }
    const hasRange = Boolean(from || to);
    return {
      mode,
      loading,
      error: Boolean(loadError),
      refreshing,
      refreshError,
      // The web page shows no summary tiles on the feed.
      summary: [],
      days,
      filters: {
        ...filters,
        rangeLabel: hasRange ? `${from || 'earliest'} to ${to || 'today'}` : null,
      },
      options: {
        genres: genreOptions.genres,
        impliedGenres: genreOptions.impliedGenres,
      },
      nav: hasRange ? {} : historyMonthNav(monthCursor),
      pagination,
    };
  }, [server, days, filters, genreOptions, loadError, loading, monthCursor, mode, pagination, refreshing, refreshError, from, to]);

  function handleNavigate(intent) {
    if (intent?.kind === 'page') {
      setServerOffset(current => Math.max(0, current + (intent.direction || 0) * SERVER_PAGE_DAYS));
    } else if (intent?.kind === 'current') {
      setMonthCursor(monthKey());
      setServerOffset(0);
    } else if (intent?.kind === 'year') {
      setMonthCursor(current => shiftMonth(current, -12));
      setServerOffset(0);
    } else if (intent?.kind === 'month') {
      setMonthCursor(current => shiftMonth(current, intent.direction || 0));
      setServerOffset(0);
    } else {
      return;
    }
    beginRefresh();
  }

  function applyFilters(state) {
    const activeCount = (state.from ? 1 : 0) + (state.to ? 1 : 0)
      + (state.mediaTypes?.length ? 1 : 0) + (state.genres?.length ? 1 : 0)
      + (state.impliedGenres?.length ? 1 : 0) + (state.mode !== 'activity' ? 1 : 0);
    setFilters({ ...state, activeCount });
    setServerOffset(0);
    beginRefresh();
  }

  function clearFilters() {
    setFilters({ mode: 'activity', mediaTypes: [], genres: [], impliedGenres: [], activeCount: 0 });
    setServerOffset(0);
    beginRefresh();
  }

  // Show the tapped entry at once, then replace its one-row session list with
  // every session the server has for the item.
  function openActivity(entry) {
    setActivityEntry(entry);
    transport.loadSessions(entry)
      .then(payload => {
        if (!Array.isArray(payload?.sessions) || !payload.sessions.length) return;
        setActivityEntry(current => (current?.key === entry.key
          ? { ...entry, sessions: payload.sessions }
          : current));
      })
      .catch(() => {
        // The entry's own session stays on screen.
      });
  }

  function requestEdit(entry) {
    setActivityEntry(null);
    setEditingEntry(entry);
  }

  function requestDelete(entry) {
    setActivityEntry(null);
    setEditingEntry(null);
    setDeletingEntry(entry);
  }

  async function saveEntry(entry, { value, score, status }) {
    setEditingEntry(null);
    if (!server || entry.instanceId == null) return;
    const fields = entry.editFields || [];
    try {
      await transport.updateEntry({
        mediaType: entry.mediaType,
        instanceId: entry.instanceId,
        date: value && value !== entry.whenValue ? value : undefined,
        score: fields.includes('score') && score !== entry.score && score !== null ? score : undefined,
        status: fields.includes('status') && status !== (entry.status || '') ? status : undefined,
      });
    } catch (error) {
      setTransportError(error?.message || 'The server rejected the history change.');
      return;
    }
    beginRefresh();
  }

  async function deleteEntry(entry) {
    setDeletingEntry(null);
    if (!server || entry.instanceId == null) return;
    try {
      await transport.deleteEntry({ mediaType: entry.mediaType, instanceId: entry.instanceId });
    } catch (error) {
      setTransportError(error?.message || 'The server rejected the history change.');
      return;
    }
    beginRefresh();
  }

  async function saveView(state, name) {
    try {
      await transport.saveView({ name, query: historySavedViewQuery(state) });
      setTransportError('');
    } catch (error) {
      setTransportError(error?.message || 'The view could not be saved.');
    }
  }

  // A day past the per-day cap keeps its loaded entries and appends the next
  // page from the day route.
  async function loadMore(day) {
    setLoadingDays(current => ({ ...current, [day.key]: true }));
    try {
      const payload = await transport.loadDay({
        dayKey: day.key,
        offset: day.entries.length,
        ...filterParams(filters, '', '', scope),
      });
      const [next] = payload?.day ? historyDaysFromPayload({ results: [payload.day] }) : [];
      if (next) {
        setRawDays(current => current.map(existing => {
          if (existing.key !== day.key) return existing;
          const known = new Set(existing.entries.map(entry => entry.key));
          const entries = [...existing.entries, ...next.entries.filter(entry => !known.has(entry.key))];
          const count = Math.max(next.entryCount, entries.length);
          return { ...existing, entries, entryCount: count, hasMore: count > entries.length, remaining: Math.max(count - entries.length, 0) };
        }));
      }
    } catch (error) {
      setTransportError(error?.message || 'This day could not be loaded.');
    } finally {
      setLoadingDays(current => ({ ...current, [day.key]: false }));
    }
  }

  // Music and podcast entries live on the Music screen, like the web's links.
  function openEntry(item) {
    if (item?.mediaType === 'music' && item.album?.id) {
      navigate('Music', { albumId: item.album.id });
    } else if (item?.mediaType === 'podcast' && item.show?.id) {
      navigate('Music', { showId: item.show.id });
    } else {
      navigate('Detail', { item });
    }
  }

  // "Add to custom lists": the lists the account can edit, then the same PUT
  // the web's list modal sends for the chosen one.
  function openListPicker(entry) {
    const session = getActiveAuthSession();
    const lists = createListTransport(session ? { auth: session } : {});
    setListPicker({ entry, title: entry.title, lists: [], loading: true, error: '', savingId: null });
    lists.load().then(payload => {
      const rows = listsFromPayload(payload, session?.user?.username).filter(list => list.canEdit && !list.isSmart);
      setListPicker(current => (current ? { ...current, lists: rows, loading: false } : current));
    }).catch(cause => {
      setListPicker(current => (current ? { ...current, loading: false, error: cause?.message || 'Your lists could not be loaded.' } : current));
    });
  }

  function chooseList(list) {
    const target = listPicker?.entry;
    const item = target?.item || {};
    const session = getActiveAuthSession();
    setListPicker(current => ({ ...current, savingId: list.id, error: '' }));
    createListTransport(session ? { auth: session } : {}).addItem({
      listId: list.id,
      item: {
        identity: {
          mediaType: item.media_type || target.mediaType,
          source: item.source,
          mediaId: String(item.media_id),
          seasonNumber: item.season_number ?? undefined,
          episodeNumber: item.episode_number ?? undefined,
        },
      },
    }).then(() => {
      setListPicker(null);
      setTransportError('');
    }).catch(cause => {
      setListPicker(current => (current ? { ...current, savingId: null, error: cause?.message || `Could not add "${target.title}" to ${list.name}.` } : current));
    });
  }

  const notice = transportError
    || (server ? '' : transport.reason || 'Sign in to your Floppy server to see your history.');

  function toggleFilters(open) {
    if (open) requestOptions();
    setFiltersOpen(open);
  }

  return <Screen chrome={false} contentStyle={pageTop}
    floating={server ? <HistoryFilterFab count={filters.activeCount} label={labels.filters}
      palette={historyPalette} onPress={() => toggleFilters(true)} /> : null}>
    <HistoryView
      title={null}
      subtitle={null}
      filtersOpen={filtersOpen}
      onFiltersOpenChange={toggleFilters}
      Feed={HistoryFeed}
      model={model}
      labels={server ? labels : { ...labels, ...signedOutLabels }}
      palette={historyPalette}
      notice={notice}
      onNavigate={handleNavigate}
      onRequestOptions={requestOptions}
      onOpenEntry={openEntry}
      onOpenActivity={openActivity}
      onAddToList={openListPicker}
      onEditEntry={requestEdit}
      onDeleteEntry={requestDelete}
      onLoadMore={loadMore}
      onApplyFilters={applyFilters}
      onClearFilters={clearFilters}
      onSaveView={server ? saveView : undefined}
      onRetry={server ? beginRefresh : undefined}
      onRefresh={beginRefresh}>
      <DiscoverListPicker picker={listPicker} onChoose={chooseList} onClose={() => setListPicker(null)} />
      <HistoryActivityDialog
        entry={activityEntry}
        labels={labels}
        palette={historyPalette}
        onClose={() => setActivityEntry(null)}
        onEdit={requestEdit}
        onDelete={requestDelete} />
      <HistoryEditDialog
        entry={editingEntry}
        labels={labels}
        palette={historyPalette}
        onClose={() => setEditingEntry(null)}
        onSave={saveEntry}
        onDelete={requestDelete} />
      <HistoryDeleteDialog
        entry={deletingEntry}
        labels={labels}
        palette={historyPalette}
        onClose={() => setDeletingEntry(null)}
        onConfirm={deleteEntry} />
    </HistoryView>
  </Screen>;
}
