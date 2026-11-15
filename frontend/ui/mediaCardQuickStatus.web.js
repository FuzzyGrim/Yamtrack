// Browser transport for the quick status control. A state read prevents adding
// a new consumption when the card's initial tracking state has gone stale.
export async function saveQuickStatus(quick, status, csrfToken, fetcher = fetch) {
  const { mediaType, source, mediaId, seasonNumber, instanceId } = quick.identity;
  const segment = value => encodeURIComponent(value);
  const coordinated = mediaType === 'season';
  const base = `/api/v1/media/${segment(coordinated ? 'tv' : mediaType)}/${segment(source)}/${segment(mediaId)}`
    + (coordinated ? `/${segment(seasonNumber)}` : '');
  const stateResponse = await fetcher(`${base}/state/`, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
  if (!stateResponse.ok) throw new Error('Could not check tracking status. Please try again.');
  const state = await stateResponse.json();
  const tracked = Boolean(state.tracked);
  if (!tracked && source === 'manual') throw new Error('Use the tracking dialog to add this manual item.');
  const path = tracked
    ? `${base}/${instanceId != null ? `history/${segment(instanceId)}/` : ''}`
    : `/api/v1/media/${segment(mediaType)}/`;
  const body = tracked ? { status } : { source, media_id: mediaId, ...(coordinated ? { season_number: seasonNumber } : {}), status };
  const response = await fetcher(path, { method: tracked ? 'PATCH' : 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken || '' }, body: JSON.stringify(body) });
  if (!response.ok) throw new Error('Could not change status. Please try again.');
}
