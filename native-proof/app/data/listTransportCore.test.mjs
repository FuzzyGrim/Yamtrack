import assert from 'node:assert/strict';
import test from 'node:test';
import {
  LIST_SORT_OPTIONS,
  activityFromPayload,
  applyTierPlacements,
  compareListDateAdded,
  hasMorePages,
  memberUsernames,
  smartOptionsFromPayload,
  smartRulesFromPayload,
  tiersFromPayload,
  createLocalListActions,
  createRemoteListActions,
  listItemsFromPayload,
  listSummaryFromPayload,
  listsFromPayload,
  recommendationsFromPayload,
  searchPaginationFromPayload,
  searchResultsFromPayload,
} from './listTransportCore.js';

// Payload shapes mirror the Django serializers: ListSerializer
// (src/api/serializers.py), the media-shaped item entries of
// paginate_list_items, _serialize_recommendation and ListActivityView in
// src/api/fork_views_lists.py, and the SearchProviderView contract example.
const listPayload = (overrides = {}) => ({
  id: 7,
  name: 'Weekend watchlist',
  description: 'Saturday picks',
  image: 'https://server.example/media/list-cover.jpg',
  owner: { id: 1, username: 'dana' },
  collaborators: [
    { id: 4, username: 'lee' },
    { id: 5, username: 'priya' },
  ],
  viewers: [{ id: 6, username: 'kai' }],
  items_count: 12,
  latest_update: '2026-11-01T10:00:00Z',
  visibility: 'public',
  is_public: true,
  is_unlisted: false,
  public_slug: 'weekend',
  allow_recommendations: true,
  items: { pagination: { total: 12, limit: 200, offset: 0 }, results: [] },
  ...overrides,
});

const itemEntry = (overrides = {}) => ({
  id: 42,
  item_id: 'movie/tmdb/550',
  consumption_id: null,
  item: {
    media_type: 'movie',
    library_media_type: 'movie',
    source: 'tmdb',
    media_id: '550',
    title: 'Fight Club',
    image: 'https://server.example/media/poster.jpg',
    release_datetime: '1999-10-15T00:00:00Z',
    season_number: null,
    episode_number: null,
  },
  tracked: false,
  created_at: '2026-10-02T09:00:00Z',
  score: 8.5,
  status: 1,
  progress: 1,
  progress_unit: 'plays',
  episodes_left: null,
  start_date: null,
  end_date: null,
  rank: null,
  ...overrides,
});

test('maps a list payload into the hub card summary', () => {
  const summary = listSummaryFromPayload(listPayload(), 'dana');
  assert.ok(summary);
  assert.equal(summary.id, '7');
  assert.equal(summary.name, 'Weekend watchlist');
  assert.equal(summary.visibility, 'public');
  assert.equal(summary.visibilityLabel, 'Public');
  assert.equal(summary.role, 'Owner');
  assert.equal(summary.isOwner, true);
  assert.equal(summary.canEdit, true);
  assert.equal(summary.canDelete, true);
  assert.equal(summary.canRecommend, true);
  assert.equal(summary.itemsCount, 12);
  assert.equal(summary.publicSlug, 'weekend');
  assert.equal(summary.href, '/lists/7');
  assert.equal(summary.isSmart, false);
  assert.equal(summary.tags.length, 0);
  assert.equal(summary.completionPercent, null);
  assert.deepEqual(summary.mediaTypeBreakdown, []);
  assert.deepEqual(listSummaryFromPayload({ id: 8, name: 'x', owner: { id: 1, username: 'a' }, media_type_breakdown: [{ value: 'movie', label: 'Movie', count: 2, percent: 100, color: '#fff' }] }, 'a').mediaTypeBreakdown, [{ value: 'movie', label: 'Movie', count: 2, percent: 100, color: '#fff' }]);
  assert.equal(listSummaryFromPayload({ id: 8, name: 'x', owner: { id: 1, username: 'a' }, completion_percent: 50 }, 'a').completionPercent, 50);
  assert.deepEqual(summary.members, [
    { id: '1', name: 'dana', handle: '@dana', role: 'owner' },
    { id: '4', name: 'lee', handle: '@lee', role: 'collaborator' },
    { id: '5', name: 'priya', handle: '@priya', role: 'collaborator' },
    { id: '6', name: 'kai', handle: '@kai', role: 'viewer' },
  ]);
});

