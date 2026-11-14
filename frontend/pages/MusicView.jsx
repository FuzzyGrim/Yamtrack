import React, { useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Image,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
  useWindowDimensions,
} from 'react-native';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { DateTimePicker } from '../ui/DateTimePicker';
import { MediaCard } from '../ui/MediaCard';
import { MarkdownNotes } from '../ui/MarkdownNotes';
import { MusicAlbumGrid } from '../ui/MusicAlbumCard';
import { MusicRelationCard, MusicRelationGrid } from '../ui/MusicRelationCard';
import { ProgressBar } from '../ui/ProgressBar';
import { RatingChip } from '../ui/RatingChip';
import { RatingStars } from '../ui/RatingStars';
import { actionIcons } from '../ui/actionIcons';

const palette = {
  background: '#18181b',
  surface: '#27272a',
  surfaceStrong: '#3f3f46',
  surfaceMuted: '#52525b',
  border: '#3f3f46',
  text: '#fafafa',
  textSecondary: '#d4d4d8',
  textMuted: '#a1a1aa',
  accent: '#6366f1',
  accentStrong: '#4f46e5',
  success: '#10b981',
  warning: '#f59e0b',
  danger: '#ef4444',
};
const spacing = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24 };
const radius = { sm: 6, md: 10, lg: 16, pill: 9999 };
const ratingPalette = {
  lit: '#f59e0b', unlit: '#52525b', muted: '#a1a1aa', text: '#fafafa',
  textSecondary: '#d4d4d8', surface: '#27272a', border: '#3f3f46',
  chipBackground: 'rgba(245, 158, 11, 0.07)', chipBorder: 'rgba(251, 191, 36, 0.18)',
  chipHoverBackground: 'rgba(245, 158, 11, 0.11)', chipHoverBorder: 'rgba(252, 211, 77, 0.28)',
  starBadgeBackground: 'rgba(245, 158, 11, 0.14)', starBadgeBorder: 'rgba(251, 191, 36, 0.28)',
  cardBackground: 'rgba(251, 191, 36, 0.10)', cardBorder: 'rgba(251, 191, 36, 0.26)',
  compactValue: '#fcd34d', focusStyle: {},
};
const mediaCardPalette = {
  card: palette.surface, text: palette.text, textSecondary: palette.textSecondary,
  textMuted: palette.textMuted, border: palette.border, link: '#818cf8', star: '#f59e0b',
  chipBackground: 'rgba(24, 24, 27, 0.92)', chipText: '#ffffff', placeholder: palette.surfaceStrong,
  progressTrack: 'rgba(24, 24, 27, 0.6)', progressFill: palette.accent,
  progressPaused: 'rgba(99, 102, 241, 0.4)',
  statusColors: { Completed: '#10b981', 'In progress': '#6366f1', Paused: '#f59e0b', Planning: '#38bdf8', Dropped: '#ef4444' },
  rating: ratingPalette,
};
const progressDetailPalette = {
  background: palette.background, text: palette.text, textSecondary: palette.textSecondary,
  textMuted: palette.textMuted, surface: palette.surface, surfaceStrong: palette.surfaceStrong,
  surfaceBorder: palette.border, surfaceMuted: palette.surfaceMuted, surfaceHover: palette.surfaceStrong,
  fill: palette.success, glow: '#a7f3d0', glowShadow: '#6ee7b7',
  watchedBorder: 'rgba(16, 185, 129, 0.4)', watchedBg: 'rgba(16, 185, 129, 0.1)',
  watchedText: palette.success, upcomingBorder: palette.border, droppedBackground: palette.surfaceMuted,
  droppedText: palette.textMuted, focusStyle: {},
};
const dateTimePalette = {
  text: palette.text, textSecondary: palette.textSecondary, textMuted: palette.textMuted,
  surface: palette.surface, surfaceStrong: palette.surfaceStrong, surfaceMuted: palette.surfaceMuted,
  border: palette.border, accent: palette.accent, accentContrast: '#ffffff', link: '#818cf8',
  danger: palette.danger, radius: radius.md, radiusSmall: radius.sm, fontSize: 14, smallFontSize: 12,
};

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
  'August', 'September', 'October', 'November', 'December'];

export const STATUS_OPTIONS = ['Planning', 'In progress', 'Paused', 'Completed', 'Dropped'];

const STATUS_COLORS = {
  Completed: palette.success,
  'In progress': palette.accent,
  Paused: palette.warning,
  Planning: '#38bdf8',
  Dropped: palette.danger,
};

const statusPalette = STATUS_OPTIONS.reduce((result, option) => {
  result[option] = { color: '#ffffff', backgroundColor: STATUS_COLORS[option] };
  return result;
}, {});

const outlinePalette = {
  outline: { color: palette.textSecondary, borderColor: palette.surfaceMuted, borderWidth: 1 },
};

const markdownPalette = {
  background: palette.background,
  surface: palette.surface,
  surfaceStrong: palette.surfaceStrong,
  border: palette.border,
  text: palette.text,
  textSecondary: palette.textSecondary,
  textMuted: palette.textMuted,
  accent: palette.accent,
  link: palette.accent,
  danger: palette.danger,
  codeBackground: palette.background,
  codeText: palette.text,
};

const markdownLabels = {
  write: 'Write',
  preview: 'Preview',
  hint: 'Markdown supported',
  heading: 'Heading',
  bold: 'Bold',
  italic: 'Italic',
  code: 'Code',
  link: 'Link',
  quote: 'Quote',
  bulletedList: 'Bulleted list',
  numberedList: 'Numbered list',
  emptyPreview: 'Nothing to preview yet.',
};

