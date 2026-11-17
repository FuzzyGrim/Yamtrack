import assert from 'node:assert/strict';
import test from 'node:test';
import {
  createLocalMusicActions,
  createRemoteMusicActions,
  endDateStamp,
  flattenAlbums,
  formatDurationMs,
  groupAlbums,
  loadArtistWithRelations,
  mapAlbumPayload,
  mapArtistPayload,
  mapBrowseList,
  mapManagedEpisodes,
  mapReleases,
  mapTrackIdentity,
  mapTrackListens,
  managedEpisodeBody,
  mapEpisodesPayload,
  mapHistoryEntries,
  mapShowPayload,
  mapTracksPayload,
  mapTracker,
  splitDateTime,
  todayStr,
} from './musicTransportCore.js';

// Payload shapes mirror src/api/fork_views_music.py and fork_views_podcast.py.
const artistPayload = {
  id: 7,
  name: 'Real Artist',
  musicbrainz_id: 'mb-artist',
  image: 'https://example.com/a.jpg',
  country: 'Japan',
  genres: ['rock'],
  artist_type: 'Group',
  discography_synced_at: null,
  tracker: { status: 'Completed', score: '8', start_date: null, end_date: null, notes: '' },
  albums: [
    {
      id: 12,
      title: 'First LP',
      musicbrainz_release_id: 'rel-1',
      musicbrainz_release_group_id: 'rg-1',
      artist_id: 7,
      artist_name: 'Real Artist',
      release_date: '1999-04-01',
      release_type: 'Album',
      image: 'https://example.com/al.jpg',
      genres: [],
      tracker: null,
    },
    {
      id: 13,
      title: 'B Sides',
      release_date: '2001-06-30',
      release_type: 'EP + Live',
      image: '',
      genres: [],
      tracker: null,
    },
  ],
};

const trackRows = {
  results: [
    {
      id: 31,
      title: 'Opening',
      track_number: 1,
      disc_number: 1,
      duration_ms: 214000,
      musicbrainz_recording_id: 'rec-1',
      music_id: 91,
      last_played: '2026-11-08T21:40:00Z',
      play_count: 3,
      score: '7',
    },
    {
      id: 32,
      title: 'Untouched',
      track_number: 2,
      disc_number: 1,
      duration_ms: null,
      musicbrainz_recording_id: 'rec-2',
      music_id: null,
      last_played: null,
      play_count: 0,
      score: null,
    },
  ],
};

test('artist and relations load together regardless of response order', async () => {
  for (const first of ['artist', 'relations']) {
    const pending = {};
    const transport = {
      loadArtist: () => new Promise(resolve => { pending.artist = resolve; }),
      loadArtistRelations: () => new Promise(resolve => { pending.relations = resolve; }),
    };
    const result = loadArtistWithRelations(transport, 7);
    const values = { artist: artistPayload, relations: { members: [{ id: 1 }] } };
    pending[first](values[first]);
    pending[first === 'artist' ? 'relations' : 'artist'](values[first === 'artist' ? 'relations' : 'artist']);
    assert.deepEqual(await result, values);
  }
  const result = await loadArtistWithRelations({
    loadArtist: async () => artistPayload,
    loadArtistRelations: async () => { throw new Error('unavailable'); },
  }, 7);
  assert.deepEqual(result, { artist: artistPayload, relations: null });
});

test('artist payload maps without demo fields and never exposes a musicbrainz crash', () => {
  const artist = mapArtistPayload(artistPayload);
  assert.equal(artist.artistId, 7);
  assert.equal(artist.name, 'Real Artist');
  assert.equal(artist.status, 'Completed');
  assert.equal(artist.score, 8);
  assert.deepEqual(artist.musicbrainz, { rating: null, votes: null });
  assert.equal(artist.bio, '');
  assert.equal(artist.totalPlays, null);
  assert.deepEqual(artist.collection, {
    albums: null, totalAlbums: null, tracks: null, totalTracks: null,
  });
  assert.deepEqual(artist.relations, { members: [], alsoIn: [] });
  // Discography groups mirror the web release_type grouping.
  assert.deepEqual(artist.discography.map(group => group.label), ['Album', 'EP + Live']);
  assert.equal(flattenAlbums(artist)[0].artistName, 'Real Artist');
});