test('summary carries the smart flag, tags and notes flag from the serializer', () => {
  const summary = listSummaryFromPayload(
    listPayload({ is_smart: true, tags: ['Weekend', 7], include_notes: true }),
    'dana',
  );
  assert.equal(summary.isSmart, true);
  assert.deepEqual(summary.tags, ['Weekend']);
  assert.equal(summary.includeNotes, true);
});

test('roles follow the viewer identity from the payload', () => {
  const collaborator = listSummaryFromPayload(listPayload(), 'lee');
  assert.equal(collaborator.role, 'Collaborator');
  assert.equal(collaborator.canEdit, true);
  assert.equal(collaborator.canDelete, false);
  assert.equal(collaborator.isOwner, false);
  const viewer = listSummaryFromPayload(listPayload(), 'kai');
  assert.equal(viewer.role, 'Viewer');
  assert.equal(viewer.canEdit, false);
  const stranger = listSummaryFromPayload(listPayload(), 'sasha');
  assert.equal(stranger.role, 'Viewer');
  assert.equal(stranger.canEdit, false);
});

test('falls back to the is_public and is_unlisted flags when visibility is absent', () => {
  const unlisted = listSummaryFromPayload(listPayload({ visibility: null, is_public: false, is_unlisted: true }), '');
  assert.equal(unlisted.visibility, 'unlisted');
  assert.equal(unlisted.visibilityLabel, 'Unlisted');
  const privateList = listSummaryFromPayload(listPayload({ visibility: null, is_public: false, is_unlisted: false }), '');
  assert.equal(privateList.visibility, 'private');
  // Recommendations only apply to public lists, matching CustomList.can_recommend.
  const privateRecs = listSummaryFromPayload(
    listPayload({ visibility: 'private', allow_recommendations: true }),
    '',
  );
  assert.equal(privateRecs.canRecommend, false);
});

test('returns null for malformed summaries and maps paginated hub payloads', () => {
  assert.equal(listSummaryFromPayload(null, ''), null);
  assert.equal(listSummaryFromPayload({ name: 'No id' }, ''), null);
  const hub = listsFromPayload({ results: [listPayload(), { broken: true }, null] }, 'dana');
  assert.equal(hub.length, 1);
  assert.equal(hub[0].id, '7');
  assert.deepEqual(listsFromPayload(undefined, ''), []);
});

test('maps item entries into detail rows with media identity and rank', () => {
  const rows = listItemsFromPayload({
    items: {
      pagination: { total: 2 },
      results: [
        itemEntry(),
        itemEntry({
          id: 43,
          item_id: 'tv/tmdb/1396',
          item: {
            media_type: 'season',
            library_media_type: 'season',
            source: 'tmdb',
            media_id: '1396',
            title: 'Better Call Saul',
            image: null,
            release_datetime: '2015-02-08T00:00:00Z',
            season_number: 2,
            episode_number: null,
          },
          score: null,
          status: 2,
          progress: 4,
          progress_unit: 'episodes',
          episodes_left: 9,
          rank: 1,
        }),
      ],
    },
  });
  assert.equal(rows[0].id, 'movie/tmdb/550');
  assert.equal(rows[0].itemId, 42);
  assert.equal(rows[0].title, 'Fight Club');
  assert.equal(rows[0].status, 'In progress');
  assert.equal(rows[0].progressLabel, '1 play');
  assert.equal(rows[0].releaseLabel, '1999');
  assert.equal(rows[0].rank, 1);
  assert.deepEqual(rows[0].identity, {
    mediaType: 'movie',
    source: 'tmdb',
    mediaId: '550',
    title: 'Fight Club',
    seasonNumber: null,
    episodeNumber: null,
  });
  assert.equal(rows[1].identity.seasonNumber, 2);
  assert.equal(rows[1].rank, 1);
  assert.equal(rows[1].progressLabel, '4 / 13 episodes');
  assert.equal(rows[1].cardShape, 'poster');
  assert.deepEqual(listItemsFromPayload({ items: null }), []);
});

