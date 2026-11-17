import assert from 'node:assert/strict';
import test from 'node:test';
import { createRemoteSearchActions, suggestionsFromPayload } from './searchTransportCore.js';

const payload = {
  suggestions: [
    {
      id: '0', title: 'Columbo', subtitle: 'TV · 1971 · In progress', image: '/images/cache/abc.jpg',
      url: '/details/tmdb/tv/1/columbo', identity: { mediaType: 'tv', source: 'tmdb', mediaId: '1', title: 'Columbo' },
    },
    { id: '1', title: 'Some Artist', subtitle: 'Music · Artist', image: null, url: '/music/artist/9', identity: null },
  ],
};

test('typeahead rows keep the server order and carry item identity for navigation', () => {
  const rows = suggestionsFromPayload(payload, 'https://floppy.example');
  assert.deepEqual(rows.map(row => row.title), ['Columbo', 'Some Artist']);
  assert.equal(rows[0].image, 'https://floppy.example/images/cache/abc.jpg');
  assert.equal(rows[0].mediaType, 'tv');
  assert.equal(rows[0].identity.mediaId, '1');
  assert.equal(rows[1].identity, null);
  assert.equal(rows[1].href, '/music/artist/9');
});

test('a session sign-in uses the web typeahead; a token sign-in keeps the media list', async () => {
  const calls = [];
  const request = async (path, options) => { calls.push({ path, query: options.query }); return payload; };

  const session = createRemoteSearchActions(request, { suggestionsViaWeb: true, serverUrl: 'https://floppy.example' });
  const rows = await session.searchSuggestions({ search: 'col', mediaType: 'tv' });
  assert.equal(rows.length, 2);
  const { sid, ...query } = calls[0].query;
  assert.equal(calls[0].path, '/search/suggestions');
  assert.deepEqual(query, { q: 'col', media_type: 'tv', format: 'json' });
  // One id per search box, so the server can stop the request a newer one replaced.
  assert.match(sid, /^[A-Za-z0-9-]{8,64}$/);
  await session.searchSuggestions({ search: 'colu', mediaType: 'tv' });
  assert.equal(calls[1].query.sid, sid);

  const token = createRemoteSearchActions(request);
  assert.equal(token.searchSuggestions, undefined);
  assert.equal(typeof token.searchLocal, 'function');
});
