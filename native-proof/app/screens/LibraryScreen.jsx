import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ActivityIndicator, Linking, Pressable, StyleSheet, Text, TextInput, View, useWindowDimensions } from 'react-native';
import { AlphabetBar } from '../../../frontend/ui/AlphabetBar.native';
import { getActiveAuthSession } from '../data/authSession';
import { LibraryPageView } from '../../../frontend/pages/LibraryPageView';
import { FilterBar } from '../../../frontend/ui/FilterBar';
import { Button } from '../../../frontend/ui/Button';
import { Screen } from '../components/Screen';
import { createLibraryTransport, mediaRowsFromPayload } from '../data/libraryTransport';
import { FILTER_MODES, libraryFilterSections, libraryQuery } from '../data/libraryTransportCore';
import { actionIcons } from '../../../frontend/ui/actionIcons';
import { palette, radius, spacing } from '../theme';
import LibraryGrid from '../surfaces/LibraryGrid';
import LibraryBulkActions from '../surfaces/LibraryBulkActions';
import LibrarySettings from '../surfaces/LibrarySettings';
import TableSlot from '../surfaces/TableSlot';

const MEDIA_TYPE_LABELS = {
  movie: 'Movies',
  tv: 'Shows',
  anime: 'Anime',
  book: 'Books',
  comic: 'Comics',
  manga: 'Manga',
  game: 'Games',
  boardgame: 'Board Games',
  music: 'Music',
  podcast: 'Podcasts',
  video: 'Videos',
};

const PAGE_LIMIT = 30;
const STATUSES = [
  { value: 'planning', label: 'Planning' },
  { value: 'in_progress', label: 'In progress' },
  { value: 'paused', label: 'Paused' },
  { value: 'completed', label: 'Completed' },
  { value: 'dropped', label: 'Dropped' },
  { value: 'no_status', label: 'No Status' },
];
const SORTS = [
  { value: 'date_added', label: 'Date added' },
  { value: 'title', label: 'Title' },
  { value: 'score', label: 'Rating' },
  { value: 'release_date', label: 'Release date' },
  { value: 'progress', label: 'Progress' },
  { value: 'end_date', label: 'Last watched' },
  { value: 'start_date', label: 'Start date' },
  { value: 'runtime', label: 'Runtime' },
  { value: 'time_watched', label: 'Time watched' },
  { value: 'plays', label: 'Plays' },
  { value: 'time_to_beat', label: 'Time to beat' },
  { value: 'platform', label: 'Platform' },
  { value: 'author', label: 'Author' },
  { value: 'popularity', label: 'Popularity' },
  { value: 'next_episode_air_date', label: 'Episode air date' },
  { value: 'time_left', label: 'Time left' },
];
const RANGE_UNITS = [
  { value: 'days', label: 'Days' }, { value: 'weeks', label: 'Weeks' },
  { value: 'months', label: 'Months' }, { value: 'years', label: 'Years' },
];
// The A to Z rail the web shows from 768px up: All, #, A-Z.
const RAIL_MIN_WIDTH = 768;
const ALPHABET = [
  { value: '', label: 'All', caption: true, accessibilityLabel: 'All titles' },
  { value: '0-9', label: '#', accessibilityLabel: 'Titles starting with a number' },
  ...'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('').map(letter => ({
    value: letter.toLowerCase(), label: letter, accessibilityLabel: `Titles starting with ${letter}`,
  })),
];
const RAIL_PALETTE = {
  interactiveStyle: {},
  focusStyle: {},
  badgeHover: palette.surfaceMuted,
  selectedText: palette.accent,
  mutedText: palette.textMuted,
  fontFamily: undefined,
  captionSize: 10,
  letterSize: 12,
};
// Web's music Media Type menu (filter_menu.html) lists Artists, Albums and Tracks;
// the podcast library lists shows.
const MUSIC_SUBVIEWS = [['artists', 'Artists'], ['albums', 'Albums'], ['items', 'Tracks']];
const ENTITY_SUBVIEWS = ['artists', 'albums', 'shows'];
const ALL_MATCHING_LIMIT = 200;

// The server stores the status filter by status name; the query names them in lower case.
const STORED_STATUS = { planning: 'Planning', in_progress: 'In progress', paused: 'Paused', completed: 'Completed', dropped: 'Dropped' };
const QUERY_STATUS = Object.fromEntries(Object.entries(STORED_STATUS).map(([query, stored]) => [stored, query]));

function defaultSubview(mediaType) {
  if (mediaType === 'comic') return 'comics';
  if (mediaType === 'music') return 'artists';
  if (mediaType === 'podcast') return 'shows';
  return 'items';
}