test('Date Added sorts by membership timestamp within a day, not tracking creation', () => {
  const rows = listItemsFromPayload({ items: { results: [
    itemEntry({ id: 1, list_date_added: '2026-11-09T08:00:00Z', created_at: '2026-10-01T00:00:00Z' }),
    itemEntry({ id: 2, list_date_added: '2026-11-09T15:00:00Z', created_at: '2026-09-01T00:00:00Z' }),
  ] } });
  assert.deepEqual([...rows].sort((a, b) => compareListDateAdded(a, b, 'desc')).map(row => row.itemId), [2, 1]);
  assert.deepEqual([...rows].sort((a, b) => compareListDateAdded(a, b, 'asc')).map(row => row.itemId), [1, 2]);
  assert.equal(rows[0].addedDate, '2026-11-09');
});

test('podcast list rows use tracking statuses and keep untracked rows blank', () => {
  const rows = listItemsFromPayload({ items: { results: [
    itemEntry({ id: 1, status: 1, item: { media_type: 'podcast', title: 'Show' } }),
    itemEntry({ id: 2, status: 3, item: { media_type: 'podcast', title: 'Episode' } }),
    itemEntry({ id: 3, status: null, item: { media_type: 'podcast', title: 'Untracked' } }),
  ] } });
  assert.deepEqual(rows.map(row => row.status), ['In progress', 'Completed', null]);
  assert.deepEqual(rows.map(row => row.cardShape), ['square', 'square', 'square']);
});

test('activity entries carry the server description wording', () => {
  const entries = activityFromPayload({
    results: [
      {
        id: 9,
        activity_type: 'item_added',
        user: 'lee',
        item_id: 42,
        item_title: 'Fight Club',
        details: '',
        timestamp: '2026-11-08T14:30:00Z',
      },
      {
        id: 10,
        activity_type: 'list_edited',
        user: 'dana',
        item_id: null,
        item_title: null,
        details: 'Renamed from Drafts',
        timestamp: '2026-11-08T15:00:00Z',
      },
      {
        id: 11,
        activity_type: 'unknown_type',
        user: null,
        item_id: null,
        item_title: null,
        details: '',
        timestamp: null,
      },
    ],
  });
  assert.equal(entries[0].description, 'lee added Fight Club');
  assert.equal(entries[1].description, 'dana edited the list: Renamed from Drafts');
  assert.equal(entries[2].description, 'Unknown activity');
  assert.match(entries[0].agoLabel, /minute|hour|day|moments/);
  assert.equal(entries[0].itemTitle, 'Fight Club');
  assert.deepEqual(activityFromPayload({}), []);
});

test('maps pending recommendations with the serializer fields', () => {
  const recs = recommendationsFromPayload({
    results: [{
      id: 3,
      item_id: 42,
      title: 'Fight Club',
      media_type: 'movie',
      recommended_by: 'lee',
      note: 'For the rewatch',
      date_recommended: new Date(Date.now() - 7200000).toISOString(),
    }],
  });
  assert.equal(recs.length, 1);
  assert.equal(recs[0].id, '3');
  assert.equal(recs[0].itemId, 42);
  assert.equal(recs[0].recommender, 'lee');
  assert.equal(recs[0].note, 'For the rewatch');
  assert.match(recs[0].dateLabel, /2 hours/);
  assert.equal(recs[0].media.title, 'Fight Club');
  assert.deepEqual(recommendationsFromPayload({ results: 'nope' }), []);
});

