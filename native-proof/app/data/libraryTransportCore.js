const STATUS_LABELS = {
  0: 'Planning',
  1: 'In progress',
  2: 'Paused',
  3: 'Completed',
  4: 'Dropped',
};

const STATUS_RANK = {
  Planning: 0,
  'In progress': 1,
  Completed: 2,
  Paused: 3,
  Dropped: 4,
};

const MEDIA_LABELS = {
  movie: 'Movie',
  tv: 'Show',
  season: 'Season',
  episode: 'Episode',
  anime: 'Anime',
  book: 'Book',
  comic: 'Comic',
  manga: 'Manga',
  game: 'Game',
  boardgame: 'Board Game',
  music: 'Music',
  podcast: 'Podcast',
  video: 'Video',
};

function numericDate(value) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return date.getUTCFullYear() * 10000 + (date.getUTCMonth() + 1) * 100 + date.getUTCDate();
}

function dateLabel(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

function yearLabel(value) {
  if (!value) return '';
  const year = new Date(value).getUTCFullYear();
  return Number.isNaN(year) ? '' : String(year);
}

function runtimeLabel(item) {
  if (item.runtime) return String(item.runtime);
  const minutes = Number(item.runtime_minutes);
  if (!Number.isFinite(minutes) || minutes <= 0) return '';
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (!hours) return `${rest}m`;
  return rest ? `${hours}h ${rest}m` : `${hours}h`;
}

function progressText(row, unit) {
  const progress = row.progress;
  if (progress == null) return { text: '', maxText: '' };
  if (unit === 'plays') {
    return { text: String(progress), maxText: progress === 1 ? ' play' : ' plays' };
  }
  if (unit === 'minutes') {
    const minutes = Number(progress);
    if (!Number.isFinite(minutes)) return { text: String(progress), maxText: '' };
    const hours = Math.floor(minutes / 60);
    const rest = Math.round(minutes % 60);
    const label = hours ? `${hours}h${rest ? ` ${rest}m` : ''}` : `${rest}m`;
    return { text: label, maxText: ' played' };
  }
  if (unit === 'pages' || unit === 'chapters' || unit === 'issues') {
    return { text: String(progress), maxText: ` ${unit}` };
  }
  if (unit === 'percent') {
    return { text: `${progress}%`, maxText: '' };
  }
  const left = row.episodes_left;
  if (left != null) {
    return { text: String(progress), maxText: ` / ${Number(progress) + Number(left)} episodes` };
  }
  return { text: String(progress), maxText: ' episodes' };
}

const MEDIA_TYPE_PLURALS = {
  movie: 'Movies',
  tv: 'TV Shows',
  season: 'TV Seasons',
  episode: 'Episodes',
  anime: 'Anime',
  book: 'Books',
  comic: 'Comics',
  comicissue: 'Comic Issues',
  manga: 'Manga',
  game: 'Games',
  boardgame: 'Board Games',
  music: 'Music',
  podcast: 'Podcasts',
  video: 'Videos',
};

export function libraryMediaTypeOptions() {
  return Object.keys(MEDIA_TYPE_PLURALS).map(value => ({ value, label: MEDIA_TYPE_PLURALS[value] }));
}

export function mediaRowFromPayload(row) {
  const item = row.item || {};
  const mediaType = item.library_media_type || item.media_type || '';
  const status = STATUS_LABELS[row.status] || (typeof row.status === 'string' ? row.status : '');
  const progress = progressText(row, row.progress_unit);
  const release = item.release_datetime;
  const genres = Array.isArray(item.genres) ? item.genres : [];
  const id = row.item_id || String(row.id || '');
  const title = item.title || '';
  return {
    id,
    title,
    identity: {
      mediaType,
      source: item.source,
      mediaId: item.media_id,
      libraryMediaType: item.library_media_type,
      itemId: row.id,
      title,
      image: item.image || null,
    },
    sort: {
      score: row.score == null ? null : Number(row.score),
      progress: typeof row.progress === 'number' ? row.progress : null,
      status: STATUS_RANK[status] ?? null,
      runtime: item.runtime_minutes ?? null,
      time_watched: null,
      release_date: numericDate(release),
      date_added: numericDate(row.created_at),
      genres: genres.join(', '),
    },
    track: {
      mediaType,
      progressLabel: row.progress_unit === 'minutes' ? 'Minutes played'
        : row.progress_unit === 'pages' ? 'Pages read'
          : row.progress_unit === 'plays' ? 'Times watched'
            : 'Progress',
      progressValue: row.progress == null ? '' : String(row.progress),
      progressMax: row.episodes_left != null && row.progress != null
        ? Number(row.progress) + Number(row.episodes_left)
        : null,
      startDate: row.start_date || '',
      endDate: row.end_date || '',
    },
    cells: {
      image: { kind: 'image', src: item.card_image || item.image || null, alt: title },
      title: { kind: 'title', text: title, href: `#open:${id}` },
      media_type: { kind: 'text', text: MEDIA_LABELS[mediaType] || mediaType, muted: true },
      score: {
        kind: 'score',
        rated: row.score != null,
        text: row.score == null ? '' : String(row.score),
      },
      progress: { kind: 'progress', text: progress.text, maxText: progress.maxText, note: 'Total' },
      status: { kind: 'status', text: status || '—', muted: !status },
      runtime: { kind: 'text', text: runtimeLabel(item) || '—', muted: !runtimeLabel(item) },
      time_watched: { kind: 'text', text: '—', muted: true },
      release_date: { kind: 'text', text: yearLabel(release) || '—', muted: !release },
      date_added: { kind: 'text', text: dateLabel(row.created_at) || '—', muted: !row.created_at },
      genres: { kind: 'genres', items: genres.map(label => ({ label })) },
    },
    media: row,
  };
}

export function mediaRowsFromPayload(payload, { subview } = {}) {
  const results = Array.isArray(payload) ? payload : payload?.results || [];
  if (['artists', 'albums', 'shows'].includes(subview)) {
    return results.map((entry, index) => {
      const artist = subview === 'artists';
      const show = subview === 'shows';
      const tracker = entry.tracker || {};
      const title = artist ? entry.name || '' : entry.title || '';
      const source = show ? entry.source || 'pocketcasts' : 'musicbrainz';
      const mediaId = show ? entry.podcast_uuid || String(entry.id)
        : artist ? entry.musicbrainz_id || String(entry.id) : entry.musicbrainz_release_id || String(entry.id);
      const kind = show ? 'podcast' : 'music';
      const row = {
        id: `${subview}-${entry.id}`,
        item_id: entry.id,
        tracked: true,
        status: tracker.status,
        score: tracker.score,
        created_at: tracker.created_at,
        progress_unit: 'plays',
        progress: null,
        item: {
          media_type: kind, library_media_type: kind, source, media_id: mediaId,
          title, image: entry.image || null, release_datetime: artist ? null : entry.release_date,
          genres: entry.genres || [],
        },
        _entity: { subview, id: entry.id, artistName: entry.artist_name || entry.author || '' },
        _rank: index,
      };
      return { ...mediaRowFromPayload(row), identity: {
        ...mediaRowFromPayload(row).identity,
        entityType: show ? 'show' : artist ? 'artist' : 'album',
        entityId: entry.id,
      }, title, subtitle: row._entity.artistName, media: row };
    });
  }
  return results.map(mediaRowFromPayload);
}

const RANGE_FIELDS = {
  rating: 'rating',
  completedDate: 'completed_date',
  releaseDate: 'release_date',
  dateAdded: 'date_added',
};

export function libraryQuery({
  mediaType,
  limit = 30,
  offset = 0,
  search,
  statuses,
  sort,
  direction,
  fields = {},
  ranges = {},
  modes = {},
  ...filters
} = {}) {
  const query = { limit, offset };
  if (mediaType === 'all') query.media_type = 'all';
  if (search) query.search = search;
  if (Array.isArray(statuses) && statuses.length) query.status = statuses;
  if (sort) query.sort = sort;
  if (direction) query.direction = direction;
  const simpleFilters = [
    'rating', 'rating_min', 'rating_max', 'collection', 'progress', 'genre',
    'implied_genre', 'year', 'release', 'source', 'media_status', 'language',
    'country', 'platform', 'platform_mode', 'platforms', 'origin', 'format',
    'author', 'provider', 'provider_region', 'pinned_providers', 'tags', 'tag_mode',
    'exclude', 'media_type', 'group_by', 'group', 'subview', 'initial',
    'layout',
  ];
  simpleFilters.forEach(key => {
    if (filters[key] !== undefined && filters[key] !== '' && filters[key] !== null) {
      query[key] = filters[key];
    }
  });
  Object.entries(RANGE_FIELDS).forEach(([stateKey, queryKey]) => {
    const range = ranges[stateKey];
    if (!range) return;
    if (stateKey === 'rating') {
      if (range.from) query.rating_min = range.from;
      if (range.to) query.rating_max = range.to;
      return;
    }
    ['from', 'to', 'within'].forEach(bound => {
      if (range[bound]) query[`${queryKey}_${bound}`] = range[bound];
    });
    if (range.within && range.unit) query[`${queryKey}_within_unit`] = range.unit;
  });
  Object.entries(fields).forEach(([key, value]) => {
    // The server reads tag names from `tag`.
    const apiKey = key === 'selectedTags' || key === 'tags' ? 'tag' : key;
    if (Array.isArray(value) ? value.length > 0 : value !== '' && value != null) {
      query[apiKey] = value;
    }
  });
  Object.entries(modes).forEach(([key, value]) => {
    if (key === 'tags' && value) query.tag_mode = value;
    if ((key === 'platforms' || key === 'platform') && value) query.platform_mode = value;
  });
  return query;
}

export function mediaCardModelFromRow(row) {
  const item = row?.item || {};
  const mediaType = item.library_media_type || item.media_type || '';
  const status = STATUS_LABELS[row?.status] || (typeof row?.status === 'string' ? row.status : '');
  const progress = Number(row?.progress);
  const hasProgress = row?.progress != null && Number.isFinite(progress);
  const total = row?.episodes_left == null ? null : progress + Number(row.episodes_left);
  const unit = row?.progress_unit || '';
  const progressLabel = hasProgress
    ? unit === 'minutes' ? progressText(row, unit).text + progressText(row, unit).maxText
      : unit === 'plays' ? progressText(row, unit).text + progressText(row, unit).maxText
        : `${progressText(row, unit).text}${progressText(row, unit).maxText}`
    : '';
  const release = item.release_datetime;
  const year = yearLabel(release);
  // The server picks the provider size a card shows (`card_image`); `image` stays
  // full size for Detail.
  const image = item.card_image || item.image || null;
  const square = ['music', 'podcast'].includes(mediaType);
  const landscape = ['game', 'boardgame', 'video'].includes(mediaType);
  const shape = square ? 'square' : landscape ? 'landscape' : 'poster';
  const score = row?.score == null ? null : Number(row.score);
  const percent = total > 0 && hasProgress ? Math.min(100, Math.max(0, (progress / total) * 100)) : null;
  const metadata = [year, progressLabel].filter(Boolean);
  return {
    mediaType,
    identity: {
      mediaType,
      source: item.source,
      mediaId: item.media_id,
      itemId: row?.id,
    },
    shape,
    video: mediaType === 'video',
    image: { src: image, fallback: '', source: 'primary' },
    title: item.title || '',
    altTitle: item.alternative_title || null,
    statusChip: status
      ? { kind: 'status', status, hideOnMobile: false }
      : { kind: 'no-status', label: 'No Status', hideOnMobile: false },
    cornerChips: [],
    titleLinks: [],
    progressBar: percent == null ? null : { percent, paused: status === 'Paused' },
    lines: metadata.length ? [{ join: 'plain', parts: metadata.flatMap((text, index) => [
      ...(index ? [{ text: ' · ', muted: true }] : []),
      { text, muted: index > 0 },
    ]) }] : [],
    rating: score == null ? null : { label: String(score), value: score, scaleMax: 10, rateLabel: 'Rate' },
    ratingEditable: Boolean(row?.tracked),
    actions: null,
  };
}

// The web card's hover actions: tracking, lists and the item's history.
export function cardActionsFor(title, identity) {
  return {
    href: '',
    items: [
      { kind: 'modal', modal: 'track', title: 'Edit tracking', media: identity },
      { kind: 'modal', modal: 'lists', title: 'Add to custom lists', media: identity },
      { kind: 'link', title: 'View your activity history', href: '/history', media: identity },
    ],
    ordering: null,
    modals: { track: 'track', lists: 'lists' },
    csrfToken: '',
    clickable: true,
    title,
    media: identity,
  };
}

const STATIC_SECTIONS = {
  rating: ['Rating', 'Any Rating', [['rated', 'Rated'], ['not_rated', 'Not Rated']]],
  collection: ['Collection', 'Any Collection', [['collected', 'Collected'], ['not_collected', 'Not Collected']]],
  progress: ['Progress', 'Any Progress', [['not_caught_up', 'Not Caught Up'], ['caught_up', 'Caught Up']]],
  release: ['Released', 'All', [['released', 'Released'], ['not_released', 'Not Released']]],
};

function staticSection(key) {
  const [label, any, entries] = STATIC_SECTIONS[key];
  return {
    key,
    label,
    state: key,
    type: 'single',
    clearValue: 'all',
    idleLabel: any,
    options: [{ value: 'all', label: any }, ...entries.map(([value, text]) => ({ value, label: text }))],
  };
}

function optionSection(key, label, entries, extra = {}) {
  const options = (Array.isArray(entries) ? entries : [])
    .map(entry => (typeof entry === 'string'
      ? { value: entry, label: entry }
      : { value: String(entry?.value ?? ''), label: String(entry?.label ?? entry?.value ?? '') }))
    .filter(option => option.value);
  if (!options.length) return null;
  return { key, label, state: key, type: 'single', clearValue: '', searchable: true, options, ...extra };
}

export const FILTER_MODES = [
  { value: 'or', label: 'Any' },
  { value: 'and', label: 'All' },
  { value: 'not', label: 'Not' },
];

// Filter sections in the order of the web filter menu (filter_menu.html). The
// option lists come from GET /api/v1/media/<type>/filter-options/, which uses
// the same builder as the web menu; a section with nothing to choose is left
// out, like the web does. Tags keep their own section, listed from /api/v1/tags/.
export function libraryFilterSections(data) {
  const info = data || {};
  const sections = [staticSection('rating'), staticSection('collection')];
  if (info.show_progress) sections.push(staticSection('progress'));
  sections.push(
    optionSection('genre', 'Genre', info.genres),
    optionSection('implied_genre', 'Implied Genre', info.implied_genres),
    optionSection('year', 'Date', info.years),
    staticSection('release'),
    optionSection('source', 'Source', info.sources),
    optionSection('media_status', 'Availability', info.media_statuses),
  );
  if (info.show_languages) sections.push(optionSection('language', 'Language', info.languages));
  if (info.show_countries) sections.push(optionSection('country', 'Country', info.countries));
  if (info.show_platforms) {
    sections.push(optionSection('platform', 'Platform', info.platforms, {
      type: 'multi', clearValue: [], modes: FILTER_MODES, modeState: 'platform', modeValue: 'or',
    }));
  }
  if (info.show_formats) sections.push(optionSection('format', 'Format', info.formats));
  if (info.show_authors) {
    sections.push(optionSection('author', info.author_is_channel ? 'Channel' : 'Author', info.authors));
  }
  if (info.show_providers) sections.push(optionSection('provider', 'Streaming Service', info.providers));
  return sections.filter(Boolean);
}

// The query a filter state stands for, without paging: what the option lists,
// the matching ids, a saved view and a shared view all describe.
function filterQuery(params, drop = []) {
  const query = libraryQuery(params);
  ['limit', 'offset', ...drop].forEach(key => { delete query[key]; });
  return query;
}

export function libraryPath(mediaType, subview) {
  if (mediaType === 'music' && ['artists', 'albums'].includes(subview)) {
    return `/api/v1/music/${subview}/`;
  }
  if (mediaType === 'podcast' && subview === 'shows') {
    return '/api/v1/podcasts/shows/';
  }
  if (mediaType === 'comic' && subview === 'issues') {
    return '/api/v1/media/comicissue/';
  }
  return mediaType && mediaType !== 'all'
    ? `/api/v1/media/${encodeURIComponent(mediaType)}/`
    : '/api/v1/media/';
}

export function createRemoteLibraryActions(request) {
  return {
    mode: 'server',
    load(params = {}) {
      const { subview, groupBy, ...queryParams } = params;
      const path = libraryPath(params.mediaType, subview);
      const query = libraryQuery({ ...queryParams, group_by: groupBy, subview });
      return request(path, {
        query,
      });
    },
    loadFilterOptions(params = {}) {
      return request(`/api/v1/media/${encodeURIComponent(params.mediaType)}/filter-options/`, {
        query: filterQuery(params, ['sort', 'direction', 'subview', 'group_by']),
      });
    },
    async loadItemIds(params = {}) {
      const payload = await request(`/api/v1/media/${encodeURIComponent(params.mediaType)}/item-ids/`, {
        query: filterQuery(params, ['subview', 'group_by']),
      });
      return (payload?.item_ids || []).map(Number).filter(Number.isFinite);
    },
    loadPreferences(mediaType) {
      return request(`/api/v1/library/preferences/${encodeURIComponent(mediaType)}/`);
    },
    savePreferences(mediaType, patch) {
      return request(`/api/v1/library/preferences/${encodeURIComponent(mediaType)}/`, { method: 'PATCH', body: patch });
    },
    async shareView({ mediaType, ...params }) {
      const payload = await request('/api/v1/library/share-view/', {
        method: 'POST', body: { mediaType, query: filterQuery({ mediaType, ...params }, ['subview', 'group_by']) },
      });
      return payload?.url || '';
    },
    loadSavedViews({ mediaType }) {
      return request('/api/v1/library/saved-views/', { query: { media_type: mediaType } });
    },
    applySavedView({ id }) {
      return request(`/api/v1/library/saved-views/${encodeURIComponent(id)}/`);
    },
    createSavedView({ mediaType, name, query }) {
      return request('/api/v1/library/saved-views/', {
        method: 'POST', json: { media_type: mediaType, name, query },
      });
    },
    deleteSavedView({ id }) {
      return request(`/api/v1/library/saved-views/${encodeURIComponent(id)}/`, { method: 'DELETE' });
    },
    reorderSavedViews({ mediaType, ids }) {
      return request('/api/v1/library/saved-views/reorder/', {
        method: 'POST', json: { media_type: mediaType, ids },
      });
    },
    startBulkPlays({ rows, options, dryRun }) {
      return request('/api/v1/library/bulk/plays/', {
        method: 'POST', json: { item_ids: rows.map(row => row.identity.itemId), options, dry_run: dryRun },
      });
    },
    bulkPlaysStatus({ operationId }) {
      return request(`/api/v1/library/bulk/plays/${encodeURIComponent(operationId)}/`);
    },
    loadLists() {
      return request('/api/v1/lists/', { query: { limit: 200, offset: 0 } });
    },
    loadTags() {
      return request('/api/v1/tags/');
    },
    bulkStatus({ rows, status }) {
      return Promise.all(rows.map(row => this.update({ identity: row.identity, values: { status } })));
    },
    bulkCollection({ rows }) {
      return request('/api/v1/library/bulk/collection/', {
        method: 'POST', json: { item_ids: rows.map(row => row.identity.itemId) },
      });
    },
    bulkAddToList({ listId, rows }) {
      return request(`/api/v1/lists/${encodeURIComponent(listId)}/items/bulk/`, {
        method: 'POST', json: { item_ids: rows.map(row => row.identity.itemId) },
      });
    },
    async bulkTag({ rows, tagId, mode }) {
      await Promise.all(rows.map(async row => {
        const path = `/api/v1/media/${encodeURIComponent(row.identity.mediaType)}/${encodeURIComponent(row.identity.source)}/${encodeURIComponent(row.identity.mediaId)}/tags/`;
        const current = await request(path);
        const ids = new Set((current?.results || []).map(tag => tag.id));
        if (mode === 'remove') ids.delete(tagId);
        else ids.add(tagId);
        return request(path, { method: 'PUT', json: { tag_ids: [...ids] } });
      }));
    },
    update({ identity, values = {} } = {}) {
      if (identity?.entityType) {
        const key = identity.entityType === 'artist' ? 'artists' : 'albums';
        return request(`/api/v1/music/${key}/${encodeURIComponent(identity.entityId)}/`, {
          method: 'PATCH', json: values,
        });
      }
      const mediaType = identity?.mediaType;
      const source = identity?.source;
      const mediaId = identity?.mediaId;
      if (!mediaType || !source || !mediaId) {
        return Promise.reject(new Error('This row has no provider identity to update.'));
      }
      const body = {};
      if (values.status !== undefined) body.status = values.status || null;
      if (values.score !== undefined) body.score = values.score === '' ? null : values.score;
      if (values.progress !== undefined) body.progress = values.progress === '' ? null : values.progress;
      if (values.start_date !== undefined) body.start_date = values.start_date || null;
      if (values.end_date !== undefined) body.end_date = values.end_date || null;
      return request(
        `/api/v1/media/${encodeURIComponent(mediaType)}/${encodeURIComponent(source)}/${encodeURIComponent(mediaId)}/`,
        { method: 'PATCH', body },
      );
    },
    remove({ identity } = {}) {
      const mediaType = identity?.mediaType;
      const source = identity?.source;
      const mediaId = identity?.mediaId;
      if (!mediaType || !source || !mediaId) {
        return Promise.reject(new Error('This row has no provider identity to delete.'));
      }
      return request(
        `/api/v1/media/${encodeURIComponent(mediaType)}/${encodeURIComponent(source)}/${encodeURIComponent(mediaId)}/`,
        { method: 'DELETE' },
      );
    },
  };
}

export function createLocalLibraryActions(reason) {
  return {
    mode: 'local',
    reason,
    load: async () => ({ local: true, reason }),
    loadFilterOptions: async () => ({ local: true, reason }),
    loadItemIds: async () => [],
    loadPreferences: async () => ({ local: true, reason }),
    savePreferences: async () => ({ local: true, reason }),
    shareView: async () => '',
    loadSavedViews: async () => [],
    applySavedView: async () => ({ local: true, reason }),
    createSavedView: async () => ({ local: true, reason }),
    deleteSavedView: async () => ({ local: true, reason }),
    reorderSavedViews: async () => ({ local: true, reason }),
    startBulkPlays: async () => ({ local: true, reason }),
    bulkPlaysStatus: async () => ({ local: true, reason }),
    loadLists: async () => [],
    loadTags: async () => ({ results: [] }),
    bulkStatus: async () => ({ local: true, reason }),
    bulkCollection: async () => ({ local: true, reason }),
    bulkAddToList: async () => ({ local: true, reason }),
    bulkTag: async () => ({ local: true, reason }),
    update: async () => ({ local: true, reason }),
    remove: async () => ({ local: true, reason }),
  };
}