const buttonPalette = {
  primary: { background: palette.accentStrong, pressed: '#4338ca', hovered: '#4338ca', text: '#ffffff', border: 'transparent' },
  surface: { background: palette.surfaceStrong, pressed: palette.surfaceMuted, hovered: palette.surfaceMuted, text: palette.text, border: palette.border },
  radius: radius.sm,
  focusStyle: {},
};

const labels = {
  loading: 'Loading your library…',
  retry: 'Try again',
  emptyTitle: 'Nothing here yet',
  emptyAction: 'Browse Discover',
};

const BASE_FILTERS = mediaType => ({
  mediaTypes: mediaType && mediaType !== 'all' ? [mediaType] : [],
  statuses: [], sort: 'date_added', direction: 'desc', view: 'grid',
  ranges: {
    rating: { from: '', to: '', within: '', unit: 'days' },
    dateAdded: { from: '', to: '', within: '', unit: 'days' },
    releaseDate: { from: '', to: '', within: '', unit: 'days' },
    completedDate: { from: '', to: '', within: '', unit: 'days' },
  },
  fields: { genre: '', year: '', tags: [] },
  modes: { tags: 'or' },
  initial: '',
});

// One query-string-to-filters mapping for saved views and for rows that open
// Library from Home, so both land on the same filters the web media list shows.
export function filtersFromQuery(params, current) {
  return {
    ...current,
    statuses: params.getAll('status'),
    sort: params.get('sort') || 'date_added',
    direction: params.get('direction') || 'desc',
    view: params.get('layout') || current.view,
    rating: params.get('rating') || 'all',
    collection: params.get('collection') || 'all',
    progress: params.get('progress') || 'all',
    initial: params.get('initial') || '',
    fields: { ...current.fields, genre: params.get('genre') || '', year: params.get('year') || '',
      rating: params.get('rating') || 'all', collection: params.get('collection') || 'all',
      progress: params.get('progress') || 'all', release: params.get('release') || 'all',
      ...Object.fromEntries(['implied_genre', 'source', 'media_status', 'language', 'country', 'format', 'author', 'provider']
        .map(key => [key, params.get(key) || ''])),
      platform: params.getAll('platform'),
      tags: [...params.getAll('tag'), ...params.getAll('tags')] },
    modes: { ...current.modes, tags: params.get('tag_mode') || 'or', platform: params.get('platform_mode') || 'or' },
    ranges: {
      ...current.ranges,
      rating: { ...current.ranges.rating, from: params.get('rating_min') || '', to: params.get('rating_max') || '' },
      dateAdded: { ...current.ranges.dateAdded, from: params.get('date_added_from') || '',
        to: params.get('date_added_to') || '', within: params.get('date_added_within') || '',
        unit: params.get('date_added_within_unit') || 'days' },
      releaseDate: { ...current.ranges.releaseDate, from: params.get('release_date_from') || '',
        to: params.get('release_date_to') || '', within: params.get('release_date_within') || '',
        unit: params.get('release_date_within_unit') || 'days' },
      completedDate: { ...current.ranges.completedDate, from: params.get('completed_date_from') || '',
        to: params.get('completed_date_to') || '', within: params.get('completed_date_within') || '',
        unit: params.get('completed_date_within_unit') || 'days' },
    },
  };
}

// One page instance per library, so the filters, rows and selection of one
// media type never carry into the next one the drawer opens.
export default function LibraryScreen(props) {
  const { mediaType, query, tag } = props.params || {};
  return <LibraryPage key={`${mediaType || ''}|${query || ''}|${tag || ''}`} {...props} />;
}

