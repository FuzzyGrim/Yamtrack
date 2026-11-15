import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AccessibilityInfo, FlatList, Platform, ScrollView, StyleSheet, Text, useWindowDimensions, View } from 'react-native';
import { Button } from '../../../frontend/ui/Button';
import { ListsForm } from '../../../frontend/ui/ListsFormView';
import { MediaCardActionHost } from '../../../frontend/ui/MediaCardActionHost';
import { MediaCard } from '../../../frontend/ui/MediaCard';
import { Icon } from '../../../frontend/ui/Icon';
import { actionIcons } from '../../../frontend/ui/actionIcons';
import { TrackDialog } from '../../../frontend/ui/TrackDialog';
import { RatingStars } from '../../../frontend/ui/RatingStars';
import {
  applyListToggle,
  initialTrackValues,
  listsMembership,
  normalizeListsModel,
  normalizeTrackForm,
} from '../../../frontend/ui/mediaCardForms';
import { libraryGridLayout } from '../../../frontend/ui/mediaCardSizing';
import { mediaCardPalette, palette, spacing } from '../theme';
import { AdaptivePresentationSheet } from '../components/AdaptivePresentationSheet';
import NativeCardMenu from './NativeCardMenu';
import { emitMediaCardChange, subscribeMediaCardChange } from './mediaCardEvents';
import {
  createTag,
  deleteTrack,
  hasMediaCardTransport,
  loadLists,
  loadTrack,
  loadTrackTabs,
  reorderListItem,
  reorderListItems,
  runDiscoverAction,
  saveArtwork,
  saveScore,
  saveTrack,
  setDiscoverHidden,
  setTags,
  toggleList,
} from './mediaCardTransport';

export const surface = {
  id: 'MEDIA-CARD',
  title: 'Media card grid',
  where: 'Library grid layout',
  status: 'ported',
  note: 'Renders the card models handed in through the items prop; the Library grid builds them from the same /api/v1/media/ rows the table shows. The native action host owns the Track and Lists form bodies through the platform transport, card-change events, ratings and link routing. A signed-out or unreachable transport shows a sign-in prompt instead of sample content. The Expo export compiles the finished card; iOS, Android, browser and screen-reader runtime interaction remain unverified.',
};

const labels = {
  altTitle: 'Show alternative title',
  nextEpisode: 'Jump to next episode',
  noStatus: 'No Status',
  rate: 'Rate',
  close: 'Close',
  open: 'Open',
};

const signInLabels = {
  title: 'Sign in required',
  body: 'Sign in to your Floppy server to use this action.',
};

function identityKey(identity) {
  if (!identity) return '';
  return [
    identity.mediaType,
    identity.source,
    identity.mediaId,
    identity.seasonNumber || '',
    identity.episodeNumber || '',
  ].join('|');
}

function actionGlyph(action) {
  if (action.kind === 'drag') return '≡';
  if (action.modal === 'track') return '✎';
  if (action.modal === 'lists') return '☰';
  if (action.vals?.action === 'planning') return '＋';
  if (action.vals?.action === 'dismiss') return '⦸';
  if (action.kind === 'link') return '◷';
  return '•';
}

