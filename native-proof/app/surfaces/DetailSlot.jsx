import React from 'react';
import { Text, View } from 'react-native';
import { Badge } from '../../../frontend/ui/Badge';
import { detailSecondaryModel } from '../../../frontend/ui/detailSecondaryModel';
import { palette, spacing } from '../theme';
import { getActiveAuthSession } from '../data/authSession';
import { detailHeaderText, trackingEntriesFromPayload } from '../data/detailTransportCore';
import { formatUserDate, loadDatePreferences } from '../data/userDate';
import { detailEndpoint, detailGalleryEndpoint, identityFromDetailItem } from './detailIdentity.mjs';

export const surface = {
  id: 'DETAIL',
  status: 'partial',
  title: 'Structured detail pages',
  where: 'Film, show, anime, game, book, comic, podcast, video and music detail pages',
  note: 'Expo uses the shared native detail composition with provider-backed data, permission-aware tracking state and media-specific sections. Browser pages remain server-owned. Runtime and accessibility evidence is tracked per platform.',
};

const labels = {
  altTitle: 'Show alternative title',
  publicView: 'Public view',
  readMore: 'Read more',
  showLess: 'Show less',
  noSynopsis: 'No synopsis available.',
};

const detailPalette = {
  background: palette.background,
  surface: palette.surface,
  surfaceStrong: palette.surfaceStrong,
  border: palette.border,
  text: palette.text,
  textSecondary: palette.textSecondary,
  textMuted: palette.textMuted,
  accent: palette.accent,
  accentStrong: palette.accentStrong,
  accentContrast: '#ffffff',
  link: '#818cf8',
  danger: palette.danger,
  placeholder: palette.surfaceStrong,
};

function asList(value) {
  if (Array.isArray(value)) return value.filter(Boolean);
  return value ? [value] : [];
}

function toDisplay(value) {
  if (typeof value === 'string' || typeof value === 'number') return String(value);
  if (value && typeof value === 'object') {
    if (value.value != null && value.label) return `${value.label}: ${value.value}`;
    return value.name || value.title || value.label || value.provider_name || value.person || value.character || '';
  }
  return '';
}

function section(id, title, values) {
  const items = asList(values).map(toDisplay).filter(Boolean);
  if (!items.length) return null;
  return {
    id,
    title,
    body: <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm }}>
      {items.map((label, index) => <Badge key={`${label}-${index}`} label={label} tone="outline"
        palette={{ outline: { color: palette.textSecondary, borderColor: palette.surfaceMuted, borderWidth: 1 } }} />)}
    </View>,
  };
}

function musicCard(record, href, kind) {
  if (!record?.id || !(record.name || record.title)) return null;
  const title = record.name || record.title;
  const key = `${kind}-${record.id}`;
  return {
    key,
    model: {
      cardKey: key,
      mediaType: kind,
      shape: 'square',
      href,
      image: { src: record.image || record.cover || record.artwork || null, fallback: '', source: 'primary' },
      title,
      altTitle: null,
      statusChip: null,
      cornerChips: [],
      titleLinks: [],
      progressBar: null,
      lines: record.release_date ? [{ join: 'plain', parts: [{ text: String(record.release_date).slice(0, 4) }]}] : [],
      rating: null,
      ratingEditable: false,
      actions: null,
      identity: null,
    },
  };
}

