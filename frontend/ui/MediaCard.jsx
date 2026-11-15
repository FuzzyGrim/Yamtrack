import { Modal } from './Modal';
import React, { useEffect, useRef, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { ArtworkImage } from './ArtworkImage';
import { CardScrim } from './CardScrim';
import { Icon } from './Icon';
import { STATUS_ICON_SPECS } from './statusChipIcons';
import { MediaCardActionRow, MediaCardActions } from './MediaCardActions';
import { shouldRevealMediaCardActions, shouldShowMediaCardCorner, shouldShowMediaCardLine } from './mediaCardBehavior';
import { actionIcons } from './actionIcons';

const ASPECT = { poster: 2 / 3, square: 1, landscape: 16 / 9 };
const DEFAULT_PALETTE = {
  card: '#27272a',
  text: '#fafafa',
  textSecondary: '#d4d4d8',
  textMuted: '#a1a1aa',
  border: '#3f3f46',
  link: '#818cf8',
  star: '#facc15',
  chipBackground: 'rgba(24, 24, 27, 0.92)',
  chipText: '#ffffff',
  placeholder: '#3f3f46',
  progressTrack: 'rgba(24, 24, 27, 0.6)',
  progressFill: '#6366f1',
  progressPaused: 'rgba(99, 102, 241, 0.4)',
  actionText: '#ffffff',
  overlay: 'rgba(0, 0, 0, 0.5)',
  reorder: 'rgba(79, 70, 229, 0.95)',
  actionTones: {
    indigo: '#4f46e5',
    emerald: '#059669',
    amber: '#d97706',
    rose: '#e11d48',
  },
  local: {
    backgroundColor: 'rgba(245, 158, 11, 0.15)',
    borderColor: 'rgba(245, 158, 11, 0.3)',
    color: '#fde68a',
  },
  statusColors: {
    Completed: '#10b981',
    'In progress': '#6366f1',
    Paused: '#f97316',
    Planning: '#38bdf8',
    Dropped: '#ef4444',
  },
  rating: { lit: '#facc15', unlit: '#52525b', muted: '#a1a1aa', focusStyle: {} },
};

function resolvePalette(palette) {
  return {
    ...DEFAULT_PALETTE,
    ...(palette || {}),
    local: { ...DEFAULT_PALETTE.local, ...(palette?.local || {}) },
    statusColors: { ...DEFAULT_PALETTE.statusColors, ...(palette?.statusColors || {}) },
    rating: { ...DEFAULT_PALETTE.rating, ...(palette?.rating || {}) },
  };
}

function Star({ size = 12, color }) {
  return <Text style={{ fontSize: size, color }}>★</Text>;
}

function Artwork({ model, palette, selection, onPress, onLongPress }) {
  const image = model.image || {};
  const aspectRatio = Number(model.aspectRatio) || ASPECT[model.shape] || ASPECT.poster;
  const frame = <View style={[styles.artworkFrame, { aspectRatio }]}
    accessible={!onPress} accessibilityLabel={onPress ? undefined : model.title}>
    <ArtworkImage src={image.src} fallback={image.fallback} title={model.title}
      source={image.source} variant="media-card" loading="lazy" decoding="sync"
      fallbackOnSmall={Boolean(model.video)} stale={Boolean(image.stale)}
      style={StyleSheet.absoluteFill} palette={palette}>
      {selection?.active ? <Pressable accessibilityRole="checkbox"
        accessibilityLabel={selection.label || model.title}
        accessibilityState={{ checked: !!selection.selected }}
        onPress={event => { event.stopPropagation?.(); selection.toggle?.(); }}
        style={[styles.selection, {
          backgroundColor: selection.selected ? palette.link : 'rgba(0, 0, 0, 0.55)',
          borderColor: palette.chipText,
        }]}>
        {selection.selected ? <Text style={[styles.selectionCheck, { color: palette.chipText }]}>✓</Text> : null}
      </Pressable> : null}
    </ArtworkImage>
  </View>;

  if (!onPress) return frame;
  return <Pressable accessibilityRole="button" accessibilityLabel={model.title} onPress={onPress} onLongPress={onLongPress}>
    {frame}
  </Pressable>;
}

function MediaTypeChip({ chip, palette }) {
  const solid = chip.style === 'solid';
  const soft = chip.style === 'soft';
  const color = solid ? chip.contrast || palette.chipText : chip.color || palette.textSecondary;
  const backgroundColor = solid ? chip.color : soft && chip.color ? `${chip.color}38` : 'transparent';
  return <View style={[styles.chip, styles.statusChip, {
    backgroundColor,
    borderColor: chip.color || palette.border,
    borderWidth: solid ? 0 : 1,
  }]} accessible accessibilityLabel={chip.label}>
    <Text style={[styles.mediaChipText, { color }]}>{chip.label}</Text>
  </View>;
}

function StatusChip({ chip, palette, labels }) {
  if (!chip) return null;
  if (chip.kind === 'no-status') {
    return <View style={[styles.chip, styles.statusChip, { backgroundColor: palette.chipBackground }]}
      accessible accessibilityLabel={chip.label || labels.noStatus}>
      <Text style={[styles.chipText, { color: palette.chipText }]}>{chip.label || labels.noStatus}</Text>
    </View>;
  }
  if (chip.kind === 'event') {
    return <View style={[styles.chip, styles.statusChip, { backgroundColor: palette.chipBackground }]}
      accessible accessibilityLabel={chip.label}>
      <Icon spec={actionIcons.history} size={12} color={palette.chipText} />
      <Text style={[styles.chipText, { color: palette.chipText }]}>{chip.label}</Text>
    </View>;
  }
  const status = chip.status || '';
  const color = palette.statusColors[status] || palette.accent || palette.link;
  return <View style={[styles.chip, styles.statusChip, { backgroundColor: palette.chipBackground }]}
    accessible accessibilityLabel={chip.label || status}>
    {STATUS_ICON_SPECS[status]
      ? <Icon spec={STATUS_ICON_SPECS[status]} color={color} size={16} />
      : chip.icon
        ? <Icon spec={chip.icon} color={color} size={16} />
        : <Text style={[styles.chipGlyph, { color }]}>•</Text>}
  </View>;
}

function CornerChip({ chip, palette, labels, onOpenLink, compact }) {
  const local = chip.kind === 'local-only' || chip.kind === 'collected';
  const position = chip.kind === 'local-only'
    ? styles.bottomLeft
    : chip.kind === 'runtime' || chip.kind === 'collected' ? styles.bottomRight : styles.topRight;
  const text = chip.kind === 'next-episode'
    ? `› ${compact ? (chip.compactText || labels.nextEpisodeCompact || 'Next') : chip.text}`
    : chip.kind === 'queue' ? `${chip.text} ›` : chip.text;
  const label = chip.kind === 'next-episode' ? labels.nextEpisode : chip.title || chip.text;
  const appearance = [styles.cornerChip, position, local ? {
    backgroundColor: palette.local.backgroundColor,
    borderColor: palette.local.borderColor,
    borderWidth: 1,
  } : { backgroundColor: palette.chipBackground }];
  const content = <Text style={[styles.cornerText, { color: local ? palette.local.color : palette.chipText }]}
    numberOfLines={1}>{text}</Text>;
  if (chip.href && onOpenLink) {
    return <Pressable accessibilityRole="link" accessibilityLabel={label}
      onPress={() => onOpenLink(chip.href)} style={appearance}>{content}</Pressable>;
  }
  return <View accessible accessibilityLabel={label} style={appearance}>{content}</View>;
}

function ProgressBar({ bar, palette }) {
  if (!bar || bar.percent == null) return null;
  const percent = Math.max(0, Math.min(100, bar.percent));
  return <View accessible accessibilityRole="progressbar" accessibilityLabel={`${Math.round(percent)}%`}
    accessibilityValue={{ min: 0, max: 100, now: Math.round(percent) }}
    style={[styles.progressTrack, { backgroundColor: palette.progressTrack }]}>
    <View style={[styles.progressFill, {
      width: `${percent}%`,
      backgroundColor: bar.paused ? palette.progressPaused : palette.progressFill,
    }]} />
  </View>;
}

function Line({ line, palette }) {
  if (!line || !line.parts?.length) return null;
  if (line.variant === 'provenance') {
    return <Text accessibilityLabel={line.tooltip} numberOfLines={1}
      style={[styles.provenance, { color: palette.textMuted }]}>{line.parts[0].text}</Text>;
  }
  const plain = line.join === 'plain';
  return <Text accessibilityLabel={line.tooltip} numberOfLines={line.clamp ? 1 : undefined}
    style={[styles.line, { color: palette.textMuted }]}>
    {line.parts.map((part, index) => {
      const separator = !plain && index > 0 ? ' · ' : '';
      const style = part.score
        ? { color: palette.star, fontWeight: '600' }
        : part.link ? { color: palette.link, fontWeight: '500' }
          : part.muted ? { color: palette.textMuted } : null;
      return <Text key={index} style={style}>{separator}{part.score ? <Text style={{ color: palette.star }}>★ </Text> : null}{part.text}</Text>;
    })}
  </Text>;
}

function Title({ model, palette, onOpen, onOpenLink, onLongPress }) {
  if (model.titleLinks?.length) {
    return <View style={styles.titleColumn}>
      {model.titleLinks.map((link, index) => <Pressable key={`${link.url}-${index}`}
        accessibilityRole="link" accessibilityLabel={link.label}
        onPress={onOpenLink ? () => onOpenLink(link.url) : undefined} onLongPress={onLongPress}
        disabled={!onOpenLink} style={styles.titleLinkPress}>
        <Text numberOfLines={1} style={[index === 0 ? styles.title : styles.titleSecondary, {
          color: index === 0 ? palette.text : palette.textMuted,
        }]}>{link.label}</Text>
      </Pressable>)}
    </View>;
  }
  const text = <Text numberOfLines={1} style={[styles.title, { color: palette.text }]}>{model.title}</Text>;
  if (!onOpen) return <View style={styles.titleColumn}>{text}</View>;
  return <Pressable accessibilityRole="link" accessibilityLabel={model.title} onPress={onOpen} onLongPress={onLongPress}
    style={styles.titleColumn}>{text}</Pressable>;
}

function Rating({ model, palette, labels, renderRating, onRate, readOnly }) {
  const rating = model.rating;
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState(rating?.value ?? null);

  useEffect(() => {
    setValue(rating?.value ?? null);
  }, [rating?.value]);

  if (!rating) return null;
  if (readOnly || !model.ratingEditable || !renderRating) {
    if (rating.label == null) return null;
    return <View accessible accessibilityLabel={`${labels.rate}: ${rating.label}`} style={styles.ratingReadOnly}>
      <Star color={palette.star} />
      <Text style={[styles.ratingValue, { color: palette.star }]}>{rating.label}</Text>
    </View>;
  }
  function select(score) {
    setValue(score);
    setOpen(false);
    onRate?.(score);
  }
  return <View style={styles.ratingWrap}>
    <Pressable accessibilityRole="button" accessibilityLabel={rating.rateLabel || labels.rate}
      accessibilityState={{ expanded: open }} onPress={() => setOpen(true)} style={styles.ratingButton}>
      <Star color={value == null ? palette.textMuted : palette.star} />
      {value != null ? <Text style={[styles.ratingValue, { color: palette.star }]}>{value}</Text> : null}
    </Pressable>
    <Modal visible={open} transparent animationType="fade" statusBarTranslucent
      onRequestClose={() => setOpen(false)}>
      <Pressable accessibilityRole="button" accessibilityLabel={labels.close}
        onPress={() => setOpen(false)} style={styles.modalBackdrop}>
        <View accessibilityViewIsModal onStartShouldSetResponder={() => true}
          style={[styles.ratingDialog, { backgroundColor: palette.card, borderColor: palette.border }]}>
          <Text style={[styles.ratingTitle, { color: palette.text }]}>{rating.rateLabel || labels.rate}</Text>
          {renderRating({
            value,
            label: rating.rateLabel || labels.rate,
            scaleMax: rating.scaleMax || 10,
            palette: palette.rating,
            onSelect: select,
          })}
        </View>
      </Pressable>
    </Modal>
  </View>;
}

function AltTitle({ label, open, onPress, palette }) {
  return <Pressable accessibilityRole="button" accessibilityLabel={label}
    accessibilityState={{ expanded: open }} onPress={onPress} style={styles.altButton}>
    <Text style={[styles.altIcon, { color: palette.textMuted }]}>ⓘ</Text>
  </Pressable>;
}

function CardMetadata({ model, palette, labels, onOpen, onOpenLink, onLongPress, onRate, renderRating, readOnly, altOpen, setAltOpen, revealed = true }) {
  return <>
    <View style={styles.titleRow}>
      <Title model={model} palette={palette} onOpen={onOpen} onOpenLink={onOpenLink} onLongPress={onLongPress} />
      {model.altTitle ? <AltTitle label={labels.altTitle} open={altOpen}
        onPress={() => setAltOpen(current => !current)} palette={palette} /> : null}
      {model.scoreText != null ? <View style={styles.score}>
        <Star color={palette.star} />
        <Text style={[styles.ratingValue, { color: palette.star }]}>{model.scoreText}</Text>
      </View> : null}
      <Rating model={model} palette={palette} labels={labels} renderRating={renderRating} onRate={onRate} readOnly={readOnly} />
    </View>
    {altOpen && model.altTitle ? <Text style={[styles.altText, { color: palette.textMuted }]}>{model.altTitle}</Text> : null}
    {model.lines?.map((line, index) => shouldShowMediaCardLine(model, line, revealed)
      ? <Line key={index} line={line} palette={palette} /> : null)}
  </>;
}

// Memoized on its props: a list re-render (a shelf arriving, a page appended)
// skips cards whose model and callbacks are unchanged. Parents pass stable
// props for this to hold; a changed model still re-renders the card.
export const MediaCard = React.memo(function MediaCard({
  model,
  labels = {},
  palette,
  width = 168,
  selection,
  actions,
  onOpen,
  onOpenLink,
  onAction,
  onLongPress,
  onReorder,
  onReorderDrag,
  dragging = false,
  readOnly = false,
  onRevealChange,
  revealed,
  renderActionIcon,
  onRate,
  renderRating,
  style,
}) {
  const colors = resolvePalette(palette);
  const copy = {
    altTitle: 'Show alternative title',
    nextEpisode: 'Jump to next episode',
    nextEpisodeCompact: 'Next',
    noStatus: 'No Status',
    rate: 'Rate',
    close: 'Close',
    dragHint: 'Drag to reorder or use the step buttons',
    ...labels,
  };
  const [altOpen, setAltOpen] = useState(false);
  const cardRef = useRef(null);
  const [localRevealed, setLocalRevealed] = useState(false);
  const activeSelection = readOnly ? undefined : selection;
  const shownActions = readOnly ? null : (actions === undefined ? model.actions : actions);
  const isRevealed = revealed === undefined ? localRevealed : revealed;

  function longPress() {
    if (!onLongPress || activeSelection?.active || readOnly) return;
    if (cardRef.current?.measureInWindow) {
      cardRef.current.measureInWindow((x, y, width, height) => onLongPress({ x, y, width, height }));
    } else onLongPress(null);
  }

  function setReveal(value) {
    if (revealed === undefined) setLocalRevealed(value);
    onRevealChange?.(value);
  }

  function open() {
    if (activeSelection?.active) {
      activeSelection.toggle?.();
      return;
    }
    if (shouldRevealMediaCardActions(shownActions, isRevealed)) {
      setReveal(true);
      return;
    }
    onOpen?.();
  }

  const rowOpen = activeSelection?.active ? activeSelection.toggle : onOpen;

  function gatedOpen() {
    if (!activeSelection?.active && shouldRevealMediaCardActions(shownActions, isRevealed)) {
      setReveal(true);
      return;
    }
    onOpen?.();
  }

  function gatedOpenLink(href) {
    if (!activeSelection?.active && shouldRevealMediaCardActions(shownActions, isRevealed)) {
      setReveal(true);
      return;
    }
    onOpenLink?.(href);
  }

  if (model.layout === 'row') {
    // `rowSplit` (the search list) puts the title block and the actions on one
    // line, actions at the end, like the web's list rows.
    const split = Boolean(model.rowSplit);
    const metadata = <>
      <CardMetadata model={model} palette={colors} labels={copy} onOpen={onOpen}
        onOpenLink={onOpenLink} onLongPress={onLongPress ? longPress : undefined} onRate={onRate} renderRating={renderRating} readOnly={readOnly}
        altOpen={altOpen} setAltOpen={setAltOpen} />
      {model.matched ? <Text numberOfLines={1} accessibilityLabel={model.matched.title}
        style={[styles.matched, { color: colors.textMuted }]}>{model.matched.text}</Text> : null}
    </>;
    const actionRow = shownActions && !activeSelection?.active ? <MediaCardActionRow actions={shownActions}
      palette={colors} onAction={onAction} renderIcon={renderActionIcon} /> : null;
    return <View ref={cardRef} style={[styles.row, split && styles.rowSplit, split && model.rowWidth ? { width: model.rowWidth } : null, style]}>
      <View style={split ? styles.rowArtworkSplit : styles.rowArtwork}>
        <Artwork model={model} palette={colors} selection={activeSelection} onPress={rowOpen}
          onLongPress={onLongPress ? longPress : undefined} />
      </View>
      {split
        ? <View style={styles.rowBodySplit}>
          <View style={styles.rowMeta}>{metadata}</View>
          {actionRow}
        </View>
        : <View style={styles.rowBody}>{metadata}{actionRow}</View>}
    </View>;
  }

  return <View ref={cardRef} style={[styles.card, {
    width,
    backgroundColor: colors.card,
    borderColor: colors.border,
  }, style]}
    onPointerEnter={event => {
      if (event?.nativeEvent?.pointerType === 'mouse' && (shownActions || model.hoverMetadata) && !activeSelection?.active) setReveal(true);
    }}
    onPointerLeave={event => {
      if (event?.nativeEvent?.pointerType === 'mouse' && (shownActions || model.hoverMetadata) && !activeSelection?.active) setReveal(false);
    }}>
    <View style={styles.artworkWrap}>
      <View style={styles.artworkStage}>
        <Artwork model={model} palette={colors} selection={activeSelection} onPress={open}
          onLongPress={onLongPress ? longPress : undefined} />
        {model.statusChip?.kind === 'media-type'
          ? <MediaTypeChip chip={model.statusChip} palette={colors} />
          : <StatusChip chip={model.statusChip} palette={colors} labels={copy} />}
        {model.cornerChips?.filter(chip => shouldShowMediaCardCorner(model, chip, isRevealed)).map((chip, index) => <CornerChip key={`${chip.kind}-${index}`}
          chip={chip} palette={colors} labels={copy} onOpenLink={gatedOpenLink} compact={width <= 120} />)}
        <ProgressBar bar={model.progressBar} palette={colors} />
      </View>
      <View style={styles.bodyStack}>
        <CardScrim style={styles.scrim} />
        <View style={styles.body}>
          <CardMetadata model={model} palette={colors} labels={copy} onOpen={gatedOpen}
            onOpenLink={gatedOpenLink} onLongPress={onLongPress ? longPress : undefined} onRate={onRate} renderRating={renderRating} readOnly={readOnly}
            altOpen={altOpen} setAltOpen={setAltOpen} revealed={isRevealed} />
        </View>
      </View>
      {shownActions && !activeSelection?.active ? <MediaCardActions actions={shownActions}
        palette={colors} labels={copy} revealed={isRevealed} onOpen={onOpen}
        onAction={onAction} onReorder={onReorder} onReorderDrag={onReorderDrag}
        dragging={dragging} renderIcon={renderActionIcon}
        onLongPress={onLongPress ? longPress : undefined}
        style={{ top: 0, height: width / (ASPECT[model.shape] || ASPECT.poster) }} /> : null}
    </View>
  </View>;
});

const styles = StyleSheet.create({
  card: { position: 'relative', borderWidth: 1, borderRadius: 8, overflow: 'hidden' },
  artworkWrap: { position: 'relative' },
  artworkStage: { position: 'relative' },
  artworkFrame: {
    width: '100%',
    overflow: 'hidden',
    alignItems: 'center',
    justifyContent: 'center',
  },
  artworkPlaceholder: { paddingHorizontal: 8, textAlign: 'center', fontSize: 11, fontWeight: '600' },
  body: { padding: 10, gap: 2, zIndex: 1 },
  bodyStack: { position: 'absolute', left: 0, right: 0, bottom: 0, zIndex: 11 },
  // The web card darkens a fixed 3rem above its body, not a percentage of the
  // body, so square and 2:3 cards read the same.
  scrim: { position: 'absolute', left: 0, right: 0, bottom: 0, top: -48, zIndex: 0 },
  titleRow: { flexDirection: 'row', alignItems: 'center', gap: 4, minWidth: 0 },
  titleColumn: { flex: 1, minWidth: 0 },
  titleLinkPress: { minWidth: 0 },
  title: { fontSize: 14, fontWeight: '600', lineHeight: 18 },
  titleSecondary: { fontSize: 11, fontWeight: '500', lineHeight: 15 },
  altButton: { width: 20, height: 20, alignItems: 'center', justifyContent: 'center', flexShrink: 0 },
  altIcon: { fontSize: 13 },
  altText: { fontSize: 11, lineHeight: 15, marginTop: 2 },
  line: { fontSize: 11, lineHeight: 15, marginTop: 2 },
  provenance: { fontSize: 10, lineHeight: 14, marginTop: 1 },
  matched: { fontSize: 11, lineHeight: 15, marginTop: 2 },
  score: { flexDirection: 'row', alignItems: 'center', gap: 2, flexShrink: 0 },
  chip: { position: 'absolute', flexDirection: 'row', alignItems: 'center', gap: 6,
    borderRadius: 6, paddingHorizontal: 8, paddingVertical: 4, maxWidth: '90%' },
  statusChip: { top: 8, left: 8, zIndex: 6, height: 24, justifyContent: 'center', paddingVertical: 0 },
  chipText: { fontSize: 11, fontWeight: '600' },
  chipGlyph: { fontSize: 16, fontWeight: '700', lineHeight: 16 },
  mediaChipText: { fontSize: 9, fontWeight: '700', letterSpacing: 0.4, textTransform: 'uppercase' },
  cornerChip: { position: 'absolute', height: 24, justifyContent: 'center', borderRadius: 6,
    paddingHorizontal: 8, paddingVertical: 0, zIndex: 10, maxWidth: '90%' },
  cornerText: { fontSize: 11, fontWeight: '600', lineHeight: 16 },
  topRight: { top: 8, right: 8 },
  bottomRight: { bottom: 9, right: 6 },
  bottomLeft: { bottom: 9, left: 6 },
  progressTrack: { position: 'absolute', left: 0, right: 0, bottom: 0, height: 4, zIndex: 20 },
  progressFill: { height: '100%' },
  selection: { position: 'absolute', top: 6, left: 6, zIndex: 20, width: 24, height: 24,
    borderRadius: 4, borderWidth: 2, alignItems: 'center', justifyContent: 'center' },
  selectionCheck: { fontSize: 14, fontWeight: '700' },
  ratingWrap: { flexShrink: 0 },
  ratingButton: { flexDirection: 'row', alignItems: 'center', gap: 2, minHeight: 20, paddingHorizontal: 2 },
  ratingReadOnly: { flexDirection: 'row', alignItems: 'center', gap: 2, flexShrink: 0 },
  ratingValue: { fontSize: 11, fontWeight: '600' },
  modalBackdrop: { flex: 1, backgroundColor: 'rgba(0, 0, 0, 0.6)',
    alignItems: 'center', justifyContent: 'center', padding: 24 },
  ratingDialog: { width: '100%', maxWidth: 320, borderRadius: 12, borderWidth: 1, padding: 16, gap: 8 },
  ratingTitle: { fontSize: 14, fontWeight: '600' },
  row: { flexDirection: 'row', gap: 12, padding: 12, alignItems: 'flex-start' },
  rowArtwork: { width: 64 },
  rowSplit: { alignItems: 'center', padding: 16 },
  rowArtworkSplit: { width: 64 },
  rowBodySplit: { flex: 1, minWidth: 0, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8 },
  rowMeta: { flex: 1, minWidth: 0 },
  rowBody: { flex: 1, minWidth: 0 },
});