test('maps provider search results and pagination', () => {
  const results = searchResultsFromPayload({
    pagination: { total: 1, limit: 20, offset: 0 },
    results: [{
      media_id: '550',
      source: 'tmdb',
      media_type: 'movie',
      title: 'Fight Club',
      image: 'https://server.example/media/poster.jpg',
      year: 1999,
    }],
  });
  assert.equal(results[0].id, 'movie-tmdb-550');
  assert.equal(results[0].mediaId, '550');
  assert.equal(results[0].mediaTypeLabel, 'Movie');
  assert.equal(results[0].year, '1999');
  assert.deepEqual(results[0].identity, {
    mediaType: 'movie',
    source: 'tmdb',
    mediaId: '550',
    title: 'Fight Club',
  });
  assert.deepEqual(searchResultsFromPayload({ results: [] }), []);
  const pagination = searchPaginationFromPayload({ pagination: { total: 45, limit: 20, offset: 40 } });
  assert.equal(pagination.totalPages, 3);
  assert.equal(searchPaginationFromPayload({}).totalPages, 1);
});

test('hub sort options stay inside the API sort vocabulary', () => {
  // src/api/helpers.py LIST_SORTS plus the view's `watched` (Last Watched).
  assert.deepEqual(
    LIST_SORT_OPTIONS.map(option => option.value).sort(),
    ['items', 'name', 'new', 'update', 'watched'],
  );
  const name = LIST_SORT_OPTIONS.find(option => option.value === 'name');
  assert.equal(name.direction, 'asc');
});

function recordingRequest() {
  const calls = [];
  const request = async (path, options = {}) => {
    calls.push({ path, options });
    return {};
  };
  return { calls, request };
}

test('read actions target the list API paths and query params', async () => {
  const { calls, request } = recordingRequest();
  const transport = createRemoteListActions(request);
  await transport.load({ search: 'week', sort: 'name_asc' });
  assert.equal(calls[0].path, '/api/v1/lists/');
  assert.deepEqual(calls[0].options.query, { limit: 200, offset: 0, search: 'week', sort: 'name_asc' });
  await transport.load();
  assert.deepEqual(calls[1].options.query, { limit: 200, offset: 0 });
  await transport.loadDetail({ listId: 7 });
  // The first detail load also reads the time zone and date format once.
  for (const path of ['/api/v1/info/', '/api/v1/user/preferences/']) {
    const at = calls.findIndex(call => call.path === path);
    if (at >= 0) calls.splice(at, 1);
  }
  assert.equal(calls[2].path, '/api/v1/lists/7/');
  assert.deepEqual(calls[2].options.query, { limit: 200, offset: 0 });
  await transport.loadActivity({ listId: 7 });
  assert.equal(calls[3].path, '/api/v1/lists/7/activity/');
  await transport.loadRecommendations({ listId: 7 });
  assert.equal(calls[4].path, '/api/v1/lists/7/recommendations/');
  await transport.searchMedia({ mediaType: 'game', search: 'bal', limit: 20, offset: 40 });
  assert.equal(calls[5].path, '/api/v1/search/game/');
  assert.deepEqual(calls[5].options.query, { limit: 20, offset: 40, search: 'bal' });
});

test('create and update send only the fields the API accepts', async () => {
  const { calls, request } = recordingRequest();
  const transport = createRemoteListActions(request);
  await transport.create({
    name: 'New picks',
    description: '',
    visibility: 'public',
    isPublic: true,
    allowRecommendations: true,
    publicSlug: 'picks',
    tags: ['Weekend'],
    includeNotes: true,
    isSmart: true,
  });
  assert.deepEqual(calls[0].options.json, {
    name: 'New picks',
    description: '',
    visibility: 'public',
    is_public: true,
    allow_recommendations: true,
    public_slug: 'picks',
    tags: ['Weekend'],
    include_notes: true,
    is_smart: true,
  });
  await transport.create({ name: 'Private', visibility: 'private', allowRecommendations: true });
  assert.equal(calls[1].options.json.public_slug, undefined);
  assert.equal('collaborators' in calls[1].options.json, false);
  await transport.update({ listId: 7, visibility: 'private' });
  assert.deepEqual(calls[2].options.json, { visibility: 'private' });
  await transport.update({ listId: 7, name: 'Renamed', publicSlug: '', tags: [], isSmart: false });
  assert.deepEqual(calls[3].options.json, {
    name: 'Renamed',
    public_slug: '',
    tags: [],
    is_smart: false,
  });
  await transport.remove({ listId: 7 });
  assert.equal(calls[4].path, '/api/v1/lists/7/');
  assert.equal(calls[4].options.method, 'DELETE');
});

