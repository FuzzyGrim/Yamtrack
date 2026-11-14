import test from 'node:test';
import assert from 'node:assert/strict';

import {
  activityLine,
  activityLineWithWatched,
  detailHeaderText,
  detailModelFromPayloads,
  detailRows,
  episodeActionPath,
  episodesFromPayload,
  historyFromConsumptions,
  identityFromItem,
  identityKey,
  mediaPath,
  notesFromConsumptions,
  statusLabel,
  trackingFromState,
  trackingEntriesFromPayload,
} from './detailTransportCore.js';
import { getDatePreferences, setDatePreferences } from './userDate.js';

test('identityFromItem prefers an explicit identity object', () => {
  assert.deepEqual(
    identityFromItem({ identity: { mediaType: 'movie', source: 'tmdb', mediaId: '42', title: 'X' } }),
    { mediaType: 'movie', source: 'tmdb', mediaId: '42' },
  );
});

test('identityFromItem reads a nested library row item', () => {
  assert.deepEqual(
    identityFromItem({ item: { media_type: 'tv', source: 'manual', media_id: 'tv1' } }),
    { mediaType: 'tv', source: 'manual', mediaId: 'tv1' },
  );
});

test('identityFromItem parses a plain web detail href', () => {
  assert.deepEqual(
    identityFromItem({ href: '/details/manual/movie/m2/signal-to-noise' }),
    { source: 'manual', mediaType: 'movie', mediaId: 'm2' },
  );
});

test('identityFromItem parses a href behind a base path', () => {
  assert.deepEqual(
    identityFromItem({ href: '/dw/details/tmdb/tv/1668/some-show' }),
    { source: 'tmdb', mediaType: 'tv', mediaId: '1668' },
  );
});

test('identityFromItem returns null without any identity', () => {
  assert.equal(identityFromItem(null), null);
  assert.equal(identityFromItem({ title: 'No identity' }), null);
});

test('mediaPath encodes each identity segment once', () => {
  assert.equal(
    mediaPath({ mediaType: 'movie', source: 'manual', mediaId: 'm2' }),
    '/api/v1/media/movie/manual/m2',
  );
});

test('statusLabel maps numeric codes and passes labels through', () => {
  assert.equal(statusLabel(1), 'In progress');
  assert.equal(statusLabel(3), 'Completed');
  assert.equal(statusLabel('Paused'), 'Paused');
  assert.equal(statusLabel(null), '');
});

test('trackingFromState returns null when untracked', () => {
  assert.equal(trackingFromState({ tracked: false, consumptions: [] }), null);
  assert.equal(trackingFromState(null), null);
});

test('trackingFromState summarizes the first consumption', () => {
  const tracking = trackingFromState({
    tracked: true,
    consumptions: [{ status: 1, score: 7.0, progress: 12, start_date: '2026-11-01T10:00:00Z', end_date: null, notes: 'revisit' }],
  });
  assert.equal(tracking.status, 'In progress');
  assert.equal(tracking.score, 7);
  assert.equal(tracking.progress, 12);
  assert.equal(tracking.notes, 'revisit');
  assert.equal(tracking.endDate, '');
  assert.ok(tracking.startDate.length > 0);
});

test('detailModelFromPayloads merges metadata, state and card fallback', () => {
  const model = detailModelFromPayloads({
    item: { title: 'Card title' },
    metadata: {
      fields: {
        title: 'Signal to Noise',
        original_title: 'Signal',
        image: 'http://example.com/art.jpg',
        release_datetime: '2024-03-01T00:00:00Z',
        runtime_minutes: 96,
        genres: ['Drama', ''],
        studios: ['Northline'],
        synopsis: 'A radio astronomer...',
      },
      watch_providers: { US: ['Northline'] },
    },
    state: {
      tracked: true,
      consumptions: [{ status: 1, score: 7.0, progress: 0, notes: '' }],
    },
  });
  assert.equal(model.title, 'Signal to Noise');
  assert.equal(model.subtitle, 'Signal');
  assert.equal(model.tracked, true);
  assert.equal(model.genres.length, 1);
  assert.deepEqual(model.studios, []); // not provider-linked: the Details card lists them
  assert.deepEqual(model.watchProviders, [{ market: 'US', names: ['Northline'] }]);
  assert.equal(model.year, '2024');
  assert.equal(model.score, 7);
  assert.equal(model.activityLine, '1h 36min');
  assert.deepEqual(model.details.map(row => row.label), ['Release Date', 'RUNTIME', 'Studios']);
  assert.equal(model.synopsis.text, 'A radio astronomer...');
});

