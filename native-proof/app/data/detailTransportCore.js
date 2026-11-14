// Pure detail-page model mapping shared by the native detail screen and its
// tests. All server access stays in detailTransport.js.
import { formatUserDate, formatUserDateTime } from './userDate.js';

const STATUS_LABELS = {
  0: 'Planning',
  1: 'In progress',
  2: 'Paused',
  3: 'Completed',
  4: 'Dropped',
};

export function statusLabel(code) {
  if (code == null) return '';
  if (typeof code === 'string') {
    return STATUS_LABELS[Object.keys(STATUS_LABELS).find(key => STATUS_LABELS[key] === code)] || code;
  }
  return STATUS_LABELS[code] || '';
}

function asArray(value) {
  if (Array.isArray(value)) return value.filter(Boolean);
  return value ? [String(value)] : [];
}

function yearOf(value) {
  const text = String(value || '');
  const match = text.match(/^(\d{4})/);
  return match ? match[1] : '';
}

function dateLabel(value) {
  return formatUserDate(value);
}

function hasValue(value) {
  return value !== undefined && value !== null && value !== '';
}

const SEASONED_TYPES = new Set(['tv', 'season', 'episode']);

function withCoordinates(base, season, episode) {
  const identity = { ...base };
  if (!SEASONED_TYPES.has(base.mediaType) || !hasValue(season) || Number.isNaN(Number(season))) {
    return identity;
  }
  identity.seasonNumber = Number(season);
  identity.mediaType = hasValue(episode) && !Number.isNaN(Number(episode)) ? 'episode' : 'season';
  if (identity.mediaType === 'episode') identity.episodeNumber = Number(episode);
  return identity;
}

/**
 * Resolve the provider identity a detail page needs from any item shape the
 * app navigates with: a card model (identity or href), a library row
 * (nested item), a search result, or a calendar release (web href). Season and
 * episode coordinates survive so season and episode pages address themselves.
 */