function menuActionLabel(action) {
  if (action.modal === 'track') {
    if (action.media?.mediaType === 'episode') return action.title === 'Watched' ? 'Edit watch' : 'Mark watched';
    return 'Edit tracking';
  }
  if (action.modal === 'lists') return 'Edit lists';
  if (action.kind === 'link' && /\/history(?:[/?#]|$)/.test(action.href || '')) return 'View history';
  if (action.vals?.action === 'planning') return 'Plan title';
  if (action.vals?.action === 'dismiss') return 'Hide title';
  return action.title;
}

// The shapes the web card's action buttons draw, by the action they perform.
function actionSpec(action) {
  if (action.icon) return action.icon;
  if (action.modal === 'track') return actionIcons.edit;
  if (action.modal === 'lists') return actionIcons.listAdd;
  if (action.kind === 'link') return actionIcons.history;
  return null;
}

function renderActionIcon(action) {
  const spec = actionSpec(action);
  if (spec) return <Icon spec={spec} color={mediaCardPalette.chipText} size={18} />;
  return <Text accessible={false} style={styles.actionGlyph}>{actionGlyph(action)}</Text>;
}

function SignInPrompt() {
  return <View style={styles.hostStack}>
    <Text accessibilityRole="header" style={styles.signInTitle}>{signInLabels.title}</Text>
    <Text style={styles.hostBody}>{signInLabels.body}</Text>
  </View>;
}

const listLabels = {
  title: 'Lists',
  close: 'Close',
  loading: 'Loading',
  loadFailed: "Couldn't load your lists.",
  retry: 'Try again',
  filterByTag: 'Filter by tag',
  allLists: 'All lists',
  countOne: 'On %s list',
  countMany: 'On %s lists',
  add: 'Add',
  remove: 'Remove',
  addTo: 'Add item to %s',
  removeFrom: 'Remove item from %s',
  emptyTag: 'No lists with this tag.',
  emptyLists: "You haven't created any lists yet.",
  toggleFailed: "Couldn't update this list. Please try again.",
};

const trackLabels = {
  title: 'Tracking',
  close: 'Close',
  loading: 'Loading',
  loadFailed: "Couldn't load this form.",
  retry: 'Try again',
  save: 'Save',
  saving: 'Saving',
  delete: 'Delete',
  required: '%s is required.',
  invalidNumber: 'Enter a number.',
  minimum: 'Must be at least %s.',
  maximum: 'Must be at most %s.',
  dateOrder: 'End date must be on or after the start date.',
  dateTime: {},
};

const formPalette = {
  ...mediaCardPalette,
  textSecondary: mediaCardPalette.textSecondary,
  surfaceStrong: palette.surfaceStrong,
  surfaceMuted: palette.surfaceMuted,
  accent: palette.accentStrong,
  accentContrast: mediaCardPalette.chipText,
  danger: palette.danger,
  radius: 8,
  focusStyle: {},
  dateTime: mediaCardPalette,
};

function updateTab(tabs, id, updater) {
  return tabs.map(tab => (tab.id === id ? updater(tab) : tab));
}

function ListsAction({ action, onClose }) {
  const transportReady = hasMediaCardTransport();
  const identity = action?.media || null;
  const [model, setModel] = useState(null);
  const [status, setStatus] = useState('loading');
  const [membership, setMembership] = useState({});
  const [tag, setTag] = useState('');
  const [pending, setPending] = useState({});
  const [errors, setErrors] = useState({});
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!transportReady || !identity) return undefined;
    let alive = true;
    setStatus('loading');
    setErrors({});
    loadLists(identity, action.mediaTitle || identity.title || '')
      .then(data => {
        if (!alive) return;
        const next = normalizeListsModel(data);
        setModel(next);
        setMembership(listsMembership(next));
        setStatus('ready');
      })
      .catch(() => { if (alive) setStatus('error'); });
    return () => { alive = false; };
  }, [attempt, transportReady, identity?.mediaType, identity?.source, identity?.mediaId, action?.mediaTitle]);

  async function toggle(list) {
    if (pending[list.id]) return;
    setPending(current => ({ ...current, [list.id]: true }));
    setErrors(current => ({ ...current, [list.id]: '' }));
    try {
      const has = Boolean(membership[list.id]);
      const data = await toggleList(identity, list.id, has);
      setMembership(current => applyListToggle(current, list.id, data.hasItem));
    } catch (error) {
      setErrors(current => ({ ...current, [list.id]: error.message || listLabels.toggleFailed }));
    } finally {
      setPending(current => ({ ...current, [list.id]: false }));
    }
  }

  if (!transportReady || !identity) {
    return <SignInPrompt />;
  }

  return <ListsForm
    model={model}
    membership={membership}
    tag={tag}
    status={status}
    pending={pending}
    errors={errors}
    labels={listLabels}
    palette={formPalette}
    showHeader={false}
    onTagChange={setTag}
    onRetry={() => setAttempt(current => current + 1)}
    onClose={onClose}
    onToggle={toggle} />;
}

