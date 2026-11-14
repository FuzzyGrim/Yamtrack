import { expectObject } from './payloadGuard.js';

// Search endpoints. Library and online results, source pills, suggestions and
// parent pickers all come from the server in the shapes the web page renders.

const STATUS_LABELS = {
  0: 'Planning',
  1: 'In progress',
  2: 'Paused',
  3: 'Completed',
  4: 'Dropped',
};

const TYPE_LABELS = {
  movie: 'Movie',
  tv: 'TV',
  book: 'Book',
  music: 'Music',
  game: 'Game',
  anime: 'Anime',
  manga: 'Manga',
  comic: 'Comic',
  boardgame: 'Board Game',
  podcast: 'Podcast',
  video: 'Video',
};

function yearOf(value) {
  if (!value) return null;
  if (typeof value === 'number') return value;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.getUTCFullYear();
}

export function localResultFromMedia(row) {
  const item = row.item || {};
  const mediaType = item.library_media_type || item.media_type || '';
  const status = STATUS_LABELS[row.status] || null;
  return {
    key: row.item_id || `media-${row.id}`,
    type: mediaType,
    title: item.title || '',
    year: yearOf(item.release_datetime) ? String(yearOf(item.release_datetime)) : '',
    note: TYPE_LABELS[mediaType] || mediaType,
    image: item.image || null,
    status,
    rating: row.score == null ? null : Number(row.score),
    identity: {
      mediaType,
      source: item.source,
      mediaId: item.media_id,
      title: item.title || '',
    },
    href: item.url || '',
  };
}

export function onlineResultFromSearch(result) {
  const mediaType = result.media_type || '';
  return {
    key: `${mediaType}-${result.source}-${result.media_id}`,
    type: mediaType,
    title: result.title || '',
    year: result.year ? String(result.year) : '',
    note: TYPE_LABELS[mediaType] || mediaType,
    image: result.image || null,
    status: null,
    rating: result.rating == null ? null : Number(result.rating),
    identity: {
      mediaType,
      source: result.source,
      mediaId: String(result.media_id),
      title: result.title || '',
    },
    href: '',
  };
}

export function localResultsFromPayload(payload) {
  const results = Array.isArray(payload) ? payload : payload?.results || [];
  return results.map(localResultFromMedia);
}

export function onlineResultsFromPayload(payload) {
  const results = Array.isArray(payload) ? payload : payload?.results || [];
  return results.map(onlineResultFromSearch);
}

export function searchQuery({ search, limit = 20, offset = 0, source } = {}) {
  const query = { limit, offset };
  if (search) query.search = search;
  if (source) query.source = source;
  return query;
}

// The saved-library typeahead the web search bar uses, mapped for the shared search field.
// Images may be server-relative; the app needs absolute addresses.
export function suggestionsFromPayload(payload, serverUrl = '') {
  const rows = Array.isArray(payload?.suggestions) ? payload.suggestions : [];
  // A null row is skipped, like the other mappers (tagsFromPayload etc.).
  return rows.filter(row => row && typeof row === 'object').map((row, index) => ({
    id: String(row.id ?? index),
    title: row.title || '',
    subtitle: row.subtitle || '',
    image: row.image && row.image.startsWith('/') ? `${serverUrl}${row.image}` : row.image || null,
    mediaType: row.identity?.mediaType || '',
    identity: row.identity || null,
    // Saved items carry their web detail URL; Detail resolves it to an identity.
    href: row.url || '',
    url: row.url || '',
  }));
}

export function searchBarFromPayload(payload) {
  return {
    mediaTypes: Array.isArray(payload?.media_types) ? payload.media_types : [],
    barcodeTypes: Array.isArray(payload?.barcode_types) ? payload.barcode_types : [],
    labels: payload?.labels || {},
    sources: Array.isArray(payload?.sources) ? payload.sources : [],
    defaultSource: payload?.default_source || '',
  };
}

export function resultsQuery({ query, mediaType, source, page, layout } = {}) {
  const params = { q: query || '', media_type: mediaType || 'all' };
  if (source) params.source = source;
  if (page && page > 1) params.page = page;
  if (layout) params.layout = layout;
  return params;
}

export function createRemoteSearchActions(request, { suggestionsViaWeb = false, serverUrl = '' } = {}) {
  return {
    mode: 'server',
    // One typeahead for both apps, so the same query gives the same results in the same order.
    // It is the Django view, which reads the session; a token sign-in reads the same
    // typeahead from the API (loadSuggestions) and keeps the media list as a fallback.
    ...(suggestionsViaWeb ? {
      async searchSuggestions({ search, mediaType } = {}) {
        const payload = await request('/search/suggestions', {
          query: { q: search, media_type: mediaType || 'all', format: 'json' },
        });
        return suggestionsFromPayload(payload, serverUrl);
      },
    } : {}),
    searchLocal({ search, mediaType, limit = 20, offset = 0 } = {}) {
      const path = mediaType && mediaType !== 'all'
        ? `/api/v1/media/${encodeURIComponent(mediaType)}/`
        : '/api/v1/media/';
      return request(path, { query: searchQuery({ search, limit, offset }) });
    },
    searchOnline({ search, mediaType, source, limit = 20, offset = 0 } = {}) {
      return request(`/api/v1/search/${encodeURIComponent(mediaType)}/`, {
        query: searchQuery({ search, source, limit, offset }),
      });
    },
    saveType({ mediaType } = {}) {
      return request('/api/v1/search/type/', { method: 'PUT', body: { media_type: mediaType } });
    },
    loadSources({ mediaType = 'all' } = {}) {
      return request('/api/v1/search/sources/', { query: { media_type: mediaType } }).then(data => expectObject(data, 'Search'));
    },
    loadSuggestions({ query, mediaType = 'all' } = {}) {
      return request('/api/v1/search/suggestions/', { query: { q: query, media_type: mediaType } }).then(data => expectObject(data, 'Search'));
    },
    loadResults(params = {}) {
      return request('/api/v1/search/results/', { query: resultsQuery(params) }).then(data => expectObject(data, 'Search'));
    },
    loadParents({ kind, query } = {}) {
      return request('/api/v1/search/parents/', { query: { kind, q: query } });
    },
    loadManualOptions() {
      return request('/api/v1/search/manual-entry/');
    },
    createManualEntry(data) {
      return request('/api/v1/search/manual-entry/', { method: 'POST', body: data });
    },
    createMusic({ kind, id } = {}) {
      return request('/api/v1/search/music/create/', { method: 'POST', body: { kind, id } });
    },
  };
}

export function createLocalSearchActions(reason) {
  const local = async () => ({ local: true, reason });
  const unavailable = async () => { throw new Error(reason); };
  return {
    mode: 'local',
    reason,
    searchLocal: local,
    searchOnline: local,
    loadSources: unavailable,
    loadSuggestions: unavailable,
    loadResults: unavailable,
    loadParents: unavailable,
    loadManualOptions: unavailable,
    createManualEntry: unavailable,
    createMusic: unavailable,
  };
}