export const dateTimeLabels = {
  field: 'Listen date',
  notSet: 'Not set',
  openPicker: 'Open date picker',
  picker: 'Choose date',
  quickActions: 'Quick actions',
  clear: 'Clear date',
  close: 'Close date picker',
  previousMonth: 'Previous month',
  nextMonth: 'Next month',
  selectMonth: 'Select month',
  previousYear: 'Previous year',
  nextYear: 'Next year',
  selectYear: 'Select year',
  previousYears: 'Previous years',
  nextYears: 'Next years',
  year: 'Year',
  back: 'Back',
  time: 'Time',
  now: 'Now',
  none: 'None',
  suggestedDate: 'Suggested date',
  hour: 'Hour',
  minute: 'Minute',
  second: 'Second',
  earlierHour: 'Earlier hour',
  laterHour: 'Later hour',
  earlierMinute: 'Earlier minute',
  laterMinute: 'Later minute',
  earlierSecond: 'Earlier second',
  laterSecond: 'Later second',
  activity: 'View activity for',
};

const quickDateActions = [
  { id: 'today', label: 'Today' },
  { id: 'just-finished', label: 'Just Finished' },
];

export function star(lit, size = 20) {
  return <Text style={[styles.star, { fontSize: size, lineHeight: size, color: lit ? ratingPalette.lit : ratingPalette.unlit }]}>★</Text>;
}

export function dateLabel(value) {
  const [year, month, day] = String(value || '').split('-').map(Number);
  if (!year || !month || !day) return value || 'No date';
  return `${MONTHS[month - 1]} ${day}, ${year}`;
}

export function timeLabel(value) {
  const [hourText, minuteText] = String(value || '00:00').split(':');
  const hour = Number(hourText);
  const suffix = hour >= 12 ? 'PM' : 'AM';
  const shown = hour % 12 || 12;
  return `${shown}:${String(minuteText || '00').padStart(2, '0')} ${suffix}`;
}

export function listenDate(entry) {
  return `${dateLabel(entry.date)} · ${timeLabel(entry.time)}`;
}

function durationLabel(minutes) {
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (!hours) return `${rest}m`;
  return rest ? `${hours}h ${rest}m` : `${hours}h`;
}

function actionGroup(title, items) {
  return { title, items, ordering: null, clickable: true };
}

function countLabel(count, singular, plural) {
  return count === 1 ? `1 ${singular}` : `${count} ${plural}`;
}

export function MusicTabs({ tabs, active, onChange }) {
  return <View style={styles.tabs} accessibilityRole="tablist">
    {tabs.map(tab => {
      const selected = tab.key === active;
      return <Pressable key={tab.key}
        accessibilityRole="tab"
        accessibilityState={{ selected }}
        accessibilityLabel={tab.label}
        onPress={() => onChange(tab.key)}
        style={({ pressed }) => [styles.tab, selected && styles.tabActive, pressed && styles.tabPressed]}>
        <Text style={[styles.tabLabel, selected && styles.tabLabelActive]}>{tab.label}</Text>
      </Pressable>;
    })}
  </View>;
}

export function FilterChips({ options, value, onChange, label }) {
  return <View role="radiogroup" accessibilityLabel={label} style={styles.chipRow}>
    {options.map(option => {
      const selected = option.value === value;
      return <Pressable key={option.value}
        accessibilityRole="radio"
        accessibilityState={{ selected }}
        accessibilityLabel={option.count == null ? option.label : `${option.label}, ${option.count}`}
        onPress={() => onChange(option.value)}
        style={({ pressed }) => [styles.filterChip, selected && styles.filterChipActive, pressed && styles.filterChipPressed]}>
        <Text style={[styles.filterChipText, selected && styles.filterChipTextActive]}>
          {option.label}{option.count == null ? '' : ` ${option.count}`}
        </Text>
      </Pressable>;
    })}
  </View>;
}

export function SectionHeading({ title, meta, copy }) {
  return <View style={styles.sectionHeading}>
    <View style={styles.sectionTitleRow}>
      <Text role="heading" aria-level={2} style={styles.sectionTitle}>{title}</Text>
      {meta ? <Text style={styles.sectionMeta}>{meta}</Text> : null}
    </View>
    {copy ? <Text style={styles.sectionCopy}>{copy}</Text> : null}
  </View>;
}

export function StatCard({ label, value }) {
  return <View style={styles.statCard}>
    <Text style={styles.statLabel}>{label}</Text>
    <Text style={styles.statValue}>{value}</Text>
  </View>;
}

export function FactRow({ label, value }) {
  return <View style={styles.factRow}>
    <Text style={styles.factLabel}>{label}</Text>
    <Text style={styles.factValue}>{value}</Text>
  </View>;
}

export function ScoreChip({ value, onSelect, scaleMax = 10, label = 'Your score' }) {
  const [open, setOpen] = useState(false);
  return <RatingChip
    value={value}
    scaleMax={scaleMax}
    displayValue={value == null ? null : String(value)}
    cardDisplayValue={value == null ? null : `${value}`}
    layout="pill"
    labels={{ yourScore: label, rate: 'Rate', close: 'Close', addRating: 'Add rating', editRating: 'Edit rating' }}
    renderStar={star}
    palette={ratingPalette}
    open={open}
    onOpenChange={setOpen}
    onSelect={onSelect} />;
}

export function MusicBrainzScore({ rating, votes }) {
  if (rating == null) return null;
  return <View style={styles.mbScore}>
    <View style={styles.mbBadge}><Text style={styles.mbBadgeText}>MB</Text></View>
    <Text style={styles.mbValue}>{Number(rating).toFixed(1)}</Text>
    <Text style={styles.mbDivider}>|</Text>
    <Text style={styles.mbVotes}>{countLabel(votes, 'vote', 'votes')}</Text>
  </View>;
}

