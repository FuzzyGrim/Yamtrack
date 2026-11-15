import assert from 'node:assert/strict';
import test from 'node:test';
import {
  libraryPath,
  libraryQuery,
  mediaCardModelFromRow,
  mediaRowsFromPayload,
  createRemoteLibraryActions,
} from './libraryTransportCore.js';

test('library query maps filter-bar state to the shared API filter contract', () => {
  assert.deepEqual(libraryQuery({
    mediaType: 'all', limit: 30, offset: 30, search: 'orbit',
    statuses: ['completed', 'no_status'], sort: 'date_added', direction: 'desc',
    ranges: {
      rating: { from: '6', to: '9' },
      dateAdded: { within: '3', unit: 'weeks' },
    },
    fields: { genre: 'Drama', year: '2024', selectedTags: ['favorite'] },
    modes: { tags: 'and' },
  }), {
    limit: 30, offset: 30, media_type: 'all', search: 'orbit',
    status: ['completed', 'no_status'], sort: 'date_added', direction: 'desc',
    rating_min: '6', rating_max: '9', date_added_within: '3',
    date_added_within_unit: 'weeks', genre: 'Drama', year: '2024',
    tag: ['favorite'], tag_mode: 'and',
  });
});

test('library path uses the all-media endpoint for the all type', () => {
  assert.equal(libraryPath('all'), '/api/v1/media/');
  assert.equal(libraryPath('movie'), '/api/v1/media/movie/');
  assert.equal(libraryPath('music', 'artists'), '/api/v1/music/artists/');
  assert.equal(libraryPath('comic', 'issues'), '/api/v1/media/comicissue/');
});

test('payload rows retain real tracker identity and map to card shapes', () => {
  const [row] = mediaRowsFromPayload({ results: [{
    id: 42, item_id: 42, tracked: true, status: 1, score: 8,
    progress: 4, episodes_left: 6, progress_unit: 'episodes',
    item: { media_type: 'tv', library_media_type: 'tv', source: 'tmdb', media_id: '99',
      title: 'The Quiet Orbit', image: 'https://example.test/poster.jpg', release_datetime: '2024-01-01' },
  }] });
  const card = mediaCardModelFromRow(row.media);
  assert.equal(row.identity.mediaId, '99');
  assert.equal(card.shape, 'poster');
  assert.equal(card.title, 'The Quiet Orbit');
  assert.equal(card.progressBar.percent, 40);
  assert.equal(card.rating.value, 8);
});

test('card projection keeps missing artwork, score and progress nullable', () => {
  const card = mediaCardModelFromRow({ item: { media_type: 'music', title: 'Untitled' }, tracked: false });
  assert.equal(card.shape, 'square');
  assert.equal(card.image.src, null);
  assert.equal(card.rating, null);
  assert.equal(card.progressBar, null);
});

test('native library writes use scoped bulk endpoints and preserve item identity', async () => {
  const calls = [];
  const transport = createRemoteLibraryActions(async (path, options) => {
    calls.push({ path, options });
    return { operation_id: 'op-1' };
  });
  const rows = [{ identity: { itemId: 7 } }, { identity: { itemId: 9 } }];
  await transport.bulkCollection({ rows });
  await transport.bulkAddToList({ listId: 12, rows });
  await transport.startBulkPlays({ rows, options: { scope: 'aired' }, dryRun: true });
  assert.deepEqual(calls.map(call => [call.path, call.options.json]), [
    ['/api/v1/library/bulk/collection/', { item_ids: [7, 9] }],
    ['/api/v1/lists/12/items/bulk/', { item_ids: [7, 9] }],
    ['/api/v1/library/bulk/plays/', { item_ids: [7, 9], options: { scope: 'aired' }, dry_run: true }],
  ]);
});

