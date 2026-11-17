const api = path => `/api/v1${path}`;
const artistPath = artistId => api(`/music/artists/${encodeURIComponent(artistId)}/`);
const albumPath = albumId => api(`/music/albums/${encodeURIComponent(albumId)}/`);
const showPath = showId => api(`/podcasts/shows/${encodeURIComponent(showId)}/`);
const trackPath = trackId => api(`/music/tracks/${encodeURIComponent(trackId)}/`);
const episodePath = episodeId => api(`/podcasts/episodes/${encodeURIComponent(episodeId)}/`);

// Web release-type grouping (app/services/music.py build_discography_groups).
const GROUP_ORDER = { Album: 0, EP: 1, Single: 2, Broadcast: 3, Other: 4, Unknown: 5 };

function releaseGroup(releaseType) {
  if (!releaseType) return { primary: 'Unknown', label: 'Unknown' };
  const [primaryPart, rest] = String(releaseType).split(' + ', 2);
  const secondary = (rest || '').split(',').map(part => part.trim()).filter(Boolean);
  let primary = primaryPart.trim() || 'Unknown';
  let label = String(releaseType);
  if (primary === 'Compilation' && !secondary.length) {
    primary = 'Album';
    label = 'Album + Compilation';
  } else if (primary === 'Other' && secondary.length) {
    label = secondary.join(', ');
  }
  return { primary, label };
}

export function groupAlbums(albums) {
  const groups = new Map();
  for (const album of albums || []) {
    const { primary, label } = releaseGroup(album.releaseType);
    if (!groups.has(label)) groups.set(label, { label, primary, albums: [] });
    groups.get(label).albums.push(album);
  }
  return [...groups.values()].sort((left, right) => (
    (GROUP_ORDER[left.primary] ?? GROUP_ORDER.Unknown)
    - (GROUP_ORDER[right.primary] ?? GROUP_ORDER.Unknown)
    || left.label.localeCompare(right.label)
  ));
}

export function flattenAlbums(artist) {
  return (artist?.discography || []).flatMap(group => group.albums || []);
}

export function todayStr(now = new Date()) {
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const day = String(now.getDate()).padStart(2, '0');
  return `${now.getFullYear()}-${month}-${day}`;
}

export function nowTimeStr(now = new Date()) {
  const hours = String(now.getHours()).padStart(2, '0');
  const minutes = String(now.getMinutes()).padStart(2, '0');
  return `${hours}:${minutes}`;
}

export function nowDateTimeStr(now = new Date()) {
  return `${todayStr(now)}T${nowTimeStr(now)}`;
}

// 'YYYY-MM-DDTHH:mm[:ss]' -> { date, time }; date-only values keep a 12:00 time
// like the web listen form.
export function splitDateTime(value) {
  const raw = String(value || '');
  const [date, timePart = ''] = raw.split('T');
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return { date: '', time: '' };
  const time = /^\d{2}:\d{2}/.test(timePart) ? timePart.slice(0, 5) : '12:00';
  return { date, time };
}

export function endDateStamp(value) {
  const { date, time } = splitDateTime(value);
  return date ? `${date}T${time}:00` : '';
}