export function CoverArt({ image, title, shape = 'square', width = 200 }) {
  const [failed, setFailed] = useState(false);
  const aspect = shape === 'poster' ? 2 / 3 : shape === 'landscape' ? 16 / 9 : 1;
  useEffect(() => { setFailed(false); }, [image]);
  return <View style={[styles.cover, { width, aspectRatio: aspect }]}>
    {image && !failed
      ? <Image source={{ uri: image }} style={StyleSheet.absoluteFill} resizeMode="cover"
        accessible={false} accessibilityElementsHidden importantForAccessibility="no-hide-descendants"
        onError={() => setFailed(true)} />
      : <View style={styles.coverFallback}>
        <Text style={styles.coverGlyph}>♪</Text>
        <Text numberOfLines={2} style={styles.coverFallbackText}>{title}</Text>
      </View>}
  </View>;
}

export function MusicHero({ image, title, shape = 'square', eyebrow, subtitle, children }) {
  const { width } = useWindowDimensions();
  const stacked = width < 640;
  return <View style={[styles.hero, stacked && styles.heroStacked]}>
    <CoverArt image={image} title={title} shape={shape} width={stacked ? 168 : 208} />
    <View style={styles.heroBody}>
      {eyebrow ? <Text style={styles.heroEyebrow}>{eyebrow}</Text> : null}
      <Text role="heading" aria-level={1} style={[styles.heroTitle, stacked && styles.heroTitleStacked]}>{title}</Text>
      {subtitle ? <Text style={styles.heroSubtitle}>{subtitle}</Text> : null}
      {children}
    </View>
  </View>;
}

export function BadgeRow({ labels, status }) {
  return <View style={styles.badgeRow}>
    {status ? <Badge label={status} size="sm" tone={status} palette={statusPalette} /> : null}
    {labels.map(label => <Badge key={label} label={label} size="tag" tone="outline" palette={outlinePalette} />)}
  </View>;
}

export function PrimaryActions({ children }) {
  return <View style={styles.primaryActions}>{children}</View>;
}

export function MusicCardGrid({ items, renderItem, minWidth = 110, maxWidth = 178 }) {
  const { width } = useWindowDimensions();
  const columns = width >= 900 ? 6 : width >= 720 ? 4 : width >= 520 ? 3 : 2;
  const cardWidth = Math.floor(Math.min(maxWidth,
    Math.max(minWidth, (width - spacing.lg * 2 - (columns - 1) * spacing.md) / columns)));
  return <View style={styles.cardGrid}>
    {items.map((item, index) => renderItem(item, cardWidth, index))}
  </View>;
}

function MusicRatingPicker({ value, label, scaleMax, palette: colors, onSelect }) {
  return <RatingStars scaleMax={scaleMax} value={value} label={label} onSelect={onSelect}
    palette={colors} renderStar={lit => <Text style={[styles.star, {
      color: lit ? colors.lit : colors.unlit,
    }]}>★</Text>} />;
}


function trackActions() {
  return actionGroup('Track actions', [
    { kind: 'action', key: 'listen', title: 'Add listen', icon: actionIcons.listAdd, tone: 'indigo' },
    { kind: 'action', key: 'notes', title: 'Track notes', icon: actionIcons.edit, tone: 'surface' },
    { kind: 'action', key: 'history', title: 'View your activity history', icon: actionIcons.history, tone: 'amber' },
  ]);
}

function episodeActions() {
  return actionGroup('Episode actions', [
    { kind: 'action', key: 'play', title: 'Add play', icon: actionIcons.listAdd, tone: 'indigo' },
    { kind: 'action', key: 'notes', title: 'Episode notes', icon: actionIcons.edit, tone: 'surface' },
    { kind: 'action', key: 'history', title: 'View your activity history', icon: actionIcons.history, tone: 'amber' },
  ]);
}

export function TrackRow({ track, onOpen, onAction, onRate }) {
  const listens = track.listens.length;
  const last = track.listens[0];
  const model = {
    layout: 'row',
    mediaType: 'music',
    title: track.title,
    image: { src: track.image || '', fallback: '', source: 'primary' },
    altTitle: null,
    statusChip: null,
    lines: [
      {
        join: 'dot',
        parts: [
          { text: `Track ${track.number}${track.disc > 1 ? ` (Disc ${track.disc})` : ''}` },
          { text: track.duration, muted: true },
        ],
      },
      {
        join: 'dot',
        parts: listens
          ? [
            { text: last ? `Last listened ${dateLabel(last.date)}` : 'Listened', muted: true },
            { text: countLabel(listens, 'listen', 'listens'), muted: true },
            ...(track.codec ? [{ text: `Collected: ${track.codec}`, muted: true }] : []),
          ]
          : [
            { text: 'Not listened yet', muted: true },
            ...(track.codec ? [{ text: `Collected: ${track.codec}`, muted: true }] : []),
          ],
      },
    ],
    rating: {
      label: track.score == null ? null : String(track.score),
      value: track.score,
      scaleMax: 10,
      rateLabel: 'Rate',
    },
    ratingEditable: true,
    actions: trackActions(),
  };
  return <MediaCard model={model}
    labels={{ rate: 'Rate', close: 'Close' }}
    palette={mediaCardPalette}
    onOpen={() => onOpen?.(track)}
    onAction={action => onAction?.(action.key, track)}
    onRate={score => onRate?.(track, score)}
    renderRating={MusicRatingPicker} />;
}