function detailFromProviderPayload(payload, item, identity) {
  const publicView = Boolean(identity?.publicView || payload?.public_view || payload?.read_only);
  const sections = (payload?.sections || []).map(value => section(value.id, value.title, value.items)).filter(Boolean);
  const providerItems = Object.values(payload?.providers || {}).flatMap(value => asList(value));
  const providerSection = section('providers', 'Where to watch', providerItems);
  if (providerSection) sections.push(providerSection);
  const formatSection = section('book-formats', 'Formats', payload?.book_formats);
  if (formatSection) sections.push(formatSection);
  const gameLengthItems = [
    ...asList(payload?.game_lengths?.cards),
    ...asList(payload?.game_lengths?.single_player_rows),
  ];
  const gameLengthSection = section('game-lengths', 'Game lengths', gameLengthItems);
  if (gameLengthSection) sections.push(gameLengthSection);
  if (payload?.youtube_watch) {
    const watched = payload.youtube_watch;
    const youtubeSection = section('youtube-watch', 'Watched on YouTube', [
      watched.seconds ? `~${watched.hours_label}` : 'Started',
      `${watched.videos || 0} tagged videos`,
      'Estimated from video progress; does not affect playtime.',
    ]);
    if (youtubeSection) sections.push(youtubeSection);
  }
  const activity = payload?.activity_subtitle;
  const activitySection = section('activity', 'Your activity', [
    activity?.primary_text,
    activity?.date_start ? `Started ${activity.date_start}` : '',
    activity?.date_end ? `Last activity ${activity.date_end}` : '',
    activity?.duration_text,
  ]);
  if (activitySection) sections.push(activitySection);
  const collectionItems = (payload?.collections || []).map(record => [
    record.media_type,
    record.resolution,
    record.hdr,
    record.is_3d ? '3D' : '',
    record.audio_codec,
    record.audio_channels,
    record.bitrate ? `${record.bitrate} kbps` : '',
    record.purchase_location,
  ].filter(Boolean).join(' · ')).filter(Boolean);
  const collectionSection = section('collection', 'Collection', collectionItems);
  if (collectionSection) sections.push(collectionSection);
  const historyItems = (payload?.history || []).map(entry => [
    entry.date ? String(entry.date).slice(0, 10) : '',
    entry.status,
    entry.progress != null ? `Progress ${entry.progress}` : '',
    entry.score != null ? `Score ${entry.score}` : '',
  ].filter(Boolean).join(' · ')).filter(Boolean);
  const historySection = section('history', 'Recent history', historyItems);
  if (historySection) sections.push(historySection);
  if (payload?.tracking?.notes) sections.push({
    id: 'notes',
    title: 'Your notes',
    body: <Text style={{ color: palette.textSecondary }}>{payload.tracking.notes}</Text>,
  });
  const facts = [...(payload?.facts || [])];
  if (payload?.tracking?.status) facts.push({ label: 'Status', value: String(payload.tracking.status) });
  if (payload?.tracking?.score != null) facts.push({ label: 'Your score', value: String(payload.tracking.score) });
  if (payload?.tracking?.progress != null) {
    const max = payload.tracking.max_progress;
    facts.push({ label: 'Progress', value: max ? `${payload.tracking.progress} / ${max}` : String(payload.tracking.progress) });
  }
  const capabilities = payload?.capabilities || {};
  const canTrack = capabilities.can_track;
  const canUpdate = capabilities.can_update;
  const request = getActiveAuthSession()?.request;
  const key = payload?.identity || identity;
  const keyMediaType = key?.mediaType || key?.media_type;
  const keyMediaId = key?.mediaId || key?.media_id;
  const routeMediaType = key?.parent_media_type || keyMediaType;
  const detailParams = new URLSearchParams();
  if (identity.episodePage != null) detailParams.set('episode_page', String(identity.episodePage));
  if (key?.season_number != null || key?.seasonNumber != null) {
    detailParams.set('detail_type', keyMediaType);
    detailParams.set('season_number', String(key.season_number ?? key.seasonNumber));
  }
  if (key?.episode_number != null || key?.episodeNumber != null) {
    detailParams.set('detail_type', keyMediaType);
    detailParams.set('episode_number', String(key.episode_number ?? key.episodeNumber));
  }
  const detailQuery = detailParams.size ? `?${detailParams.toString()}` : '';
  const actionPath = keyMediaType && routeMediaType && key?.source && keyMediaId
    ? `/api/v1/media/${encodeURIComponent(routeMediaType)}/${encodeURIComponent(key.source)}/${encodeURIComponent(keyMediaId)}/${detailQuery}`
    : null;
  const seasonNumber = key?.season_number ?? key?.seasonNumber;
  const episodeNumber = key?.episode_number ?? key?.episodeNumber;
  const episodeWatchPath = keyMediaType === 'episode' && routeMediaType === 'tv'
    ? `/api/v1/media/tv/${encodeURIComponent(key.source)}/${encodeURIComponent(keyMediaId)}/${encodeURIComponent(seasonNumber)}/episodes/${encodeURIComponent(episodeNumber)}/watch/`
    : null;
  const updatePath = keyMediaType === 'season' && seasonNumber != null
    ? `/api/v1/media/${encodeURIComponent(routeMediaType)}/${encodeURIComponent(key.source)}/${encodeURIComponent(keyMediaId)}/${encodeURIComponent(seasonNumber)}/`
    : actionPath;

  return {
    identity: key,
    title: payload?.title || item?.title || '',
    altTitle: payload?.alt_title || '',
    subtitle: payload?.subtitle || '',
    seasonLabel: keyMediaType === 'season' ? (payload?.season_title || `Season ${seasonNumber}`) : '',
    seasonOptions: payload?.season_options || [],
    parentIdentity: keyMediaType === 'season' ? { mediaType: routeMediaType, source: key.source, mediaId: keyMediaId } : null,
    ...detailHeaderText(payload?.activity_subtitle, payload?.media_details),
    publicView,
    readOnly: Boolean(payload?.read_only),
    seerrConfigured: capabilities.can_request_seerr,
    trackingEntries: trackingEntriesFromPayload(payload),
    viewerRating: payload?.tracking && !publicView && payload?.viewer_rating?.enabled !== false ? {
      value: payload.viewer_rating?.value ?? payload.tracking.score,
      scaleMax: payload.viewer_rating?.scale_max || 10,
      editable: Boolean(capabilities.can_update),
    } : null,
    artwork: {
      src: payload?.artwork?.src || item?.image?.src || item?.image || '',
      aspect: payload?.artwork?.aspect || 'poster',
      backdrop: payload?.artwork?.backdrop || '',
      logo: payload?.artwork?.logo || '',
      gallery: payload?.artwork?.gallery || null,
      gallerySupported: Boolean(payload?.artwork?.gallery_supported),
    },
    facts,
    synopsis: { text: payload?.synopsis || '', previewLines: 3 },
    labels,
    palette: detailPalette,
    sections,
    scoreChips: [
      ...(payload?.score_chips || []).map(chip => ({ ...chip, value: chip.value ?? chip.rating ?? chip.score ?? chip.tier })),
      ...(payload?.tag_sections || []).flatMap(group => (group.entries || []).map(entry => ({
        ...entry,
        label: group.title_label || group.title,
        text: entry.label,
      }))),
    ],
    relatedSections: payload?.related_sections || [],
    people: {
      cast: (payload?.people?.cast || []).map(person => ({
        ...(person && typeof person === 'object' ? person : { name: String(person || '') }),
        person_id: person.personId || person.person_id,
        imageUrl: person.imageUrl || person.image || person.image_url || person.profile_path || '',
        name: person.name || person.person || person.actor || person.title || '',
        role: person.role || person.character || person.job || '',
        image: person.image || person.image_url || person.profile_path || '',
      })),
      crew: (payload?.people?.crew || []).map(person => ({
        ...(person && typeof person === 'object' ? person : { name: String(person || '') }),
        person_id: person.personId || person.person_id,
        imageUrl: person.imageUrl || person.image || person.image_url || person.profile_path || '',
        name: person.name || person.person || person.title || '',
        role: person.role || person.job || '',
        image: person.image || person.image_url || person.profile_path || '',
      })),
    },
    children: payload?.children ? {
      kind: payload.children.kind,
      seasons: payload.children.seasons || [],
      episodes: payload.children.kind === 'episode' ? payload.children.episodes || [] : [],
      issues: payload.children.kind === 'issue' ? payload.children.episodes || [] : [],
      nextPage: payload.children.next_page,
      nextLabel: payload.children.next_label,
    } : null,
    details: payload?.media_details || {},
    secondary: detailSecondaryModel(payload, { formatDate: formatUserDate }),
    providers: payload?.providers || [],
    linkSections: payload?.link_sections || [],
    gameLengths: payload?.game_lengths || null,
    bookFormats: payload?.book_formats || [],
    providerGroups: payload?.providers || {},
    actions: canTrack && actionPath && request ? {
      primary: {
        label: 'Add to library',
        tone: 'primary',
        onPress: async () => request(`/api/v1/media/${encodeURIComponent(keyMediaType)}/`, {
          method: 'POST',
          json: {
            source: key.source,
            media_id: keyMediaId,
            status: 'Planning',
            ...(key.season_number != null ? { season_number: key.season_number } : {}),
          },
        }),
      },
    } : canUpdate && actionPath && request ? {
      items: [{
        label: keyMediaType === 'episode'
          ? payload?.tracking?.status === 'Completed' ? 'Mark unwatched' : 'Mark watched'
          : payload?.tracking?.status === 'Completed' ? 'Mark in progress' : 'Mark completed',
        tone: 'surface',
        onPress: async () => keyMediaType === 'episode' && episodeWatchPath
          ? request(episodeWatchPath, { method: payload?.tracking?.status === 'Completed' ? 'DELETE' : 'POST' })
          : request(updatePath, {
            method: 'PATCH',
            json: { status: payload?.tracking?.status === 'Completed' ? 'In progress' : 'Completed' },
          }),
      }],
    } : null,
  };
}

