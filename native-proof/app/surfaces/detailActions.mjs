// What a detail-page action changes on screen, applied before the server
// answers, and which parts of the page still need fresh data afterwards.
// The server stays the source of truth: the page refreshes the parts listed
// in RELOAD_SCOPES in the background, and a failed write restores the
// previous value. Pure functions, shared by the Expo detail screen and its
// feature adapter.

// The page payload's tracking pieces, mapped the same way for the first load
// and for the on-screen change, so the two cannot drift apart.

/** "Status", "Your score" and "Progress" after the payload's own facts. */
export function trackingFactsFromPayload(payload) {
  const facts = [...(payload?.facts || [])];
  const tracking = payload?.tracking;
  if (tracking?.status) facts.push({ label: 'Status', value: String(tracking.status) });
  if (tracking?.score != null) facts.push({ label: 'Your score', value: String(tracking.score) });
  if (tracking?.progress != null) {
    const max = tracking.max_progress;
    facts.push({ label: 'Progress', value: max ? `${tracking.progress} / ${max}` : String(tracking.progress) });
  }
  return facts;
}

/** The viewer's own rating control, or null when the page shows none. */
export function viewerRatingFromPayload(payload, publicView = false) {
  if (!payload?.tracking || publicView || payload?.viewer_rating?.enabled === false) return null;
  return {
    value: payload.viewer_rating?.value ?? payload.tracking.score,
    scaleMax: payload.viewer_rating?.scale_max || 10,
    editable: Boolean(payload?.capabilities?.can_update),
  };
}

/** Score chips (the viewer's and stored sources) followed by tag chips. */
export function scoreChipsFromPayload(payload) {
  return [
    ...(payload?.score_chips || []).map(chip => ({ ...chip, value: chip.value ?? chip.rating ?? chip.score ?? chip.tier })),
    ...(payload?.tag_sections || []).flatMap(group => (group.entries || []).map(entry => ({
      ...entry,
      label: group.title_label || group.title,
      text: entry.label,
    }))),
  ];
}

/** Parts of the detail page that load separately. */
export const DETAIL_PARTS = Object.freeze(['page', 'header', 'collection', 'episodes']);

/**
 * Parts each action can change. Rating changes the page payload only
 * (the viewer score, its chip, "Your score" and recent history). Watching an
 * episode changes the episode rows, the progress card and status (header)
 * and the page payload, but never owned copies or provider scores.
 */
export const RELOAD_SCOPES = Object.freeze({
  rate: Object.freeze(['page']),
  episodeWatch: Object.freeze(['page', 'header', 'episodes']),
  episodeRate: Object.freeze(['episodes']),
  all: DETAIL_PARTS,
});

/** Stored score (0 to 10) for a value picked on the viewer's scale. */
export function storedScore(value, scaleMax = 10) {
  if (value == null) return null;
  return Number(value) * 10 / (Number(scaleMax) || 10);
}

/**
 * The page model with the viewer's rating set: the rating control, the
 * "Your score" chip and fact all show the new value. Untouched when the page
 * has no editable rating.
 */
export function applyViewerRating(model, value) {
  if (!model?.viewerRating) return model;
  const stored = storedScore(value, model.viewerRating.scaleMax);
  const facts = (model.facts || []).flatMap(fact => {
    if (fact?.label !== 'Your score') return [fact];
    return stored == null ? [] : [{ ...fact, value: String(stored) }];
  });
  if (stored != null && !facts.some(fact => fact?.label === 'Your score')
    && (model.facts || []).some(fact => fact?.label === 'Status')) {
    const at = facts.findIndex(fact => fact?.label === 'Status') + 1;
    facts.splice(at, 0, { label: 'Your score', value: String(stored) });
  }
  return {
    ...model,
    viewerRating: { ...model.viewerRating, value: value == null ? null : Number(value) },
    // The server lists "Your score" first and leaves it out when there is none.
    scoreChips: [
      ...(stored == null ? [] : [{ ...((model.scoreChips || []).find(chip => chip?.kind === 'viewer')
        || { label: 'Your score', kind: 'viewer' }), value: stored }]),
      ...(model.scoreChips || []).filter(chip => chip?.kind !== 'viewer'),
    ],
    facts,
  };
}

function sameEpisode(row, identity) {
  const rowIdentity = row?.identity || {};
  return Number(rowIdentity.seasonNumber) === Number(identity?.seasonNumber)
    && Number(rowIdentity.episodeNumber) === Number(identity?.episodeNumber);
}

/**
 * Episode rows with one episode marked watched or unwatched. The watched
 * date line waits for the server's answer; unwatching clears it.
 */
export function applyEpisodeWatched(rows, identity, watched) {
  return (rows || []).map(row => {
    if (!sameEpisode(row, identity)) return row;
    return {
      ...row,
      watched: Boolean(watched),
      ...(watched ? {} : { endedLabel: '', playsLabel: '' }),
    };
  });
}

/** Episode rows with one episode's score set, in the row's own scale. */
export function applyEpisodeScore(rows, identity, score) {
  return (rows || []).map(row => (sameEpisode(row, identity)
    ? { ...row, score: score == null ? null : Number(score) }
    : row));
}

/** Header tracking state with the status the viewer just saved. */
export function applyTrackingStatus(tracking, status) {
  if (!status || !tracking?.tracking) return tracking;
  return { ...tracking, tracking: { ...tracking.tracking, status } };
}

/**
 * Orders overlapping actions and loads: a result is applied only when no
 * newer action or load started after it, so a late answer never overwrites
 * a newer tap.
 */
export function createLatestGate() {
  let latest = 0;
  return {
    begin() { latest += 1; return latest; },
    isLatest(token) { return token === latest; },
  };
}