export function EpisodeRow({ episode, showImage, onOpen, onAction, onRate }) {
  const state = episode.inProgress ? 'In progress' : episode.plays.length ? 'Played' : 'Unplayed';
  const last = episode.plays[0];
  const model = {
    layout: 'row',
    mediaType: 'podcast',
    title: episode.title,
    image: { src: showImage, fallback: '', source: 'primary' },
    altTitle: null,
    statusChip: null,
    lines: [
      {
        join: 'dot',
        parts: [
          { text: `Episode ${episode.number}` },
          { text: dateLabel(episode.published), muted: true },
          { text: episode.duration, muted: true },
        ],
      },
      {
        join: 'dot',
        parts: [
          { text: state, muted: true },
          ...(episode.plays.length
            ? [{ text: countLabel(episode.plays.length, 'play', 'plays'), muted: true }]
            : []),
          ...(last ? [{ text: `Last played ${dateLabel(last.date)}`, muted: true }] : []),
          ...(episode.inProgress && last?.progress ? [{ text: last.progress, muted: true }] : []),
        ],
      },
    ],
    rating: {
      label: episode.score == null ? null : String(episode.score),
      value: episode.score,
      scaleMax: 10,
      rateLabel: 'Rate',
    },
    ratingEditable: true,
    actions: episodeActions(),
  };
  return <MediaCard model={model}
    labels={{ rate: 'Rate', close: 'Close' }}
    palette={mediaCardPalette}
    onOpen={() => onOpen?.(episode)}
    onAction={action => onAction?.(action.key, episode)}
    onRate={score => onRate?.(episode, score)}
    renderRating={MusicRatingPicker} />;
}

export function ArtistPage({ artist, albums, onOpenAlbum, onTrack, onHistory, onRate, onSyncMembers }) {
  return <View style={styles.page}>
    <MusicHero image={artist.image} title={artist.name} shape="poster"
      eyebrow={artist.type}
      subtitle={`${artist.origin} · Formed ${artist.formed}`}>
      <BadgeRow labels={artist.genres} status={artist.status} />
      <View style={styles.scoreRow}>
        <MusicBrainzScore rating={artist.musicbrainz.rating} votes={artist.musicbrainz.votes} />
        <ScoreChip value={artist.score} onSelect={onRate} />
      </View>
      <PrimaryActions>
        <Button label={artist.status || 'Add to Library'} onPress={onTrack} />
        <Button label="Activity history" tone="surface" onPress={onHistory} />
        {onSyncMembers ? <Button label="Sync members" tone="surface" onPress={onSyncMembers} /> : null}
      </PrimaryActions>
      <Text style={styles.bio}>{artist.bio}</Text>
    </MusicHero>

    <View style={styles.panel}>
      <SectionHeading title="Details" />
      <FactRow label="Type" value={artist.type} />
      <FactRow label="Origin" value={artist.origin} />
      <FactRow label="Formed" value={artist.formed} />
      <FactRow label="Releases" value={String(albums.length)} />
      <FactRow label="Plays" value={String(artist.totalPlays)} />
      <FactRow label="Collected albums" value={`${artist.collection.albums} / ${artist.collection.totalAlbums}`} />
      <FactRow label="Collected tracks" value={`${artist.collection.tracks} / ${artist.collection.totalTracks}`} />
    </View>

    {artist.relations.members.length
      ? <View style={styles.section}>
        <SectionHeading title="Members" meta={String(artist.relations.members.length)} />
        <MusicCardGrid items={artist.relations.members}
          renderItem={(relation, width) => <MusicRelationCard key={relation.id} relation={relation} width={width} />} />
      </View>
      : null}

    {artist.relations.alsoIn.length
      ? <View style={styles.section}>
        <SectionHeading title="Also In" meta={String(artist.relations.alsoIn.length)} />
        <MusicCardGrid items={artist.relations.alsoIn}
          renderItem={(relation, width) => <MusicRelationCard key={relation.id} relation={relation} width={width} />} />
      </View>
      : null}

    <View style={styles.section}>
      <SectionHeading title="Discography" meta={countLabel(albums.length, 'release', 'releases')} />
      {artist.discography.map(group => <View key={group.label} style={styles.group}>
        <View style={styles.groupHeading}>
          <Text style={styles.groupTitle}>{group.label}</Text>
          <Text style={styles.groupCount}>{countLabel(group.albums.length, 'release', 'releases')}</Text>
        </View>
        <MusicAlbumGrid albums={group.albums}
          onOpen={album => onOpenAlbum(album)} />
      </View>)}
    </View>
  </View>;
}

export function AlbumPage({ album, onBack, onOpenTrack, onTrack, onHistory, onRate, onAction, onTrackRate, onSyncMetadata }) {
  return <View style={styles.page}>
    <View style={styles.inlineNav}>
      <Button label="Back to artist" tone="surface" onPress={onBack} />
    </View>
    <MusicHero image={album.image} title={album.title} shape="square"
      eyebrow={album.releaseType}
      subtitle={`Mira Vale · ${album.year}`}>
      <BadgeRow labels={album.genres} status={null} />
      <View style={styles.scoreRow}>
        <ScoreChip value={album.score} onSelect={onRate} />
        <Badge label={album.format} size="sm" tone="outline" palette={outlinePalette} />
      </View>
      <PrimaryActions>
        <Button label={album.status || 'Add to Library'} onPress={onTrack} />
        <Button label="Activity history" tone="surface" onPress={onHistory} />
        {onSyncMetadata ? <Button label="Sync metadata" tone="surface" onPress={onSyncMetadata} /> : null}
      </PrimaryActions>
    </MusicHero>

    <View style={styles.panel}>
      <SectionHeading title="Details" />
      <FactRow label="Format" value={album.format} />
      <FactRow label="Release date" value={dateLabel(album.releaseDate)} />
      <FactRow label="Tracks" value={String(album.tracks.length)} />
      <FactRow label="Total runtime" value={album.runtime} />
      <FactRow label="Plays" value={String(album.plays)} />
    </View>

    <View style={styles.section}>
      <SectionHeading title="Tracks" meta={countLabel(album.tracks.length, 'track', 'tracks')}
        copy="Tap a track for its notes, score and play history. The action row records another listen and opens notes or history." />
      <View style={styles.rowList}>
        {album.tracks.map(track => <TrackRow key={track.id} track={{ ...track, image: album.image }}
          onOpen={() => onOpenTrack(track)}
          onAction={(key, target) => onAction(key, album, target)}
          onRate={onTrackRate} />)}
      </View>
    </View>
  </View>;
}