function TrackAction({ action, onClose }) {
  const transportReady = hasMediaCardTransport();
  const identity = action?.media || null;
  const [model, setModel] = useState(null);
  const [values, setValues] = useState({});
  const [tabs, setTabs] = useState([]);
  const [tracked, setTracked] = useState(false);
  const [status, setStatus] = useState('loading');
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState('');
  const [errors, setErrors] = useState({});
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!transportReady || !identity) return undefined;
    let alive = true;
    setStatus('loading');
    setServerError('');
    loadTrack(identity, { trackTime: true })
      .then(data => {
        if (!alive) return;
        const next = normalizeTrackForm(data.model);
        setModel(next);
        setValues(initialTrackValues(next));
        setTabs(data.tabs || []);
        setTracked(Boolean(data.tracked));
        setStatus('ready');
      })
      .catch(error => {
        if (!alive) return;
        setServerError(error.message || '');
        setStatus('error');
      });
    return () => { alive = false; };
  }, [attempt, transportReady, identity?.mediaType, identity?.source, identity?.mediaId,
    identity?.seasonNumber, identity?.episodeNumber, identity?.albumId, identity?.trackId]);

  function change(name, value) {
    setValues(current => ({ ...current, [name]: value }));
    setErrors(current => ({ ...current, [name]: '' }));
  }

  async function submit(nextValues = values) {
    setBusy(true);
    setServerError('');
    try {
      await saveTrack(identity, model, nextValues, tracked);
      const refreshed = await loadTrack(identity, { trackTime: true });
      setTracked(Boolean(refreshed.tracked));
      setTabs(refreshed.tabs || []);
      emitMediaCardChange({
        identity,
        source: 'track',
        tracked: true,
        ...(nextValues.status !== undefined ? { status: nextValues.status } : {}),
        ...(nextValues.score !== undefined ? { score: nextValues.score === '' ? null : Number(nextValues.score) } : {}),
      });
      onClose?.();
    } catch (error) {
      setServerError(error.message || '');
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    setServerError('');
    try {
      await deleteTrack(identity, model);
      emitMediaCardChange({ identity, source: 'track', tracked: false, status: null });
      onClose?.();
    } catch (error) {
      setServerError(error.message || '');
    } finally {
      setBusy(false);
    }
  }

  async function retryTabs() {
    if (!transportReady) return;
    setTabs(current => current.map(tab => ({ ...tab, status: 'loading', error: '' })));
    setTabs(await loadTrackTabs(identity));
  }

  async function toggleTag(tag) {
    const tagTab = tabs.find(tab => tab.id === 'tags');
    const applied = (tagTab?.tags || []).filter(entry => entry.hasTag).map(entry => entry.id);
    const ids = tag.hasTag ? applied.filter(id => id !== tag.id) : [...applied, tag.id];
    setTabs(current => updateTab(current, 'tags', tab => ({
      ...tab,
      tags: tab.tags.map(entry => (entry.id === tag.id ? { ...entry, pending: true, error: '' } : entry)),
    })));
    try {
      await setTags(identity, ids);
      setTabs(current => updateTab(current, 'tags', tab => ({
        ...tab,
        tags: tab.tags.map(entry => (entry.id === tag.id ? { ...entry, hasTag: !tag.hasTag, pending: false } : entry)),
      })));
    } catch (error) {
      setTabs(current => updateTab(current, 'tags', tab => ({
        ...tab,
        tags: tab.tags.map(entry => (entry.id === tag.id ? { ...entry, pending: false, error: error.message } : entry)),
      })));
    }
  }

  async function addTag(name) {
    const created = await createTag(name);
    const tagTab = tabs.find(tab => tab.id === 'tags');
    const applied = (tagTab?.tags || []).filter(entry => entry.hasTag).map(entry => entry.id);
    await setTags(identity, [...applied, created.id]);
    setTabs(current => updateTab(current, 'tags', tab => ({
      ...tab,
      tags: [...(tab.tags || []), { id: created.id, name: created.name, hasTag: true, pending: false, error: '' }],
    })));
  }

  async function saveArtworkUrl(imageUrl) {
    if (!imageUrl) return;
    await saveArtwork(identity, imageUrl);
    setTabs(current => updateTab(current, 'metadata', tab => ({ ...tab, imageUrl })));
  }

  async function toggleDiscover(hidden, itemId) {
    await setDiscoverHidden(itemId, hidden);
    setTabs(current => updateTab(current, 'discover', tab => ({ ...tab, hidden })));
    emitMediaCardChange({
      identity,
      source: 'discover',
      action: hidden ? 'hide' : 'unhide',
      hidden,
    });
  }

  if (!transportReady || !identity) {
    return <SignInPrompt />;
  }

  return <TrackDialog
    open
    embedded
    model={model}
    values={values}
    errors={errors}
    status={status}
    serverError={serverError}
    busy={busy}
    labels={trackLabels}
    palette={formPalette}
    showHeader={false}
    tabs={tabs}
    onChange={change}
    onRetry={() => setAttempt(current => current + 1)}
    onRetryTabs={retryTabs}
    onDelete={tracked ? remove : undefined}
    onClose={onClose}
    onSubmit={submit}
    onTagToggle={toggleTag}
    onTagCreate={addTag}
    onArtworkSave={saveArtworkUrl}
    onDiscoverToggle={toggleDiscover} />;
}