test('filter sections follow the server option lists and leave out empty ones', async () => {
  const { libraryFilterSections } = await import('./libraryTransportCore.js');
  const sections = libraryFilterSections({
    genres: ['Action'], implied_genres: [], years: [{ value: '2024', label: '2024' }],
    sources: [{ value: 'tmdb', label: 'TMDB' }], media_statuses: [],
    languages: [{ value: 'English', label: 'English' }], show_languages: true,
    countries: [{ value: 'US', label: 'US' }], show_countries: false,
    platforms: [{ value: 'pc', label: 'PC' }], show_platforms: true,
    authors: [{ value: 'a', label: 'A' }], show_authors: true, author_is_channel: true,
    show_progress: true,
  });
  assert.deepEqual(sections.map(section => section.key), [
    'rating', 'collection', 'progress', 'genre', 'year', 'release', 'source', 'language', 'platform', 'author',
  ]);
  assert.equal(sections.find(section => section.key === 'author').label, 'Channel');
  assert.equal(sections.find(section => section.key === 'platform').type, 'multi');
  assert.deepEqual(sections.find(section => section.key === 'genre').options, [{ value: 'Action', label: 'Action' }]);
  // No payload: only the sections that need no list.
  assert.deepEqual(libraryFilterSections(null).map(section => section.key), ['rating', 'collection', 'release']);
});

test('filter options, matching ids, preferences and share view call their endpoints', async () => {
  const calls = [];
  const transport = createRemoteLibraryActions(async (path, options) => {
    calls.push({ path, options });
    if (path.endsWith('/item-ids/')) return { item_ids: [3, '4', 'x'] };
    if (path.endsWith('/share-view/')) return { url: '/list/9' };
    return {};
  });
  const state = { mediaType: 'movie', statuses: ['planning'], sort: 'score', direction: 'desc',
    fields: { genre: 'Action', tags: ['favorite'], platform: ['pc'] }, modes: { tags: 'not', platform: 'and' }, subview: 'items' };
  await transport.loadFilterOptions(state);
  assert.deepEqual(await transport.loadItemIds(state), [3, 4]);
  await transport.loadPreferences('movie');
  await transport.savePreferences('movie', { layout: 'table' });
  assert.equal(await transport.shareView(state), '/list/9');
  assert.deepEqual(calls.map(call => call.path), [
    '/api/v1/media/movie/filter-options/', '/api/v1/media/movie/item-ids/',
    '/api/v1/library/preferences/movie/', '/api/v1/library/preferences/movie/', '/api/v1/library/share-view/',
  ]);
  assert.deepEqual(calls[0].options.query, {
    status: ['planning'], genre: 'Action', tag: ['favorite'], platform: ['pc'], tag_mode: 'not', platform_mode: 'and',
  });
  assert.equal(calls[1].options.query.sort, 'score');
  assert.deepEqual(calls[3].options, { method: 'PATCH', body: { layout: 'table' } });
  assert.equal(calls[4].options.body.mediaType, 'movie');
  assert.equal(calls[4].options.body.query.limit, undefined);
});

test('podcast shows map to show rows and card actions carry the item identity', async () => {
  const { cardActionsFor } = await import('./libraryTransportCore.js');
  assert.equal(libraryPath('podcast', 'shows'), '/api/v1/podcasts/shows/');
  const [row] = mediaRowsFromPayload({ results: [{ id: 5, title: 'Tape', author: 'Seed', source: 'pocketcasts',
    podcast_uuid: 'abc', tracker: { status: 'In progress', score: null } }] }, { subview: 'shows' });
  assert.equal(row.title, 'Tape');
  assert.deepEqual([row.identity.entityType, row.identity.entityId, row.identity.mediaType], ['show', 5, 'podcast']);
  assert.equal(mediaCardModelFromRow(row.media).statusChip.status, 'In progress');
  const identity = { mediaType: 'movie', source: 'tmdb', mediaId: '603' };
  const actions = cardActionsFor('The Matrix', identity);
  assert.deepEqual(actions.items.map(item => item.modal || item.href), ['track', 'lists', '/history']);
  assert.equal(actions.media, identity);
});

test('library cards load the server card size and keep the full image for Detail', () => {
  const row = {
    id: 7,
    status: 'Completed',
    item: {
      media_type: 'game', source: 'igdb', media_id: '1', title: 'Portal 2',
      image: 'https://images.igdb.com/igdb/image/upload/t_original/co1rs4.jpg',
      card_image: 'https://images.igdb.com/igdb/image/upload/t_cover_big_2x/co1rs4.jpg',
    },
  };
  assert.equal(mediaCardModelFromRow(row).image.src, row.item.card_image);
  const [mapped] = mediaRowsFromPayload({ results: [row] });
  assert.equal(mapped.identity.image, row.item.image);
  assert.equal(mapped.cells.image.src, row.item.card_image);
  const older = { ...row, item: { ...row.item, card_image: undefined } };
  assert.equal(mediaCardModelFromRow(older).image.src, row.item.image);
});