export function TrackPage({ album, track, onBack, onRate, onAddListen, onOpenNotes, onSaveNotes, onDeleteListen, onOpenHistory, Preview }) {
  const [scoreOpen, setScoreOpen] = useState(false);
  const [notes, setNotes] = useState(track.notes);
  const [saved, setSaved] = useState('');
  useEffect(() => {
    setNotes(track.notes);
  }, [track.id, track.notes]);
  useEffect(() => {
    setSaved('');
  }, [track.id]);
  const listens = track.listens;
  return <View style={styles.page}>
    <View style={styles.inlineNav}>
      <Button label="Back to album" tone="surface" onPress={onBack} />
    </View>
    <MusicHero image={album.image} title={track.title} shape="square"
      eyebrow={`Track ${track.number} · ${album.title}`}
      subtitle={`Mira Vale · ${track.duration}`}>
      <BadgeRow labels={[track.codec || 'Not collected', `Track ${track.number}`, track.duration]} status={null} />
      <View style={styles.scoreRow}>
        <RatingChip value={track.score} scaleMax={10}
          displayValue={track.score == null ? null : String(track.score)}
          cardDisplayValue={track.score == null ? null : String(track.score)}
          labels={{ yourScore: 'Your score', rate: 'Rate', close: 'Close', addRating: 'Add rating', editRating: 'Edit rating' }}
          renderStar={star} palette={ratingPalette} open={scoreOpen} onOpenChange={setScoreOpen}
          onSelect={onRate} />
        <Text style={styles.metaText}>{countLabel(listens.length, 'listen', 'listens')}</Text>
      </View>
      <PrimaryActions>
        <Button label="Add listen" onPress={onAddListen} />
        <Button label="Notes" tone="surface" onPress={onOpenNotes} />
        <Button label="Activity history" tone="surface" onPress={onOpenHistory} />
      </PrimaryActions>
    </MusicHero>

    <View style={styles.panel}>
      <SectionHeading title="Notes" copy="Private notes stay attached to this track and never create a listen." />
      <MarkdownNotes
        value={notes}
        onChangeText={value => { setNotes(value); setSaved(''); }}
        label="Track notes"
        labelHidden
        hint="Markdown supported"
        placeholder="What do you want to remember about this track?"
        accessibilityLabel="Track notes"
        labels={markdownLabels}
        palette={markdownPalette}
        Preview={Preview} />
      <View style={styles.inlineEnd}>
        {saved ? <Text style={styles.savedText}>{saved}</Text> : null}
        <Button label="Save notes" onPress={() => { onSaveNotes(notes); setSaved('Notes saved'); }} />
      </View>
    </View>

    <View style={styles.section}>
      <SectionHeading title="Play history" meta={countLabel(listens.length, 'listen', 'listens')} />
      {listens.length ? <View style={styles.rowList}>
        {listens.map((entry, index) => <View key={entry.id} style={styles.historyRow}>
          <View style={styles.historyCopy}>
            <Text style={styles.historyDate}>{listenDate(entry)}</Text>
            <Text style={styles.historyMeta}>{index === 0 ? 'Latest listen' : 'Repeat listen'}</Text>
          </View>
          <Button label="Delete" tone="ghost-danger" onPress={() => onDeleteListen(entry.id)} />
        </View>)}
      </View> : <View style={styles.emptyPanel}>
        <Text style={styles.emptyText}>Not listened yet</Text>
      </View>}
    </View>
  </View>;
}

export function ShowPage({ show, episodes, episodeFilter, onFilter, visibleCount, onLoadMore,
  onTrack, onHistory, onRate, onAction, onEpisodeRate }) {
  const listened = episodes.filter(episode => !episode.inProgress && episode.plays.length > 0).length;
  const inProgress = episodes.filter(episode => episode.inProgress).length;
  const unplayed = episodes.filter(episode => !episode.plays.length).length;
  const minutes = episodes.reduce((total, episode) => {
    const entry = episode.plays[0];
    if (!entry) return total;
    return total + (entry.minutes || episode.minutes);
  }, 0);
  const filtered = episodes.filter(episode => {
    if (episodeFilter === 'played') return !episode.inProgress && episode.plays.length > 0;
    if (episodeFilter === 'in_progress') return episode.inProgress;
    if (episodeFilter === 'unplayed') return !episode.plays.length;
    return true;
  });
  const visible = filtered.slice(0, visibleCount);
  const percent = episodes.length ? Math.round((listened / episodes.length) * 100) : 0;
  const filterOptions = [
    { value: 'all', label: 'All', count: episodes.length },
    { value: 'unplayed', label: 'Unplayed', count: unplayed },
    { value: 'in_progress', label: 'In progress', count: inProgress },
    { value: 'played', label: 'Played', count: listened },
  ];
  return <View style={styles.page}>
    <MusicHero image={show.image} title={show.title} shape="square"
      eyebrow="Podcast"
      subtitle={`by ${show.author}`}>
      <BadgeRow labels={show.genres} status={show.status} />
      <View style={styles.scoreRow}>
        <ScoreChip value={show.score} onSelect={onRate} />
        <Badge label={`${episodes.length} episodes`} size="sm" tone="outline" palette={outlinePalette} />
      </View>
      <PrimaryActions>
        <Button label={show.status || 'Add to Library'} onPress={onTrack} />
        <Button label="Activity history" tone="surface" onPress={onHistory} />
      </PrimaryActions>
      <Text style={styles.bio}>{show.description}</Text>
    </MusicHero>

    <View style={styles.statsRow}>
      <StatCard label="Episodes" value={String(episodes.length)} />
      <StatCard label="Listened" value={String(listened)} />
      <StatCard label="In progress" value={String(inProgress)} />
      <StatCard label="Time" value={durationLabel(minutes)} />
    </View>

    <View style={styles.progressPanel}>
      <View style={styles.progressHeader}>
        <Text style={styles.progressLabel}>Show progress</Text>
        <Text style={styles.progressValue}>{percent}%</Text>
      </View>
      <ProgressBar percent={percent} height={8} palette={progressDetailPalette} label={`${percent}% of episodes played`} />
      <Text style={styles.progressCopy}>{listened} of {episodes.length} episodes played · {unplayed} unplayed</Text>
    </View>

    <View style={styles.section}>
      <SectionHeading title="Episodes" meta={`${Math.min(visibleCount, filtered.length)} of ${filtered.length}`}
        copy="Filter the feed, load older pages and record repeats. Each play is dated and kept in the episode history." />
      <FilterChips label="Filter episodes" options={filterOptions} value={episodeFilter} onChange={onFilter} />
      <View style={styles.rowList}>
        {visible.map(episode => <EpisodeRow key={episode.id} episode={episode} showImage={show.image}
          onOpen={() => onAction('play', episode)}
          onAction={(key, target) => onAction(key, target)}
          onRate={onEpisodeRate} />)}
      </View>
      {!visible.length ? <View style={styles.emptyPanel}>
        <Text style={styles.emptyText}>No episodes match this filter.</Text>
      </View> : null}
      {visible.length < filtered.length
        ? <View style={styles.loadMore}>
          <Button label={`Load more (${filtered.length - visible.length} left)`} tone="surface" onPress={onLoadMore} />
        </View>
        : null}
    </View>
  </View>;
}

