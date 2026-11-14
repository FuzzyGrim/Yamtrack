import React, { useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { Image, Linking, useWindowDimensions } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { DetailView } from '../../../frontend/pages/DetailView';
import { shelfCardWidth } from '../../../frontend/ui/HomeShelves';
import WindowedRail from '../surfaces/WindowedRail';
import { useMobileGridLayout } from '../data/useMobileGridLayout';
import { useDevicePose } from '../data/useDevicePose';
import { Screen } from '../components/Screen';
import { createDetailModelForItem, loadDetailModel } from '../surfaces/DetailSlot';
import { identityFromDetailItem } from '../surfaces/detailIdentity.mjs';
import { useDetailFeatures } from '../surfaces/DetailFeatures';
import { setShellPageType, ShellLayoutContext } from '../surfaces/shellContext';
import { horizontalContentPadding } from '../safeAreaSpacing.mjs';
import { getActiveAuthSession } from '../data/authSession';
import { formatUserDate } from '../data/userDate';
import { prepareDetailArtwork } from '../data/prepareDetailArtwork.mjs';
import { palette } from '../theme';

export default function DetailScreen({ params, onBack, navigate }) {
  const viewportWindow = useWindowDimensions();
  const mobileGridLayout = useMobileGridLayout();
  // The device pose (closed, open or partial; vertical or horizontal), for laying Media Details out per pose.
  const pose = useDevicePose();
  const insets = useSafeAreaInsets();
  const shellLayout = useContext(ShellLayoutContext);
  const heroViewport = { width: shellLayout?.width || viewportWindow.width - insets.left - insets.right,
    height: shellLayout?.height || viewportWindow.height - insets.top - insets.bottom,
    headerHeight: shellLayout?.headerHeight || 0, insets, pose,
    gutters: horizontalContentPadding(insets, 22) };
  const item = useMemo(
    () => params?.item || (params?.href ? { href: params.href } : null),
    [params?.item, params?.href],
  );
  const pendingModel = useMemo(() => {
    const identity = identityFromDetailItem(item);
    if (!identity?.mediaType || !identity.source || !identity.mediaId) return null;
    return { ...createDetailModelForItem(item),
      identity: { ...identity, mediaType: identity.detailType || identity.mediaType,
        ...(identity.detailType && identity.mediaType === 'anime' ? { libraryMediaType: 'anime' } : {}) },
      publicView: Boolean(identity.publicView || params?.publicView) };
  }, [item, params?.publicView]);
  const [model, setModel] = useState(() => createDetailModelForItem(item));
  const [loading, setLoading] = useState(Boolean(item));
  const [dataReady, setDataReady] = useState(false);
  const [error, setError] = useState('');
  const [loadFailed, setLoadFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [actionBusy, setActionBusy] = useState(false);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    if (!item) {
      setLoading(false);
      setDataReady(false);
      setModel(createDetailModelForItem(null));
      return undefined;
    }
    let cancelled = false;
    setLoading(true);
    setDataReady(false);
    setLoadFailed(false);
    setError('');
    let earlyArtwork;
    loadDetailModel(item, {
      publicView: params?.publicView,
      onGallery: gallery => {
        if (cancelled) return;
        const backdrop = gallery?.hero?.url || gallery?.overview?.url || '';
        const logo = gallery?.overview?.logo_url || gallery?.hero?.logo_url || '';
        if (backdrop || logo) {
          earlyArtwork = { backdrop, logo, prepared: prepareDetailArtwork({ artwork: { backdrop, logo } }, Image) };
        }
      },
    }).then(async next => {
      if (cancelled) return;
      // Start the header requests while artwork is being prepared.
      setModel(next);
      setDataReady(true);
      const matchesEarly = earlyArtwork && earlyArtwork.backdrop === next.artwork?.backdrop
        && earlyArtwork.logo === (next.artwork?.logo || '');
      const earlyResult = matchesEarly ? await earlyArtwork.prepared : null;
      const prepared = earlyResult
        ? { ...next, artwork: { ...next.artwork, ...earlyResult.artwork } }
        : await prepareDetailArtwork(next, Image);
      if (cancelled) return;
      setModel(prepared);
      setLoading(false);
    }).catch(cause => {
      if (cancelled) return;
      setModel(createDetailModelForItem(item));
      setError(cause?.message || 'The detail could not be loaded from the server.');
      setLoadFailed(true);
      setLoading(false);
    });
    return () => { cancelled = true; };
  }, [item, attempt, params?.publicView]);

  // A write from one of the page's own controls reloads the page payload in place.
  const refresh = useCallback(() => {
    if (!item) return;
    loadDetailModel(item, { publicView: params?.publicView })
      .then(next => { setModel(next); setVersion(value => value + 1); })
      .catch(cause => setError(cause?.message || 'The detail could not be loaded from the server.'));
  }, [item, params?.publicView]);

  const wrapAction = action => ({
    ...action,
    onPress: async () => {
      if (actionBusy) return;
      setActionBusy(true);
      setError('');
      try {
        await action.onPress?.();
        const next = await loadDetailModel(item, { publicView: params?.publicView });
        setModel(next);
      } catch (cause) {
        setError(cause?.message || 'The action could not be completed.');
      } finally {
        setActionBusy(false);
      }
    },
  });
  const actionableModel = model?.actions ? {
    ...model,
    actions: {
      ...model.actions,
      primary: model.actions.primary ? wrapAction(model.actions.primary) : null,
      items: (model.actions.items || []).map(wrapAction),
    },
  } : model;

  async function loadMoreEpisodes() {
    const page = model?.children?.nextPage;
    if (!page) return;
    setActionBusy(true);
    setError('');
    try {
      const next = await loadDetailModel(item, { publicView: params?.publicView, episodePage: page });
      setModel(current => ({
        ...next,
        children: {
          ...next.children,
          episodes: [...(current?.children?.episodes || []), ...(next.children?.episodes || [])],
        },
      }));
    } catch (cause) {
      setError(cause?.message || 'The next episodes could not be loaded.');
    } finally {
      setActionBusy(false);
    }
  }

  function openDetail(identity) {
    const target = identity?.identity || identity;
    if (target?.href) navigate?.('Detail', { href: target.href });
    else if (target) navigate?.('Detail', { item: { identity: target } });
  }

  function openExternal(url) {
    if (!url) return;
    const serverUrl = getActiveAuthSession()?.serverUrl;
    const target = String(url).startsWith('/') && serverUrl
      ? new URL(url, serverUrl).toString()
      : url;
    Linking.openURL(target).catch(() => {});
  }

  function openVideo(video) {
    const key = video?.key || video?.id;
    if (key) openExternal(`https://www.youtube.com/watch?v=${encodeURIComponent(key)}`);
  }

  async function rateRelatedItem(card, score) {
    const identity = card?.identity;
    const request = getActiveAuthSession()?.request;
    if (!identity?.mediaType || !identity?.source || !identity?.mediaId || !request) return;
    await request(`/api/v1/media/${encodeURIComponent(identity.mediaType)}/${encodeURIComponent(identity.source)}/${encodeURIComponent(identity.mediaId)}/`, {
      method: 'PATCH',
      json: { score },
    });
    const next = await loadDetailModel(item, { publicView: params?.publicView });
    setModel(next);
  }

  async function runChildAction(key, child) {
    const identity = child?.identity;
    const request = getActiveAuthSession()?.request;
    if (!identity?.mediaId || !identity?.source || identity.seasonNumber == null
      || identity.episodeNumber == null || !request) return;
    const path = `/api/v1/media/tv/${encodeURIComponent(identity.source)}/${encodeURIComponent(identity.mediaId)}/${encodeURIComponent(identity.seasonNumber)}/episodes/${encodeURIComponent(identity.episodeNumber)}/watch/`;
    setActionBusy(true);
    setError('');
    try {
      await request(path, { method: key === 'unwatch' ? 'DELETE' : 'POST' });
      setModel(await loadDetailModel(item, { publicView: params?.publicView }));
    } catch (cause) {
      setError(cause?.message || 'The episode action could not be completed.');
    } finally {
      setActionBusy(false);
    }
  }

  async function rateChild(child, score) {
    const identity = child?.identity;
    const request = getActiveAuthSession()?.request;
    if (!identity?.mediaId || !identity?.source || identity.seasonNumber == null
      || identity.episodeNumber == null || !request) return;
    setActionBusy(true);
    setError('');
    try {
      await request(`/api/v1/media/tv/${encodeURIComponent(identity.source)}/${encodeURIComponent(identity.mediaId)}/${encodeURIComponent(identity.seasonNumber)}/episodes/${encodeURIComponent(identity.episodeNumber)}/score/`, {
        method: 'PATCH', json: { score: score == null ? null : score * 10 / (child.scaleMax || 10) },
      });
      setModel(await loadDetailModel(item, { publicView: params?.publicView }));
    } catch (cause) {
      setError(cause?.message || 'The episode rating could not be saved.');
    } finally {
      setActionBusy(false);
    }
  }

  async function rateMain(score) {
    const request = getActiveAuthSession()?.request;
    const identity = model?.identity;
    if (!identity?.media_type || !identity?.source || !identity?.media_id || !request) return;
    const parentType = identity.parent_media_type || identity.media_type;
    const base = `/api/v1/media/${encodeURIComponent(parentType)}/${encodeURIComponent(identity.source)}/${encodeURIComponent(identity.media_id)}/`;
    const path = identity.media_type === 'episode'
      ? `${base}${encodeURIComponent(identity.season_number)}/episodes/${encodeURIComponent(identity.episode_number)}/score/`
      : identity.media_type === 'season' ? `${base}${encodeURIComponent(identity.season_number)}/` : base;
    setActionBusy(true);
    setError('');
    try {
      await request(path, {
        method: 'PATCH', json: { score: score == null ? null : Number(score) * 10 / (model.viewerRating?.scaleMax || 10) },
      });
      setModel(await loadDetailModel(item, { publicView: params?.publicView }));
    } catch (cause) {
      setError(cause?.message || 'The rating could not be saved.');
    } finally {
      setActionBusy(false);
    }
  }

  const features = useDetailFeatures({
    // Native starts read-only header requests from the navigation identity;
    // web receives the same information in its server-rendered response.
    model: dataReady ? model : (pendingModel || model),
    ready: Boolean(item) && !loadFailed,
    dataReady,
    version,
    refresh,
    navigate,
    onOpenItem: openDetail,
    onOpenExternal: openExternal,
    shelfWidth: shelfCardWidth(viewportWindow.width, mobileGridLayout),
  });

  // The header search follows the page like the web header: a film page searches Movies.
  const pageType = features.enabled ? features.pageType : null;
  useEffect(() => {
    setShellPageType(pageType);
    return () => setShellPageType(null);
  }, [pageType]);

  const children = actionableModel?.children;
  const hasChildren = Boolean(children?.seasons?.length || children?.episodes?.length || children?.issues?.length);
  const viewModel = {
    ...actionableModel,
    // A film has no seasons, episodes or issues: the web page shows no such section.
    children: hasChildren && !features.replacesChildren ? {
      ...children,
      // A season page switches season from its title, like the web; it does not list them again.
      seasons: model?.identity?.media_type === 'season' ? [] : children.seasons,
      episodes: (children.episodes || []).map(episode => ({
        ...episode, dateLabel: formatUserDate(episode.dateLabel) || episode.dateLabel,
      })),
    } : null,
    // The score row already shows each stored source: keep the viewer's score and the tag chips.
    scoreChips: features.scoreSlot
      ? (actionableModel.scoreChips || []).filter(chip => chip.kind === 'viewer' || chip.text)
      : actionableModel.scoreChips,
    sections: features.enabled
      ? (actionableModel.sections || []).filter(section => !features.replacedSections.includes(section.id))
      : actionableModel.sections,
  };

  function openPerson(person) {
    navigate?.('People', {
      identity: { kind: 'person', source: model?.identity?.source, personId: person.person_id },
    });
  }

  return <Screen onBack={onBack} chrome={false} artworkBleed
    contentStyle={{ paddingTop: heroViewport.headerHeight }}>
    <DetailView
      model={viewModel}
      heroViewport={heroViewport}
      shelfCardWidth={shelfCardWidth(viewportWindow.width, mobileGridLayout)}
      railComponent={WindowedRail}
      labels={model.labels}
      palette={palette}
      loading={loading || (features.enabled && !features.headerReady)}
      error={error}
      hideContent={loadFailed}
      onRetry={item ? () => setAttempt(value => value + 1) : undefined}
      onOpenItem={openDetail}
      onOpenExternal={openExternal}
      onOpenVideo={openVideo}
      onLoadMore={loadMoreEpisodes}
      onChildAction={runChildAction}
      onRate={rateRelatedItem}
      onRateChild={rateChild}
      onRateMain={rateMain}
      onOpenPerson={model?.identity?.source ? openPerson : undefined}
      actionsSlot={features.actionsSlot}
      scoreSlot={features.headerSlot || features.scoreSlot
        ? <>{features.headerSlot}{features.scoreSlot}</> : null}
      sectionSlots={features.sectionSlots}
      childSlots={features.childSlots}
      noticeSlot={features.noticeSlot}
      ratingInScoreRow={features.ratingInScoreRow}
      sectionsSlot={features.sectionsSlot}
      childrenSlot={features.childrenSlot}
    >{features.overlays}</DetailView>
  </Screen>;
}
