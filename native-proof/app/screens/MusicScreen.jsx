import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Linking, StyleSheet, Text, View } from 'react-native';
import { Button } from '../../../frontend/ui/Button';
import { DetailHistoryDialog } from '../../../frontend/ui/DetailChildren';
import { MarkdownPreview } from '../../../frontend/ui/MarkdownPreview.native';
import { Screen } from '../components/Screen';
import { setShellPageType } from '../surfaces/shellContext';
import {
  BrowseList,
  DateEntryModal,
  ManageEpisodesModal,
  NotesModal,
  ReleasePickerModal,
  SectionHeading,
  SheetModal,
  ScoreChip,
  MusicBrainzScore,
  TrackRow,
  EpisodeRow,
  FilterChips,
  listenDate,
  TrackingModal,
} from '../components/MusicParts';
import { AlbumPage, ArtistPage, ShowPage, TrackPage } from '../../../frontend/ui/MusicParts';
import { MusicView } from '../../../frontend/pages/MusicView';
import { createMusicTransport } from '../data/musicTransport';
import { formatUserDate } from '../data/userDate';
import {
  endDateStamp,
  flattenAlbums,
  loadArtistWithRelations,
  mapAlbumPayload,
  mapArtistPayload,
  mapBrowseList,
  mapEpisodesPayload,
  mapHistoryEntries,
  mapManagedEpisodes,
  mapReleases,
  mapShowPayload,
  mapTrackIdentity,
  mapTrackListens,
  mapTracksPayload,
  mapTracker,
  nowDateTimeStr,
  REPAIR_POLL_MS,
  repairPending,
} from '../data/musicTransportCore';
import { mediaCardPalette, palette, radius, spacing } from '../theme';

// Platform rendering stays in this adapter; the detail page layout is shared.
const detailPresentation = {
  palette,
  cardPalette: mediaCardPalette,
  formatDate: value => /^\d{4}-\d{2}-\d{2}/.test(String(value || '')) ? formatUserDate(value) : String(value || ''),
  formatListenDate: listenDate,
  renderScore: (value, onSelect) => <ScoreChip value={value} onSelect={onSelect} />,
  renderProviderScore: (rating, votes) => <MusicBrainzScore rating={rating} votes={votes} />,
  renderNotes: markdown => <MarkdownPreview markdown={markdown} palette={palette} />,
  renderTrack: (track, album, actions) => <TrackRow key={track.id} track={{ ...track, image: album.image }} {...actions} />,
  renderEpisode: (episode, show, actions) => <EpisodeRow key={episode.id} episode={episode} showImage={show.image} {...actions} />,
  renderFilters: (options, value, onChange) => <FilterChips label="Filter episodes" options={options} value={value} onChange={onChange} />,
};

export const surface = {
  id: 'MUSIC',
  status: 'ported',
  title: 'Music and podcasts',
  where: 'Artist, album, track and podcast browse and detail',
  note: 'The screen browses tracked artists, albums and podcast shows (GET /api/v1/music/artists/, /music/albums/, /podcasts/shows/, every page) and opens their detail from the fork music and podcast endpoints through the authenticated session. A song row opened from the Library resolves its artist, album and track from the artist_id, album_id and track_id the media row now carries (GET /api/v1/music/tracks/{id}/ is the fallback). Track pages list every real listen (GET .../tracks/{id}/plays/) and delete one (DELETE .../plays/{history_id}/). Albums offer the MusicBrainz release picker (GET/POST .../albums/{id}/releases/). Manual shows add, edit and delete episodes (.../episodes/manage/ and .../podcasts/episodes/{id}/) and episodes take a score (PATCH .../episodes/{id}/score/). Plays are recorded with the real device date; tracker saves, scores, notes, discography and member syncs, delete-all-plays, remove-tracker and mark-all-played write to the server and reload the affected slice. Per-episode history still matches the show history by title. iOS/Android runtime passes are outstanding.',
};

const TABS = [
  { key: 'artist', label: 'Artist' },
  { key: 'album', label: 'Album' },
  { key: 'podcast', label: 'Podcast' },
];

const EPISODE_PAGE_SIZE = 20;
const EMPTY_DIALOG = null;
// run() returns undefined when a server write fails and the raw payload
// (possibly null for 204 responses) when it succeeds.
const REQUEST_FAILED = undefined;

function countLabel(count, singular, plural) {
  return count === 1 ? `1 ${singular}` : `${count} ${plural}`;
}

function EmptyPanel({ title, copy, actionLabel, onAction }) {
  return <View style={styles.empty}>
    <Text style={styles.emptyTitle}>{title}</Text>
    {copy ? <Text style={styles.emptyCopy}>{copy}</Text> : null}
    {actionLabel ? <Button label={actionLabel} tone="surface" onPress={onAction} /> : null}
  </View>;
}

function BrowsePanel({ kind, state, serverMode, title, emptyTitle, emptyCopy, onOpen, onRetry }) {
  const current = state || { rows: [], loading: serverMode, error: '' };
  return <View style={styles.section}>
    <SectionHeading title={title} meta={current.loading || current.error ? '' : String(current.rows.length)} />
    <BrowseList rows={current.rows} loading={current.loading} error={serverMode ? current.error : 'Sign in to your Floppy server to load music and podcasts.'}
      emptyTitle={emptyTitle} emptyCopy={emptyCopy} onOpen={onOpen} onRetry={onRetry} />
  </View>;
}

function remoteId(value) {
  const number = Number(value);
  return Number.isInteger(number) && number > 0 ? number : null;
}