export function SheetModal({ open, title, subtitle, children, onClose }) {
  return <Modal visible={open} transparent animationType="fade" onRequestClose={onClose}>
    <View style={styles.modalRoot}>
      <Pressable accessibilityRole="button" accessibilityLabel="Close" onPress={onClose}
        style={[StyleSheet.absoluteFill, styles.modalBackdrop]} />
      <View accessibilityViewIsModal style={styles.modalSheet}>
        <View style={styles.modalHeader}>
          <View style={styles.modalHeading}>
            <Text role="heading" aria-level={2} style={styles.modalTitle}>{title}</Text>
            {subtitle ? <Text style={styles.modalSubtitle}>{subtitle}</Text> : null}
          </View>
          <Pressable accessibilityRole="button" accessibilityLabel="Close" onPress={onClose} style={styles.modalClose}>
            <Text style={styles.modalCloseText}>×</Text>
          </Pressable>
        </View>
        <ScrollView contentContainerStyle={styles.modalBody} keyboardShouldPersistTaps="handled">
          {children}
        </ScrollView>
      </View>
    </View>
  </Modal>;
}

export function DateEntryModal({ open, title, subtitle, value, onChange, onClose, onConfirm, confirmLabel = 'Save', suggestionDate = '' }) {
  return <SheetModal open={open} title={title} subtitle={subtitle} onClose={onClose}>
    <DateTimePicker
      value={value}
      onChange={onChange}
      trackTime={false}
      use12Hour={false}
      weekStart="monday"
      locale="en-US"
      labels={dateTimeLabels}
      quickActions={quickDateActions}
      suggestionDate={suggestionDate}
      suggestionLabel={suggestionDate ? 'Release date' : ''}
      onQuickAction={action => {
        if (action === 'today') onChange('2026-11-04');
        else onChange('2026-11-04');
      }}
      palette={dateTimePalette} />
    <View style={styles.modalActions}>
      <Button label="Cancel" tone="surface" onPress={onClose} />
      <Button label={confirmLabel} onPress={onConfirm} />
    </View>
  </SheetModal>;
}

export function TrackingModal({ open, title, subtitle, status, score, notes, onClose, onSave, Preview }) {
  const [draft, setDraft] = useState({ status, score, notes });
  useEffect(() => {
    if (open) setDraft({ status, score, notes });
  }, [open, status, score, notes]);
  return <SheetModal open={open} title={title} subtitle={subtitle} onClose={onClose}>
    <Text style={styles.fieldLabel}>Status</Text>
    <View style={styles.statusGrid}>
      {STATUS_OPTIONS.map(option => {
        const selected = draft.status === option;
        return <Pressable key={option}
          accessibilityRole="radio"
          accessibilityState={{ selected }}
          accessibilityLabel={option}
          onPress={() => setDraft(current => ({ ...current, status: option }))}
          style={({ pressed }) => [styles.statusOption, selected && {
            borderColor: STATUS_COLORS[option],
            backgroundColor: `${STATUS_COLORS[option]}22`,
          }, pressed && styles.filterChipPressed]}>
          <View style={[styles.statusDot, { backgroundColor: STATUS_COLORS[option] }]} />
          <Text style={[styles.statusOptionText, selected && { color: palette.text }]}>{option}</Text>
        </Pressable>;
      })}
    </View>
    <Text style={styles.fieldLabel}>Your score</Text>
    <RatingStars scaleMax={10} value={draft.score} label="Your score"
      palette={ratingPalette} renderStar={lit => star(lit, 28)}
      onSelect={value => setDraft(current => ({ ...current, score: value }))} />
    <Text style={styles.fieldLabel}>Notes</Text>
    <MarkdownNotes value={draft.notes}
      onChangeText={value => setDraft(current => ({ ...current, notes: value }))}
      label="Tracking notes"
      labelHidden
      hint="Markdown supported"
      placeholder="Add a private note"
      accessibilityLabel="Tracking notes"
      labels={markdownLabels}
      palette={markdownPalette}
      Preview={Preview} />
    <View style={styles.modalActions}>
      <Button label="Cancel" tone="surface" onPress={onClose} />
      <Button label="Save" onPress={() => onSave(draft)} />
    </View>
  </SheetModal>;
}