function LibraryPage({ params, navigate }) {
  const mediaType = params?.mediaType;
  const title = MEDIA_TYPE_LABELS[mediaType] || 'Library';
  const [transport] = useState(() => createLibraryTransport());
  const [filters, setFilters] = useState(() => {
    const base = params?.query
      ? filtersFromQuery(new URLSearchParams(params.query), BASE_FILTERS(mediaType))
      : BASE_FILTERS(mediaType);
    // The Tags page opens a library already narrowed to one tag.
    return params?.tag ? { ...base, fields: { ...base.fields, tags: [String(params.tag)] } } : base;
  });
  const { width: windowWidth } = useWindowDimensions();
  const isServer = transport.mode === 'server';
  const hasPreferences = isServer && Boolean(mediaType) && mediaType !== 'all';
  // A library that arrives with its own filters (a Home row, a tag) keeps them.
  const usesSavedPreferences = hasPreferences && !params?.query && !params?.tag;
  const [prefs, setPrefs] = useState({ loaded: !usesSavedPreferences, columns: [] });
  const savedPrefs = useRef(null);
  const [filterData, setFilterData] = useState(null);
  const [notice, setNotice] = useState('');
  const [selectingAll, setSelectingAll] = useState(false);
  const [search, setSearch] = useState('');
  const [querySearch, setQuerySearch] = useState('');
  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(null);
  const [loading, setLoading] = useState(transport.mode === 'server');
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState('');
  const [reloadToken, setReloadToken] = useState(0);
  const [selectionMode, setSelectionMode] = useState(false);
  const [selectedRows, setSelectedRows] = useState([]);
  const [savedViews, setSavedViews] = useState([]);
  const [savedViewName, setSavedViewName] = useState('');
  // The name field only shows after Save View is chosen from the gear menu.
  const [namingView, setNamingView] = useState(false);
  const [tagOptions, setTagOptions] = useState([]);
  const [subview, setSubview] = useState(params?.subview || defaultSubview(mediaType));
  const [groupBy, setGroupBy] = useState(params?.groupBy || 'item');
  const queryState = useMemo(() => ({
    mediaType: filters.mediaTypes?.length === 1 ? filters.mediaTypes[0]
      : filters.mediaTypes?.length ? 'all' : mediaType,
    media_type: filters.mediaTypes?.length > 1 ? filters.mediaTypes : undefined,
    search: querySearch,
    statuses: filters.statuses,
    sort: filters.sort,
    direction: filters.direction,
    ranges: filters.ranges,
    fields: filters.fields,
    modes: filters.modes,
    rating: filters.rating,
    collection: filters.collection,
    progress: filters.progress,
    initial: filters.initial,
    group_by: groupBy,
    subview,
  }), [filters, groupBy, mediaType, querySearch, subview]);
  const queryKey = JSON.stringify(queryState);
  const activeQueryKey = useRef(queryKey);

  useEffect(() => {
    const timer = setTimeout(() => setQuerySearch(search.trim()), 250);
    return () => clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    if (transport.mode !== 'server') {
      setLoading(false);
      return undefined;
    }
    // The saved layout, sort and status arrive first, so the page loads once.
    if (!prefs.loaded) return undefined;
    let active = true;
    activeQueryKey.current = queryKey;
    setSelectionMode(false);
    setSelectedRows([]);
    setLoading(true);
    setLoadingMore(false);
    setError('');
    setRows([]);
    transport.load({ ...queryState, limit: PAGE_LIMIT, offset: 0 })
      .then(payload => {
        if (!active) return;
        const nextRows = mediaRowsFromPayload(payload, { subview });
        setRows(nextRows);
        const value = Number(payload?.pagination?.total);
        setTotal(Number.isFinite(value) ? value : null);
        setHasMore(Boolean(payload?.pagination?.next));
        setLoading(false);
      })
      .catch(cause => {
        if (!active) return;
        setRows([]);
        setTotal(null);
        setError(cause?.message || 'Your library could not be loaded from the server.');
        setLoading(false);
      });
    return () => { active = false; };
  }, [transport, queryState, queryKey, reloadToken, prefs.loaded]);

  // The viewer's saved layout, sort, status and table columns open the page the way the web does.
  useEffect(() => {
    if (!hasPreferences) return undefined;
    let active = true;
    transport.loadPreferences(mediaType).then(payload => {
      if (!active) return;
      if (usesSavedPreferences && payload && !payload.local) {
        const next = {
          view: payload.layout === 'table' ? 'table' : 'grid',
          sort: payload.sort || 'date_added',
          direction: payload.direction || 'desc',
          statuses: (Array.isArray(payload.statuses) ? payload.statuses : [])
            .map(value => QUERY_STATUS[value]).filter(Boolean),
        };
        savedPrefs.current = { ...next, statuses: next.statuses.join(',') };
        setFilters(current => ({ ...current, ...next }));
      }
      setPrefs({ loaded: true, columns: payload?.columns || [] });
    }).catch(() => { if (active) setPrefs({ loaded: true, columns: [] }); });
    return () => { active = false; };
  }, [transport, hasPreferences, usesSavedPreferences, mediaType]);

  // Changing layout, sort, direction or status stores it for the next visit,
  // as the web media list does with its URL parameters.
  useEffect(() => {
    if (!usesSavedPreferences || !prefs.loaded) return;
    const current = { view: filters.view, sort: filters.sort, direction: filters.direction, statuses: (filters.statuses || []).join(',') };
    const last = savedPrefs.current;
    savedPrefs.current = current;
    if (!last) return;
    const patch = {};
    if (current.view !== last.view) patch.layout = current.view;
    if (current.sort !== last.sort) patch.sort = current.sort;
    if (current.direction !== last.direction) patch.direction = current.direction;
    // "No Status" is not a stored status: it applies to this visit only.
    if (current.statuses !== last.statuses) patch.statuses = (filters.statuses || []).map(value => STORED_STATUS[value]).filter(Boolean);
    if (Object.keys(patch).length) transport.savePreferences(mediaType, patch).catch(() => {});
  }, [transport, usesSavedPreferences, mediaType, prefs.loaded, filters.view, filters.sort, filters.direction, filters.statuses]);

  // The filter menu's option lists come from the server, like the web menu. A failed
  // load leaves the sections that need no list; nothing is made up in their place.
  const entitySubview = ENTITY_SUBVIEWS.includes(subview);
  useEffect(() => {
    if (!hasPreferences || entitySubview) { setFilterData(null); return undefined; }
    let active = true;
    transport.loadFilterOptions({ ...queryState, fields: {}, ranges: {}, statuses: filters.statuses })
      .then(payload => { if (active) setFilterData(payload && !payload.local ? payload : null); })
      .catch(() => { if (active) setFilterData(null); });
    return () => { active = false; };
  }, [transport, hasPreferences, entitySubview, mediaType, filters.statuses, reloadToken]);

  useEffect(() => {
    if (transport.mode !== 'server' || !mediaType || mediaType === 'all') {
      setSavedViews([]);
      return undefined;
    }
    let active = true;
    transport.loadSavedViews({ mediaType }).then(views => {
      if (active) setSavedViews(Array.isArray(views) ? views : []);
    }).catch(() => {
      if (active) setSavedViews([]);
    });
    return () => { active = false; };
  }, [transport, mediaType]);

  useEffect(() => {
    if (transport.mode !== 'server') return undefined;
    let active = true;
    transport.loadTags().then(payload => {
      if (active) setTagOptions((payload?.results || []).map(tag => ({ value: String(tag.name), label: tag.name })));
    }).catch(() => { if (active) setTagOptions([]); });
    return () => { active = false; };
  }, [transport]);

  function changeFilter(intent) {
    if (intent.type === 'clear') {
      setFilters(current => ({ ...current, statuses: [], sort: 'date_added', direction: 'desc',
        ranges: Object.fromEntries(Object.entries(current.ranges).map(([key, range]) => [key, { ...range, from: '', to: '', within: '' }])),
        fields: { genre: '', year: '', selectedTags: [] }, modes: { tags: 'or' } }));
      setSearch('');
    } else if (intent.type === 'toggle') {
      setFilters(current => ({ ...current, [intent.key]: current[intent.key].includes(intent.value)
        ? current[intent.key].filter(value => value !== intent.value)
        : [...current[intent.key], intent.value] }));
    } else if (intent.type === 'set') {
      setFilters(current => ({ ...current, [intent.key]: intent.value }));
    } else if (intent.type === 'range') {
      setFilters(current => ({ ...current, ranges: { ...current.ranges, [intent.key]: intent.value } }));
    } else if (intent.type === 'field') {
      setFilters(current => ({ ...current, fields: { ...current.fields, [intent.key]: intent.value } }));
    } else if (intent.type === 'toggleField') {
      setFilters(current => {
        const values = current.fields[intent.key] || [];
        return { ...current, fields: { ...current.fields,
          [intent.key]: values.includes(intent.value) ? values.filter(value => value !== intent.value) : [...values, intent.value] } };
      });
    } else if (intent.type === 'fieldMode') {
      setFilters(current => ({ ...current, modes: { ...current.modes, [intent.key]: intent.value } }));
    } else if (intent.type === 'savedView') {
      transport.applySavedView?.({ id: intent.value }).then(saved => {
        const params = new URLSearchParams(saved?.query || '');
        setSearch(params.get('search') || '');
        setFilters(current => filtersFromQuery(params, current));
      }).catch(cause => setError(cause?.message || 'Saved view could not be loaded.'));
    }
  }

  function savedViewQuery() {
    const params = new URLSearchParams();
    const query = libraryQuery({ ...queryState, layout: filters.view });
    Object.entries(query).forEach(([key, value]) => {
      if (value == null || ['limit', 'offset', 'media_type'].includes(key)) return;
      const values = Array.isArray(value) ? value : [value];
      values.forEach(item => params.append(key, String(item)));
    });
    return params.toString();
  }

  async function saveCurrentView() {
    const name = savedViewName.trim();
    if (!name || transport.mode !== 'server' || !mediaType || mediaType === 'all') return;
    try {
      const saved = await transport.createSavedView({ mediaType, name, query: savedViewQuery() });
      setSavedViews(current => [...current, saved]);
      setSavedViewName('');
      setNamingView(false);
    } catch (cause) {
      setError(cause?.message || 'Saved view could not be created.');
    }
  }

  async function deleteSavedView(id) {
    try {
      await transport.deleteSavedView({ id });
      setSavedViews(current => current.filter(view => view.id !== id));
    } catch (cause) {
      setError(cause?.message || 'Saved view could not be deleted.');
    }
  }

  async function moveSavedView(id, direction) {
    const index = savedViews.findIndex(view => view.id === id);
    const target = index + direction;
    if (index < 0 || target < 0 || target >= savedViews.length) return;
    const reordered = [...savedViews];
    [reordered[index], reordered[target]] = [reordered[target], reordered[index]];
    try {
      await transport.reorderSavedViews({ mediaType, ids: reordered.map(view => view.id) });
      setSavedViews(reordered);
    } catch (cause) {
      setError(cause?.message || 'Saved views could not be reordered.');
    }
  }

  async function loadMore() {
    if (loadingMore || !hasMore) return;
    const requestedQuery = queryKey;
    setLoadingMore(true);
    setError('');
    try {
      const payload = await transport.load({ ...queryState, limit: PAGE_LIMIT, offset: rows.length });
      if (activeQueryKey.current === requestedQuery) {
        setRows(current => [...current, ...mediaRowsFromPayload(payload, { subview })]);
        setHasMore(Boolean(payload?.pagination?.next));
      }
    } catch (cause) {
      if (activeQueryKey.current === requestedQuery) {
        setError(cause?.message || 'More library items could not be loaded.');
      }
    } finally {
      setLoadingMore(false);
    }
  }

  function toggleSelected(id) {
    setSelectedRows(current => current.includes(id) ? current.filter(value => value !== id) : [...current, id]);
  }

  // Walks every loaded row, so only when the rows change, not on every keystroke.
  const dynamicGenres = useMemo(
    () => [...new Set(rows.flatMap(row => row.media?.item?.genres || []))].map(value => ({ value, label: value })),
    [rows],
  );
  const dynamicYears = useMemo(() => [...new Set(rows.map(row => {
    const value = row.media?.item?.release_datetime;
    return value ? String(new Date(value).getUTCFullYear()) : '';
  }).filter(Boolean))].map(value => ({ value, label: value })).sort((a, b) => b.value.localeCompare(a.value)), [rows]);
  const musicSubview = entitySubview;
  const filterOptions = {
    mediaTypes: Object.entries(MEDIA_TYPE_LABELS).map(([value, label]) => ({ value, label })),
    statuses: musicSubview ? [
      { value: '0', label: 'Planning' }, { value: '1', label: 'In progress' },
      { value: '2', label: 'Paused' }, { value: '3', label: 'Completed' }, { value: '4', label: 'Dropped' },
      { value: 'no_status', label: 'No Status' },
    ] : STATUSES,
    sorts: (musicSubview ? SORTS.filter(option => ['date_added', 'title', 'score', 'release_date', 'start_date', 'end_date'].includes(option.value)) : SORTS)
      .filter(option => !(option.value === 'time_left' && mediaType !== 'tv')
      && !(option.value === 'time_to_beat' && mediaType !== 'game')
      && !(option.value === 'platform' && mediaType !== 'game')),
    views: [{ value: 'grid', label: 'Grid' }, { value: 'table', label: 'Table' }],
    savedViews: savedViews.map(view => ({ id: String(view.id), label: view.name })),
    ranges: musicSubview ? [
      { key: 'rating', label: 'Rating', type: 'number', placeholder: '0–10' },
    ] : [
      { key: 'rating', label: 'Rating', type: 'number', placeholder: '0–10' },
      ...['dateAdded', 'releaseDate', 'completedDate'].map((key, index) => ({
        key, label: ['Date added', 'Release date', 'Completed date'][index], type: 'date',
        placeholder: 'YYYY-MM-DD', relative: true, units: RANGE_UNITS,
      })),
    ],
    sections: musicSubview ? [] : [
      // Server-built sections replace the two the loaded rows could only approximate.
      ...(filterData ? libraryFilterSections(filterData) : [
        { key: 'genre', label: 'Genre', state: 'genre', type: 'single', clearValue: '', searchable: true, options: dynamicGenres },
        { key: 'year', label: 'Year', state: 'year', type: 'single', clearValue: '', searchable: true, options: dynamicYears },
      ]),
      { key: 'tags', label: 'Tags', type: 'multi', clearValue: [], searchable: true, modeValue: 'or',
        modes: FILTER_MODES, modeState: 'tags', options: tagOptions },
    ],
  };

  async function shareView() {
    setNotice('');
    try {
      const url = await transport.shareView({ ...queryState, mediaType });
      if (!url) throw new Error('The view could not be shared.');
      const server = String(getActiveAuthSession()?.serverUrl || '').replace(/\/$/, '');
      await Linking.openURL(/^https?:/.test(url) ? url : `${server}${url}`);
    } catch (cause) {
      setNotice(cause?.message || 'The view could not be shared.');
    }
  }

  // Select all selects every item behind the filters, not only the loaded page.
  async function selectAllMatching() {
    if (selectingAll) return;
    setSelectingAll(true);
    setError('');
    const requestedQuery = queryKey;
    try {
      let all = rows;
      let more = hasMore;
      while (more && activeQueryKey.current === requestedQuery) {
        const payload = await transport.load({ ...queryState, limit: ALL_MATCHING_LIMIT, offset: all.length });
        const next = mediaRowsFromPayload(payload, { subview });
        if (!next.length) break;
        all = [...all, ...next];
        more = Boolean(payload?.pagination?.next);
      }
      if (activeQueryKey.current !== requestedQuery) return;
      setRows(all);
      setHasMore(false);
      setSelectedRows(all.map(row => String(row.id)));
    } catch (cause) {
      setError(cause?.message || 'The matching items could not be loaded.');
    } finally {
      setSelectingAll(false);
    }
  }

  function saveColumns(columns) {
    transport.savePreferences(mediaType, { columns }).then(next => {
      if (next?.columns) setPrefs(current => ({ ...current, columns: next.columns }));
    }).catch(() => {});
  }

  function openRow(row) {
    // A podcast show opens its own page; the other rows open their detail.
    if (row.identity?.entityType === 'show') navigate('Music', { showId: row.identity.entityId });
    else navigate('Detail', { item: row });
  }

  const showRail = isServer && windowWidth >= RAIL_MIN_WIDTH && Boolean(mediaType) && mediaType !== 'all';
  const allSelected = rows.length > 0 && !hasMore && selectedRows.length >= rows.length;

  // The web settings gear: view layout, share, save and select, one menu. `icon` is the shared glyph
  // (header menu) and `symbol` the SF Symbol the iOS rail menu draws; the label names the current view.
  const viewItem = filters.view === 'table'
    ? { label: 'Table View', icon: actionIcons.table, symbol: 'tablecells',
      onSelect: () => changeFilter({ type: 'set', key: 'view', value: 'grid' }) }
    : { label: 'Grid View', icon: actionIcons.grid, symbol: 'square.grid.2x2',
      onSelect: () => changeFilter({ type: 'set', key: 'view', value: 'table' }) };
  // Below the divider the actions are in alphabetical order (Save, Select, Share), then saved views by name.
  const belowView = [
    ...(hasPreferences ? [{ label: 'Save View', icon: actionIcons.save, symbol: 'square.and.arrow.down',
      onSelect: () => setNamingView(true) }] : []),
    ...(!entitySubview ? [{ label: selectionMode ? 'Cancel Select' : 'Select Items',
      icon: actionIcons.circleCheck, symbol: 'checkmark.circle',
      onSelect: () => { setSelectionMode(value => !value); setSelectedRows([]); } }] : []),
    ...(hasPreferences ? [{ label: 'Share View', icon: actionIcons.share, symbol: 'square.and.arrow.up',
      onSelect: shareView }] : []),
    ...(hasPreferences && savedViews.length ? [{ heading: 'Saved views' },
      ...[...savedViews].sort((a, b) => a.name.localeCompare(b.name)).map(view => ({ key: `view-${view.id}`,
        label: view.name, onSelect: () => changeFilter({ type: 'savedView', value: String(view.id) }) }))] : []),
  ];
  const settingsItems = [viewItem, ...(belowView.length ? [{ separator: true }, ...belowView] : [])];

  const state = loading
    ? 'loading'
    : error && rows.length === 0
      ? 'error'
      : rows.length
        ? 'ready'
        : 'empty';

  const gridView = filters.view === 'grid';
  const onRate = (row, score) => transport.update({ identity: row.identity, values: { score } })
    .then(() => setReloadToken(value => value + 1))
    .catch(cause => setError(cause?.message || 'Rating could not be saved.'));

  const rail = showRail ? <View style={styles.rail}>
    <AlphabetBar options={ALPHABET.map(option => ({ ...option, selected: option.value === (filters.initial || '') }))}
      label="Filter by starting character" palette={RAIL_PALETTE}
      onNavigate={(_event, option) => setFilters(current => ({ ...current, initial: option.value }))} />
  </View> : null;
  const page = scrollProps => <LibraryPageView
      title={title}
      count={total ?? (state === 'ready' ? rows.length : undefined)}
      countLabel={total === 1 ? 'item' : 'items'}
      labels={labels}
      palette={palette}
      state={state}
      error={error ? { title: 'Library could not load', body: error } : undefined}
      empty={transport.mode === 'local'
        ? { title: 'Sign in to see your library', body: 'Connect your Floppy server to load tracked media.' }
        : { title: 'Nothing here yet', body: 'Track something and it will appear in this library.' }}
      onRetry={() => setReloadToken(current => current + 1)}
      onEmptyAction={() => navigate('Discover')}
      headerActions={hasPreferences || !entitySubview
        ? <LibrarySettings items={settingsItems} palette={palette} /> : null}
      filterForm={namingView ? <View style={styles.savedViews}>
        <View style={styles.saveViewRow}>
          <TextInput value={savedViewName} onChangeText={setSavedViewName} placeholder="Name this view"
            placeholderTextColor={palette.textMuted} accessibilityLabel="Saved view name" autoFocus style={styles.nameInput} />
          <Button label="Save view" tone="surface" disabled={!savedViewName.trim()}
            onPress={saveCurrentView} palette={buttonPalette} />
          <Button label="Cancel" tone="surface" onPress={() => { setNamingView(false); setSavedViewName(''); }}
            palette={buttonPalette} />
        </View>
        {savedViews.map(view => <View key={view.id} style={styles.savedViewRow}>
          <Text style={styles.savedViewName}>{view.name}</Text>
          <Pressable accessibilityRole="button" accessibilityLabel={`Move ${view.name} up`}
            disabled={savedViews[0]?.id === view.id} onPress={() => moveSavedView(view.id, -1)}>
            <Text style={styles.reorderView}>↑</Text></Pressable>
          <Pressable accessibilityRole="button" accessibilityLabel={`Move ${view.name} down`}
            disabled={savedViews[savedViews.length - 1]?.id === view.id} onPress={() => moveSavedView(view.id, 1)}>
            <Text style={styles.reorderView}>↓</Text></Pressable>
          <Pressable accessibilityRole="button" accessibilityLabel={`Delete ${view.name} saved view`}
            onPress={() => deleteSavedView(view.id)}><Text style={styles.deleteSavedView}>Delete</Text></Pressable>
        </View>)}
      </View> : null}
      filters={<View style={styles.controls}>
        <FilterBar variant="toolbar" showClear={false} filters={filters} options={filterOptions} palette={palette}
          labels={{ filters: 'Filters', status: 'Status', allStatuses: 'Status', statuses: 'Statuses', sort: 'Sort', range: 'Range' }}
          busy={loading || loadingMore} onChange={changeFilter} />
        {mediaType === 'game' ? <View style={styles.modeRow}>
          {['item', 'series', 'franchise'].map(value => <Pressable key={value}
            accessibilityRole="button" accessibilityState={{ selected: groupBy === value }}
            onPress={() => setGroupBy(value)} style={[styles.modeButton, groupBy === value && styles.modeButtonActive]}>
            <Text style={styles.modeText}>{value[0].toUpperCase() + value.slice(1)}</Text>
          </Pressable>)}
        </View> : null}
        {mediaType === 'comic' ? <View style={styles.modeRow}>
          {['comics', 'issues'].map(value => <Pressable key={value}
            accessibilityRole="button" accessibilityState={{ selected: subview === value }}
            onPress={() => setSubview(value)} style={[styles.modeButton, subview === value && styles.modeButtonActive]}>
            <Text style={styles.modeText}>{value === 'issues' ? 'Issues' : 'Comics'}</Text>
          </Pressable>)}
        </View> : null}
        {mediaType === 'music' ? <View style={styles.modeRow}>
          {MUSIC_SUBVIEWS.map(([value, label]) => <Pressable key={value}
            accessibilityRole="button" accessibilityState={{ selected: subview === value }}
            onPress={() => setSubview(value)} style={[styles.modeButton, subview === value && styles.modeButtonActive]}>
            <Text style={styles.modeText}>{label}</Text>
          </Pressable>)}
        </View> : null}
        {mediaType === 'podcast' ? <View style={styles.modeRow}>
          {[['shows', 'Shows'], ['items', 'Episodes']].map(([value, label]) => <Pressable key={value}
            accessibilityRole="button" accessibilityState={{ selected: subview === value }}
            onPress={() => setSubview(value)} style={[styles.modeButton, subview === value && styles.modeButtonActive]}>
            <Text style={styles.modeText}>{label}</Text>
          </Pressable>)}
        </View> : null}
        {!entitySubview ? <LibraryBulkActions rows={rows} selectedRows={selectedRows}
          transport={transport} selecting={selectionMode}
          onToggleSelect={() => { setSelectionMode(value => !value); setSelectedRows([]); }}
          onClearSelection={() => setSelectedRows([])}
          onDone={() => { setReloadToken(value => value + 1); setSelectedRows([]); setSelectionMode(false); }} /> : null}
        {!entitySubview && selectionMode ? <View style={styles.modeRow}>
          <Button label={total ? `Select all ${total}` : 'Select all'} tone="surface"
            busy={selectingAll} disabled={selectingAll || allSelected || !rows.length}
            onPress={selectAllMatching} palette={buttonPalette} />
        </View> : null}
        {notice ? <Text accessibilityRole="alert" style={styles.inlineError}>{notice}</Text> : null}
      </View>}
      renderScroll={gridView ? ({ header, footer, ready }) => <LibraryGrid rows={ready ? rows : undefined}
        selecting={selectionMode} selected={selectedRows} onToggle={toggleSelected} groupBy={groupBy}
        onOpen={openRow} onOpenHistory={() => navigate('History')} onRate={onRate}
        header={header} footer={footer} scrollProps={scrollProps}
        onEndReached={ready && !loading ? loadMore : undefined} /> : undefined}
      content={gridView
        ? null
        : <TableSlot rows={rows} onOpenDetail={openRow}
          columnConfig={prefs.columns} onColumnsSave={hasPreferences ? saveColumns : undefined}
          selecting={selectionMode} selectedRows={selectedRows} onToggleSelect={toggleSelected}
          querySort={filters.sort} queryDirection={filters.direction}
          onQuerySort={(sort, direction) => setFilters(current => ({ ...current, sort, direction }))}
          onTrackSave={(row, values) => transport.update({ identity: row.identity, values })
            .then(() => setReloadToken(value => value + 1))}
          onTrackDelete={row => transport.remove({ identity: row.identity })
            .then(() => setReloadToken(value => value + 1))} />}
      footer={<View style={styles.footer}>
        {error ? <Text accessibilityRole="alert" style={styles.inlineError}>{error}</Text> : null}
        {hasMore ? <Button label="Load more" tone="surface" busy={loadingMore}
          disabled={loadingMore} onPress={loadMore} palette={buttonPalette} /> : null}
        {loadingMore ? <ActivityIndicator color={palette.accent} /> : null}
      </View>}
    />;

  // The grid scrolls itself (a windowed list) with Screen's own scroll settings;
  // the table keeps the page scroll.
  // The A to Z rail stays outside the scrolling list: in grid view it goes in
  // Screen's `floating` slot, beside the list, so its height spans the screen
  // and it does not scroll away with the header.
  return <Screen chrome={false} renderScroll={gridView ? page : undefined} floating={gridView ? rail : undefined}>
    {gridView ? null : rail}
    {gridView ? null : page()}
  </Screen>;
}