function DiscoverAction({ action, onClose }) {
  const transportReady = hasMediaCardTransport();
  const vals = action?.vals || {};
  const [attempt, setAttempt] = useState(0);
  const [failed, setFailed] = useState('');
  const planning = vals.action === 'planning';

  useEffect(() => {
    if (!transportReady) return undefined;
    let alive = true;
    setFailed('');
    runDiscoverAction(vals)
      .then(() => {
        if (!alive) return;
        emitMediaCardChange({
          identity: {
            mediaType: vals.candidate_media_type,
            source: vals.source,
            mediaId: vals.media_id,
          },
          source: 'discover',
          action: planning ? 'planning' : 'dismiss',
          ...(planning ? { status: 'Planning', tracked: true } : { hidden: true, tracked: false }),
        });
        onClose?.();
      })
      .catch(error => { if (alive) setFailed(error.message || ''); });
    return () => { alive = false; };
  }, [attempt, transportReady, planning, action]);

  if (!transportReady) {
    return <SignInPrompt />;
  }
  if (failed) {
    return <View style={styles.hostStack}>
      <Text accessibilityRole="alert" style={styles.hostError}>{failed}</Text>
      <Button label="Try again" tone="surface" palette={formPalette}
        onPress={() => setAttempt(current => current + 1)} />
    </View>;
  }
  return <Text accessibilityRole="status" accessibilityLiveRegion="polite" style={styles.hostBody}>
    {planning ? `Adding "${vals.title || action.title}" to Planning…` : `Hiding "${vals.title || action.title}" from Discover…`}
  </Text>;
}

function ActionBody({ action, onClose }) {
  if (!action) return null;
  if (action.modal === 'lists') return <ListsAction action={action} onClose={onClose} />;
  if (action.modal === 'track') return <TrackAction action={action} onClose={onClose} />;
  if (action.kind === 'post') return <DiscoverAction action={action} onClose={onClose} />;
  return null;
}

function nearestCardIndex(order, layouts, key, dx, dy, fallback) {
  const start = layouts[key];
  if (!start) return fallback;
  const x = start.x + start.width / 2 + dx;
  const y = start.y + start.height / 2 + dy;
  let best = fallback;
  let bestDistance = Infinity;
  order.forEach((candidate, index) => {
    const layout = layouts[candidate];
    if (!layout) return;
    const distance = (layout.x + layout.width / 2 - x) ** 2
      + (layout.y + layout.height / 2 - y) ** 2;
    if (distance < bestDistance) {
      bestDistance = distance;
      best = index;
    }
  });
  return best;
}

async function persistListOrder(ordering, orderedKeys, targetIndex, fromIndex) {
  const itemIds = orderedKeys.map(key => ordering.itemIds?.[key]);
  if (ordering.itemIds && itemIds.every(Boolean)) {
    await reorderListItems(ordering, itemIds);
    return;
  }
  if (targetIndex === 0) {
    await reorderListItem(ordering, 'first');
    return;
  }
  if (targetIndex === orderedKeys.length - 1) {
    await reorderListItem(ordering, 'last');
    return;
  }
  const step = targetIndex < fromIndex ? 'back' : 'next';
  for (let index = 0; index < Math.abs(targetIndex - fromIndex); index += 1) {
    await reorderListItem(ordering, step);
  }
}

function moveByStep(order, key, step) {
  const index = order.indexOf(key);
  if (index < 0) return order;
  const target = step === 'first' ? 0
    : step === 'last' ? order.length - 1
      : step === 'back' ? Math.max(0, index - 1)
        : step === 'next' ? Math.min(order.length - 1, index + 1) : index;
  if (target === index) return order;
  const next = [...order];
  next.splice(index, 1);
  next.splice(target, 0, key);
  return next;
}