export function NotesModal({ open, title, subtitle, value, error, onClose, onSave, Preview }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => { if (open) setDraft(value); }, [open, value]);
  return <SheetModal open={open} title={title} subtitle={subtitle} onClose={onClose}>
    <MarkdownNotes
      value={draft}
      onChangeText={setDraft}
      label="Notes"
      hint="Markdown supported"
      placeholder="What do you want to remember?"
      error={error}
      labels={markdownLabels}
      palette={markdownPalette}
      Preview={Preview} />
    <View style={styles.modalActions}>
      <Button label="Cancel" tone="surface" onPress={onClose} />
      <Button label="Save notes" onPress={() => onSave(draft)} />
    </View>
  </SheetModal>;
}

export const musicLabels = {
  loading: 'Loading music…',
  retry: 'Try again',
  error: 'The music detail could not be loaded.',
  close: 'Close',
};

function StateNotice({ message, tone, palette: colors, actionLabel, onAction }) {
  return <View style={[styles.stateNotice, {
    borderColor: tone === 'error' ? 'rgba(248, 113, 113, 0.4)' : 'rgba(16, 185, 129, 0.4)',
    backgroundColor: tone === 'error' ? 'rgba(248, 113, 113, 0.1)' : 'rgba(16, 185, 129, 0.1)',
  }]}>
    <Text accessibilityLiveRegion="polite" style={[styles.stateNoticeText, {
      color: tone === 'error' ? (colors.danger || '#f87171') : (colors.success || '#10b981'),
    }]}>{message}</Text>
    {onAction ? <Button label={actionLabel} tone="surface" palette={colors} onPress={onAction} /> : null}
  </View>;
}

// One music/podcast page composition for web and native. `part` mounts the
// shared album or relation grid used by the server islands; the full
// composition owns the tab order and the loading/error/notice states while the
// artist, album, track and show sections keep their shared order in this file.
export function MusicView({
  part = 'full',
  tabs = [],
  active,
  onChange,
  albums = [],
  relations = [],
  onOpenAlbum,
  onOpenRelation,
  labels = {},
  palette: colors = {},
  loading = false,
  error = '',
  notice = '',
  onRetry,
  children,
}) {
  const copy = { ...musicLabels, ...(labels || {}) };
  if (part === 'albums') {
    return <MusicAlbumGrid albums={albums} labels={labels} palette={colors} onOpen={onOpenAlbum} />;
  }
  if (part === 'relations') {
    return <MusicRelationGrid relations={relations} labels={labels} palette={colors} onOpen={onOpenRelation} />;
  }
  return <View style={styles.page}>
    {error ? <StateNotice message={error || copy.error} tone="error" palette={colors}
      actionLabel={copy.retry} onAction={onRetry} /> : null}
    {notice ? <StateNotice message={notice} tone="success" palette={colors} /> : null}
    {tabs.length ? <MusicTabs tabs={tabs} active={active} onChange={onChange} /> : null}
    {loading ? <View accessibilityLiveRegion="polite" style={styles.stateLoading}>
      <ActivityIndicator color={colors.accent || '#6366f1'} />
      <Text style={[styles.stateLoadingText, { color: colors.textMuted || '#a1a1aa' }]}>{copy.loading}</Text>
    </View> : children}
  </View>;
}

