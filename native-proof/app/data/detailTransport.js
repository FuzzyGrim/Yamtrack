// Detail page server access. Every call goes through the signed-in session
// (cookie or Bearer token) against /api/v1; the pure mapping is in
// detailTransportCore.js.
import { getActiveAuthSession } from './authSession';
import { formatUserDate, loadDatePreferences } from './userDate.js';
import {
  episodeActionPath,
  episodesFromPayload,
  issuesFromPayload,
  mediaPath,
  seasonEpisodesPath,
  trackingFromPayloads,
} from './detailTransportCore';

function call(path, options) {
  const session = getActiveAuthSession();
  if (typeof session?.request !== 'function') {
    return Promise.reject(new Error('Sign in to your Floppy server to load this page.'));
  }
  return session.request(path, options);
}

function resultList(payload) {
  if (Array.isArray(payload)) return payload;
  return payload && Array.isArray(payload.results) ? payload.results : [];
}

function settled(result) {
  return result.status === 'fulfilled' ? result.value : null;
}

/**
 * What the detail page's write controls need and the page payload leaves out:
 * the entry ids (to edit a note or remove a play), the stored item id (cover
 * and edition pickers) and whether the item is tracked. Either read may fail;
 * the controls that depend on it then stay hidden.
 */
export async function loadDetailTracking(identity) {
  const datePreferences = loadDatePreferences(call);
  const path = mediaPath(identity);
  const [metadata, state] = await Promise.allSettled([
    call(`${path}/metadata/`),
    call(`${path}/state/`),
  ]);
  await datePreferences;
  return trackingFromPayloads({ identity, metadata: settled(metadata), state: settled(state) });
}

/** One page of a comic volume's issues (the web "Issues" list). */
export async function loadIssues(identity, offset = 0, limit = 20) {
  const payload = await call(`${mediaPath(identity)}/issues/?limit=${limit}&offset=${offset}`);
  return issuesFromPayload(payload, identity);
}

export async function loadSeasonEpisodes(identity, seasonNumber, { offset = 0, withPage = false } = {}) {
  const query = new URLSearchParams();
  if (withPage) { query.set('limit', '20'); query.set('offset', String(offset)); }
  if (identity.libraryMediaType === 'anime' || identity.mediaType === 'anime') query.set('library_media_type', 'anime');
  const payload = await call(`${seasonEpisodesPath(identity, seasonNumber)}${query.size ? `?${query}` : ''}`);
  const rows = episodesFromPayload(payload, identity, seasonNumber);
  if (!withPage) return rows;
  const total = Number(payload?.pagination?.total) || rows.length;
  const nextOffset = offset + rows.length;
  return { rows, total, loadMore: nextOffset < total ? { offset: nextOffset, label: `${Math.min(20, total - nextOffset)} more episodes` } : null };
}

export async function loadAnimeEpisodes(identity, queryString = '') {
  const payload = await call(`${mediaPath(identity)}/episode-preview/${queryString ? `?${queryString}` : ''}`);
  return { rows: episodesFromPayload(payload, identity, null), loadMore: payload.load_more || null, related: payload.related || {} };
}

export function watchEpisode(identity) {
  return call(episodeActionPath(identity, 'watch'), {
    method: 'POST',
    body: { end_date: new Date().toISOString() },
  });
}

export function dropEpisode(identity) {
  return call(episodeActionPath(identity, 'drop'), { method: 'POST', body: {} });
}

export function deleteHistoryEntry(identity, consumptionId) {
  return call(`${mediaPath(identity)}/history/${encodeURIComponent(consumptionId)}/`, {
    method: 'DELETE',
  });
}

export function saveEntryNotes(identity, consumptionId, notes) {
  return call(`${mediaPath(identity)}/history/${encodeURIComponent(consumptionId)}/`, {
    method: 'PATCH',
    body: { notes },
  });
}

export function syncMetadata(identity) {
  return call(`${mediaPath(identity)}/sync/`, { method: 'POST', body: {} });
}

function collectedLine(collected, total) {
  const percent = total > 0 ? ` \u2022 ${Math.floor((collected / total) * 100)}%` : '';
  return `${collected}/${total}${percent}`;
}

/** Owned copies plus, for shows and seasons, the web card's collected counts. */
export async function loadCollectionEntries(identity) {
  const payload = await call(`${mediaPath(identity)}/collection/entries/`);
  const stats = payload?.stats;
  const counts = [];
  if (stats && identity.mediaType !== 'season' && identity.mediaType !== 'episode') {
    counts.push({ label: 'COLLECTED SEASONS', value: collectedLine(stats.collected_seasons || 0, stats.total_seasons || 0) });
  }
  if (stats) {
    counts.push({ label: 'COLLECTED EPISODES', value: collectedLine(stats.collected_episodes || 0, stats.total_episodes || 0) });
  }
  return {
    counts,
    context: { stats, copies: payload?.context_copies || [], auditKind: payload?.audit_kind, auditEntries: payload?.audit_entries || [], resolutionCounts: payload?.resolution_counts },
    entries: resultList(payload).map(entry => ({
      id: String(entry.id),
      title: [entry.media_type, entry.resolution, entry.hdr, entry.is_3d ? '3D' : '']
        .filter(Boolean)
        .join(' \u00b7 ') || 'Owned copy',
      date: formatUserDate(entry.collected_at),
    })),
  };
}