function applyMediaCardChange(model, change) {
  if (!change) return model;
  const next = { ...model };
  if (Object.prototype.hasOwnProperty.call(change, 'status')) {
    next.statusChip = change.status
      ? { kind: 'status', status: change.status, hideOnMobile: false }
      : { kind: 'no-status', hideOnMobile: false };
  }
  if (Object.prototype.hasOwnProperty.call(change, 'score') && next.rating) {
    next.rating = {
      ...next.rating,
      value: change.score,
      label: change.score == null ? null : String(change.score),
    };
  }
  return next;
}

function renderRatingStars({ value: score, label, scaleMax, palette: colors, onSelect }) {
  return <RatingStars scaleMax={scaleMax} value={score} label={label} onSelect={onSelect} palette={colors}
    renderStar={lit => <Text style={[styles.pickerStar, { color: lit ? colors.lit : colors.unlit }]}>★</Text>} />;
}

// One grid card. Memoized, with callbacks bound to its item through stable
// handlers, so appending a page or toggling another card's selection skips it.
const SlotCard = React.memo(function SlotCard({
  item, model, cardWidth, readOnly, selectionMode, selected, dragging, dropTarget, handlers,
}) {
  const selection = useMemo(() => (selectionMode ? {
    active: true,
    selected,
    label: `Select ${model.title}`,
    toggle: () => handlers.toggle(item),
  } : undefined), [selectionMode, selected, model.title, handlers, item]);
  const onOpen = useCallback(() => handlers.open(item), [handlers, item]);
  const onOpenLink = useCallback(href => handlers.openLink(item, href), [handlers, item]);
  const onReorder = useCallback(step => handlers.reorder(item, step), [handlers, item]);
  const onReorderDrag = useCallback(event => handlers.drag(item, event), [handlers, item]);
  const onRate = useCallback(score => handlers.rate(item, score), [handlers, item]);
  const onLayout = useCallback(event => handlers.layout(item, event), [handlers, item]);
  const onAction = useCallback(action => handlers.runAction(action, model.title), [handlers, model.title]);
  const canMenu = Boolean(model.actions?.items?.length && !readOnly && !selectionMode && !dragging);
  const menuActions = useMemo(() => {
    if (!canMenu) return [];
    const canStatus = model.identity && ['movie', 'tv', 'season', 'anime', 'manga', 'book', 'comic', 'comicissue', 'game', 'boardgame'].includes(model.identity.mediaType)
      && model.actions?.items?.some(action => action.modal === 'track');
    const actions = (model.actions?.items || []).map((action, index) => ({
      id: `action-${index}`, title: menuActionLabel(action), attributes: { disabled: Boolean(action.disabled) },
    }));
    if (canStatus) actions.push({ id: 'statuses', title: 'Update status',
      subactions: ['Planning', 'In progress', 'Paused', 'Completed', 'Dropped'].map(status => ({
        id: `status-${status}`, title: status, state: model.statusChip?.status === status ? 'on' : 'off',
      })) });
    return actions;
  }, [canMenu, model]);
  const onPressMenu = useCallback(event => {
    const id = event.nativeEvent.event;
    if (id.startsWith('status-')) handlers.changeStatus({ ...item, model }, id.slice(7));
    else if (id.startsWith('action-')) handlers.runAction(model.actions.items[Number(id.slice(7))], model.title);
  }, [handlers, item, model]);
  const card = <MediaCard model={model} labels={labels} palette={mediaCardPalette}
    width={cardWidth}
    onOpen={handlers.hasOpen() ? onOpen : undefined}
    onOpenLink={handlers.hasLink() ? onOpenLink : undefined}
    onAction={onAction}
    readOnly={readOnly}
    selection={selection}
    onReorder={onReorder}
    onReorderDrag={onReorderDrag}
    dragging={dragging}
    renderActionIcon={renderActionIcon}
    onRate={onRate}
    renderRating={renderRatingStars} />;
  return <View onLayout={onLayout}
    style={[dragging && styles.draggingCard, !dragging && dropTarget && styles.dropTarget]}>
    {/* Native platforms use system menus; Expo web uses the shared dropdown. */}
    {canMenu ? <NativeCardMenu actions={menuActions} palette={{ ...palette, overlay: 'rgba(0, 0, 0, 0.55)' }}
      onPressAction={onPressMenu}>{card}</NativeCardMenu> : card}
  </View>;
});