test('detailModelFromPayloads works with no payloads at all', () => {
  const model = detailModelFromPayloads({ item: { title: 'Only a card' } });
  assert.equal(model.title, 'Only a card');
  assert.equal(model.tracked, false);
  assert.equal(model.synopsis, null);
  assert.deepEqual(model.details, []);
  assert.deepEqual(model.providerScores, []);
});

test('identityFromItem keeps season and episode coordinates from an identity', () => {
  assert.deepEqual(
    identityFromItem({ identity: { mediaType: 'season', source: 'tmdb', mediaId: '1', seasonNumber: 2 } }),
    { mediaType: 'season', source: 'tmdb', mediaId: '1', seasonNumber: 2 },
  );
  assert.deepEqual(
    identityFromItem({ identity: { mediaType: 'tv', source: 'tmdb', mediaId: '1', seasonNumber: 2, episodeNumber: 5 } }),
    { mediaType: 'episode', source: 'tmdb', mediaId: '1', seasonNumber: 2, episodeNumber: 5 },
  );
});

test('identityFromItem drops coordinates for types without seasons', () => {
  assert.deepEqual(
    identityFromItem({ identity: { mediaType: 'movie', source: 'tmdb', mediaId: '1', seasonNumber: 2 } }),
    { mediaType: 'movie', source: 'tmdb', mediaId: '1' },
  );
});

test('identityFromItem parses season and episode hrefs', () => {
  assert.deepEqual(
    identityFromItem({ href: '/details/tmdb/tv/1668/friends/season/3' }),
    { source: 'tmdb', mediaType: 'season', mediaId: '1668', seasonNumber: 3 },
  );
  assert.deepEqual(
    identityFromItem({ href: '/dw/details/tmdb/tv/1668/friends/season/3/episode/9?x=1' }),
    { source: 'tmdb', mediaType: 'episode', mediaId: '1668', seasonNumber: 3, episodeNumber: 9 },
  );
});

test('identityFromItem reads a library row season', () => {
  assert.deepEqual(
    identityFromItem({ item: { media_type: 'season', source: 'tmdb', media_id: '7', season_number: 1 } }),
    { mediaType: 'season', source: 'tmdb', mediaId: '7', seasonNumber: 1 },
  );
});

test('mediaPath builds tv routes for seasons and episodes', () => {
  assert.equal(
    mediaPath({ mediaType: 'season', source: 'tmdb', mediaId: '7', seasonNumber: 2 }),
    '/api/v1/media/tv/tmdb/7/2',
  );
  assert.equal(
    mediaPath({ mediaType: 'episode', source: 'tmdb', mediaId: '7', seasonNumber: 2, episodeNumber: 4 }),
    '/api/v1/media/tv/tmdb/7/2/4',
  );
  assert.equal(
    episodeActionPath({ source: 'tmdb', mediaId: '7', seasonNumber: 2, episodeNumber: 4 }, 'watch'),
    '/api/v1/media/tv/tmdb/7/2/episodes/4/watch/',
  );
});

test('identityKey separates episodes of one show', () => {
  const base = { mediaType: 'episode', source: 'tmdb', mediaId: '7', seasonNumber: 1 };
  assert.notEqual(identityKey({ ...base, episodeNumber: 1 }), identityKey({ ...base, episodeNumber: 2 }));
  assert.equal(identityKey(null), '');
});

test('episodesFromPayload keeps the season and episode on each row identity', () => {
  const rows = episodesFromPayload(
    { results: [{ item: { episode_number: 3, title: 'Three' }, end_date: '2026-01-02T00:00:00Z' }] },
    { source: 'tmdb', mediaId: '7' },
    2,
  );
  assert.deepEqual(rows[0].identity, {
    mediaType: 'episode', source: 'tmdb', mediaId: '7', seasonNumber: 2, episodeNumber: 3,
  });
  assert.equal(rows[0].watched, true);
});

test('activityLineWithWatched puts the watched time in front like the web hero', () => {
  assert.equal(activityLineWithWatched('Progress: 4/6', '2h 48min'), '2h 48min (4/6)');
  assert.equal(activityLineWithWatched('Progress: 4/6', ''), 'Progress: 4/6');
  assert.equal(activityLineWithWatched('', '42min'), '42min');
});