/**
 * The "Your Progress" card and breakdown of a tracked season; null when not
 * tracked. The web show page renders no card (its tracker carries no total).
 */
export async function loadProgressCard(identity) {
  if (identity.mediaType !== 'season' && identity.mediaType !== 'tv') return null;
  try {
    return await call(`${mediaPath(identity)}/progress-card/`);
  } catch (error) {
    if (error && error.status === 404) return null;
    throw error;
  }
}

/** Seerr state, or null when Seerr is not configured for this account. */
export async function loadSeerr(identity) {
  try {
    return await call(`${mediaPath(identity)}/request/`);
  } catch (error) {
    if (error && error.status === 404) return null;
    throw error;
  }
}

export function requestSeerr(identity, seasons) {
  return call(`${mediaPath(identity)}/request/`, {
    method: 'POST',
    body: seasons && seasons.length ? { seasons } : {},
  });
}

/** The web Check-in split button's actions for the tracked entry, or null when it offers none. */
export async function loadCheckIn(identity) {
  if (!identity || identity.mediaType === 'season' || identity.mediaType === 'episode') return null;
  try {
    return await call(`${mediaPath(identity)}/checkin/`);
  } catch (error) {
    if (error && error.status === 404) return null;
    throw error;
  }
}

/** Run one Check-in action (start, resume, pause, finish, stop, cancel, log_play); returns the new actions. */
export function runCheckIn(identity, action) {
  return call(`${mediaPath(identity)}/checkin/`, { method: 'POST', body: { action } });
}

const NO_SCORES = { chips: [], graphs: {}, gameLengths: null };

/** Stored Trakt, IMDb, MAL and OpenCritic chips plus the series graphs. */
export async function loadScores(identity) {
  if (!identity) return NO_SCORES;
  if (identity.mediaType === 'episode') {
    // Episodes have no stored chips; the season's response still names the source logos.
    try {
      const season = await call(`${mediaPath({ ...identity, mediaType: 'season', episodeNumber: undefined })}/scores/`);
      return { ...NO_SCORES, logos: season?.logos || {} };
    } catch {
      return NO_SCORES;
    }
  }
  try {
    return await call(`${mediaPath(identity)}/scores/`);
  } catch {
    return NO_SCORES;
  }
}

/** The web "Activity History" entries, grouped by day; null when the request fails. */
export async function loadActivityTimeline(identity) {
  const seasoned = identity.mediaType === 'season' || identity.mediaType === 'episode';
  const base = mediaPath(identity);
  const payload = await call(`${base}/activity-timeline/`);
  return {
    multipleInstances: Boolean(payload?.multipleInstances),
    days: (payload?.days || []).map(day => ({
      day: day.day,
      label: day.label || day.day,
      entries: (day.entries || []).map(entry => ({
        id: String(entry.id),
        instance: entry.instance,
        date: entry.date,
        changes: entry.changes || [],
      })),
    })),
    seasoned,
  };
}

/** Covers on offer for a tracked item (Open Library by barcode, then provider search). */
export async function loadCoverCandidates(itemId) {
  const payload = await call(`/api/v1/metadata/items/${encodeURIComponent(itemId)}/cover/`);
  return { current: payload?.current || '', candidates: payload?.candidates || [] };
}

/** Store a picked file (base64) or a linked image as the item's cover. */
export function saveCover(itemId, { base64, url }) {
  return call(`/api/v1/metadata/items/${encodeURIComponent(itemId)}/cover/`, {
    method: 'POST',
    body: base64 ? { cover_base64: base64 } : { cover_url: url },
  });
}

/** Hardcover editions of a book, filtered by `query`, and the edition in use. */
export async function loadEditions(itemId, query = '') {
  const payload = await call(`/api/v1/metadata/items/${encodeURIComponent(itemId)}/hardcover-editions/`, {
    query: query ? { q: query } : undefined,
  });
  return {
    selected: payload?.selected ? String(payload.selected) : '',
    rows: (payload?.results || []).map(edition => ({
      id: String(edition.id),
      uri: edition.image || '',
      title: edition.title || '',
      meta: [[edition.language, edition.format].filter(Boolean).join(' · ')].filter(Boolean),
    })),
  };
}

export function saveEdition(itemId, editionId) {
  return call(`/api/v1/metadata/items/${encodeURIComponent(itemId)}/hardcover-editions/`, {
    method: 'POST',
    body: { edition_id: editionId },
  });
}