test('members are written by username through their own endpoints', async () => {
  const { calls, request } = recordingRequest();
  const transport = createRemoteListActions(request);
  const members = [
    { id: '1', name: 'dana', role: 'owner' },
    { id: '4', name: 'lee', role: 'collaborator' },
    { id: 'sasha', name: 'sasha', role: 'collaborator' },
    { id: '6', name: 'kai', role: 'viewer' },
  ];
  assert.deepEqual(memberUsernames(members, 'collaborator'), ['lee', 'sasha']);
  assert.deepEqual(memberUsernames(members, 'viewer'), ['kai']);
  await transport.setMembers({ listId: 7, collaborators: ['lee', 'sasha'], viewers: ['kai'] });
  assert.equal(calls[0].path, '/api/v1/lists/7/collaborators/');
  assert.equal(calls[0].options.method, 'PUT');
  assert.deepEqual(calls[0].options.json, { usernames: ['lee', 'sasha'] });
  assert.equal(calls[1].path, '/api/v1/lists/7/viewers/');
  assert.deepEqual(calls[1].options.json, { usernames: ['kai'] });
});

test('every page of a hub or list is fetched', async () => {
  const pages = [
    { pagination: { total: 450 }, results: new Array(200).fill({ id: 1 }) },
    { pagination: { total: 450 }, results: new Array(200).fill({ id: 2 }) },
    { pagination: { total: 450 }, results: new Array(50).fill({ id: 3 }) },
  ];
  const offsets = [];
  const transport = createRemoteListActions(async (path, options) => {
    offsets.push(options.query.offset);
    return pages[offsets.length - 1];
  });
  const payload = await transport.load();
  assert.equal(payload.results.length, 450);
  assert.deepEqual(offsets, [0, 200, 400]);
  assert.equal(hasMorePages({ items: { pagination: { total: 450 } } }, 0), true);
  assert.equal(hasMorePages({ items: { pagination: { total: 450 } } }, 400), false);
  assert.equal(hasMorePages({}, 0), false);
});

test('smart rules, options and sync use the smart-list endpoints', async () => {
  const { calls, request } = recordingRequest();
  const transport = createRemoteListActions(request);
  await transport.loadSmartRules({ listId: 7 });
  await transport.loadSmartOptions({ listId: 7 });
  await transport.saveSmartRules({ listId: 7, rules: { media_types: ['movie'] } });
  await transport.syncSmartList({ listId: 7 });
  assert.deepEqual(calls.map(call => call.path), [
    '/api/v1/lists/7/smart-rules/',
    '/api/v1/lists/7/smart-rules/options/',
    '/api/v1/lists/7/smart-rules/',
    '/api/v1/lists/7/smart-sync/',
  ]);
  assert.equal(calls[2].options.method, 'PUT');
  assert.deepEqual(calls[2].options.json, { media_types: ['movie'] });
  assert.equal(smartRulesFromPayload({}), null);
  assert.deepEqual(smartRulesFromPayload({ rules: { media_types: ['movie'] } }), { media_types: ['movie'] });
  const options = smartOptionsFromPayload({
    media_types: [{ value: 'movie', label: 'Movie' }],
    options: { genres: ['Drama'], implied_genres: ['Noir'], show_languages: false, author_is_channel: true },
  });
  assert.deepEqual(options.mediaTypes, [{ value: 'movie', label: 'Movie' }]);
  assert.deepEqual(options.genres, ['Drama']);
  assert.deepEqual(options.impliedGenres, ['Noir']);
  assert.equal(options.showLanguages, false);
  assert.equal(options.authorIsChannel, true);
});