test('hero duration, progress, metadata and dates follow the PWA order', () => {
  assert.deepEqual(detailHeaderText({
    duration_text: '2h 48min watched',
    primary_text: 'Progress: 4/6',
    date_start: '2026-01-18',
    date_end: '2026-01-20',
  }, { first_air_date: '2024-06-02', runtime: '42min' }), {
    activityText: 'Jan. 18, 2026 - Jan. 20, 2026 • 2h 48min watched',
    mobileMeta: '',
  });
  assert.deepEqual(detailHeaderText({
    primary_text: 'Progress: 4/6',
    date_start: '2026-01-18',
    date_end: '2026-01-20',
  }, { first_air_date: '2024-06-02', runtime: '42min' }), {
    activityText: 'Jan. 18, 2026 - Jan. 20, 2026',
    mobileMeta: '',
  });
});

test('hero dates use the server timezone and user format and collapse a local day', () => {
  const previous = getDatePreferences();
  try {
    setDatePreferences({ timeZone: 'America/Chicago', dateFormat: 'd_m_yyyy' });
    assert.deepEqual(detailHeaderText({
      primary_text: 'Played once',
      date_start: '2026-11-11T01:00:00Z',
      date_end: '2026-11-11T04:00:00Z',
    }), { activityText: '10/11/2026', mobileMeta: '' });
    assert.equal(detailHeaderText({
      primary_text: 'Played once',
      date_start: '2026-11-11T01:00:00Z',
      date_end: '2026-11-12T04:00:00Z',
      collapse_same_day: true,
    }).activityText, '10/11/2026');
  } finally {
    setDatePreferences(previous);
  }
});

test('hero omits missing or invalid dates without raw timestamps or duplicate metadata', () => {
  assert.deepEqual(detailHeaderText(null, { release_date: '2012-07-28', runtime: '1h 30min' }), {
    activityText: '', mobileMeta: '2012 · 1h 30min',
  });
  assert.deepEqual(detailHeaderText({ primary_text: 'Read once', date_end: 'not-a-date' }, { year: 2021 }), {
    activityText: 'Read once', mobileMeta: '',
  });
  assert.deepEqual(detailHeaderText({ date_start: '2026-01-18' }), {
    activityText: 'Jan. 18, 2026', mobileMeta: '',
  });
  for (const verb of ['watched', 'played', 'listened', 'read']) {
    assert.equal(detailHeaderText({ duration_text: `45min ${verb}` }).activityText, `45min ${verb}`);
  }
  assert.deepEqual(detailHeaderText({}, { start_year: 1997 }), {
    activityText: '', mobileMeta: '1997',
  });
  assert.deepEqual(detailHeaderText(null, null), { activityText: '', mobileMeta: '' });
});

test('activityLine matches the web subtitle', () => {
  assert.equal(activityLine({ mediaType: 'movie', runtime: '1h 58min', plays: 1 }), '1h 58min (Watched once)');
  assert.equal(activityLine({ mediaType: 'movie', runtime: '1h 58min', plays: 3 }), '1h 58min (Watched 3 times)');
  assert.equal(activityLine({ mediaType: 'game', runtime: '', plays: 2 }), 'Played 2 times');
  assert.equal(activityLine({ mediaType: 'movie', runtime: '', plays: 0 }), '');
});

test('notes and history rows come from the consumptions', () => {
  const rows = [
    { consumption_id: 4, status: 3, notes: 'great', end_date: '2026-03-01T00:00:00Z', score: 9 },
    { consumption_id: 3, status: 3, notes: '  ', end_date: '2026-01-01T00:00:00Z' },
  ];
  assert.equal(notesFromConsumptions(rows).length, 1);
  assert.equal(notesFromConsumptions(rows)[0].id, '4');
  const history = historyFromConsumptions(rows);
  assert.equal(history.length, 2);
  assert.equal(history[0].title, 'Completed');
  assert.match(history[0].detail, /Score 9/);
});

test('detailRows prefers the provider details and hides empty values', () => {
  const rows = detailRows({
    details: { release_date: '2020-01-02', runtime: '1h 58min', country: ['US', 'GB'], tvdb_id: 5, studios: [] },
  });
  assert.deepEqual(rows, [
    { label: 'Release Date', value: 'Jan. 2, 2020' },
    { label: 'RUNTIME', value: '1h 58min' },
    { label: 'Country', value: 'US, GB' },
  ]);
});

test('detail model reads provider scores, cast and recommendations', () => {
  const model = detailModelFromPayloads({
    item: { title: 'Card' },
    identity: { mediaType: 'movie', source: 'tmdb', mediaId: '1' },
    detail: {
      title: 'Real', source: 'tmdb', score: 7.84, score_count: 10, imdb_rating: 8.2, imdb_rating_count: 1,
      cast: [{ person_id: '9', name: 'Ann', role: 'Lead', image: 'https://img/a.jpg' }, { name: '' }],
      crew: [],
      related: { recommendations: [{ media_id: 5, media_type: 'movie', title: 'Other', image: 'https://img/o.jpg' }] },
      details: { runtime: '2h' },
      consumptions: [],
    },
  });
  assert.equal(model.title, 'Real');
  assert.deepEqual(model.providerScores.map(chip => chip.value), ['7.8', '8.2']);
  assert.equal(model.cast.length, 1);
  assert.equal(model.recommendations[0].identity.mediaId, '5');
  assert.equal(model.canRequest, true);
});