export function formatDurationMs(durationMs) {
  const total = Number(durationMs);
  if (!Number.isFinite(total) || total <= 0) return '';
  const secondsTotal = Math.round(total / 1000);
  const hours = Math.floor(secondsTotal / 3600);
  const minutes = Math.floor((secondsTotal % 3600) / 60);
  const seconds = secondsTotal % 60;
  if (hours) return `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
  return `${minutes}:${String(seconds).padStart(2, '0')}`;
}

function numericId(value) {
  const number = Number(value);
  return Number.isInteger(number) && number > 0 ? number : null;
}

// Tracker payloads arrive as string scores on a raw 0-10 storage scale.
export function mapTracker(tracker) {
  if (!tracker || typeof tracker !== 'object') {
    return { status: null, score: null, startDate: '', endDate: '', notes: '' };
  }
  return {
    status: tracker.status || null,
    score: tracker.score == null ? null : Number(tracker.score),
    startDate: tracker.start_date || '',
    endDate: tracker.end_date || '',
    notes: tracker.notes || '',
  };
}

export function mapAlbumPayload(payload) {
  if (!payload || typeof payload !== 'object') return null;
  const tracker = mapTracker(payload.tracker);
  const releaseDate = payload.release_date || '';
  return {
    id: numericId(payload.id),
    albumId: numericId(payload.id),
    title: payload.title || '',
    image: payload.image || '',
    releaseType: payload.release_type || '',
    hasReleaseGroup: Boolean(payload.musicbrainz_release_group_id),
    artistId: numericId(payload.artist_id),
    artistName: payload.artist_name || '',
    year: /^\d{4}/.test(String(releaseDate)) ? String(releaseDate).slice(0, 4) : '',
    releaseDate,
    genres: Array.isArray(payload.genres) ? payload.genres.filter(Boolean) : [],
    status: tracker.status,
    score: tracker.score,
    notes: tracker.notes,
    format: null,
    runtime: null,
    plays: payload.play_count == null ? null : Number(payload.play_count),
    firstListened: splitDateTime(payload.first_listened).date,
    lastListened: splitDateTime(payload.last_listened).date,
    libraryTracks: payload.library_track_count == null ? null : Number(payload.library_track_count),
    totalTracks: payload.total_tracks == null ? null : Number(payload.total_tracks),
    tracks: [],
  };
}

export function mapArtistPayload(payload) {
  if (!payload || typeof payload !== 'object') return null;
  const tracker = mapTracker(payload.tracker);
  const albums = (payload.albums || []).map(mapAlbumPayload).filter(Boolean);
  return {
    id: numericId(payload.id),
    artistId: numericId(payload.id),
    name: payload.name || '',
    image: payload.image || '',
    type: payload.artist_type || '',
    origin: payload.country || '',
    formed: '',
    bio: '',
    genres: Array.isArray(payload.genres) ? payload.genres.filter(Boolean) : [],
    status: tracker.status,
    score: tracker.score,
    notes: tracker.notes,
    // The artist endpoint exposes no MusicBrainz rating or bio.
    musicbrainz: { rating: null, votes: null },
    totalPlays: payload.total_plays == null ? null : Number(payload.total_plays),
    firstListened: splitDateTime(payload.first_listened).date,
    lastListened: splitDateTime(payload.last_listened).date,
    collection: payload.collection
      ? {
        albums: payload.collection.albums,
        totalAlbums: payload.collection.total_albums,
        tracks: payload.collection.tracks,
        totalTracks: payload.collection.total_tracks,
      }
      : { albums: null, totalAlbums: null, tracks: null, totalTracks: null },
    relations: { members: [], alsoIn: [] },
    discography: groupAlbums(albums),
  };
}

export async function loadArtistWithRelations(transport, artistId) {
  const [artist, relations] = await Promise.all([
    transport.loadArtist({ artistId }),
    transport.loadArtistRelations({ artistId }).catch(() => null),
  ]);
  return { artist, relations };
}

// A new artist or album is filled in by a background repair on the server
// (app/music_page_repair.py). While it runs the API says so, and the screen
// loads again quietly, as the web page's poller does: every 2 seconds for
// about two minutes.
export const REPAIR_POLL_MS = 2000;
export const REPAIR_POLL_MAX_ATTEMPTS = 60;

export function repairPending(payload, attempts = 0) {
  if (attempts >= REPAIR_POLL_MAX_ATTEMPTS) return false;
  return Boolean(payload?.repair_pending || payload?.pending);
}

export function mapTracksPayload(payload) {
  const results = Array.isArray(payload?.results)
    ? payload.results
    : Array.isArray(payload) ? payload : [];
  return results.filter(Boolean).map(track => {
    const lastPlayed = splitDateTime(track.last_played);
    return {
      id: numericId(track.id),
      trackId: numericId(track.id),
      musicId: numericId(track.music_id),
      number: track.track_number == null ? 0 : Number(track.track_number),
      disc: track.disc_number == null ? 1 : Number(track.disc_number),
      title: track.title || '',
      duration: formatDurationMs(track.duration_ms),
      durationMs: track.duration_ms ?? null,
      playCount: Number(track.play_count) || 0,
      lastPlayed: lastPlayed.date ? lastPlayed : null,
      score: track.score == null || track.score === '' ? null : Number(track.score),
      notes: '',
      codec: null,
      image: '',
      listens: [],
    };
  });
}

export function mapShowPayload(payload) {
  if (!payload || typeof payload !== 'object') return null;
  const tracker = mapTracker(payload.tracker);
  return {
    id: numericId(payload.id),
    showId: numericId(payload.id),
    title: payload.title || '',
    author: payload.author || '',
    image: payload.image || '',
    description: payload.description || '',
    genres: Array.isArray(payload.genres) ? payload.genres.filter(Boolean) : [],
    status: tracker.status,
    score: tracker.score,
    notes: tracker.notes,
    websiteUrl: payload.website_url || '',
    rssFeedUrl: payload.rss_feed_url || '',
    source: payload.source || '',
    // Only a manual show's episodes are edited by hand; feed shows own theirs.
    isManual: payload.source === 'manual',
    episodeTotal: payload.episode_count == null ? null : Number(payload.episode_count),
    listenedCount: payload.listened_count == null ? null : Number(payload.listened_count),
  };
}

export function mapEpisodesPayload(payload) {
  const results = Array.isArray(payload?.results) ? payload.results : [];
  const episodes = results.filter(Boolean).map(episode => {
    const status = episode.status || null;
    return {
      id: numericId(episode.id),
      episodeId: numericId(episode.id),
      number: episode.episode_number == null ? 0 : Number(episode.episode_number),
      title: episode.title || '',
      published: splitDateTime(episode.published).date,
      duration: episode.duration || '',
      durationSeconds: episode.duration_seconds ?? null,
      description: episode.description || '',
      websiteUrl: episode.website_url || '',
      status,
      hasHistory: Boolean(episode.has_history),
      inProgress: status === 'In progress',
      played: status === 'Completed' || Boolean(episode.has_history),
      notes: '',
      score: episode.score == null || episode.score === '' ? null : Number(episode.score),
    };
  });
  return {
    episodes,
    total: Number(payload?.pagination?.total) || episodes.length,
  };
}

// Flat history entries (GET /api/v1/history/?...&flat=true) feed the activity
// dialogs. Podcast entry_key is the history record id, so podcast rows can be
// deleted; music day-group entry keys are not record ids and stay read-only.
export function mapHistoryEntries(payload) {
  const results = Array.isArray(payload?.results)
    ? payload.results
    : Array.isArray(payload) ? payload : [];
  return results.filter(Boolean).map(entry => {
    const played = splitDateTime(entry.played_at_local || entry.end_date);
    const notes = [
      entry.play_count > 1 ? `${entry.play_count} plays` : '',
      entry.time_range_display || '',
      entry.progress_display || '',
      entry.runtime_display || '',
    ].filter(Boolean).join(' · ');
    const recordId = numericId(entry.entry_key);
    return {
      key: String(entry.entry_key ?? played.date),
      recordId,
      deletable: recordId != null,
      date: played.date,
      time: played.time,
      dateLabel: played.date ? `${played.date} ${played.time}` : 'No date',
      title: entry.title || entry.display_title || '',
      note: notes,
    };
  });
}

// Rows of the artist, album and show browse lists (tracked items).
export function mapBrowseList(payload, kind) {
  const results = Array.isArray(payload?.results) ? payload.results : [];
  return results.filter(Boolean).map(row => {
    const tracker = mapTracker(row.tracker);
    const id = numericId(row.id);
    return {
      key: `${kind}-${id}`,
      kind,
      id,
      title: kind === 'artist' ? row.name || '' : row.title || '',
      subtitle: kind === 'album' ? row.artist_name || '' : kind === 'show' ? row.author || '' : '',
      image: row.image || '',
      status: tracker.status,
      score: tracker.score,
    };
  }).filter(row => row.id != null);
}

// GET /music/tracks/{id}/: ids that place a song under its artist and album.
export function mapTrackIdentity(payload) {
  if (!payload || typeof payload !== 'object') return null;
  return {
    trackId: numericId(payload.id),
    albumId: numericId(payload.album_id),
    artistId: numericId(payload.artist_id),
    musicId: numericId(payload.music_id),
    title: payload.title || '',
  };
}

// GET /music/tracks/{id}/plays/: one row per listen, newest first.
export function mapTrackListens(payload) {
  const results = Array.isArray(payload?.results) ? payload.results : [];
  return results.filter(Boolean).map(entry => {
    const played = splitDateTime(entry.played_at);
    return {
      id: numericId(entry.history_id),
      date: played.date,
      time: played.time,
      score: entry.score == null ? null : Number(entry.score),
    };
  }).filter(entry => entry.id != null);
}

// MusicBrainz releases of an album and the one this account chose.
export function mapReleases(payload) {
  const results = Array.isArray(payload?.results) ? payload.results : [];
  return {
    selectedId: payload?.selected_release_id || '',
    releases: results.filter(Boolean).map(release => ({
      id: String(release.release_id || ''),
      title: release.title || '',
      detail: [release.date, release.country, release.format, release.label,
        release.track_count ? `${release.track_count} tracks` : '']
        .filter(Boolean).join(' · '),
    })).filter(release => release.id),
  };
}

// Manager-shaped episode rows from the manual show endpoints.
export function mapManagedEpisodes(payload) {
  const results = Array.isArray(payload?.results) ? payload.results : [];
  return results.filter(Boolean).map(mapManagedEpisode);
}

export function mapManagedEpisode(episode) {
  return {
    id: numericId(episode.id),
    title: episode.title || '',
    description: episode.description || '',
    episodeNumber: episode.episodeNumber == null ? '' : String(episode.episodeNumber),
    seasonNumber: episode.seasonNumber == null ? '' : String(episode.seasonNumber),
    published: episode.published || '',
    duration: episode.duration || '',
    audioUrl: episode.audioUrl || '',
    websiteUrl: episode.websiteUrl || '',
    imageUrl: episode.imageUrl || '',
  };
}

// Form values to the snake_case body of POST/PATCH episodes; blank optional
// fields are left out so the form defaults apply.
export function managedEpisodeBody(values) {
  const body = { title: String(values.title || '').trim() };
  [
    ['description', 'description'],
    ['episodeNumber', 'episode_number'],
    ['seasonNumber', 'season_number'],
    ['published', 'published'],
    ['duration', 'duration'],
    ['audioUrl', 'audio_url'],
    ['websiteUrl', 'website_url'],
    ['imageUrl', 'image'],
  ].forEach(([from, to]) => {
    const value = values[from];
    if (value !== undefined && value !== null && String(value).trim() !== '') body[to] = String(value).trim();
  });
  return body;
}

const MUSIC_PAGE = 200;

async function pageAll(request, path) {
  const results = [];
  let offset = 0;
  for (;;) {
    const payload = await request(`${path}?limit=${MUSIC_PAGE}&offset=${offset}`);
    const rows = Array.isArray(payload?.results) ? payload.results : [];
    results.push(...rows);
    const total = Number(payload?.pagination?.total);
    offset += MUSIC_PAGE;
    if (!rows.length || !(Number.isFinite(total) ? offset < total : payload?.pagination?.next)) break;
  }
  return { results };
}

function trackerFields({ status, score, startDate, endDate, notes }) {
  const payload = {};
  if (status !== undefined) payload.status = status;
  if (score !== undefined) payload.score = score;
  if (startDate !== undefined) payload.start_date = startDate;
  if (endDate !== undefined) payload.end_date = endDate;
  if (notes !== undefined) payload.notes = notes;
  return payload;
}

// The native feature boundary maps the fork's music and podcast REST
// endpoints. Presentation receives plain values; credentials, CSRF, base URL
// and error handling stay in the request adapter behind this contract.
export function createRemoteMusicActions(request) {
  return {
    mode: 'server',
    loadArtist({ artistId }) {
      return request(artistPath(artistId));
    },
    saveArtist({ artistId, ...fields }) {
      return request(artistPath(artistId), {
        method: 'PATCH',
        json: trackerFields(fields),
      });
    },
    removeArtist({ artistId }) {
      return request(artistPath(artistId), { method: 'DELETE' });
    },
    syncArtistDiscography({ artistId }) {
      return request(api(`/music/artists/${encodeURIComponent(artistId)}/sync-discography/`), {
        method: 'POST',
      });
    },
    loadArtistRelations({ artistId }) {
      return request(api(`/music/artists/${encodeURIComponent(artistId)}/relations/`));
    },
    syncArtistMembers({ artistId }) {
      return request(api(`/music/artists/${encodeURIComponent(artistId)}/sync-members/`), {
        method: 'POST',
      });
    },
    deleteArtistPlays({ artistId }) {
      return request(api(`/music/artists/${encodeURIComponent(artistId)}/plays/`), {
        method: 'DELETE',
      });
    },
    loadAlbum({ albumId }) {
      return request(albumPath(albumId));
    },
    loadAlbumTracks({ albumId }) {
      return request(api(`/music/albums/${encodeURIComponent(albumId)}/tracks/`));
    },
    saveAlbum({ albumId, ...fields }) {
      return request(albumPath(albumId), {
        method: 'PATCH',
        json: trackerFields(fields),
      });
    },
    removeAlbum({ albumId }) {
      return request(albumPath(albumId), { method: 'DELETE' });
    },
    deleteAlbumPlays({ albumId }) {
      return request(api(`/music/albums/${encodeURIComponent(albumId)}/plays/`), {
        method: 'DELETE',
      });
    },
    syncAlbumMetadata({ albumId }) {
      return request(api(`/music/albums/${encodeURIComponent(albumId)}/sync-metadata/`), {
        method: 'POST',
      });
    },
    addListen({ albumId, trackId, endDate }) {
      return request(api('/music/songs/plays/'), {
        method: 'POST',
        json: {
          album_id: albumId,
          ...(trackId == null ? {} : { track_id: trackId }),
          ...(endDate ? { end_date: endDate } : {}),
        },
      });
    },
    rateTrack({ musicId, score }) {
      return request(api(`/music/tracks/${encodeURIComponent(musicId)}/score/`), {
        method: 'PATCH',
        json: { score },
      });
    },
    saveTrackNotes({ trackId, notes }) {
      return request(`${trackPath(trackId)}notes/`, {
        method: 'PATCH',
        json: { notes },
      });
    },
    deleteHistory({ mediaType, historyId }) {
      return request(api(`/history/${encodeURIComponent(mediaType)}/${encodeURIComponent(historyId)}/`), {
        method: 'DELETE',
      });
    },
    loadHistory({ mediaType, artistId, albumId, showId, limit = 20 }) {
      const params = [['flat', 'true'], ['limit', String(limit)]];
      if (mediaType) params.push(['media_type', String(mediaType)]);
      if (artistId) params.push(['artist', String(artistId)]);
      if (albumId) params.push(['album', String(albumId)]);
      if (showId) params.push(['podcast_show', String(showId)]);
      const query = params.map(([key, value]) => `${key}=${encodeURIComponent(value)}`).join('&');
      return request(`${api('/history/')}?${query}`);
    },
    loadArtists() {
      return pageAll(request, api('/music/artists/'));
    },
    loadAlbums() {
      return pageAll(request, api('/music/albums/'));
    },
    loadShows() {
      return pageAll(request, api('/podcasts/shows/'));
    },
    loadTrack({ trackId }) {
      return request(`${trackPath(trackId)}`);
    },
    loadTrackPlays({ trackId }) {
      // The server pages listens; collect every page for the history dialog.
      return pageAll(request, `${trackPath(trackId)}plays/`);
    },
    deleteTrackPlay({ trackId, historyId }) {
      return request(`${trackPath(trackId)}plays/${encodeURIComponent(historyId)}/`, { method: 'DELETE' });
    },
    loadReleases({ albumId, query = '' }) {
      return request(api(`/music/albums/${encodeURIComponent(albumId)}/releases/`), query ? { query: { q: query } } : undefined);
    },
    selectRelease({ albumId, releaseId }) {
      return request(api(`/music/albums/${encodeURIComponent(albumId)}/releases/`), {
        method: 'POST',
        json: { release_id: releaseId },
      });
    },
    loadManagedEpisodes({ showId }) {
      return request(api(`/podcasts/shows/${encodeURIComponent(showId)}/episodes/manage/`));
    },
    addManagedEpisode({ showId, values }) {
      return request(api(`/podcasts/shows/${encodeURIComponent(showId)}/episodes/manage/`), {
        method: 'POST',
        json: managedEpisodeBody(values),
      });
    },
    editManagedEpisode({ episodeId, values }) {
      return request(episodePath(episodeId), { method: 'PATCH', json: managedEpisodeBody(values) });
    },
    deleteManagedEpisode({ episodeId }) {
      return request(episodePath(episodeId), { method: 'DELETE' });
    },
    rateEpisode({ episodeId, score }) {
      return request(`${episodePath(episodeId)}score/`, { method: 'PATCH', json: { score } });
    },
    loadShow({ showId }) {
      return request(showPath(showId));
    },
    loadEpisodes({ showId, limit = 20, offset = 0 }) {
      const query = `?limit=${encodeURIComponent(limit)}&offset=${encodeURIComponent(offset)}`;
      return request(api(`/podcasts/shows/${encodeURIComponent(showId)}/episodes/${query}`));
    },
    saveShow({ showId, ...fields }) {
      return request(showPath(showId), {
        method: 'PATCH',
        json: trackerFields(fields),
      });
    },
    removeShow({ showId }) {
      return request(showPath(showId), { method: 'DELETE' });
    },
    saveEpisodeNotes({ episodeId, notes }) {
      return request(`${episodePath(episodeId)}notes/`, {
        method: 'PATCH',
        json: { notes },
      });
    },
    addEpisodePlay({ showId, episodeId, episodeUuid, endDate }) {
      return request(api('/podcasts/episodes/plays/'), {
        method: 'POST',
        json: {
          show_id: showId,
          ...(episodeId == null ? {} : { episode_id: episodeId }),
          ...(episodeUuid ? { episode_uuid: episodeUuid } : {}),
          ...(endDate ? { end_date: endDate } : {}),
        },
      });
    },
    markAllPlayed({ showId }) {
      return request(api(`/podcasts/shows/${encodeURIComponent(showId)}/mark-all-played/`), {
        method: 'POST',
      });
    },
  };
}

export function createLocalMusicActions(reason) {
  const local = async () => ({ local: true, reason });
  return {
    mode: 'local',
    reason,
    loadArtist: local,
    saveArtist: local,
    removeArtist: local,
    syncArtistDiscography: local,
    loadArtistRelations: local,
    syncArtistMembers: local,
    deleteArtistPlays: local,
    loadAlbum: local,
    loadAlbumTracks: local,
    saveAlbum: local,
    removeAlbum: local,
    deleteAlbumPlays: local,
    syncAlbumMetadata: local,
    addListen: local,
    rateTrack: local,
    saveTrackNotes: local,
    deleteHistory: local,
    loadHistory: local,
    loadArtists: local,
    loadAlbums: local,
    loadShows: local,
    loadTrack: local,
    loadTrackPlays: local,
    deleteTrackPlay: local,
    loadReleases: local,
    selectRelease: local,
    loadManagedEpisodes: local,
    addManagedEpisode: local,
    editManagedEpisode: local,
    deleteManagedEpisode: local,
    rateEpisode: local,
    loadShow: local,
    loadEpisodes: local,
    saveShow: local,
    removeShow: local,
    saveEpisodeNotes: local,
    addEpisodePlay: local,
    markAllPlayed: local,
  };
}