const styles = StyleSheet.create({
  rail: { position: 'absolute', right: spacing.xs, top: 160, bottom: spacing.lg, width: 32, zIndex: 20 },
  searchGroup: { width: '100%' },
  searchInput: { color: palette.text, backgroundColor: palette.surface, borderColor: palette.border,
    borderWidth: 1, borderRadius: radius.sm, minHeight: 44, paddingHorizontal: spacing.md, fontSize: 15 },
  savedViews: { gap: spacing.xs },
  saveViewRow: { flexDirection: 'row', gap: spacing.sm },
  nameInput: { flex: 1, minWidth: 120, color: palette.text, backgroundColor: palette.surface,
    borderColor: palette.border, borderWidth: 1, borderRadius: radius.sm, minHeight: 40,
    paddingHorizontal: spacing.md, fontSize: 14 },
  savedViewRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingVertical: spacing.xs, borderBottomWidth: 1, borderColor: palette.border },
  savedViewName: { color: palette.textSecondary, fontSize: 13 },
  reorderView: { color: palette.textSecondary, fontSize: 18, paddingHorizontal: spacing.xs },
  deleteSavedView: { color: palette.danger, fontSize: 13, padding: spacing.xs },
  controls: { gap: spacing.sm },
  modeRow: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: spacing.sm },
  modeButton: { borderWidth: 1, borderColor: palette.border, backgroundColor: palette.surface,
    borderRadius: radius.sm, paddingVertical: 8, paddingHorizontal: spacing.md },
  modeButtonActive: { borderColor: palette.accent, backgroundColor: palette.surfaceStrong },
  modeText: { color: palette.text, fontSize: 13, textTransform: 'capitalize' },
  selectedCount: { color: palette.textSecondary, fontSize: 13 },
  footer: { gap: spacing.sm, alignItems: 'center' },
  inlineError: { color: palette.danger, fontSize: 13 },
});