// Library rows, detail hrefs and the browse lists are the ways into this
// screen. Song rows carry artist_id, album_id and track_id; a row that lacks
// the artist and album ids is placed through GET /api/v1/music/tracks/{id}/.
function resolveEntryParams(params) {
  const item = params?.item;
  const identity = item?.identity || {};
  const row = item?.media || item || {};
  const prefixedId = prefix => {
    const raw = String(identity.mediaId || '');
    return raw.startsWith(prefix) ? remoteId(raw.slice(prefix.length)) : null;
  };
  const show = row.show || item?.item?.show || null;
  return {
    artistId: remoteId(params?.artistId ?? row.artist_id) ?? prefixedId('artist-'),
    albumId: remoteId(params?.albumId ?? row.album_id) ?? prefixedId('album-'),
    showId: remoteId(params?.showId ?? show?.id),
    trackId: remoteId(params?.trackId ?? row.track_id),
    entryTitle: row.title || item?.title || '',
  };
}

function relationModel(relation) {
  const tracker = mapTracker(relation?.tracker);
  return {
    id: relation?.id,
    name: relation?.name || '',
    role: relation?.role || '',
    current: Boolean(relation?.current),
    image: relation?.image || '',
    status: tracker.status,
    score: tracker.score,
  };
}

// The album endpoint has no runtime; the web sums the tracks ("16m").
function albumRuntime(tracks) {
  const minutes = Math.round(tracks.reduce((sum, track) => sum + (Number(track.durationMs) || 0), 0) / 60000);
  if (!minutes) return null;
  return minutes >= 60 ? `${Math.floor(minutes / 60)}h ${minutes % 60}min` : `${minutes}m`;
}

function sortDescending(rows) {
  return [...rows].sort((left, right) => `${right.date} ${right.time}`.localeCompare(`${left.date} ${left.time}`));
}