test('artist payload with a partial tracker and no albums still renders', () => {
  const artist = mapArtistPayload({
    id: 3,
    name: 'Solo',
    tracker: { status: 'In progress', score: null },
  });
  assert.equal(artist.score, null);
  assert.deepEqual(artist.discography, []);
  assert.deepEqual(flattenAlbums(artist), []);
  // The screen derives its release list before the artist has loaded.
  assert.deepEqual(flattenAlbums(null), []);
  assert.equal(mapArtistPayload(null), null);
  assert.equal(mapArtistPayload(undefined), null);
});

test('discography groups follow the web release type order', () => {
  const albums = [
    { release_type: 'Single', title: 's' },
    { release_type: 'Album', title: 'a' },
    { release_type: 'EP', title: 'e' },
    { release_type: 'Album + Compilation', title: 'c' },
  ].map(mapAlbumPayload);
  const labels = groupAlbums(albums).map(group => group.label);
  assert.deepEqual(labels, ['Album', 'Album + Compilation', 'EP', 'Single']);
});

test('album payload keeps artist name and leaves unknown facts null', () => {
  const album = mapAlbumPayload(artistPayload.albums[0]);
  assert.equal(album.albumId, 12);
  assert.equal(album.artistName, 'Real Artist');
  assert.equal(album.year, '1999');
  assert.equal(album.releaseDate, '1999-04-01');
  assert.equal(album.format, null);
  assert.equal(album.runtime, null);
  assert.deepEqual(album.tracks, []);
  assert.equal(mapAlbumPayload(null), null);
});

test('track rows map real play state without fabricating listens', () => {
  const tracks = mapTracksPayload(trackRows);
  assert.equal(tracks.length, 2);
  assert.deepEqual(tracks[0], {
    id: 31,
    trackId: 31,
    musicId: 91,
    number: 1,
    disc: 1,
    title: 'Opening',
    duration: '3:34',
    durationMs: 214000,
    playCount: 3,
    lastPlayed: { date: '2026-11-08', time: '21:40' },
    score: 7,
    notes: '',
    codec: null,
    image: '',
    listens: [],
  });
  assert.equal(tracks[1].musicId, null);
  assert.equal(tracks[1].lastPlayed, null);
  assert.equal(tracks[1].playCount, 0);
  assert.equal(tracks[1].duration, '');
  assert.deepEqual(mapTracksPayload({ results: null }), []);
  assert.deepEqual(mapTracksPayload(undefined), []);
});

test('show and episode payloads map real play state', () => {
  const show = mapShowPayload({
    id: 5,
    title: 'Real Show',
    author: 'Host',
    image: '',
    description: 'About',
    genres: ['news'],
    website_url: 'https://example.com/show',
    rss_feed_url: 'https://example.com/feed.xml',
    tracker: { status: 'In progress', score: '6' },
  });
  assert.equal(show.showId, 5);
  assert.equal(show.status, 'In progress');
  assert.equal(show.score, 6);
  assert.equal(show.websiteUrl, 'https://example.com/show');

  const page = mapEpisodesPayload({
    pagination: { total: 42, limit: 2, offset: 0, next: 'x', previous: null },
    results: [
      {
        id: 61,
        title: 'Ep One',
        description: 'd',
        image: '',
        published: '2026-10-01T00:00:00Z',
        duration: '32:10',
        duration_seconds: 1930,
        episode_number: 1,
        website_url: '',
        status: 'Completed',
        has_history: true,
      },
      {
        id: 62,
        title: 'Ep Two',
        description: '',
        published: null,
        duration: '',
        duration_seconds: null,
        episode_number: null,
        website_url: '',
        status: 'In progress',
        has_history: false,
      },
    ],
  });
  assert.equal(page.total, 42);
  const [played, inProgress] = page.episodes;
  assert.equal(played.played, true);
  assert.equal(played.inProgress, false);
  assert.equal(played.published, '2026-10-01');
  assert.equal(played.score, null);
  assert.equal(inProgress.played, false);
  assert.equal(inProgress.inProgress, true);
  assert.deepEqual(mapEpisodesPayload({ results: [] }).episodes, []);
});