export function identityFromItem(item) {
  if (!item) return null;
  const identity = item.identity;
  if (identity && identity.mediaId && identity.source && identity.mediaType) {
    return withCoordinates(
      { mediaType: identity.mediaType, source: identity.source, mediaId: identity.mediaId },
      identity.seasonNumber,
      identity.episodeNumber,
    );
  }
  const nested = item.item;
  if (nested && nested.media_id && nested.source && nested.media_type) {
    return withCoordinates(
      { mediaType: nested.media_type, source: nested.source, mediaId: nested.media_id },
      nested.season_number,
      nested.episode_number,
    );
  }
  if (item.mediaId && item.source && item.mediaType) {
    return withCoordinates(
      { mediaType: item.mediaType, source: item.source, mediaId: item.mediaId },
      item.seasonNumber,
      item.episodeNumber,
    );
  }
  if (item.media_id && item.source && item.media_type) {
    return withCoordinates(
      { mediaType: item.media_type, source: item.source, mediaId: item.media_id },
      item.season_number,
      item.episode_number,
    );
  }
  // Card models carry the web detail URL, which may sit behind a base path
  // (for example the /dw development prefix), so match /details/ anywhere.
  const href = String(item.href || item.url || '').split(/[?#]/)[0];
  const match = href.match(/\/details\/([^/]+)\/([^/]+)\/([^/]+)/);
  if (!match) return null;
  const rest = href.slice(href.indexOf(match[0]) + match[0].length);
  const coordinates = rest.match(/\/season\/(\d+)(?:\/episode\/(\d+))?/);
  return withCoordinates(
    { source: match[1], mediaType: match[2], mediaId: match[3] },
    coordinates ? coordinates[1] : null,
    coordinates ? coordinates[2] : null,
  );
}

export function identityKey(identity) {
  if (!identity) return '';
  return [
    identity.mediaType,
    identity.source,
    identity.mediaId,
    identity.seasonNumber ?? '',
    identity.episodeNumber ?? '',
  ].join('|');
}

/** API path for one item; seasons and episodes live under the tv routes. */
export function mediaPath(identity) {
  const { mediaType, source, mediaId, seasonNumber, episodeNumber } = identity;
  const seasoned = mediaType === 'season' || mediaType === 'episode';
  let path = `/api/v1/media/${encodeURIComponent(seasoned ? 'tv' : mediaType)}`
    + `/${encodeURIComponent(source)}`
    + `/${encodeURIComponent(mediaId)}`;
  if (seasoned && seasonNumber != null) path += `/${encodeURIComponent(seasonNumber)}`;
  if (mediaType === 'episode' && episodeNumber != null) path += `/${encodeURIComponent(episodeNumber)}`;
  return path;
}

export function seasonEpisodesPath(identity, seasonNumber) {
  return `/api/v1/media/tv/${encodeURIComponent(identity.source)}`
    + `/${encodeURIComponent(identity.mediaId)}`
    + `/${encodeURIComponent(seasonNumber)}/episodes/`;
}

/** Path of one episode watch action (`watch` or `drop`) or its score. */
export function episodeActionPath(identity, action) {
  return `/api/v1/media/tv/${encodeURIComponent(identity.source)}`
    + `/${encodeURIComponent(identity.mediaId)}`
    + `/${encodeURIComponent(identity.seasonNumber)}`
    + `/episodes/${encodeURIComponent(identity.episodeNumber)}/${action}/`
    + (identity.libraryMediaType === 'anime' ? '?library_media_type=anime' : '');
}

function episodeIdentity(identity, seasonNumber, episodeNumber) {
  return {
    mediaType: 'episode',
    source: identity.source,
    mediaId: identity.mediaId,
    seasonNumber: Number(seasonNumber),
    episodeNumber: Number(episodeNumber),
    ...(identity.libraryMediaType ? { libraryMediaType: identity.libraryMediaType } : {}),
  };
}

function watchedSummary(row) {
  const status = statusLabel(row.status);
  if (row.end_date) {
    const date = dateLabel(row.end_date);
    return date ? `Watched ${date}` : 'Watched';
  }
  return status || '';
}

/**
 * Map the season rows from a media detail payload (`related.seasons`) to the
 * season model the shared DetailChildren renders.
 */
export function seasonsFromDetail(detail, identity) {
  const rows = detail?.related?.seasons;
  if (!Array.isArray(rows)) return [];
  return rows.map(row => {
    const item = row.item || {};
    const number = item.season_number;
    const progress = row.progress == null ? 0 : Number(row.progress);
    const total = row.episodes_left != null
      ? progress + Number(row.episodes_left)
      : null;
    return {
      key: `season-${number}`,
      number,
      title: item.title || `Season ${number}`,
      statusLabel: statusLabel(row.status),
      countLabel: total == null ? String(progress) : `${progress}/${total}`,
      image: item.image || '',
      score: row.score == null ? null : Number(row.score),
      percent: total ? Math.round((progress / total) * 100) : null,
      identity: {
        mediaType: 'season',
        source: item.source || identity.source,
        mediaId: item.media_id || identity.mediaId,
        seasonNumber: number,
        title: item.title,
      },
      href: item.url || '',
      progress,
      total,
    };
  });
}

/**
 * Map one page of the season episodes endpoint to the episode rows the
 * shared DetailChildren renders.
 */
/** The issues endpoint as the native "Issues" cards read it. */
export function issuesFromPayload(payload, identity) {
  const pagination = payload?.pagination || {};
  return {
    total: Number(pagination.total) || 0,
    rows: (payload?.results || []).map(issue => ({
      key: `issue-${issue.media_id}`,
      title: issue.title || 'Unknown',
      subtitle: `Issue #${issue.issue_number}${issue.cover_date ? ` \u00b7 ${issue.cover_date}` : ''}`,
      image: issue.image || '',
      read: Boolean(issue.read),
      identity: { mediaType: 'comicissue', source: issue.source || identity.source, mediaId: String(issue.media_id) },
    })),
  };
}

/** The web episode runtime: "59m", or "1h 0m" from an hour up. */
function episodeRuntime(minutes) {
  if (typeof minutes === 'string' && /[hm]/i.test(minutes)) return minutes;
  const total = Number(minutes);
  if (!Number.isFinite(total) || total <= 0) return '';
  return total >= 60 ? `${Math.floor(total / 60)}h ${Math.round(total % 60)}m` : `${total}m`;
}

export function episodesFromPayload(payload, identity, seasonNumber) {
  const rows = Array.isArray(payload) ? payload : payload?.results || [];
  return rows.map(row => {
    const item = row.item || {};
    const number = row.display_episode_number ?? item.episode_number;
    const watched = Boolean(row.watched || row.end_date) || statusLabel(row.status) === 'Completed';
    const scaleMax = Number(row.scale_max || row.scaleMax || payload?.scale_max) || 10;
    return {
      key: `episode-${item.source || identity.source}-${item.season_number ?? seasonNumber}-${item.episode_number}`,
      number,
      code: `E${number}`,
      title: item.title || `Episode ${number}`,
      image: item.image ? { src: item.image } : null,
      dateLabel: dateLabel(row.end_date || item.release_datetime),
      subtitle: [`Episode ${number}`, formatUserDate(row.air_date || item.release_datetime), episodeRuntime(row.runtime ?? item.runtime_minutes)]
        .filter(Boolean).join(' \u2022 '),
      overview: row.overview || item.synopsis || 'No synopsis available.',
      collected: Boolean(row.collected),
      endedLabel: watched ? `Ended: ${dateLabel(row.end_date) || 'No date provided'}` : '',
      playsLabel: watched ? '1\u00d7 watched' : '',
      runtime: episodeRuntime(row.runtime ?? item.runtime_minutes),
      watched,
      score: Object.hasOwn(row, 'display_score') ? row.display_score
        : row.score == null ? null : Number(row.score) * scaleMax / 10,
      scaleMax,
      canWatch: row.can_watch !== false,
      canCollect: Boolean(row.can_collect),
      canRate: row.can_rate !== false,
      canList: row.can_list !== false,
      history: { summary: watchedSummary(row) },
      identity: episodeIdentity({ ...identity, source: item.source || identity.source, mediaId: item.media_id || identity.mediaId }, item.season_number ?? seasonNumber, item.episode_number),
      href: item.url || '',
    };
  });
}

function watchProviders(source) {
  const providers = source?.watch_providers;
  if (!providers || typeof providers !== 'object') return [];
  return Object.entries(providers)
    .filter(([, value]) => Array.isArray(value) && value.length)
    .map(([market, names]) => ({ market: String(market).toUpperCase(), names: names.map(String) }));
}

function consumptionRows(state, detail) {
  const fromState = state && Array.isArray(state.consumptions) ? state.consumptions : null;
  if (fromState) return fromState;
  return Array.isArray(detail?.consumptions) ? detail.consumptions : [];
}

/**
 * Build the tracking summary values from a /state/ payload. Returns null when
 * the item is untracked. `first` is the latest entry, as the API orders them.
 */
export function trackingFromState(state, detail) {
  const rows = consumptionRows(state, detail);
  const tracked = state ? Boolean(state.tracked) : Boolean(detail?.tracked);
  if (!tracked) return null;
  const consumption = rows[0] || {};
  return {
    id: consumption.consumption_id ?? null,
    status: statusLabel(consumption.status),
    score: consumption.score == null ? null : Number(consumption.score),
    progress: consumption.progress == null ? null : Number(consumption.progress),
    startDate: dateLabel(consumption.start_date),
    endDate: dateLabel(consumption.end_date),
    notes: String(consumption.notes || ''),
  };
}

/** Bounded repeat entries, with exact instance identities for editing. */
export function trackingEntriesFromPayload(payload = {}) {
  const entries = (payload.tracking_entries || []).map(entry => {
    const scaleMax = Number(entry.scale_max) || 10;
    const score = entry.display_score;
    const progress = entry.formatted_progress ?? entry.completed_episode_count ?? entry.progress;
    return {
      id: String(entry.id),
      scoreLabel: score == null ? '' : `${score}${scaleMax === 5 ? '/5' : ''}`,
      statusLabel: statusLabel(entry.status),
      progressLabel: progress == null ? '' : `${progress}${entry.max_progress ? `/${entry.max_progress}` : ''}`,
      startedLabel: formatUserDate(entry.start_date),
      finishedLabel: formatUserDate(entry.end_date),
      notes: entry.notes || '',
      editable: Boolean(entry.can_edit),
      identity: { ...entry.identity, mediaType: entry.media_type || entry.identity?.detailType || entry.identity?.mediaType,
        ...(entry.identity?.mediaType === 'anime' ? { libraryMediaType: 'anime' } : {}) },
    };
  });
  return { entries, count: payload.tracking_entries_count ?? entries.length, hasMore: Boolean(payload.tracking_entries_has_more) };
}

/** Notes written on any play, newest first, as the web "Your Notes" section. */
export function notesFromConsumptions(rows) {
  return (rows || [])
    .filter(row => row && String(row.notes || '').trim())
    .map(row => ({
      id: String(row.consumption_id),
      text: String(row.notes),
      date: dateLabel(row.end_date || row.progressed_at || row.created),
    }));
}

/** Play history rows for the page history section. */
export function historyFromConsumptions(rows) {
  return (rows || []).map(row => {
    const start = dateLabel(row.start_date);
    const end = dateLabel(row.end_date);
    const range = start && end && start !== end ? `${start} - ${end}` : (end || start);
    return {
      id: String(row.consumption_id),
      title: statusLabel(row.status) || 'Play',
      detail: [
        row.progress != null && row.progress !== '' ? `Progress ${row.progress}` : '',
        row.score != null ? `Score ${Number(row.score)}` : '',
      ].filter(Boolean).join(' · '),
      date: range || dateLabel(row.created),
    };
  });
}

function plural(count, one, many) {
  return count === 1 ? one : many.replace('%s', String(count));
}

const PROGRESS_LINE_TYPES = new Set(['tv', 'season', 'anime', 'manga', 'book', 'comic']);

const ACTIVITY_VERBS = {
  movie: 'Watched',
  tv: 'Watched',
  season: 'Watched',
  episode: 'Watched',
  anime: 'Watched',
  manga: 'Read',
  book: 'Read',
  comic: 'Read',
  game: 'Played',
  boardgame: 'Played',
  music: 'Listened',
  podcast: 'Listened',
};

function runtimeText(minutes) {
  const total = Number(minutes);
  if (!Number.isFinite(total) || total <= 0) return '';
  const hours = Math.floor(total / 60);
  const rest = Math.round(total % 60);
  if (!hours) return `${rest}min`;
  return rest ? `${hours}h ${rest}min` : `${hours}h`;
}

/**
 * The activity line under the title: "1h 58min (Watched once)". Mirrors the
 * web subtitle's runtime plus play count; empty when there is neither.
 */
export function activityLine({ mediaType, runtime, plays, progress, maxProgress }) {
  if (PROGRESS_LINE_TYPES.has(mediaType)) {
    // The web subtitle for these types prints the tracked progress, with the
    // total for shows and the reading types, instead of a play count.
    if (progress == null || progress === '') return '';
    const withTotal = mediaType !== 'season' && maxProgress
      && !(['tv', 'book', 'manga', 'comic'].includes(mediaType) && Number(progress) === 0);
    return `Progress: ${progress}${withTotal ? `/${maxProgress}` : ''}`;
  }
  const verb = ACTIVITY_VERBS[mediaType] || 'Played';
  const count = plays || 0;
  const played = count
    ? `${verb} ${plural(count, 'once', '%s times')}`
    : '';
  if (runtime && played) return `${runtime} (${played})`;
  return runtime || played;
}

/**
 * A show's hero line with the watched time in front, as the web prints it:
 * "Progress: 4/6" becomes "2h 48min (4/6)". Unchanged without a watched time.
 */
export function activityLineWithWatched(line, watched) {
  if (!watched) return line || '';
  const progress = String(line || '').replace(/^Progress:\s*/, '');
  return progress ? `${watched} (${progress})` : watched;
}

/** The phone hero subtitle, in the same order as detail_activity_subtitle.html. */
export function detailHeaderText(activity, details = {}) {
  const metadata = details || {};
  const mobileMeta = [
    yearOf(metadata.year || metadata.start_year || metadata.release_date || metadata.first_air_date),
    metadata.runtime,
  ].filter(hasValue).join(' · ');
  if (!activity) return { activityText: '', mobileMeta };

  const primary = String(activity.primary_text || '').trim();
  const duration = String(activity.duration_text || '').trim();
  const start = formatUserDate(activity.date_start);
  const end = formatUserDate(activity.date_end);
  const dates = start && end
    ? (activity.collapse_same_day || start === end ? start : `${start} - ${end}`)
    : start || end;
  const activityText = [dates, duration].filter(Boolean).join(' • ') || primary;
  if (!activityText) return { activityText: '', mobileMeta };
  return { activityText, mobileMeta: '' };
}

function activityDates(rows, label = dateLabel) {
  const dated = (rows || []).filter(row => row.end_date || row.start_date);
  if (!dated.length) return '';
  const ends = dated.map(row => String(row.end_date || row.start_date)).sort();
  const first = label(dated.map(row => String(row.start_date || row.end_date)).sort()[0]);
  const last = label(ends[ends.length - 1]);
  if (!first || !last) return first || last;
  return first === last ? first : `${first} - ${last}`;
}

const DETAIL_ORDER = [
  'release_date', 'first_air_date', 'last_air_date', 'air_date', 'runtime', 'total_runtime',
  'format', 'status', 'seasons', 'episodes', 'certification', 'country', 'languages',
  'studios', 'authors', 'publisher', 'isbn', 'number_of_pages', 'platforms', 'developers',
  'publishers', 'designers', 'players', 'playtime', 'min_age', 'year', 'budget', 'revenue',
];
const DETAIL_LABELS = {
  release_date: 'Release Date',
  first_air_date: 'AIR DATES',
  last_air_date: 'Last Air Date',
  air_date: 'Air Date',
  runtime: 'RUNTIME',
  total_runtime: 'RUNTIME',
  number_of_pages: 'Number Of Pages',
  start_date: 'Start Date',
  end_date: 'End Date',
  min_age: 'Minimum Age',
};

const DATE_DETAIL_KEYS = new Set(['release_date', 'last_air_date', 'air_date', 'start_date', 'end_date', 'publish_date']);

function humanize(key) {
  if (DETAIL_LABELS[key]) return DETAIL_LABELS[key];
  // The web card title-cases the provider's key.
  return key.replace(/_/g, ' ').replace(/\b\w/g, char => char.toUpperCase());
}

function detailValue(value) {
  if (Array.isArray(value)) {
    const parts = value.map(entry => (typeof entry === 'object' ? '' : String(entry))).filter(Boolean);
    return parts.join(', ');
  }
  if (value && typeof value === 'object') return '';
  return hasValue(value) ? String(value) : '';
}

/**
 * The "Details" card rows. Uses the detail payload's `details` block and falls
 * back to the stored metadata projection when the provider detail is missing.
 */
export function detailRows(detail, fields) {
  const source = { ...(detail?.details || {}) };
  const seasons = Number(source.seasons);
  // These are API serialization helpers; web provider details keep them outside the card.
  delete source.last_episode_season;
  delete source.next_episode_season;
  // The web card prints "runtime" as "2h 16min", prints "total_runtime" as the provider gave it,
  // and keeps one RUNTIME row (the total wins).
  if (typeof source.runtime === 'string') source.runtime = source.runtime.replace(/(\d)m$/, '$1min');
  if (!Object.keys(source).length && fields) {
    if (fields.release_datetime) source.release_date = String(fields.release_datetime).slice(0, 10);
    if (fields.runtime_minutes) source.runtime = runtimeText(fields.runtime_minutes);
    source.format = fields.format;
    source.status = fields.status;
    source.country = fields.country;
    source.languages = fields.languages;
    source.studios = fields.studios;
  }
  if (hasValue(fields?.total_runtime)) source.total_runtime = fields.total_runtime;
  if (hasValue(source.total_runtime)) delete source.runtime;
  if (!hasValue(source.runtime) && !hasValue(source.total_runtime) && fields?.runtime_minutes) {
    source.runtime = runtimeText(fields.runtime_minutes);
  }
  // The web card prints one "AIR DATES" row for a show's first and last air date.
  if (hasValue(source.first_air_date)) {
    const first = formatUserDate(source.first_air_date) || detailValue(source.first_air_date);
    const last = hasValue(source.last_air_date) ? (formatUserDate(source.last_air_date) || '') : '';
    source.first_air_date = last ? `${first} \u2013 ${last}` : first;
  }
  delete source.last_air_date;
  if (detail?.media_type === 'anime' && !hasValue(source.total_runtime)) {
    // The web card multiplies the episode length by the episode count.
    const each = Number(String(source.runtime || '').replace(/[^0-9.]/g, ''));
    const count = Number(source.episodes);
    if (each > 0 && count > 0) {
      source.total_runtime = runtimeText(each * count);
      delete source.runtime;
    }
  }
  if (['tv', 'anime', 'season'].includes(detail?.media_type)) {
    // The web card folds the episode count into the runtime row and has no season row.
    const episodes = Number(source.episodes);
    delete source.seasons;
    delete source.episodes;
    const runtimeKey = hasValue(source.total_runtime) ? 'total_runtime' : 'runtime';
    if (episodes > 0 && (detail.media_type !== 'season' || hasValue(source[runtimeKey]))) {
      const count = `${episodes} episode${episodes === 1 ? '' : 's'}`;
      // A runtime key the provider left empty reads "Unknown"; with no key at all only the count shows.
      const lead = hasValue(source[runtimeKey]) ? source[runtimeKey] : (runtimeKey in source ? 'Unknown' : '');
      source[runtimeKey] = lead ? `${lead} \u00b7 ${count}` : count;
    }
  }
  // The web card lists the provider's keys in the provider's order, and keeps
  // Studios for its own section. Without provider details the known order applies.
  const studiosLinked = Boolean(detail?.studios?.length);
  let keys = Object.keys(source).filter(key => !(studiosLinked && (key === 'studios' || key === 'companies'))
    && (DETAIL_ORDER.includes(key) || !/(_id|_ids|crew|guest_stars|number|type|game_lengths)$/.test(key)));
  const provided = [...new Set(Object.keys(detail?.details || {}).map(key =>
    key === 'runtime' && hasValue(source.total_runtime) ? 'total_runtime' : key))];
  if (provided.length) {
    const known = provided.filter(key => keys.includes(key));
    const added = keys.filter(key => !provided.includes(key));
    const anchor = known.findIndex(key => ['release_date', 'first_air_date', 'status'].includes(key));
    keys = [...known];
    keys.splice(anchor + 1, 0, ...added);
  } else {
    keys = [...DETAIL_ORDER.filter(key => keys.includes(key)), ...keys.filter(key => !DETAIL_ORDER.includes(key))];
  }
  // The web card prints the linked authors last, in capitals.
  keys = [...keys.filter(key => !/^authors?$/.test(key)), ...keys.filter(key => /^authors?$/.test(key))];
  return keys
    .map(key => ({
      label: key === 'total_runtime' && seasons > 0 ? `RUNTIME (${seasons} SEASON${seasons === 1 ? '' : 'S'})`
        : /^authors?$/.test(key) ? 'AUTHOR' : humanize(key),
      // The web card prints "Unknown" for a key the provider returned empty.
      value: source[key] === null ? 'Unknown'
        : (DATE_DETAIL_KEYS.has(key) ? (formatUserDate(source[key]) || detailValue(source[key]))
          : (key === 'isbn' && Array.isArray(source[key]) ? source[key].join('\n') : detailValue(source[key]))),
    }))
    .filter(row => row.value);
}

// The web crew list leads with the director and the writers; the rest keeps the provider's order.
function crewRank(role) {
  if (role === 'Director') return 0;
  return role === 'Writer' ? 1 : 2;
}

function peopleRows(rows) {
  return (Array.isArray(rows) ? rows : [])
    .filter(person => person && person.name)
    .map(person => ({
      id: String(person.person_id || person.id || person.name),
      personId: person.person_id || person.id ? String(person.person_id || person.id) : '',
      name: String(person.name),
      role: String(person.role || person.character || person.job || person.department || ''),
      // Episode credits come straight from TMDB, with a profile path instead of an image URL.
      image: person.image || (person.profile_path ? `https://image.tmdb.org/t/p/w185${person.profile_path}` : ''),
    }));
}

function relatedRow(row, name, source, index) {
  const item = row && typeof row === 'object' && row.item ? row.item : row;
  if (!item || !(item.title || item.season_header_title) || !item.media_id) return null;
  return {
    key: `${name}-${item.source || source}-${item.media_type}-${item.media_id}-${index}`,
    title: String(item.season_header_title || item.title),
    image: item.image || '',
    identity: {
      mediaType: item.media_type,
      source: item.source || source,
      mediaId: String(item.media_id),
    },
  };
}

function recommendationRows(detail, source, extra) {
  const rows = detail?.related?.recommendations?.length ? detail.related.recommendations : extra;
  return (Array.isArray(rows) ? rows : [])
    .map((row, index) => relatedRow(row, 'recommendations', source, index))
    .filter(Boolean);
}

/** Web "Related Media" sections: every related list except seasons and recommendations. */
export function relatedSections(detail, source) {
  const related = detail?.related;
  if (!related || typeof related !== 'object') return [];
  return Object.entries(related)
    .filter(([name, rows]) => name !== 'seasons' && name !== 'episodes' && name !== 'recommendations' && Array.isArray(rows) && rows.length)
    .map(([name, rows]) => ({
      id: name,
      title: name === 'dlcs' ? 'DLCs' : name.replace(/_/g, ' ').replace(/\b\w/g, char => char.toUpperCase()),
      items: rows.map((row, index) => relatedRow(row, name, source, index)).filter(Boolean),
    }))
    .filter(section => section.items.length);
}

function plural_(count, one, many) {
  return Number(count) === 1 ? one : many;
}

function countLabel(count, noun) {
  if (count == null || count === '') return '';
  const formatted = Number(count).toLocaleString('en-US');
  return `${formatted} ${plural_(count, noun, `${noun}s`)}`;
}

const SOURCE_NAMES = { tmdb: 'TMDB', tvdb: 'TVDB', mal: 'MAL', igdb: 'IGDB', mangaupdates: 'MU', openlibrary: 'OL' };

function scoreChips(detail, mediaType) {
  const chips = [];
  // The web chip is gated by a vote count, including zero, rather than a
  // stored rating alone. A provider can return a score without votes.
  if (detail?.score_count != null) {
    chips.push({
      key: 'provider',
      label: SOURCE_NAMES[detail.source] || String(detail.source || 'Provider').toUpperCase(),
      value: hasValue(detail.score) ? Number(detail.score).toFixed(1) : '-',
      count: countLabel(detail.score_count, 'vote'),
      graph: 'provider',
    });
  }
  if (detail?.imdb_rating_count != null && ['movie', 'tv', 'anime', 'season', 'episode'].includes(mediaType)) {
    chips.push({
      key: 'imdb',
      label: 'IMDb',
      value: hasValue(detail.imdb_rating) ? Number(detail.imdb_rating).toFixed(1) : '-',
      count: countLabel(detail.imdb_rating_count, 'rating'),
      graph: 'imdb',
    });
  }
  return chips;
}

/**
 * Merge the server's stored chips (Trakt, IMDb, MAL, OpenCritic) into the
 * chips the detail payload already yields, in the web row's order.
 */
export function mergeScoreChips(base, scores) {
  const chips = [...(base || [])];
  for (const row of scores?.chips || []) {
    if (row.key === 'provider') continue;
    const value = row.rating == null ? '-' : (row.key === 'opencritic' ? String(row.rating) : Number(row.rating).toFixed(1));
    const chip = {
      key: row.key,
      label: row.label,
      value,
      count: row.key === 'opencritic'
        ? (row.percentRecommended != null ? `${row.percentRecommended}% recommend` : (row.tier || ''))
        : countLabel(row.count, 'rating'),
      url: row.url || '',
      logo: scores?.logos?.[row.key] || '',
      graph: row.key === 'trakt' ? 'trakt' : null,
    };
    const at = chips.findIndex(existing => existing.key === row.key);
    if (at >= 0) chips[at] = { ...chips[at], ...chip, graph: chips[at].graph || chip.graph };
    else chips.push(chip);
  }
  const provider = chips.findIndex(existing => existing.key === 'provider');
  if (provider >= 0 && scores?.logos?.provider) chips[provider] = { ...chips[provider], logo: scores.logos.provider };
  const order = ['provider', 'trakt', 'imdb', 'mal', 'opencritic'];
  return chips.sort((a, b) => order.indexOf(a.key) - order.indexOf(b.key));
}

/** The carousel payload as the native hero, trailer and gallery read it. */
export function artworkFromPayload(payload) {
  if (!payload || !payload.available) return null;
  const photos = (payload.photos || []).filter(photo => photo && photo.url);
  return {
    hero: payload.hero?.url ? { url: payload.hero.url, logoUrl: payload.hero.logo_url || '' } : null,
    logoUrl: payload.overview?.logo_url || payload.hero?.logo_url || '',
    video: payload.video?.key ? { key: String(payload.video.key), url: `https://www.youtube.com/watch?v=${payload.video.key}` } : null,
    photos: photos.map(photo => ({ url: photo.url, thumbUrl: photo.thumb_url || photo.url })),
  };
}

/**
 * Merge the detail, stored-metadata and tracking-state payloads and the tapped
 * card into the model the native detail page renders. Any payload may be
 * missing; the tapped card is what shows while nothing else has arrived.
 */
export function detailModelFromPayloads({ item, metadata, state, detail, identity, recommendations } = {}) {
  const fields = (metadata && metadata.fields) || {};
  const fallbackTitle = item?.title || item?.identity?.title || '';
  const title = detail?.title || fields.title || fallbackTitle;
  const altTitle = fields.original_title && fields.original_title !== fields.title
    ? fields.original_title
    : '';
  const tracking = trackingFromState(state, detail);
  const rows = tracking ? consumptionRows(state, detail) : [];
  const mediaType = identity?.mediaType || detail?.media_type || item?.identity?.mediaType || '';
  const runtime = (detail?.details && detail.details.runtime) || runtimeText(fields.runtime_minutes);
  // An explicitly empty provider synopsis is also empty on the web. Stored
  // metadata is only a fallback when the detail request omitted the field.
  const synopsis = detail?.synopsis ?? fields.synopsis;
  const source = identity?.source || detail?.source || '';
  const image = detail?.image || fields.image || item?.image?.src || item?.image || '';
  const genres = asArray(detail?.genres?.length ? detail.genres : fields.genres);

  return {
    title,
    subtitle: altTitle,
    altTitle,
    publicView: false,
    readOnly: false,
    itemId: detail?.id ?? metadata?.item_id ?? null,
    catalogItemId: metadata?.item_id ?? null,
    tracked: Boolean(tracking),
    tracking,
    score: tracking ? tracking.score : null,
    genres,
    studios: detail?.studios?.length ? detail.studios.map(studio => studio.name) : [],
    studioLinks: (detail?.studios || []).filter(studio => studio.name).map(studio => ({
      id: studio.id == null ? '' : String(studio.id), name: studio.name, source: studio.source || source,
    })),
    watchProviders: watchProviders(metadata),
    artwork: {
      src: typeof image === 'string' ? image : '',
      alt: title,
      aspect: mediaType === 'video' ? 'landscape' : 'poster',
    },
    backdrop: detail?.backdrop || '',
    activityLine: activityLine({
      mediaType,
      runtime,
      plays: rows.filter(row => row.end_date).length,
      progress: rows[0]?.progress,
      maxProgress: detail?.max_progress,
    }),
    // The web season page prints the time with each date; the other types print the date alone.
    activityDates: activityDates(rows, mediaType === 'season' ? formatUserDateTime : dateLabel),
    year: yearOf(fields.release_datetime || detail?.details?.release_date),
    // The phone hero's year and runtime line reads the provider details only.
    metaYear: yearOf(detail?.details?.release_date || detail?.details?.first_air_date || detail?.details?.air_date),
    // Only films and seasons carry their runtime on the web's hero line.
    metaRuntime: ['movie', 'season'].includes(mediaType) && detail?.details?.runtime && detail.details.runtime !== 'Unknown'
      ? String(detail.details.runtime) : '',
    // The episode page prints the air date and "Watched once / N times".
    releaseDate: formatUserDate(fields.release_datetime || detail?.details?.release_date) || '',
    plays: rows.filter(row => row.end_date).length,
    runtime: runtime || '',
    providerScores: scoreChips(detail, mediaType),
    details: detailRows(detail, { ...fields, total_runtime: metadata?.total_runtime }),
    synopsis: synopsis ? { text: synopsis, previewLines: 3 } : null,
    notes: notesFromConsumptions(rows),
    history: historyFromConsumptions(rows),
    // Web "Your History": only TV shows and seasons with more than one play, listing the older plays.
    repeats: (mediaType === 'tv' || mediaType === 'season') && rows.length > 1
      ? historyFromConsumptions(rows).slice(1) : [],
    cast: peopleRows(detail?.cast?.length ? detail.cast : detail?.details?.guest_stars),
    crew: peopleRows(detail?.crew?.length ? detail.crew : detail?.details?.crew)
      .sort((a, b) => crewRank(a.role) - crewRank(b.role)),
    recommendations: recommendationRows(detail, source, recommendations),
    relatedSections: relatedSections(detail, source),
    sourceUrl: detail?.source_url || metadata?.attribution?.source_url || '',
    sourceName: String(detail?.source || metadata?.attribution?.source || '').toUpperCase(),
    maxProgress: detail?.max_progress ?? null,
    canRequest: mediaType === 'movie' || mediaType === 'tv',
    sourceKind: source,
    mediaId: identity?.mediaId != null ? String(identity.mediaId) : '',
  };
}

/**
 * The tracking half of a detail page from the stored-metadata and state
 * payloads: what the write controls need beside the page payload.
 */
export function trackingFromPayloads({ identity, metadata, state } = {}) {
  const tracking = trackingFromState(state, null);
  const rows = tracking ? consumptionRows(state, null) : [];
  return {
    catalogItemId: metadata?.item_id ?? null,
    tracked: Boolean(tracking),
    tracking,
    score: tracking ? tracking.score : null,
    plays: rows.filter(row => row.end_date).length,
    notes: notesFromConsumptions(rows),
    history: historyFromConsumptions(rows),
    sourceUrl: metadata?.attribution?.source_url || '',
    sourceName: String(metadata?.attribution?.source || identity?.source || '').toUpperCase(),
  };
}

export const DETAIL_STATUS_LABELS = STATUS_LABELS;