export default function MusicScreen({ params, transport: injectedTransport }) {
  const [transport] = useState(() => injectedTransport || createMusicTransport());
  const [entry] = useState(() => resolveEntryParams(params));
  const [serverMode] = useState(() => transport.mode === 'server');
  const [showId, setShowId] = useState(entry.showId);
  const [tab, setTab] = useState(entry.showId && !entry.artistId ? 'podcast' : entry.albumId ? 'album' : 'artist');
  useEffect(() => {
    setShellPageType(tab === 'podcast' ? 'podcast' : 'music');
    return () => setShellPageType(null);
  }, [tab]);
  const pendingTrack = useRef(entry.trackId);
  const [browse, setBrowse] = useState({});
  const [releases, setReleases] = useState({ open: false, loading: false, error: '', releases: [], selectedId: '' });
  const [manage, setManage] = useState({ open: false, loading: false, saving: false, episodes: [], errors: {} });
  const [artistId, setArtistId] = useState(entry.artistId);
  const [albumId, setAlbumId] = useState(entry.albumId);
  const [selectedTrackId, setSelectedTrackId] = useState(null);
  const [reloadToken, setReloadToken] = useState(0);

  const [artist, setArtist] = useState(null);
  const [album, setAlbum] = useState(null);
  const [tracks, setTracks] = useState([]);
  const [show, setShow] = useState(null);
  const [episodes, setEpisodes] = useState([]);
  const [episodeTotal, setEpisodeTotal] = useState(0);
  const [episodeFilter, setEpisodeFilter] = useState('all');

  const [loadingArtist, setLoadingArtist] = useState(false);
  const [loadingAlbum, setLoadingAlbum] = useState(false);
  const [loadingEpisodes, setLoadingEpisodes] = useState(false);
  const [transportError, setTransportError] = useState(
    serverMode ? '' : 'Sign in to your Floppy server to load music and podcasts.',
  );
  const [notice, setNotice] = useState('');

  const [dialog, setDialog] = useState(EMPTY_DIALOG);
  const [confirm, setConfirm] = useState(null);
  const [dateValue, setDateValue] = useState(nowDateTimeStr);
  const [history, setHistory] = useState({ rows: [], loading: false });
  // Set while the server is still filling in this artist or album.
  const [artistRepairing, setArtistRepairing] = useState(false);
  const [albumRepairing, setAlbumRepairing] = useState(false);
  const repairAttempts = useRef({ artist: 0, album: 0 });

  const run = useCallback(async (action, payload, errorLabel) => {
    if (!serverMode) return null;
    setTransportError('');
    try {
      return await transport[action](payload);
    } catch (error) {
      setTransportError(error?.message || errorLabel);
      return REQUEST_FAILED;
    }
  }, [serverMode, transport]);

  // Artist, relations, album and episodes all reload on the retry token.
  useEffect(() => {
    if (!serverMode || !artistId) return undefined;
    let active = true;
    setLoadingArtist(true);
    repairAttempts.current.artist = 0;
    loadArtistWithRelations(transport, artistId).then(({ artist: payload, relations }) => {
      if (!active) return;
      setArtistRepairing(repairPending(payload));
      const next = mapArtistPayload(payload);
      if (next && relations) next.relations = {
        members: (relations.members || []).map(relationModel),
        alsoIn: (relations.also_in || []).map(relationModel),
      };
      setArtist(next);
    }).catch(error => {
      if (active) {
        setArtist(null);
        setTransportError(error?.message || 'The artist could not be loaded.');
      }
    }).finally(() => {
      if (active) setLoadingArtist(false);
    });
    return () => { active = false; };
  }, [artistId, reloadToken, serverMode, transport]);

  useEffect(() => {
    if (!serverMode || !albumId) return undefined;
    let active = true;
    setLoadingAlbum(true);
    repairAttempts.current.album = 0;
    Promise.all([
      transport.loadAlbum({ albumId }),
      transport.loadAlbumTracks({ albumId }),
    ]).then(([albumPayload, trackPayload]) => {
      if (!active) return;
      setAlbumRepairing(repairPending(trackPayload));
      const mappedTracks = mapTracksPayload(trackPayload);
      setAlbum(mapAlbumPayload(albumPayload));
      setTracks(mappedTracks);
      if (pendingTrack.current) {
        // A song row opened from the Library lands on that track.
        const target = mappedTracks.find(track => track.trackId === pendingTrack.current);
        pendingTrack.current = null;
        // The web opens a track on its album page, so only the tab changes.
        if (target) setTab('album');
      }
    }).catch(error => {
      if (active) {
        setAlbum(null);
        setTracks([]);
        setTransportError(error?.message || 'The album could not be loaded.');
      }
    }).finally(() => {
      if (active) setLoadingAlbum(false);
    });
    return () => { active = false; };
  }, [albumId, reloadToken, serverMode, transport]);

  // Quiet reloads while the server fills in a new artist or album.
  useEffect(() => {
    if (!serverMode || !artistId || !artistRepairing) return undefined;
    let active = true;
    const timer = setTimeout(() => {
      repairAttempts.current.artist += 1;
      loadArtistWithRelations(transport, artistId).then(({ artist: payload, relations }) => {
        if (!active) return;
        const next = mapArtistPayload(payload);
        if (next && relations) next.relations = {
          members: (relations.members || []).map(relationModel),
          alsoIn: (relations.also_in || []).map(relationModel),
        };
        setArtist(next);
        setArtistRepairing(repairPending(payload, repairAttempts.current.artist));
      }).catch(() => {
        if (active) setArtistRepairing(false);
      });
    }, REPAIR_POLL_MS);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [artistId, artistRepairing, artist, serverMode, transport]);

  useEffect(() => {
    if (!serverMode || !albumId || !albumRepairing) return undefined;
    let active = true;
    const timer = setTimeout(() => {
      repairAttempts.current.album += 1;
      Promise.all([
        transport.loadAlbum({ albumId }),
        transport.loadAlbumTracks({ albumId }),
      ]).then(([albumPayload, trackPayload]) => {
        if (!active) return;
        setAlbum(mapAlbumPayload(albumPayload));
        setTracks(mapTracksPayload(trackPayload));
        setAlbumRepairing(repairPending(trackPayload, repairAttempts.current.album));
      }).catch(() => {
        if (active) setAlbumRepairing(false);
      });
    }, REPAIR_POLL_MS);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [albumId, albumRepairing, album, tracks, serverMode, transport]);

  useEffect(() => {
    if (!serverMode || !showId) return undefined;
    let active = true;
    setLoadingEpisodes(true);
    Promise.all([
      transport.loadShow({ showId: showId }),
      transport.loadEpisodes({ showId: showId, limit: EPISODE_PAGE_SIZE, offset: 0 }),
    ]).then(([showPayload, episodePayload]) => {
      if (!active) return;
      const page = mapEpisodesPayload(episodePayload);
      setShow(mapShowPayload(showPayload));
      setEpisodes(page.episodes);
      setEpisodeTotal(page.total);
    }).catch(error => {
      if (active) {
        setShow(null);
        setEpisodes([]);
        setTransportError(error?.message || 'The podcast could not be loaded.');
      }
    }).finally(() => {
      if (active) setLoadingEpisodes(false);
    });
    return () => { active = false; };
  }, [showId, reloadToken, serverMode, transport]);

  // A song row without artist and album ids is placed through its track id.
  useEffect(() => {
    if (!serverMode || artistId || albumId || !entry.trackId) return undefined;
    let active = true;
    transport.loadTrack({ trackId: entry.trackId }).then(payload => {
      const identity = mapTrackIdentity(payload);
      if (!active || !identity) return;
      if (identity.artistId) setArtistId(identity.artistId);
      if (identity.albumId) {
        setAlbumId(identity.albumId);
        setTab('album');
      }
    }).catch(error => {
      if (active) setTransportError(error?.message || 'The song could not be placed in your music library.');
    });
    return () => { active = false; };
  }, [serverMode, artistId, albumId, entry.trackId, transport, reloadToken]);

  // Browse lists load for a tab that has nothing selected yet.
  const browseKind = tab === 'artist' && !artistId ? 'artist'
    : tab === 'album' && !albumId ? 'album'
      : tab === 'podcast' && !showId ? 'show' : null;
  const browseAction = { artist: 'loadArtists', album: 'loadAlbums', show: 'loadShows' }[browseKind];
  useEffect(() => {
    if (!serverMode || !browseKind) return undefined;
    let active = true;
    setBrowse(current => ({ ...current, [browseKind]: { rows: [], loading: true, error: '' } }));
    transport[browseAction]().then(payload => {
      if (active) setBrowse(current => ({ ...current, [browseKind]: { rows: mapBrowseList(payload, browseKind), loading: false, error: '' } }));
    }).catch(error => {
      if (active) {
        setBrowse(current => ({
          ...current,
          [browseKind]: { rows: [], loading: false, error: error?.message || 'This list could not be loaded.' },
        }));
      }
    });
    return () => { active = false; };
  }, [serverMode, browseKind, browseAction, transport, reloadToken]);

  function openBrowseRow(row) {
    setSelectedTrackId(null);
    setNotice('');
    if (row.kind === 'artist') { setArtistId(row.id); setTab('artist'); }
    if (row.kind === 'album') { setAlbumId(row.id); setTab('album'); }
    if (row.kind === 'show') { setShowId(row.id); setTab('podcast'); }
  }

  function browseAgain(kind) {
    setSelectedTrackId(null);
    setNotice('');
    if (kind === 'artist') { setArtistId(null); setArtist(null); }
    if (kind === 'album') { setAlbumId(null); setAlbum(null); setTracks([]); }
    if (kind === 'show') { setShowId(null); setShow(null); setEpisodes([]); }
  }

  const artistRemoteId = artist?.artistId ?? artistId;
  const albumRemoteId = album?.albumId ?? albumId;
  const showRemoteId = show?.showId ?? showId;

  function reloadShow() {
    if (!serverMode || !showRemoteId) return Promise.resolve();
    return transport.loadShow({ showId: showRemoteId })
      .then(payload => setShow(mapShowPayload(payload)))
      .catch(error => setTransportError(error?.message || 'The podcast could not be reloaded.'));
  }

  function reloadArtist() {
    if (!serverMode || !artistRemoteId) return Promise.resolve();
    return transport.loadArtist({ artistId: artistRemoteId })
      .then(payload => setArtist(current => {
        const next = mapArtistPayload(payload);
        if (next && current?.id === next.id) next.relations = current.relations;
        return next;
      }))
      .catch(error => setTransportError(error?.message || 'The artist could not be reloaded.'));
  }

  function reloadAlbum() {
    if (!serverMode || !albumRemoteId) return Promise.resolve();
    return Promise.all([
      transport.loadAlbum({ albumId: albumRemoteId }),
      transport.loadAlbumTracks({ albumId: albumRemoteId }),
    ]).then(([albumPayload, trackPayload]) => {
      setAlbum(mapAlbumPayload(albumPayload));
      setTracks(mapTracksPayload(trackPayload));
    }).catch(error => setTransportError(error?.message || 'The album could not be reloaded.'));
  }

  function loadEpisodePage(append) {
    if (!serverMode || !showId) return Promise.resolve();
    setLoadingEpisodes(true);
    return transport.loadEpisodes({
      showId: showId,
      limit: EPISODE_PAGE_SIZE,
      offset: append ? episodes.length : 0,
    }).then(payload => {
      const page = mapEpisodesPayload(payload);
      setEpisodes(current => (append ? [...current, ...page.episodes] : page.episodes));
      setEpisodeTotal(page.total);
    }).catch(error => {
      setTransportError(error?.message || 'The episodes could not be loaded.');
    }).finally(() => setLoadingEpisodes(false));
  }

  function changeTab(next) {
    setTab(next);
    setSelectedTrackId(null);
    setNotice('');
  }

  function openAlbum(next) {
    const nextId = remoteId(next?.albumId ?? next?.id);
    if (!nextId) return;
    setAlbumId(nextId);
    setSelectedTrackId(null);
    setTab('album');
  }

  function askConfirm(config) {
    setConfirm(config);
  }

  function runConfirm() {
    const action = confirm?.onConfirm;
    setConfirm(null);
    if (action) action();
  }

  function updateTrackRow(trackKey, updater) {
    setTracks(current => current.map(track => (track.id === trackKey ? updater(track) : track)));
  }

  function confirmDeleteArtistPlays() {
    askConfirm({
      title: 'Delete all plays?',
      message: 'Are you sure you want to delete all plays for this artist? This cannot be undone.',
      confirmLabel: 'Delete plays',
      async onConfirm() {
        const result = await run('deleteArtistPlays', { artistId: artistRemoteId }, 'The plays could not be deleted.');
        if (result === REQUEST_FAILED) return;
        setNotice(`Deleted ${countLabel(Number(result?.deleted_plays) || 0, 'listen', 'listens')}.`);
        await reloadArtist();
      },
    });
  }

  function confirmDeleteAlbumPlays() {
    askConfirm({
      title: 'Delete all plays?',
      message: 'Are you sure you want to delete all plays for this album? This cannot be undone.',
      confirmLabel: 'Delete plays',
      async onConfirm() {
        const result = await run('deleteAlbumPlays', { albumId: albumRemoteId }, 'The plays could not be deleted.');
        if (result === REQUEST_FAILED) return;
        setNotice(`Deleted ${countLabel(Number(result?.deleted_plays) || 0, 'listen', 'listens')}.`);
        await reloadAlbum();
        await reloadArtist();
      },
    });
  }

  function confirmRemoveTracker(kind) {
    const config = {
      artist: {
        title: 'Remove artist from library?',
        confirmLabel: 'Remove artist',
        action: 'removeArtist',
        payload: { artistId: artistRemoteId },
        done: 'Artist removed from your library.',
      },
      album: {
        title: 'Remove album from library?',
        confirmLabel: 'Remove album',
        action: 'removeAlbum',
        payload: { albumId: albumRemoteId },
        done: 'Album removed from your library.',
      },
      show: {
        title: 'Remove podcast from library?',
        confirmLabel: 'Remove podcast',
        action: 'removeShow',
        payload: { showId: showRemoteId },
        done: 'Podcast removed from your library.',
      },
    }[kind];
    askConfirm({
      title: config.title,
      message: 'The tracker is removed. Listening history stays in your account.',
      confirmLabel: config.confirmLabel,
      async onConfirm() {
        const result = await run(config.action, config.payload, 'The tracker could not be removed.');
        if (result === REQUEST_FAILED) return;
        setNotice(config.done);
        if (kind === 'artist') await reloadArtist();
        if (kind === 'album') await reloadAlbum();
        if (kind === 'show') await reloadShow();
      },
    });
  }

  function confirmMarkAllPlayed() {
    askConfirm({
      title: 'Mark all episodes played?',
      message: 'Every unplayed episode is recorded as played, and the show refreshes its feed first.',
      confirmLabel: 'Mark all played',
      async onConfirm() {
        const result = await run('markAllPlayed', { showId: showRemoteId }, 'The episodes could not be marked played.');
        if (result === REQUEST_FAILED) return;
        setNotice(`Marked ${countLabel(Number(result?.marked_played) || 0, 'episode', 'episodes')} played.`);
        await loadEpisodePage(false);
      },
    });
  }

  async function syncArtistDiscography() {
    const result = await run('syncArtistDiscography', { artistId: artistRemoteId }, 'Discography sync failed.');
    if (result === REQUEST_FAILED) return;
    setNotice(`Synced ${countLabel(Number(result?.synced_albums) || 0, 'release', 'releases')} from MusicBrainz.`);
    await reloadArtist();
  }

  async function syncArtistMembers() {
    const result = await run('syncArtistMembers', { artistId: artistRemoteId }, 'Member sync failed.');
    if (result === REQUEST_FAILED) return;
    const payload = await run('loadArtistRelations', { artistId: artistRemoteId }, 'The relations could not be reloaded.');
    if (payload) {
      setArtist(current => (current ? {
        ...current,
        relations: {
          members: (payload.members || []).map(relationModel),
          alsoIn: (payload.also_in || []).map(relationModel),
        },
      } : current));
    }
    setNotice(`Synced ${countLabel(Number(result?.synced_members) || 0, 'member', 'members')} from MusicBrainz.`);
  }

  async function syncAlbumMetadata() {
    const result = await run('syncAlbumMetadata', { albumId: albumRemoteId }, 'Album metadata sync failed.');
    if (result === REQUEST_FAILED) return;
    setNotice(`Synced ${countLabel(Number(result?.synced_tracks) || 0, 'track', 'tracks')} from MusicBrainz.`);
    if (result?.merged_into) setAlbumId(result.merged_into);
    await reloadAlbum();
  }

  function saveTracker(target, draft) {
    const config = {
      artist: { action: 'saveArtist', payload: { artistId: artistRemoteId }, reload: reloadArtist },
      album: { action: 'saveAlbum', payload: { albumId: albumRemoteId }, reload: reloadAlbum },
      show: { action: 'saveShow', payload: { showId: showRemoteId }, reload: reloadShow },
    }[target];
    return run(config.action, { ...config.payload, ...draft }, 'The tracker could not be saved.')
      .then(async result => {
        if (result === REQUEST_FAILED) return;
        await config.reload();
      });
  }

  function openTracking(target, item) {
    setDialog({ type: 'tracking', target, title: item.title || item.name });
  }

  function saveTracking(draft) {
    if (!dialog) return;
    saveTracker(dialog.target, draft);
    setDialog(EMPTY_DIALOG);
  }

  function rateArtist(score) {
    saveTracker('artist', { status: artist.status, score, notes: artist.notes || '' });
  }

  function rateAlbum(score) {
    saveTracker('album', { status: album.status, score, notes: album.notes || '' });
  }

  function rateShow(score) {
    saveTracker('show', { status: show.status, score, notes: show.notes || '' });
  }

  function openDateDialog(target, item) {
    setDateValue(nowDateTimeStr());
    const albumName = album ? album.title : artist?.name || '';
    setDialog({
      type: 'date',
      target,
      item,
      title: item.title,
      subtitle: target === 'track'
        ? (item.duration ? `${albumName} · ${item.duration}` : albumName)
        : `Episode ${item.number} · ${show.title}`,
      suggestionDate: target === 'episode' ? item.published : album?.releaseDate,
    });
  }

  async function confirmDate() {
    if (!dialog) return;
    const endDate = endDateStamp(dateValue);
    if (dialog.target === 'track') {
      const track = dialog.item;
      const result = await run('addListen', {
        albumId: albumRemoteId,
        trackId: track.trackId,
        endDate,
      }, 'The listen could not be recorded.');
      setDialog(EMPTY_DIALOG);
      if (result === REQUEST_FAILED) return;
      setNotice('Listen recorded.');
      await reloadAlbum();
    } else {
      const episode = dialog.item;
      const result = await run('addEpisodePlay', {
        showId: showRemoteId,
        episodeId: episode.episodeId,
        endDate,
      }, 'The play could not be recorded.');
      setDialog(EMPTY_DIALOG);
      if (result === REQUEST_FAILED) return;
      setNotice('Play recorded.');
      await loadEpisodePage(false);
    }
  }

  function openNotes(target, item) {
    setDialog({
      type: 'notes',
      target,
      item,
      title: item.title,
      subtitle: target === 'episode'
        ? `Episode ${item.number} · ${show.title}`
        : (album ? album.title : artist?.name || ''),
      value: item.notes || '',
    });
  }

  async function saveTrackNotes(trackKey, value) {
    const track = tracks.find(candidate => candidate.id === trackKey);
    if (!track?.trackId) return;
    updateTrackRow(trackKey, current => ({ ...current, notes: value }));
    const result = await run('saveTrackNotes', { trackId: track.trackId, notes: value }, 'The notes could not be saved.');
    if (result === REQUEST_FAILED) {
      updateTrackRow(trackKey, current => ({ ...current, notes: '' }));
      return;
    }
    setNotice('Notes saved.');
  }

  function saveNotes(value) {
    if (!dialog) return;
    if (dialog.target === 'track') {
      saveTrackNotes(dialog.item.id, value);
    } else {
      const episode = dialog.item;
      run('saveEpisodeNotes', { episodeId: episode.episodeId, notes: value }, 'The notes could not be saved.')
        .then(result => {
          if (result === REQUEST_FAILED) return;
          setEpisodes(current => current.map(row => (
            row.id === episode.id ? { ...row, notes: value } : row
          )));
          setNotice('Notes saved.');
        });
    }
    setDialog(EMPTY_DIALOG);
  }

  async function rateTrack(track, score) {
    if (track.musicId == null) {
      setNotice('Record a listen before rating this track.');
      return;
    }
    updateTrackRow(track.id, current => ({ ...current, score }));
    const result = await run('rateTrack', { musicId: track.musicId, score }, 'The score could not be saved.');
    if (result === REQUEST_FAILED) {
      updateTrackRow(track.id, current => ({ ...current, score: null }));
      return;
    }
    setNotice('Score saved.');
  }

  function handleTrackAction(key, targetAlbum, track) {
    if (key === 'listen') openDateDialog('track', track);
    else if (key === 'notes') openNotes('track', { ...track, title: track.title });
    else if (key === 'history') openHistory('track', track);
  }

  function handleEpisodeAction(key, episode) {
    if (key === 'play') openDateDialog('episode', episode);
    else if (key === 'notes') openNotes('episode', episode);
    else if (key === 'history') openHistory('episode', episode);
  }

  async function openHistory(target, item) {
    setDialog({ type: 'history', target, title: item.title || item.name });
    if (target === 'track') {
      setHistory({ rows: [], loading: true });
      try {
        const listens = mapTrackListens(await transport.loadTrackPlays({ trackId: item.trackId }));
        setHistory({
          rows: listens.map(listen => ({
            key: String(listen.id),
            date: listen.date,
            time: listen.time,
            dateLabel: `${listen.date} ${listen.time}`,
            actionLabel: 'Listened',
            note: '',
          })),
          loading: false,
        });
      } catch (error) {
        setTransportError(error?.message || 'The activity history could not be loaded.');
        setHistory({ rows: [], loading: false });
      }
      return;
    }
    const filters = {
      artist: { mediaType: 'music', artistId: artistRemoteId },
      album: { mediaType: 'music', albumId: albumRemoteId },
      episode: { mediaType: 'podcast', showId: showRemoteId },
      show: { mediaType: 'podcast', showId: showRemoteId },
    };
    setHistory({ rows: [], loading: true });
    const payload = await run('loadHistory', { ...filters[target], limit: 50 }, 'The activity history could not be loaded.');
    if (payload === REQUEST_FAILED) {
      setHistory({ rows: [], loading: false });
      return;
    }
    let rows = mapHistoryEntries(payload);
    if (target === 'episode') {
      // The history endpoint filters by show only, so this episode's plays
      // are matched by entry title.
      rows = rows.filter(row => row.title === item.title);
    }
    setHistory({
      rows: sortDescending(rows.map(row => ({
        key: row.key,
        date: row.date,
        time: row.time,
        dateLabel: row.dateLabel,
        actionLabel: row.title,
        note: row.note,
      }))),
      loading: false,
    });
  }

  const selectedTrackKey = selectedTrackId
    ? tracks.find(track => track.id === selectedTrackId)?.trackId
    : null;

  const loadListens = useCallback(async trackKey => {
    if (!serverMode || !trackKey) return;
    try {
      const listens = mapTrackListens(await transport.loadTrackPlays({ trackId: trackKey }));
      setTracks(current => current.map(track => (
        track.trackId === trackKey ? { ...track, listens, playCount: listens.length } : track
      )));
    } catch (error) {
      setTransportError(error?.message || 'The listens could not be loaded.');
    }
  }, [serverMode, transport]);

  useEffect(() => {
    loadListens(selectedTrackKey);
  }, [selectedTrackKey, loadListens, reloadToken]);

  function confirmDeleteListen(track, historyId) {
    askConfirm({
      title: 'Delete this listen?',
      message: 'This removes the recorded listen and cannot be undone.',
      confirmLabel: 'Delete listen',
      async onConfirm() {
        const result = await run('deleteTrackPlay', { trackId: track.trackId, historyId }, 'The listen could not be deleted.');
        if (result === REQUEST_FAILED) return;
        setNotice('Listen deleted.');
        await loadListens(track.trackId);
        await reloadAlbum();
      },
    });
  }

  async function openReleases() {
    setReleases({ open: true, loading: true, error: '', releases: [], selectedId: '' });
    try {
      const mapped = mapReleases(await transport.loadReleases({ albumId: albumRemoteId }));
      setReleases({ open: true, loading: false, error: '', ...mapped });
    } catch (error) {
      setReleases({ open: true, loading: false, error: error?.message || 'Releases could not be loaded.', releases: [], selectedId: '' });
    }
  }

  async function chooseRelease(release) {
    const result = await run('selectRelease', { albumId: albumRemoteId, releaseId: release.id }, 'The release could not be saved.');
    if (result === REQUEST_FAILED) return;
    setReleases(current => ({ ...current, open: false }));
    setNotice(result?.synced === false
      ? 'Saved your release choice, but the tracklist could not be refreshed from MusicBrainz.'
      : `Release updated${result?.track_count ? ` - synced ${result.track_count} tracks` : ''}.`);
    await reloadAlbum();
  }

  async function reloadManaged() {
    try {
      const episodes = mapManagedEpisodes(await transport.loadManagedEpisodes({ showId: showRemoteId }));
      setManage(current => ({ ...current, loading: false, episodes }));
    } catch (error) {
      setManage(current => ({ ...current, loading: false, errors: { form: error?.message || 'Episodes could not be loaded.' } }));
    }
  }

  function openManage() {
    setManage({ open: true, loading: true, saving: false, episodes: [], errors: {} });
    reloadManaged();
  }

  async function saveManagedEpisode(editingId, values) {
    setManage(current => ({ ...current, saving: true, errors: {} }));
    try {
      if (editingId) await transport.editManagedEpisode({ episodeId: editingId, values });
      else await transport.addManagedEpisode({ showId: showRemoteId, values });
      setManage(current => ({ ...current, saving: false }));
      await reloadManaged();
      await loadEpisodePage(false);
      return true;
    } catch (error) {
      setManage(current => ({ ...current, saving: false, errors: { form: error?.message || 'The episode could not be saved.' } }));
      return false;
    }
  }

  function deleteManagedEpisode(episode) {
    askConfirm({
      title: 'Delete this episode?',
      message: `"${episode.title}" and its plays are removed.`,
      confirmLabel: 'Delete episode',
      async onConfirm() {
        const result = await run('deleteManagedEpisode', { episodeId: episode.id }, 'The episode could not be deleted.');
        if (result === REQUEST_FAILED) return;
        await reloadManaged();
        await loadEpisodePage(false);
      },
    });
  }

  async function rateEpisode(episode, score) {
    setEpisodes(current => current.map(row => (row.id === episode.id ? { ...row, score } : row)));
    const result = await run('rateEpisode', { episodeId: episode.episodeId, score }, 'The score could not be saved.');
    if (result === REQUEST_FAILED) {
      setEpisodes(current => current.map(row => (row.id === episode.id ? { ...row, score: episode.score } : row)));
      return;
    }
    setNotice('Score saved.');
  }

  const selectedTrack = selectedTrackId
    ? tracks.find(track => track.id === selectedTrackId)
    : null;
  const albums = flattenAlbums(artist);

  const loading = Boolean(
    (artistId && !artist && loadingArtist)
    || (showId && !show && loadingEpisodes),
  );

  const openWebsite = () => {
    if (!show?.websiteUrl) return;
    Linking.openURL(show.websiteUrl).catch(() => setNotice('The show website could not be opened.'));
  };

  const tracking = dialog?.type === 'tracking' ? {
    artist: { status: artist?.status, score: artist?.score, notes: artist?.notes || '' },
    album: { status: album?.status, score: album?.score, notes: album?.notes || '' },
    show: { status: show?.status, score: show?.score, notes: show?.notes || '' },
  }[dialog.target] : null;

  return <Screen chrome={false}>
    <MusicView tabs={TABS} active={tab} onChange={changeTab}
      palette={palette} error={transportError} notice={notice}
      loading={loading} onRetry={() => setReloadToken(current => current + 1)}>

    {tab === 'artist' && !artistId
      ? <BrowsePanel kind="artist" state={browse.artist} serverMode={serverMode}
        title="Your artists" emptyTitle="No artists yet"
        emptyCopy="Track an artist from search or a song in your library to see it here."
        onOpen={openBrowseRow} onRetry={() => setReloadToken(current => current + 1)} />
      : null}

    {tab === 'artist' && artistId
      ? (artist
        ? <>
          <View style={styles.actionRow}><Button label="All artists" tone="surface" onPress={() => browseAgain('artist')} /></View>
          <ArtistPage {...detailPresentation} artist={artist} albums={albums}
            onOpenAlbum={openAlbum}
            onOpenArtist={relation => openBrowseRow({ kind: 'artist', id: relation.id })}
            onTrack={() => openTracking('artist', artist)}
            onHistory={() => openHistory('artist', { title: artist.name })}
            onRate={rateArtist}
            onOpenNotes={() => openTracking('artist', artist)}
            onSyncMembers={artistRemoteId ? syncArtistMembers : null} />
          {artist.relations.members.length || artist.relations.alsoIn.length
            ? null
            : <EmptyPanel title="No band relations yet"
              copy="Sync members from MusicBrainz to show current and former collaborators."
              actionLabel={artistRemoteId ? 'Sync members' : null}
              onAction={artistRemoteId ? syncArtistMembers : undefined} />}
          {albums.length
            ? null
            : <EmptyPanel title="No releases collected yet"
              copy="Sync the discography to pull albums and EPs from MusicBrainz."
              actionLabel={artistRemoteId ? 'Sync discography' : null}
              onAction={syncArtistDiscography} />}
          <View style={styles.section}>
            <SectionHeading title="Artist actions" />
            <View style={styles.actionRow}>
              <Button label="Sync discography" tone="surface" onPress={syncArtistDiscography} />
              <Button label="Delete all plays for this artist" tone="ghost-danger" onPress={confirmDeleteArtistPlays} />
              {artist.status ? <Button label="Remove from library" tone="ghost-danger" onPress={() => confirmRemoveTracker('artist')} /> : null}
            </View>
          </View>
        </>
        : <EmptyPanel
          title="The artist could not be loaded."
          copy="Check your connection and try again."
          actionLabel="Try again"
          onAction={() => setReloadToken(current => current + 1)} />)
      : null}

    {tab === 'album' && !albumId
      ? <BrowsePanel kind="album" state={browse.album} serverMode={serverMode}
        title="Your albums" emptyTitle="No albums yet"
        emptyCopy="Track an album from an artist page to see it here."
        onOpen={openBrowseRow} onRetry={() => setReloadToken(current => current + 1)} />
      : null}

    {tab === 'album' && albumId && !selectedTrack
      ? (album
        ? <>
          <AlbumPage {...detailPresentation} album={{ ...album, tracks, format: album.format || album.releaseType, runtime: albumRuntime(tracks) }}
            onBack={() => setTab('artist')}
            onOpenTrack={track => setSelectedTrackId(track.id)}
            onTrack={() => openTracking('album', album)}
            onHistory={() => openHistory('album', { title: album.title })}
            onRate={rateAlbum}
            onOpenNotes={() => openTracking('album', album)}
            onAction={handleTrackAction}
            onTrackRate={rateTrack}
            onSyncMetadata={albumRemoteId ? syncAlbumMetadata : null}
            onChooseRelease={albumRemoteId && album.hasReleaseGroup ? openReleases : null} />
          {tracks.length
            ? null
            : albumRepairing
              ? <EmptyPanel title="Loading tracks"
                copy="Fetching them from MusicBrainz." />
              : <EmptyPanel title="No tracks yet"
              copy="Sync the album metadata to rebuild the tracklist from MusicBrainz."
              actionLabel={albumRemoteId ? 'Sync metadata' : null}
              onAction={syncAlbumMetadata} />}
          <View style={styles.section}>
            <SectionHeading title="Album actions" />
            <View style={styles.actionRow}>
              <Button label="Delete all plays for this album" tone="ghost-danger" onPress={confirmDeleteAlbumPlays} />
              {album.status ? <Button label="Remove album" tone="ghost-danger" onPress={() => confirmRemoveTracker('album')} /> : null}
            </View>
          </View>
        </>
        : <EmptyPanel title={loadingAlbum ? 'Loading album…' : 'The album could not be loaded.'}
          copy="Check your connection and try again."
          actionLabel="Try again"
          onAction={() => setReloadToken(current => current + 1)} />)
      : null}

    {tab === 'album' && selectedTrack
      ? <TrackPage {...detailPresentation} key={selectedTrack.id} album={album} track={selectedTrack}
        onBack={() => setSelectedTrackId(null)}
        onRate={score => rateTrack(selectedTrack, score)}
        onAddListen={() => openDateDialog('track', selectedTrack)}
        onOpenNotes={() => openNotes('track', selectedTrack)}
        onSaveNotes={value => saveTrackNotes(selectedTrack.id, value)}
        onDeleteListen={historyId => confirmDeleteListen(selectedTrack, historyId)}
        onOpenHistory={() => openHistory('track', selectedTrack)} />
      : null}

    {tab === 'podcast' && !showId
      ? <BrowsePanel kind="show" state={browse.show} serverMode={serverMode}
        title="Your podcasts" emptyTitle="No podcasts yet"
        emptyCopy="Add a podcast from search to see it here."
        onOpen={openBrowseRow} onRetry={() => setReloadToken(current => current + 1)} />
      : null}

    {tab === 'podcast' && showId
      ? (show
        ? <>
          <View style={styles.actionRow}><Button label="All podcasts" tone="surface" onPress={() => browseAgain('show')} /></View>
          <ShowPage {...detailPresentation} show={show} episodes={episodes} episodeTotal={episodeTotal}
            episodeFilter={episodeFilter}
            onFilter={value => { setEpisodeFilter(value); setNotice(''); }}
            hasMore={episodes.length < episodeTotal}
            loadingEpisodes={loadingEpisodes}
            onLoadMore={() => loadEpisodePage(true)}
            onTrack={() => openTracking('show', show)}
            onHistory={() => openHistory('show', { title: show.title })}
            onRate={rateShow}
            onAction={handleEpisodeAction}
            onVisitWebsite={show.websiteUrl ? openWebsite : null}
            onRateEpisode={rateEpisode}
            onManageEpisodes={show.isManual ? openManage : null} />
          <View style={styles.section}>
            <SectionHeading title="Show actions" />
            <View style={styles.actionRow}>
              <Button label="Mark all played" tone="surface" onPress={confirmMarkAllPlayed} />
              {show.status ? <Button label="Remove podcast" tone="ghost-danger" onPress={() => confirmRemoveTracker('show')} /> : null}
            </View>
          </View>
        </>
        : <EmptyPanel title={loadingEpisodes ? 'Loading podcast…' : 'The podcast could not be loaded.'}
          copy="Check your connection and try again."
          actionLabel="Try again"
          onAction={() => setReloadToken(current => current + 1)} />)
      : null}
    </MusicView>

    <SheetModal open={Boolean(confirm)}
      title={confirm?.title || 'Confirm'}
      subtitle={confirm?.message}
      onClose={() => setConfirm(null)}>
      <View style={styles.modalActions}>
        <Button label="Cancel" tone="surface" onPress={() => setConfirm(null)} />
        <Button label={confirm?.confirmLabel || 'Confirm'} tone="danger-solid" onPress={runConfirm} />
      </View>
    </SheetModal>

    <ReleasePickerModal open={releases.open} title={album?.title || 'Releases'} state={releases}
      onSelect={chooseRelease}
      onClose={() => setReleases(current => ({ ...current, open: false }))} />

    <ManageEpisodesModal open={manage.open} showTitle={show?.title || ''} state={manage}
      onSave={saveManagedEpisode}
      onDelete={deleteManagedEpisode}
      onClose={() => setManage(current => ({ ...current, open: false }))} />

    <DateEntryModal open={dialog?.type === 'date'}
      title={dialog?.target === 'track' ? 'Add listen' : 'Add play'}
      subtitle={dialog?.subtitle}
      value={dateValue}
      onChange={setDateValue}
      onClose={() => setDialog(EMPTY_DIALOG)}
      onConfirm={confirmDate}
      confirmLabel={dialog?.target === 'track' ? 'Add listen' : 'Add play'}
      suggestionDate={dialog?.suggestionDate || ''} />

    <TrackingModal open={dialog?.type === 'tracking'} Preview={MarkdownPreview}
      title={dialog?.title || 'Tracking'}
      subtitle={dialog?.target === 'show' ? 'Podcast show' : dialog?.target === 'album' ? 'Album' : 'Artist'}
      status={tracking?.status || null}
      score={tracking?.score ?? null}
      notes={tracking?.notes || ''}
      onClose={() => setDialog(EMPTY_DIALOG)}
      onSave={saveTracking} />

    <NotesModal open={dialog?.type === 'notes'} Preview={MarkdownPreview}
      title={dialog?.title || 'Notes'}
      subtitle={dialog?.subtitle}
      value={dialog?.value || ''}
      onClose={() => setDialog(EMPTY_DIALOG)}
      onSave={saveNotes} />

    <DetailHistoryDialog open={dialog?.type === 'history'}
      title={dialog ? dialog.title : 'Activity history'}
      rows={history.rows}
      labels={{ history: 'Activity history', close: 'Close', noHistory: history.loading ? 'Loading history…' : 'No activity recorded yet.' }}
      palette={palette}
      onClose={() => setDialog(EMPTY_DIALOG)} />
  </Screen>;
}

const styles = StyleSheet.create({
  section: { gap: spacing.md },
  actionRow: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  empty: {
    borderWidth: 1,
    borderColor: palette.border,
    borderRadius: radius.md,
    backgroundColor: palette.surface,
    padding: spacing.lg,
    alignItems: 'center',
    gap: spacing.sm,
  },
  emptyTitle: { color: palette.text, fontSize: 14, fontWeight: '600', textAlign: 'center' },
  emptyCopy: { color: palette.textMuted, fontSize: 12, lineHeight: 18, textAlign: 'center' },
  modalActions: { flexDirection: 'row', justifyContent: 'flex-end', flexWrap: 'wrap', gap: spacing.sm },
});