const styles = StyleSheet.create({
  page: { gap: spacing.xl },
  stateNotice: { borderWidth: 1, borderRadius: radius.sm, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, gap: spacing.sm, alignItems: 'flex-start' },
  stateNoticeText: { fontSize: 13, lineHeight: 18 },
  stateLoading: { alignItems: 'center', justifyContent: 'center', gap: spacing.sm, paddingVertical: spacing.xl },
  stateLoadingText: { fontSize: 13 },
  tabs: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.xs, borderBottomWidth: 1, borderBottomColor: palette.border, paddingBottom: spacing.sm },
  tab: { paddingHorizontal: spacing.md, paddingVertical: spacing.sm, borderRadius: radius.sm },
  tabActive: { backgroundColor: palette.surfaceStrong },
  tabPressed: { opacity: 0.8 },
  tabLabel: { color: palette.textMuted, fontSize: 14, fontWeight: '600' },
  tabLabelActive: { color: palette.text },
  chipRow: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  filterChip: { borderWidth: 1, borderColor: palette.border, borderRadius: radius.pill, paddingHorizontal: spacing.md, paddingVertical: 6 },
  filterChipActive: { borderColor: palette.accent, backgroundColor: 'rgba(99, 102, 241, 0.16)' },
  filterChipPressed: { opacity: 0.75 },
  filterChipText: { color: palette.textMuted, fontSize: 12, fontWeight: '600' },
  filterChipTextActive: { color: palette.text },
  sectionHeading: { gap: 2 },
  sectionTitleRow: { flexDirection: 'row', alignItems: 'baseline', gap: spacing.sm },
  sectionTitle: { color: palette.text, fontSize: 18, fontWeight: '700' },
  sectionMeta: { color: palette.textMuted, fontSize: 12, fontWeight: '600' },
  sectionCopy: { color: palette.textMuted, fontSize: 12, lineHeight: 17 },
  statCard: { flexGrow: 1, minWidth: 92, borderRadius: radius.md, borderWidth: 1, borderColor: palette.border, backgroundColor: palette.surface, padding: spacing.md, gap: 4 },
  statLabel: { color: palette.textMuted, fontSize: 10, fontWeight: '700', letterSpacing: 0.8, textTransform: 'uppercase' },
  statValue: { color: palette.text, fontSize: 20, fontWeight: '700' },
  factRow: { flexDirection: 'row', justifyContent: 'space-between', gap: spacing.md, borderBottomWidth: 1, borderBottomColor: palette.border, paddingVertical: spacing.sm },
  factLabel: { color: palette.textMuted, fontSize: 12, fontWeight: '600' },
  factValue: { color: palette.textSecondary, fontSize: 12, flexShrink: 1, textAlign: 'right' },
  star: { fontWeight: '600' },
  mbScore: { flexDirection: 'row', alignItems: 'center', gap: 6, borderRadius: radius.pill, borderWidth: 1, borderColor: 'rgba(244, 63, 94, 0.22)', backgroundColor: 'rgba(244, 63, 94, 0.07)', paddingRight: spacing.md },
  mbBadge: { width: 30, height: 30, borderRadius: 15, alignItems: 'center', justifyContent: 'center', borderWidth: 1, borderColor: 'rgba(244, 63, 94, 0.3)', backgroundColor: 'rgba(244, 63, 94, 0.14)' },
  mbBadgeText: { color: '#ffe4e6', fontSize: 9, fontWeight: '800', letterSpacing: 0.6 },
  mbValue: { color: palette.text, fontSize: 13, fontWeight: '700' },
  mbDivider: { color: palette.textMuted, fontSize: 13 },
  mbVotes: { color: palette.textSecondary, fontSize: 12 },
  cover: { borderRadius: radius.md, overflow: 'hidden', backgroundColor: palette.surfaceStrong },
  coverFallback: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 6, padding: spacing.md },
  coverGlyph: { color: palette.textMuted, fontSize: 36 },
  coverFallbackText: { color: palette.textMuted, fontSize: 12, textAlign: 'center' },
  hero: { flexDirection: 'row', gap: spacing.lg, alignItems: 'flex-start' },
  heroStacked: { flexDirection: 'column', alignItems: 'center' },
  heroBody: { flex: 1, minWidth: 0, gap: spacing.sm },
  heroEyebrow: { color: palette.accent, fontSize: 11, fontWeight: '700', letterSpacing: 0.8, textTransform: 'uppercase' },
  heroTitle: { color: palette.text, fontSize: 28, fontWeight: '700' },
  heroTitleStacked: { textAlign: 'center' },
  heroSubtitle: { color: palette.textMuted, fontSize: 13 },
  badgeRow: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, alignItems: 'center' },
  scoreRow: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, alignItems: 'center' },
  primaryActions: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  bio: { color: palette.textSecondary, fontSize: 13, lineHeight: 20 },
  panel: { borderWidth: 1, borderColor: palette.border, borderRadius: radius.md, backgroundColor: palette.surface, padding: spacing.lg, gap: spacing.xs },
  section: { gap: spacing.md },
  group: { gap: spacing.sm },
  groupHeading: { flexDirection: 'row', alignItems: 'baseline', justifyContent: 'space-between', gap: spacing.sm },
  groupTitle: { color: palette.textSecondary, fontSize: 15, fontWeight: '600' },
  groupCount: { color: palette.textMuted, fontSize: 11 },
  cardGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.md },
  rowList: { gap: spacing.md },
  inlineNav: { flexDirection: 'row', justifyContent: 'flex-start' },
  inlineEnd: { flexDirection: 'row', alignItems: 'center', justifyContent: 'flex-end', gap: spacing.md },
  metaText: { color: palette.textMuted, fontSize: 12 },
  savedText: { color: palette.success, fontSize: 12 },
  historyRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: spacing.md, borderWidth: 1, borderColor: palette.border, borderRadius: radius.md, backgroundColor: palette.surface, padding: spacing.md },
  historyCopy: { gap: 2, flexShrink: 1 },
  historyDate: { color: palette.text, fontSize: 13, fontWeight: '600' },
  historyMeta: { color: palette.textMuted, fontSize: 11 },
  emptyPanel: { borderWidth: 1, borderColor: palette.border, borderRadius: radius.md, backgroundColor: palette.surface, padding: spacing.lg, alignItems: 'center' },
  emptyText: { color: palette.textMuted, fontSize: 13 },
  statsRow: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  progressPanel: { borderWidth: 1, borderColor: palette.border, borderRadius: radius.md, backgroundColor: palette.surface, padding: spacing.lg, gap: spacing.sm },
  progressHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: spacing.md },
  progressLabel: { color: palette.text, fontSize: 14, fontWeight: '600' },
  progressValue: { color: palette.text, fontSize: 14, fontWeight: '700' },
  progressCopy: { color: palette.textMuted, fontSize: 12 },
  loadMore: { flexDirection: 'row', justifyContent: 'center', paddingTop: spacing.sm },
  modalRoot: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: spacing.lg },
  modalBackdrop: { backgroundColor: 'rgba(0, 0, 0, 0.6)' },
  modalSheet: { width: '100%', maxWidth: 560, maxHeight: '88%', borderRadius: radius.lg, borderWidth: 1, borderColor: palette.border, backgroundColor: palette.background, overflow: 'hidden' },
  modalHeader: { flexDirection: 'row', alignItems: 'flex-start', gap: spacing.md, borderBottomWidth: 1, borderBottomColor: palette.border, paddingHorizontal: spacing.lg, paddingVertical: spacing.md },
  modalHeading: { flex: 1, gap: 2 },
  modalTitle: { color: palette.text, fontSize: 17, fontWeight: '700' },
  modalSubtitle: { color: palette.textMuted, fontSize: 12 },
  modalClose: { width: 32, height: 32, alignItems: 'center', justifyContent: 'center' },
  modalCloseText: { color: palette.textMuted, fontSize: 22, lineHeight: 24 },
  modalBody: { padding: spacing.lg, gap: spacing.md },
  modalActions: { flexDirection: 'row', justifyContent: 'flex-end', flexWrap: 'wrap', gap: spacing.sm },
  fieldLabel: { color: palette.textSecondary, fontSize: 12, fontWeight: '700', letterSpacing: 0.4, textTransform: 'uppercase' },
  statusGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm },
  statusOption: { flexDirection: 'row', alignItems: 'center', gap: 6, borderWidth: 1, borderColor: palette.border, borderRadius: radius.pill, paddingHorizontal: spacing.md, paddingVertical: 6 },
  statusDot: { width: 8, height: 8, borderRadius: 4 },
  statusOptionText: { color: palette.textMuted, fontSize: 12, fontWeight: '600' },
});