export default function MediaCardSlot({
  items = [],
  onOpenDetail,
  onOpenLink,
  readOnly = false,
  selectionMode = false,
  selectedKeys = [],
  onToggleSelection,
  gridLayout = 'lists',
  mobileGridLayout = 'compact',
  cardWidth: cardWidthProp,
  gridGap: gridGapProp,
  // Native Library: the grid becomes the page's scrolling list (a FlatList that
  // mounts only rows near the screen), with the page above and below it as
  // `header` and `footer`. Other surfaces keep the plain wrapping grid.
  virtualized = false,
  header = null,
  footer = null,
  onEndReached,
  // The props Screen's ScrollView would have had (style, padding, safe area).
  scrollProps,
}) {
  const { width } = useWindowDimensions();
  const [ratings, setRatings] = useState({});
  const [pendingAction, setPendingAction] = useState(null);
  const [sheetMounted, setSheetMounted] = useState(false);
  const itemKeys = items.map(item => item.key);
  const orderKey = itemKeys.join('|');
  const [order, setOrder] = useState(itemKeys);
  const [changes, setChanges] = useState({});
  const [drag, setDrag] = useState(null);
  const [actionError, setActionError] = useState('');
  const [listWidth, setListWidth] = useState(0);
  const layouts = useRef({});
  const columns = width >= 760 ? 4 : width >= 560 ? 3 : 2;
  // The library page sizes cards like the web grid: three compact columns on a phone.
  const library = gridLayout === 'library' ? libraryGridLayout(width, mobileGridLayout) : null;
  const cardWidth = cardWidthProp || (library
    ? library.width
    : Math.min(180, Math.max(120, (width - 32 - (columns - 1) * spacing.md) / columns)));
  const gridGap = gridGapProp ?? (library ? library.gap : spacing.md);

  useEffect(() => {
    setOrder(itemKeys);
    // Reset whenever the server row set changes; orderKey is the identity.
  }, [orderKey]);

  useEffect(() => subscribeMediaCardChange(change => {
    const keys = [];
    if (change.identity) keys.push(identityKey(change.identity));
    if (Array.isArray(change.identities)) keys.push(...change.identities.map(identityKey));
    if (!keys.length) return;
    setChanges(current => {
      const next = { ...current };
      keys.forEach(key => { next[key] = { ...(next[key] || {}), ...change }; });
      return next;
    });
  }), []);

  async function rate(item, score) {
    setRatings(current => ({ ...current, [item.key]: score }));
    emitMediaCardChange({ identity: item.model.identity, source: 'rating', score });
    if (!hasMediaCardTransport()) return;
    try {
      await saveScore(item.model.identity, score);
    } catch (error) {
      setRatings(current => {
        const next = { ...current };
        delete next[item.key];
        return next;
      });
      setActionError(error.message || 'Could not save this rating.');
      AccessibilityInfo.announceForAccessibility?.(error.message || 'Could not save this rating.');
    }
  }

  function announce(message) {
    AccessibilityInfo.announceForAccessibility?.(message);
  }

  function runAction(action, mediaTitle) {
    if (!action) return;
    if (action.kind === 'link') {
      setPendingAction(null);
      (onOpenLink || onOpenDetail)?.(action.href);
      return;
    }
    if (['track', 'lists'].includes(action.modal)) setSheetMounted(true);
    setPendingAction({ ...action, mediaTitle });
  }

  async function changeStatus(item, status) {
    setActionError('');
    try {
      const identity = item.model.identity;
      const tracking = await loadTrack(identity);
      if (!tracking.tracked && identity.source === 'manual') throw new Error('Use the tracking dialog to add this manual item.');
      await saveTrack(identity, tracking.model, { status }, tracking.tracked);
      emitMediaCardChange({ identity, source: 'track', tracked: true, status });
      announce(`Status changed to ${status}.`);
    } catch (error) {
      const message = error.message || 'Could not change status.';
      setActionError(message);
      announce(message);
    }
  }

  async function reorder(item, step) {
    const next = moveByStep(order, item.key, step);
    if (next === order) return;
    const previous = order;
    setOrder(next);
    setActionError('');
    const ordering = item.model.actions?.ordering;
    if (!hasMediaCardTransport() || !ordering) return;
    try {
      await reorderListItem(ordering, step);
      announce(`${item.model.title} moved ${step}.`);
    } catch (error) {
      setOrder(previous);
      setActionError(error.message || 'Could not reorder this item.');
      announce(error.message || 'Could not reorder this item.');
    }
  }

  async function moveTo(item, targetIndex) {
    const fromIndex = order.indexOf(item.key);
    if (fromIndex < 0 || targetIndex < 0 || targetIndex >= order.length
      || targetIndex === fromIndex) return;
    const next = [...order];
    next.splice(fromIndex, 1);
    next.splice(targetIndex, 0, item.key);
    const previous = order;
    setOrder(next);
    setActionError('');
    const ordering = item.model.actions?.ordering;
    if (!hasMediaCardTransport() || !ordering) return;
    try {
      await persistListOrder(ordering, next, targetIndex, fromIndex);
      announce(`${item.model.title} moved to position ${targetIndex + 1} of ${order.length}.`);
    } catch (error) {
      setOrder(previous);
      setActionError(error.message || 'Could not reorder this item.');
      announce(error.message || 'Could not reorder this item.');
    }
  }

  function handleReorderDrag(item, event) {
    if (!item.model.actions?.ordering) return;
    const index = order.indexOf(item.key);
    if (event.phase === 'start') {
      setDrag({ key: item.key, fromIndex: index, dx: 0, dy: 0, targetIndex: index });
      setActionError('');
      return;
    }
    if (event.phase === 'cancel') {
      setDrag(null);
      return;
    }
    const targetIndex = nearestCardIndex(order, layouts.current, item.key,
      event.dx, event.dy, index);
    setDrag(current => (current && current.key === item.key
      ? { ...current, dx: event.dx, dy: event.dy, targetIndex }
      : current));
    if (event.phase === 'end') {
      setDrag(null);
      moveTo(item, targetIndex);
    }
  }

  // Card callbacks go through a ref to this render's functions, so the
  // handlers object never changes and memoized cards skip unrelated renders.
  const latest = useRef(null);
  latest.current = { onOpenDetail, onOpenLink, onToggleSelection, runAction, changeStatus, reorder, handleReorderDrag, rate };
  const handlers = useMemo(() => ({
    hasOpen: () => !!latest.current.onOpenDetail,
    hasLink: () => !!(latest.current.onOpenLink || latest.current.onOpenDetail),
    open: item => latest.current.onOpenDetail?.(item.row ?? item.model),
    openLink: (item, href) => (latest.current.onOpenLink
      ? latest.current.onOpenLink(href)
      : latest.current.onOpenDetail?.(item.row ?? item.model)),
    runAction: (action, mediaTitle) => latest.current.runAction(action, mediaTitle),
    changeStatus: (item, status) => latest.current.changeStatus(item, status),
    toggle: item => latest.current.onToggleSelection?.(item.key),
    reorder: (item, step) => latest.current.reorder(item, step),
    drag: (item, event) => latest.current.handleReorderDrag(item, event),
    rate: (item, score) => latest.current.rate(item, score),
    layout: (item, event) => { layouts.current[item.key] = event.nativeEvent.layout; },
  }), []);

  const itemsByKey = useMemo(() => new Map(items.map(item => [item.key, item])), [items]);
  const orderedItems = order
    .map(key => itemsByKey.get(key))
    .filter(Boolean);

  function cardModel(item, index) {
    const value = ratings[item.key];
    let model = item.model;
    if (value !== undefined) {
      model = {
        ...model,
        rating: model.rating && {
          ...model.rating,
          value,
          label: value == null ? null : String(value),
        },
      };
    }
    model = applyMediaCardChange(model, changes[identityKey(item.model.identity)]);
    if (model.actions?.ordering) {
      model = {
        ...model,
        actions: {
          ...model.actions,
          ordering: {
            ...model.actions.ordering,
            position: index + 1,
            count: orderedItems.length,
          },
        },
      };
    }
    return model;
  }

  function renderCard(item, index) {
    return <SlotCard key={item.key} item={item} model={cardModel(item, index)}
      cardWidth={cardWidth} readOnly={readOnly} selectionMode={selectionMode}
      selected={selectionMode && selectedKeys.includes(item.key)}
      dragging={drag?.key === item.key}
      dropTarget={!!drag && drag.key !== item.key && drag.targetIndex === index}
      handlers={handlers} />;
  }

  const errorText = actionError ? <Text role="status" accessibilityRole="alert" accessibilityLiveRegion="polite"
    style={styles.actionError}>{actionError}</Text> : null;
  const host = <>
    {sheetMounted && <AdaptivePresentationSheet isPresented={['track', 'lists'].includes(pendingAction?.modal)}
      onDismiss={() => setPendingAction(null)} onClose={() => setPendingAction(null)}
      title={pendingAction?.modal === 'track' ? 'Tracking' : 'Lists'} titleColor={formPalette.text}
      containerColor={palette.sidebar}>
      {pendingAction?.modal === 'track' ? <View style={{ flex: 1, paddingHorizontal: 16 }}>
        <TrackAction key={pendingAction?.target || 'track'} action={pendingAction} onClose={() => setPendingAction(null)} />
      </View> : pendingAction?.modal === 'lists' ? <ScrollView keyboardShouldPersistTaps="handled"
        contentContainerStyle={{ paddingHorizontal: 16, paddingBottom: 24 }}>
        <ListsAction key={pendingAction?.target || 'lists'} action={pendingAction} onClose={() => setPendingAction(null)} />
      </ScrollView> : null}
    </AdaptivePresentationSheet>}
    <MediaCardActionHost action={pendingAction} open={!!pendingAction && !['track', 'lists'].includes(pendingAction.modal)} labels={labels}
      palette={mediaCardPalette} onClose={() => setPendingAction(null)}>
      {pendingAction && !['track', 'lists'].includes(pendingAction.modal) ? <ActionBody
        key={pendingAction.target || pendingAction.title || 'closed'} action={pendingAction}
        onClose={() => setPendingAction(null)} /> : null}
    </MediaCardActionHost>
  </>;

  if (virtualized) {
    // Native-only windowing: hundreds of mounted cards in a ScrollView is the
    // known expensive case on phones, so only rows near the screen stay
    // mounted. Web keeps its server-paged document scroll (find-in-page and
    // anchors keep working, and its images already load lazily).
    // The column count matches the wrapping grid at this width, so the layout
    // is unchanged.
    const content = StyleSheet.flatten(scrollProps?.contentContainerStyle) || {};
    const side = key => content[key] ?? content.paddingHorizontal ?? content.padding ?? 0;
    const available = (listWidth || width) - side('paddingLeft') - side('paddingRight');
    const columns = Math.max(1, Math.floor((available + gridGap + 0.5) / (cardWidth + gridGap)));
    return <>
      <FlatList
        key={`columns-${columns}`}
        data={orderedItems}
        keyExtractor={item => item.key}
        numColumns={columns}
        renderItem={({ item, index }) => renderCard(item, index)}
        columnWrapperStyle={columns > 1 ? { gap: gridGap } : undefined}
        ItemSeparatorComponent={() => <View style={{ height: gridGap }} />}
        ListHeaderComponent={header}
        ListFooterComponent={<>{errorText}{footer}</>}
        {...scrollProps}
        style={[styles.list, scrollProps?.style]}
        contentContainerStyle={[scrollProps?.contentContainerStyle, styles.listContent]}
        onLayout={event => setListWidth(event.nativeEvent.layout.width)}
        onEndReached={onEndReached}
        onEndReachedThreshold={0.6}
        initialNumToRender={columns * 5}
        maxToRenderPerBatch={columns * 4}
        windowSize={7}
        removeClippedSubviews={Platform.OS === 'android'}
        />
      {host}
    </>;
  }

  return <>
    <View style={[styles.grid, { gap: gridGap }]}>
      {orderedItems.map((item, index) => renderCard(item, index))}
    </View>
    {errorText}
    {host}
  </>;
}

const styles = StyleSheet.create({
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.md },
  list: { flex: 1 },
  // Rows are spaced by the grid gap, not the page's section gap.
  listContent: { gap: 0, rowGap: 0 },
  draggingCard: { zIndex: 20, elevation: 8, opacity: 0.9 },
  dropTarget: { opacity: 0.72 },
  pickerStar: { fontSize: 20 },
  actionGlyph: { color: mediaCardPalette.chipText, fontSize: 15, fontWeight: '700' },
  hostBody: { color: mediaCardPalette.textMuted, fontSize: 13, lineHeight: 19 },
  hostStack: { gap: spacing.sm },
  hostError: { color: palette.danger, fontSize: 12, lineHeight: 17 },
  actionError: { color: palette.danger, fontSize: 12, lineHeight: 17 },
  signInTitle: { color: mediaCardPalette.text, fontSize: 15, fontWeight: '700' },
});