function fromMusicPayload(payload, item, kind, publicView = false) {
  const tracker = publicView ? null : payload.tracker || null;
  const name = payload.name || payload.title || item?.title || '';
  const fields = [
    payload.artist_name ? { label: 'Artist', value: payload.artist_name } : null,
    payload.release_date ? { label: 'Release date', value: payload.release_date } : null,
    payload.release_type ? { label: 'Format', value: payload.release_type } : null,
    tracker?.status ? { label: 'Status', value: tracker.status } : null,
    tracker?.score != null ? { label: 'Your score', value: tracker.score } : null,
  ].filter(Boolean);
  const albums = asList(payload.albums);
  const tracks = asList(payload.tracks);
  const sections = [
    section('genres', 'Genres', payload.genres),
  ].filter(Boolean);
  const artistId = payload.artist_id || payload.artist?.id || payload.id;
  const albumId = payload.album_id || payload.id;
  const relatedSections = [];
  if (kind === 'artist' && albums.length) {
    relatedSections.push({
      id: 'albums',
      label: 'Albums',
      items: albums.map(album => musicCard(album,
        `/details/music/artist/${encodeURIComponent(artistId)}/artist/album/${encodeURIComponent(album.id)}/album/`,
        'music')).filter(Boolean),
    });
  }
  if (kind === 'album' && tracks.length) {
    relatedSections.push({
      id: 'tracks',
      label: 'Tracks',
      items: tracks.map(track => musicCard(track,
        `/details/music/artist/${encodeURIComponent(artistId)}/artist/album/${encodeURIComponent(albumId)}/album/track/${encodeURIComponent(track.id)}/track/`,
        'music')).filter(Boolean),
    });
  }
  const request = getActiveAuthSession()?.request;
  const collectionPath = kind === 'artist'
    ? '/api/v1/music/artists/'
    : kind === 'show' ? '/api/v1/podcasts/shows/' : '/api/v1/music/albums/';
  const actionId = kind === 'artist' ? 'artist_id' : kind === 'show' ? 'show_id' : 'album_id';
  return {
    title: name,
    subtitle: payload.artist_name || '',
    artwork: { src: payload.image || '', aspect: kind === 'artist' ? 'square' : 'poster' },
    facts: fields,
    synopsis: { text: payload.description || '', previewLines: 3 },
    publicView,
    readOnly: publicView || !request,
    labels,
    palette: detailPalette,
    sections,
    relatedSections,
    actions: !publicView && !tracker && request && kind !== 'track' ? {
      primary: { label: `Track ${kind}`, tone: 'primary', onPress: () => request(collectionPath, { method: 'POST', json: { [actionId]: payload.id } }) },
    } : null,
  };
}

