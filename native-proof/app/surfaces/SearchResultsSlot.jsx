import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useWindowDimensions } from 'react-native';
import { shelfCardWidth } from '../../../frontend/ui/HomeShelves';
import WindowedRail from './WindowedRail';
import MediaCardSlot from './MediaCardSlot';
import { SearchResults } from '../../../frontend/ui/SearchResults';
import { MediaCard } from '../../../frontend/ui/MediaCard';
import { mergeSearchRowPage, onlineRowParams, searchRowParams } from '../../../frontend/ui/searchRows';
import { getActiveAuthSession, subscribeAuthSession } from '../data/authSession';
import { createSearchTransport } from '../data/searchTransport';
import { musicCreateFromHref, routeCardHref } from '../data/serverCardCore';
import { useMobileGridLayout } from '../data/useMobileGridLayout';
import { palette } from '../theme';

export const surface = {
  id: 'SEARCH-RESULTS',
  status: 'ported',
  title: 'Search results as Home shelves',
  where: 'Search results',
  note: 'Search results are Home\'s shelves: your library first, then one row per provider that has results, drawn by the shared SearchResults with Home\'s rail, card and card width (including the Mobile Grid preference). The rows come from the same server builder as the web page.',
};

// The server's rows are read the way the web page reads them: one request for the rows, and
// one per "load more". The Django search view reads the session; a token sign-in reads the
// same rows from the API.
const resultsPath = session => (session.authType === 'token' ? '/api/v1/search/results/' : '/search/results');

export default function SearchResultsSlot({ query = '', mediaType = 'all', onOpenDetail, navigate }) {
  const { width } = useWindowDimensions();
  const mobileGridLayout = useMobileGridLayout();
  const itemWidth = shelfCardWidth(width, mobileGridLayout);
  const [session, setSession] = useState(() => getActiveAuthSession());
  const [view, setView] = useState({ state: 'loading', props: {} });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => subscribeAuthSession(setSession), []);

  useEffect(() => {
    const trimmed = String(query || '').trim();
    if (!trimmed) {
      setView({ state: 'empty', props: { empty: { title: 'Search your library', body: 'Type something to search your library and your providers.' } } });
      return undefined;
    }
    if (!session || typeof session.request !== 'function') {
      setView({ state: 'empty', props: { empty: { title: 'Sign in to search', body: 'Connect your Floppy server to search your library.' } } });
      return undefined;
    }
    let cancelled = false;
    setView({ state: 'loading', props: {} });
    session.request(resultsPath(session), { query: { q: trimmed, media_type: mediaType } })
      .then(payload => { if (!cancelled) setView({ state: payload.state || 'ready', props: payload }); })
      .catch(() => { if (!cancelled) setView({ state: 'error', props: {} }); });
    return () => { cancelled = true; };
  }, [session, query, mediaType, attempt]);

  const onLoadMore = useCallback(async row => {
    const payload = await session.request(resultsPath(session), {
      query: searchRowParams(row, { query: String(query).trim(), mediaType }),
    });
    return payload.row ? mergeSearchRowPage(row, payload.row) : null;
  }, [session, query, mediaType]);

  const onLoadOnlineRow = useCallback(source => session.request(resultsPath(session), {
    query: onlineRowParams(source, { query: String(query).trim(), mediaType }),
  }), [session, query, mediaType]);

  const transport = useMemo(() => createSearchTransport(session ? { auth: session } : {}), [session]);
  const [creating, setCreating] = useState(false);

  const openCard = useCallback(async item => {
    const card = item?.card;
    // Online music artists and albums are created from the provider id first.
    const create = musicCreateFromHref(card?.href);
    if (create && navigate) {
      setCreating(true);
      try {
        const made = await transport.createMusic(create);
        navigate('Music', { artistId: made.artist_id, ...(made.album_id ? { albumId: made.album_id } : {}) });
      } catch {
        setView({ state: 'error', props: { labels: { errorBody: 'Could not add this entry.' } } });
      } finally {
        setCreating(false);
      }
      return;
    }
    if (!navigate || !routeCardHref(card?.href, navigate)) onOpenDetail?.(card);
  }, [onOpenDetail, navigate, transport]);

  const renderCard = useCallback(item => {
    const card = item.card;
    const identity = card?.quickStatus?.identity;
    // Music and other specialized cards keep their existing detail navigation.
    if (!identity) return <MediaCard model={card} palette={palette} width={itemWidth}
      onOpen={() => openCard(item)} onOpenLink={() => openCard(item)} onAction={() => openCard(item)} />;
    const model = { ...card, identity, actions: card?.actions && {
      ...card.actions, items: card.actions.items.map(action => ({ ...action, media: identity })),
    } };
    return <MediaCardSlot items={[{ key: item.key || card.cardKey || card.href, model }]}
      cardWidth={itemWidth} gridGap={0} onOpenDetail={() => openCard(item)}
      onOpenLink={href => { if (!navigate || !routeCardHref(href, navigate)) openCard(item); }} />;
  }, [itemWidth, navigate, openCard]);

  // The reviewed Expo results use the shared shelf card, rather than a Library grid host.
  // Web keeps its existing card adapter and in-place actions.
  return <SearchResults
    {...view.props}
    state={creating ? 'loading' : view.state}
    palette={palette}
    itemWidth={itemWidth}
    renderCard={renderCard}
    onOpenCard={openCard}
    onOpenLink={(_href, item) => openCard(item)}
    onAction={(_action, item) => openCard(item)}
    railComponent={WindowedRail}
    onLoadMore={onLoadMore}
    onLoadOnlineRow={onLoadOnlineRow}
    onRetry={() => setAttempt(current => current + 1)}
  />;
}
