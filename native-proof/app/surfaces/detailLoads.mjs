// Detail requests that do not depend on each other start together.
// The results and errors match the earlier one-after-the-other order: when
// both fail, the first request's error is the one reported.

async function both(first, second) {
  const [a, b] = await Promise.allSettled([first, second]);
  if (a.status === 'rejected') throw a.reason;
  if (b.status === 'rejected') throw b.reason;
  return [a.value, b.value];
}

/** An album and its track list, requested at the same time. */
export function loadAlbumWithTracks(request, albumId) {
  const id = encodeURIComponent(albumId);
  return both(
    request(`/api/v1/music/albums/${id}/`),
    request(`/api/v1/music/albums/${id}/tracks/`),
  );
}

/** A podcast show and its first page of episodes, requested at the same time. */
export function loadShowWithEpisodes(request, showId) {
  const id = encodeURIComponent(showId);
  return both(
    request(`/api/v1/podcasts/shows/${id}/`),
    request(`/api/v1/podcasts/shows/${id}/episodes/?limit=30&offset=0`),
  );
}