export function createDetailModelForItem(item) {
  return {
    title: item?.title || 'Detail unavailable',
    altTitle: '',
    subtitle: '',
    publicView: false,
    readOnly: true,
    artwork: { src: item?.image?.src || item?.image || '', aspect: 'poster' },
    facts: [],
    synopsis: null,
    labels,
    palette: detailPalette,
    actions: null,
    sections: [],
  };
}

export async function loadDetailModel(item, options = {}) {
  const identity = identityFromDetailItem(item);
  if (!identity) throw new Error('Open a detail from a media item or a Floppy detail link.');
  const request = options.request || getActiveAuthSession()?.request;
  if (typeof request !== 'function') throw new Error('Connect to your Floppy server to load this detail.');
  const datePreferences = loadDatePreferences(request);

  if (identity.musicKind) {
    await datePreferences;
    if (identity.musicKind === 'artist') {
      const response = await request(`/api/v1/music/artists/${encodeURIComponent(identity.musicId)}/`);
      return fromMusicPayload(response, item, 'artist', identity.publicView);
    }
    if (identity.musicKind === 'album') {
      const response = await request(`/api/v1/music/albums/${encodeURIComponent(identity.musicId)}/`);
      const tracks = await request(`/api/v1/music/albums/${encodeURIComponent(identity.musicId)}/tracks/`);
      return fromMusicPayload({ ...response, artist_id: identity.artistId, tracks: tracks.results || tracks }, item, 'album', identity.publicView);
    }
    const albumId = identity.albumId;
    if (!albumId) throw new Error('The track link does not include its album.');
    const album = await request(`/api/v1/music/albums/${encodeURIComponent(albumId)}/`);
    const tracks = await request(`/api/v1/music/albums/${encodeURIComponent(albumId)}/tracks/`);
    const track = asList(tracks.results || tracks).find(value => String(value.id) === String(identity.musicId));
    if (!track) throw new Error('This track is no longer available in its album.');
    return fromMusicPayload({ ...track, artist_id: identity.artistId, album_id: albumId, artist_name: album.artist_name, release_date: album.release_date }, item, 'track', identity.publicView);
  }

  if (identity.podcastId) {
    await datePreferences;
    const response = await request(`/api/v1/podcasts/shows/${encodeURIComponent(identity.podcastId)}/`);
    const episodes = await request(`/api/v1/podcasts/shows/${encodeURIComponent(identity.podcastId)}/episodes/?limit=30&offset=0`);
    return {
      ...fromMusicPayload({ ...response, name: response.title }, item, 'show', identity.publicView),
      synopsis: { text: response.description || '', previewLines: 3 },
      children: {
        kind: 'episode',
        seasons: [],
        episodes: asList(episodes.results || episodes).map((episode, index) => ({
          key: `podcast-episode-${episode.id || index}`,
          number: episode.episode_number || index + 1,
          identity: null,
          title: episode.title || episode.name || '',
          overview: episode.description || '',
          image: { src: episode.image || response.image || '', fallback: '' },
          dateLabel: episode.published || episode.release_date || '',
          runtime: episode.duration || '',
          actions: [],
        })),
      },
    };
  }

  const requestIdentity = {
    ...identity,
    publicView: Boolean(identity.publicView || options.publicView),
    episodePage: options.episodePage,
  };
  try {
    // Start supported gallery lookups alongside detail, rather than waiting
    // for detail before starting a cold provider request.
    const mayHaveGallery = (requestIdentity.source === 'tmdb' && ['movie', 'tv'].includes(requestIdentity.mediaType))
      || (requestIdentity.source === 'igdb' && requestIdentity.mediaType === 'game');
    const galleryRequest = mayHaveGallery
      ? request(detailGalleryEndpoint(requestIdentity)).then(result => {
        options.onGallery?.(result?.gallery);
        return result;
      }).catch(() => null) : null;
    const response = await request(detailEndpoint(requestIdentity));
    if (response?.artwork?.gallery_supported && !response.artwork.backdrop) {
      try {
        const gallery = galleryRequest ? await galleryRequest : await request(detailGalleryEndpoint(requestIdentity));
        response.artwork.gallery = gallery?.gallery || response.artwork.gallery;
        response.artwork.backdrop = response.artwork.backdrop
          || gallery?.gallery?.hero?.url
          || gallery?.gallery?.overview?.url
          || '';
        response.artwork.logo = response.artwork.logo
          || gallery?.gallery?.overview?.logo_url
          || gallery?.gallery?.hero?.logo_url
          || '';
      } catch {
        // The detail data remains useful if a provider gallery is unavailable.
      }
    }
    await datePreferences;
    return detailFromProviderPayload(response, item, requestIdentity);
  } catch (error) {
    if (error?.status === 404 && !/media not found/i.test(error?.message || '')) {
      throw new Error('This Floppy server needs an update to provide full detail pages.');
    }
    throw error;
  }
}