test('tier placements are read from the tiers endpoint and applied by item id', () => {
  const payload = {
    tiers: [{ id: 's', name: 'S', color: '#ff7f7f' }, { id: 'a', name: 'A', color: '#ffbf7f' }],
    placements: [
      { item_id: 10, tier: 'a' },
      { item_id: 11, tier: 's' },
      { item_id: 12, tier: 'a' },
      { item_id: 13, tier: '' },
      { item_id: 14, tier: 'gone' },
    ],
  };
  assert.deepEqual(tiersFromPayload(payload).map(tier => tier.id), ['s', 'a']);
  const items = [10, 11, 12, 13, 14, 15].map(itemId => ({ itemId, tier: '', tierRank: 0 }));
  const placed = applyTierPlacements(items, payload);
  assert.deepEqual(placed.map(item => [item.itemId, item.tier, item.tierRank]), [
    [10, 'a', 1], [11, 's', 1], [12, 'a', 2], [13, '', 1], [14, '', 2], [15, '', 0],
  ]);
});

test('bulk actions use the library bulk endpoints with item ids', async () => {
  const { calls, request } = recordingRequest();
  const transport = createRemoteListActions(request);
  await transport.bulkStatus({ itemIds: [42], status: 'Completed' });
  await transport.bulkCollection({ itemIds: [42] });
  await transport.bulkList({ itemIds: [42], listId: '9' });
  await transport.bulkTag({ itemIds: [42], tagId: '3' });
  assert.deepEqual(calls.map(call => call.path), [
    '/api/v1/library/bulk/status/',
    '/api/v1/library/bulk/collection/',
    '/api/v1/lists/9/items/bulk/',
    '/api/v1/library/bulk/tags/',
  ]);
  assert.deepEqual(calls[2].options.json, { item_ids: [42] });
  assert.deepEqual(calls[3].options.json, { item_ids: [42], tag_id: 3, action: 'add' });
});

test('add and remove items use the media-scoped list membership routes', async () => {
  const { calls, request } = recordingRequest();
  const transport = createRemoteListActions(request);
  await transport.addItem({
    listId: 7,
    item: { identity: { mediaType: 'movie', source: 'tmdb', mediaId: '550' } },
  });
  assert.equal(calls[0].path, '/api/v1/media/movie/tmdb/550/lists/7/');
  assert.equal(calls[0].options.method, 'PUT');
  await transport.addItem({
    listId: 7,
    item: { identity: { mediaType: 'anime', source: 'mal', mediaId: '1' }, libraryMediaType: 'anime' },
  });
  assert.deepEqual(calls[1].options.json, { library_media_type: 'anime' });
  await transport.removeItem({
    listId: 7,
    item: { identity: { mediaType: 'season', source: 'tmdb', mediaId: '1396', seasonNumber: 2 } },
  });
  assert.equal(calls[2].path, '/api/v1/media/season/tmdb/1396/2/lists/7/');
  assert.equal(calls[2].options.method, 'DELETE');
  await transport.removeItem({
    listId: 7,
    item: { identity: { mediaType: 'episode', source: 'tmdb', mediaId: '1396', seasonNumber: 2, episodeNumber: 3 } },
  });
  // Episode rows reuse the tv media_type segment, like mediaCardTransport.
  assert.equal(calls[3].path, '/api/v1/media/tv/tmdb/1396/2/3/lists/7/');
});