test('history entries map flat rows and only mark real record ids deletable', () => {
  const rows = mapHistoryEntries({
    results: [
      {
        media_type: 'podcast',
        title: 'Ep One',
        entry_key: '987',
        played_at_local: '2026-11-07T08:05:00-05:00',
        runtime_display: '32m',
        progress_display: '32m',
      },
      {
        media_type: 'music',
        title: 'First LP',
        entry_key: '12-20261108',
        play_count: 4,
        time_range_display: '9:00 AM - 10:00 AM',
        end_date: '2026-11-08T22:15:00-05:00',
      },
    ],
  });
  assert.equal(rows.length, 2);
  assert.equal(rows[0].recordId, 987);
  assert.equal(rows[0].deletable, true);
  assert.match(rows[0].dateLabel, /^2026-11-07 08:05/);
  assert.equal(rows[1].recordId, null);
  assert.equal(rows[1].deletable, false);
  assert.match(rows[1].note, /4 plays/);
  assert.match(rows[1].note, /9:00 AM - 10:00 AM/);
});

test('date helpers use the real clock and keep the listen form contract', () => {
  const stamp = todayStr(new Date(2026, 10, 9));
  assert.equal(stamp, '2026-11-09');
  assert.match(todayStr(), /^\d{4}-\d{2}-\d{2}$/);
  assert.equal(splitDateTime('2026-11-09T08:30:00').time, '08:30');
  assert.deepEqual(splitDateTime('2026-11-09'), { date: '2026-11-09', time: '12:00' });
  assert.deepEqual(splitDateTime(''), { date: '', time: '' });
  assert.equal(endDateStamp('2026-11-09T08:30'), '2026-11-09T08:30:00');
  assert.equal(endDateStamp(''), '');
  assert.equal(formatDurationMs(214000), '3:34');
  assert.equal(formatDurationMs(3725000), '1:02:05');
  assert.equal(formatDurationMs(null), '');
});

test('mapTracker tolerates missing trackers and string scores', () => {
  assert.deepEqual(mapTracker(null), {
    status: null, score: null, startDate: '', endDate: '', notes: '',
  });
  const tracker = mapTracker({ status: 'Paused', score: '9.5' });
  assert.equal(tracker.score, 9.5);
  assert.equal(mapTracker({}).notes, '');
});

test('remote history action targets the flat history endpoint with filters', async () => {
  const calls = [];
  const request = async (path, options) => {
    calls.push({ path, options });
    return { results: [] };
  };
  const transport = createRemoteMusicActions(request);
  await transport.loadHistory({ mediaType: 'podcast', showId: 5, limit: 10 });
  assert.equal(calls.length, 1);
  assert.equal(
    calls[0].path,
    '/api/v1/history/?flat=true&limit=10&media_type=podcast&podcast_show=5',
  );
  await transport.loadHistory({ mediaType: 'music', albumId: 12 });
  assert.equal(
    calls[1].path,
    '/api/v1/history/?flat=true&limit=20&media_type=music&album=12',
  );
});

test('local mode keeps the local marker on every action including history', async () => {
  const transport = createLocalMusicActions('Sign in to your Floppy server.');
  assert.equal(transport.mode, 'local');
  const result = await transport.loadHistory({ mediaType: 'music' });
  assert.equal(result.local, true);
  assert.equal(result.reason, 'Sign in to your Floppy server.');
});

test('browse lists map artists, albums and shows with tracker state', () => {
  const artists = mapBrowseList({ results: [{ id: 1, name: 'Band', image: 'a.jpg', tracker: { status: 'Completed', score: '8' } }, { id: 'x' }] }, 'artist');
  assert.deepEqual(artists, [{
    key: 'artist-1', kind: 'artist', id: 1, title: 'Band', subtitle: '', image: 'a.jpg', status: 'Completed', score: 8,
  }]);
  const albums = mapBrowseList({ results: [{ id: 2, title: 'LP', artist_name: 'Band', tracker: null }] }, 'album');
  assert.equal(albums[0].subtitle, 'Band');
  assert.equal(albums[0].status, null);
  const shows = mapBrowseList({ results: [{ id: 3, title: 'Show', author: 'Host' }] }, 'show');
  assert.equal(shows[0].subtitle, 'Host');
  assert.deepEqual(mapBrowseList(null, 'show'), []);
});