test('watch providers come from the metadata projection', () => {
  const model = detailModelFromPayloads({
    item: { title: 'X' },
    metadata: { fields: { title: 'X' }, watch_providers: { us: ['Netflix'] } },
  });
  assert.deepEqual(model.watchProviders, [{ market: 'US', names: ['Netflix'] }]);
});

test('trackingFromPayloads carries the ids the write controls need', async () => {
  const { trackingFromPayloads } = await import('./detailTransportCore.js');
  const tracked = trackingFromPayloads({
    identity: { mediaType: 'movie', source: 'tmdb', mediaId: '603' },
    metadata: { item_id: 12, attribution: { source_url: 'https://example.test/603', source: 'tmdb' } },
    state: { tracked: true, consumptions: [
      { consumption_id: 4, status: 3, score: 8, end_date: '2026-01-02T00:00:00Z', notes: 'Again' },
      { consumption_id: 3, status: 3, score: null, end_date: null, notes: '' },
    ] },
  });
  assert.equal(tracked.catalogItemId, 12);
  assert.equal(tracked.tracked, true);
  assert.equal(tracked.tracking.id, 4);
  assert.equal(tracked.score, 8);
  assert.equal(tracked.plays, 1);
  assert.deepEqual(tracked.notes.map(note => [note.id, note.text]), [['4', 'Again']]);
  assert.deepEqual(tracked.history.map(entry => entry.id), ['4', '3']);
  assert.equal(tracked.sourceUrl, 'https://example.test/603');
  assert.equal(tracked.sourceName, 'TMDB');
  // Untracked, or both reads failed: every control that needs an id stays off.
  const none = trackingFromPayloads({ identity: { source: 'manual' }, metadata: null, state: null });
  assert.deepEqual([none.tracked, none.tracking, none.catalogItemId, none.notes, none.history, none.sourceUrl],
    [false, null, null, [], [], '']);
});


test('episode scores use display scale and preserve explicit capability denials', () => {
  const identity = { source: 'tmdb', mediaId: '7' };
  const [row, cleared, legacy] = episodesFromPayload({ results: [
    { item: { episode_number: 1 }, score: 8, display_score: 4, scale_max: 5, can_rate: false, can_list: false, can_watch: false, can_collect: false },
    { item: { episode_number: 2 }, score: 8, display_score: null, scale_max: 5 },
    { item: { episode_number: 3 }, score: 8, scale_max: 5, can_collect: true },
  ] }, identity, 2);
  assert.equal(row.score, 4);
  assert.equal(row.scaleMax, 5);
  assert.equal(row.canRate, false);
  assert.equal(row.canList, false);
  assert.equal(row.canWatch, false);
  assert.equal(row.canCollect, false);
  assert.equal(cleared.score, null);
  assert.equal(legacy.score, 4);
  assert.equal(legacy.canCollect, true);
});

test('repeat history preserves instance coordinates, display rating and edit permissions', () => {
  const result = trackingEntriesFromPayload({ tracking_entries_count: 55, tracking_entries_has_more: true,
    tracking_entries: [{ id: 42, media_type: 'season', identity: { mediaType: 'tv', detailType: 'season', source: 'tmdb', mediaId: '7', seasonNumber: 2, instanceId: 42 },
      status: 3, display_score: 4, scale_max: 5, formatted_progress: 6, max_progress: 6,
      start_date: '2026-01-18', end_date: '2026-01-20', notes: 'A repeat', can_edit: false }] });
  assert.equal(result.count, 55);
  assert.equal(result.hasMore, true);
  assert.equal(result.entries[0].identity.instanceId, 42);
  assert.equal(result.entries[0].identity.mediaType, 'season');
  assert.equal(result.entries[0].identity.seasonNumber, 2);
  assert.equal(result.entries[0].scoreLabel, '4/5');
  assert.equal(result.entries[0].statusLabel, 'Completed');
  assert.equal(result.entries[0].progressLabel, '6/6');
  assert.equal(result.entries[0].editable, false);
  assert.equal(result.entries[0].notes, 'A repeat');
  assert.deepEqual(trackingEntriesFromPayload(), { entries: [], count: 0, hasMore: false });
});