test('ratings and recommendations target the media and list routes', async () => {
  const { calls, request } = recordingRequest();
  const transport = createRemoteListActions(request);
  await transport.rateItem({ item: { identity: { mediaType: 'movie', source: 'tmdb', mediaId: '550' } }, score: 9 });
  assert.equal(calls[0].path, '/api/v1/media/movie/tmdb/550');
  assert.deepEqual(calls[0].options.json, { score: 9 });
  await transport.recommend({ listId: 7, item: { identity: { mediaType: 'movie', source: 'tmdb', mediaId: '550' } }, note: 'Hi' });
  assert.equal(calls[1].path, '/api/v1/lists/7/recommendations/');
  assert.deepEqual(calls[1].options.json, {
    media_id: '550',
    media_type: 'movie',
    source: 'tmdb',
    note: 'Hi',
  });
  await transport.decideRecommendation({ listId: 7, recommendationId: 3, decision: 'approve' });
  assert.equal(calls[2].path, '/api/v1/lists/7/recommendations/3/approve/');
  await transport.decideRecommendation({ listId: 7, recommendationId: 3, decision: 'deny' });
  assert.equal(calls[3].path, '/api/v1/lists/7/recommendations/3/deny/');
});

test('reorder posts the numeric item id with the action word', async () => {
  const { calls, request } = recordingRequest();
  const transport = createRemoteListActions(request);
  await transport.reorderItem({ listId: 7, itemId: 42, action: 'back' });
  assert.equal(calls[0].path, '/api/v1/lists/7/items/reorder/');
  assert.deepEqual(calls[0].options.json, { item_id: 42, action: 'back' });
});

test('tier reads and writes go through /api/v1', async () => {
  const { calls, request } = recordingRequest();
  const transport = createRemoteListActions(request);
  await transport.loadTiers({ listId: 7 });
  assert.equal(calls[0].path, '/api/v1/lists/7/tiers/');
  await transport.moveTierItem({ listId: 7, itemId: 42, tierId: 's', itemIds: [42, 43] });
  assert.equal(calls[1].path, '/api/v1/lists/7/tiers/move/');
  assert.deepEqual(calls[1].options.json, { tier: 's', item_id: 42, item_ids: [42, 43] });
  await transport.saveTiers({ listId: 7, tiers: [{ id: 's', name: 'S', color: '#ff7f7f' }] });
  assert.equal(calls[2].path, '/api/v1/lists/7/tiers/');
  assert.equal(calls[2].options.method, 'PUT');
  assert.deepEqual(calls[2].options.json, { tiers: [{ id: 's', name: 'S', color: '#ff7f7f' }] });
  await transport.fillFromRatings({ listId: 7 });
  assert.equal(calls[3].path, '/api/v1/lists/7/tiers/fill/');
  await transport.undoTierFill({ listId: 7, placements: [{ item_id: 42, tier: 's' }] });
  assert.equal(calls[4].path, '/api/v1/lists/7/tiers/fill/undo/');
  assert.deepEqual(calls[4].options.json, { placements: [{ item_id: 42, tier: 's' }] });
});

test('local actions never throw and carry the signed-out reason', async () => {
  const transport = createLocalListActions('Sign in to your Floppy server to save list changes.');
  assert.equal(transport.mode, 'local');
  assert.match(transport.reason, /sign in/i);
  for (const name of [
    'load', 'loadDetail', 'loadActivity', 'loadRecommendations', 'searchMedia',
    'create', 'update', 'remove', 'updateVisibility', 'setMembers',
    'loadSmartRules', 'loadSmartOptions', 'saveSmartRules', 'syncSmartList', 'loadTiers', 'bulkList', 'bulkTag', 'loadTags',
    'addItem', 'removeItem', 'rateItem', 'recommend', 'decideRecommendation',
    'reorderItem', 'moveTierItem', 'saveTiers', 'fillFromRatings', 'undoTierFill',
    'bulkStatus', 'bulkCollection',
  ]) {
    const result = await transport[name]({ listId: 1 });
    assert.equal(result.local, true, name);
    assert.equal(result.reason, transport.reason);
  }
});