test('track identity and listens map the track endpoints', () => {
  assert.deepEqual(mapTrackIdentity({ id: 4, album_id: 5, artist_id: 6, music_id: 9, title: 'Song' }), {
    trackId: 4, albumId: 5, artistId: 6, musicId: 9, title: 'Song',
  });
  assert.equal(mapTrackIdentity(null), null);
  const listens = mapTrackListens({
    results: [{ history_id: 12, played_at: '2025-02-01T10:00:00Z', score: '7.5' }, { played_at: null }],
  });
  assert.equal(listens.length, 1);
  assert.equal(listens[0].id, 12);
  assert.equal(listens[0].score, 7.5);
  assert.ok(listens[0].date);
});

test('releases and managed episodes map their endpoint rows', () => {
  const { selectedId, releases } = mapReleases({
    selected_release_id: 'r1',
    results: [{ release_id: 'r1', title: 'Deluxe', date: '2020', format: 'CD', track_count: 12 }, { title: 'no id' }],
  });
  assert.equal(selectedId, 'r1');
  assert.deepEqual(releases, [{ id: 'r1', title: 'Deluxe', detail: '2020 · CD · 12 tracks' }]);
  const episodes = mapManagedEpisodes({ results: [{ id: 3, title: 'Pilot', episodeNumber: 1, duration: '10:00' }] });
  assert.equal(episodes[0].episodeNumber, '1');
  assert.deepEqual(managedEpisodeBody({ title: ' Pilot ', episodeNumber: '2', description: '' }), {
    title: 'Pilot', episode_number: '2',
  });
});

test('new music and podcast actions address the documented endpoints', async () => {
  const calls = [];
  const transport = createRemoteMusicActions(async (path, options) => {
    calls.push({ path, options });
    return { results: [], pagination: { total: 0 } };
  });
  await transport.loadArtists();
  await transport.loadTrack({ trackId: 4 });
  await transport.loadTrackPlays({ trackId: 4 });
  await transport.deleteTrackPlay({ trackId: 4, historyId: 12 });
  await transport.loadReleases({ albumId: 5, query: 'deluxe' });
  await transport.selectRelease({ albumId: 5, releaseId: 'r1' });
  await transport.loadManagedEpisodes({ showId: 8 });
  await transport.addManagedEpisode({ showId: 8, values: { title: 'Pilot' } });
  await transport.editManagedEpisode({ episodeId: 3, values: { title: 'Pilot 2' } });
  await transport.deleteManagedEpisode({ episodeId: 3 });
  await transport.rateEpisode({ episodeId: 3, score: 8 });
  assert.deepEqual(calls.map(call => [call.options?.method || 'GET', call.path]), [
    ['GET', '/api/v1/music/artists/?limit=200&offset=0'],
    ['GET', '/api/v1/music/tracks/4/'],
    ['GET', '/api/v1/music/tracks/4/plays/?limit=200&offset=0'],
    ['DELETE', '/api/v1/music/tracks/4/plays/12/'],
    ['GET', '/api/v1/music/albums/5/releases/'],
    ['POST', '/api/v1/music/albums/5/releases/'],
    ['GET', '/api/v1/podcasts/shows/8/episodes/manage/'],
    ['POST', '/api/v1/podcasts/shows/8/episodes/manage/'],
    ['PATCH', '/api/v1/podcasts/episodes/3/'],
    ['DELETE', '/api/v1/podcasts/episodes/3/'],
    ['PATCH', '/api/v1/podcasts/episodes/3/score/'],
  ]);
  assert.deepEqual(calls[4].options, { query: { q: 'deluxe' } });
  assert.deepEqual(calls[5].options.json, { release_id: 'r1' });
  assert.deepEqual(calls[10].options.json, { score: 8 });
});

test('repairPending follows the server flag and stops at the attempt cap', async () => {
  const { repairPending, REPAIR_POLL_MAX_ATTEMPTS } = await import('./musicTransportCore.js');
  assert.equal(repairPending({ repair_pending: true }), true);
  assert.equal(repairPending({ results: [], pending: true }), true);
  assert.equal(repairPending({ results: [], pending: false }), false);
  assert.equal(repairPending(null), false);
  assert.equal(repairPending({ pending: true }, REPAIR_POLL_MAX_ATTEMPTS), false);
});
