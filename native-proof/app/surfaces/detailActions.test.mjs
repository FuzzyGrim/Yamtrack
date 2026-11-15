import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { episodesFromPayload } from '../data/detailTransportCore.js';
import {
  RELOAD_SCOPES,
  applyEpisodeScore,
  applyEpisodeWatched,
  applyTrackingStatus,
  applyViewerRating,
  createLatestGate,
  scoreChipsFromPayload,
  storedScore,
  trackingFactsFromPayload,
  viewerRatingFromPayload,
} from './detailActions.mjs';
import { loadAlbumWithTracks, loadShowWithEpisodes } from './detailLoads.mjs';

// Real server answers (trimmed) captured before and after each write.
const fixture = JSON.parse(readFileSync(new URL('./detailActions.fixture.json', import.meta.url), 'utf8'));

// The part of the screen model the rating changes, as the first load maps it.
function ratingModel(payload) {
  return {
    viewerRating: viewerRatingFromPayload(payload),
    scoreChips: scoreChipsFromPayload(payload),
    facts: trackingFactsFromPayload(payload),
  };
}

test('rating on screen equals the page the server returns after the write', () => {
  const { before, after_6_5: after, after_clear: cleared } = fixture.tvRate;
  assert.deepEqual(applyViewerRating(ratingModel(before), 6.5), ratingModel(after));
  assert.deepEqual(applyViewerRating(ratingModel(before), null), ratingModel(cleared));
  // Rating a cleared item adds "Your score" back where the server puts it.
  assert.deepEqual(applyViewerRating(ratingModel(cleared), 6.5), ratingModel(after));
});

test('rating on a five-point scale stores and shows the server values', () => {
  const { before, after_3_5: after } = fixture.movieRateScale5;
  assert.equal(storedScore(3.5, 5), 7);
  assert.deepEqual(applyViewerRating(ratingModel(before), 3.5), ratingModel(after));
});

test('rating leaves a page without an editable rating untouched', () => {
  const model = { title: 'Public', viewerRating: null, scoreChips: [] };
  assert.equal(applyViewerRating(model, 8), model);
  assert.equal(viewerRatingFromPayload(fixture.tvRate.before, true), null);
});

test('rating then reverting restores the previous screen exactly', () => {
  const start = ratingModel(fixture.tvRate.before);
  const changed = applyViewerRating(start, 3);
  assert.deepEqual(applyViewerRating(changed, start.viewerRating.value), start);
});

const identity = { mediaType: 'season', source: 'tmdb', mediaId: '1396', seasonNumber: 1 };
const rows = payload => episodesFromPayload(payload, identity, 1);
const episodeFive = { mediaType: 'episode', source: 'tmdb', mediaId: '1396', seasonNumber: 1, episodeNumber: 5 };
const pick = list => list.map(row => ({ key: row.key, watched: row.watched, score: row.score }));

test('marking an episode watched matches the server rows that follow', () => {
  const { before, after_watch: watched, after_unwatch: unwatched } = fixture.seasonWatch;
  assert.deepEqual(pick(applyEpisodeWatched(rows(before), episodeFive, true)), pick(rows(watched)));
  const back = applyEpisodeWatched(rows(watched), episodeFive, false);
  assert.deepEqual(pick(back), pick(rows(unwatched)));
  const five = back.find(row => row.key.endsWith('-1-5'));
  assert.equal(five.endedLabel, '');
  assert.equal(five.playsLabel, '');
  // Other rows are the same objects, so they do not re-render.
  const start = rows(before);
  const next = applyEpisodeWatched(start, episodeFive, true);
  assert.equal(next[0], start[0]);
  assert.equal(next[2], start[2]);
});

test('an episode score changes only that row', () => {
  const start = rows(fixture.seasonWatch.before);
  const next = applyEpisodeScore(start, episodeFive, 7);
  assert.deepEqual(next.map(row => row.score), [null, 7, null]);
  assert.deepEqual(applyEpisodeScore(next, episodeFive, null).map(row => row.score), [null, null, null]);
});

test('a saved status shows in the header tracking state', () => {
  const tracking = { tracked: true, tracking: { id: 4, status: 'In progress' } };
  assert.deepEqual(applyTrackingStatus(tracking, 'Completed'), { tracked: true, tracking: { id: 4, status: 'Completed' } });
  assert.equal(applyTrackingStatus(tracking, ''), tracking);
  assert.equal(applyTrackingStatus({ tracked: false, tracking: null }, 'Completed').tracking, null);
});

test('later taps win and answers that arrive late are ignored', () => {
  const gate = createLatestGate();
  const first = gate.begin();
  const second = gate.begin();
  assert.equal(gate.isLatest(first), false);
  assert.equal(gate.isLatest(second), true);
});

test('actions reload only the parts they change', () => {
  assert.deepEqual([...RELOAD_SCOPES.rate], ['page']);
  assert.deepEqual([...RELOAD_SCOPES.episodeWatch].sort(), ['episodes', 'header', 'page']);
  assert.deepEqual([...RELOAD_SCOPES.episodeRate], ['episodes']);
  assert.ok(!RELOAD_SCOPES.episodeWatch.includes('collection'));
});

function deferredRequest() {
  const calls = [];
  const request = path => new Promise((resolve, reject) => calls.push({ path, resolve, reject }));
  return { calls, request };
}

test('an album and its tracks are both requested before either answers', async () => {
  const { calls, request } = deferredRequest();
  const loading = loadAlbumWithTracks(request, '7');
  assert.deepEqual(calls.map(call => call.path), ['/api/v1/music/albums/7/', '/api/v1/music/albums/7/tracks/']);
  calls[1].resolve({ results: [{ id: 1 }] });
  calls[0].resolve({ id: 7 });
  assert.deepEqual(await loading, [{ id: 7 }, { results: [{ id: 1 }] }]);
});

test('a podcast show and its episodes are both requested before either answers', async () => {
  const { calls, request } = deferredRequest();
  const loading = loadShowWithEpisodes(request, 'a b');
  assert.deepEqual(calls.map(call => call.path), [
    '/api/v1/podcasts/shows/a%20b/',
    '/api/v1/podcasts/shows/a%20b/episodes/?limit=30&offset=0',
  ]);
  calls[0].resolve({ id: 'a b' });
  calls[1].resolve([]);
  assert.deepEqual(await loading, [{ id: 'a b' }, []]);
});

test('a failed pair reports the same error as loading one after the other', async () => {
  const album = new Error('album failed');
  const tracks = new Error('tracks failed');
  let pending = deferredRequest();
  let loading = loadAlbumWithTracks(pending.request, '7');
  pending.calls[1].reject(tracks);
  pending.calls[0].reject(album);
  await assert.rejects(loading, album);

  pending = deferredRequest();
  loading = loadShowWithEpisodes(pending.request, '3');
  pending.calls[0].resolve({ id: 3 });
  pending.calls[1].reject(tracks);
  await assert.rejects(loading, tracks);
});
