import React, { useEffect, useState } from 'react';
import { Image, Modal, Pressable, ScrollView, StyleSheet, Text, View, useWindowDimensions } from 'react-native';
import { Badge } from './Badge';
import { Button } from './Button';
import { DetailBackdrop } from './DetailBackdrop';
import { DetailActivitySubtitle } from './DetailActivitySubtitle';
import { detailHeroLayout } from './detailHeroLayout';

const defaultLabels = {
  altTitle: 'Show alternative title',
  publicView: 'Public view',
  readMore: 'Read more',
  showLess: 'Show less',
  noSynopsis: 'No synopsis available.',
};

const defaultPalette = {
  background: '#18181b',
  surface: '#27272a',
  surfaceStrong: '#3f3f46',
  border: '#3f3f46',
  text: '#fafafa',
  textSecondary: '#d4d4d8',
  textMuted: '#a1a1aa',
  accent: '#6366f1',
  accentStrong: '#4f46e5',
  accentContrast: '#ffffff',
  link: '#818cf8',
  danger: '#ef4444',
  placeholder: '#3f3f46',
};

function resolvePalette(palette) {
  return { ...defaultPalette, ...(palette || {}) };
}

function resolveLabels(labels) {
  return { ...defaultLabels, ...(labels || {}) };
}

function AltTitle({ text, label, palette }) {
  const [open, setOpen] = useState(false);
  if (!text) return null;
  return <View style={styles.altWrap}>
    <Pressable accessibilityRole="button" accessibilityLabel={label}
      accessibilityState={{ expanded: open }} onPress={() => setOpen(value => !value)}
      style={styles.altButton}>
      <Text style={[styles.altGlyph, { color: palette.textMuted }]}>i</Text>
    </Pressable>
    {open ? <View style={[styles.altPanel, { backgroundColor: palette.surfaceStrong, borderColor: palette.border }]}>
      <Text style={[styles.altText, { color: palette.text }]}>{text}</Text>
    </View> : null}
  </View>;
}

function Artwork({ artwork, palette, title, onPress }) {
  const [stage, setStage] = useState(0);
  const sources = [artwork?.src, artwork?.fallback].filter(Boolean);
  const uri = sources[stage] || null;
  const frame = <View style={[styles.artworkFrame, artwork?.aspect === 'landscape' ? styles.artworkLandscape : styles.artworkPoster]}>
    <View style={[StyleSheet.absoluteFill, { backgroundColor: palette.placeholder }]} />
    {uri ? <Image source={{ uri }} style={StyleSheet.absoluteFill} resizeMode="cover"
      accessible={false} accessibilityElementsHidden importantForAccessibility="no-hide-descendants"
      onError={() => setStage(current => current + 1)} /> : null}
    {!uri ? <Text numberOfLines={3} style={[styles.artworkPlaceholder, { color: palette.textMuted }]}>{title}</Text> : null}
  </View>;
  if (!onPress) return frame;
  return <Pressable accessibilityRole="button" accessibilityLabel={title} onPress={onPress}>{frame}</Pressable>;
}

export function DetailHeader({ model, labels, palette, showArtwork = false, onPressArtwork, compact = false, viewportWidth }) {
  const text = resolveLabels(labels);
  const colors = resolvePalette(palette);
  const { width } = useWindowDimensions();
  const artworkWidth = compact ? Math.min(160, (viewportWidth || width) * 0.4)
    : Math.max(120, Math.min(220, width * 0.34));
  const facts = model?.facts || [];
  return <View style={[styles.header, showArtwork && styles.headerWithArtwork]}>
    {showArtwork ? <View style={{ width: artworkWidth }}>
      <Artwork artwork={model?.artwork} palette={colors} title={model?.title || ''} onPress={onPressArtwork} />
    </View> : null}
    <View style={[styles.headerBody, compact && { minWidth: 0 }]}>
      {model?.subtitle ? <Text style={[styles.subtitle, { color: colors.textSecondary }]}>{model.subtitle}</Text> : null}
      <View style={styles.titleRow}>
        <Text accessibilityRole="header" style={[styles.title, { color: colors.text }]}>{model?.title || ''}</Text>
        <AltTitle text={model?.altTitle} label={text.altTitle} palette={colors} />
      </View>
      {model?.publicView ? <View style={styles.publicBadge}>
        <Badge label={text.publicView} tone="outline"
          palette={{ outline: { color: colors.textSecondary, borderColor: colors.border, borderWidth: 1 } }} />
      </View> : null}
      {compact && model?.activityText ? <DetailActivitySubtitle text={model.activityText} color={colors.textMuted} style={[styles.mobileHeroMeta, { color: colors.textMuted, textShadowRadius: 0 }]} /> : null}
      {compact && model?.mobileMeta ? <Text style={[styles.mobileHeroMeta, { color: colors.textMuted, textShadowRadius: 0 }]}>{model.mobileMeta}</Text> : null}
      {!compact && facts.length ? <View style={styles.facts}>
        {facts.map((fact, index) => <View key={`${fact.label}-${index}`} style={styles.fact}>
          <Text style={[styles.factLabel, { color: colors.textMuted }]}>{fact.label}</Text>
          <Text style={[styles.factValue, { color: colors.textSecondary }]}>{String(fact.value)}</Text>
        </View>)}
      </View> : null}
    </View>
  </View>;
}

export function DetailHero({ model, palette, onOpenVideo }) {
  const colors = resolvePalette(palette);
  const [selected, setSelected] = useState(null);
  const { width } = useWindowDimensions();
  const artwork = model?.artwork || {};
  const gallery = artwork.gallery || {};
  const photos = gallery.photos || [];
  const backdrop = artwork.backdrop || gallery.hero?.url || gallery.overview?.url;
  const logo = artwork.logo || gallery.overview?.logo_url || gallery.hero?.logo_url;
  if (!backdrop && !logo && !photos.length && !gallery.video) return null;
  return <View style={styles.hero}>
    <Pressable accessibilityRole={backdrop ? 'button' : undefined}
      accessibilityLabel={model?.title ? `${model.title} artwork` : 'Detail artwork'}
      disabled={!backdrop} onPress={() => backdrop && setSelected({ url: backdrop })}
      style={[styles.heroBackdrop, { height: Math.min(320, Math.max(190, width * 0.46)), backgroundColor: colors.surfaceStrong }]}>
      {backdrop ? <Image source={{ uri: backdrop }} resizeMode="cover" style={StyleSheet.absoluteFill}
        accessible={false} accessibilityElementsHidden importantForAccessibility="no-hide-descendants" /> : null}
      <View style={[StyleSheet.absoluteFill, styles.heroScrim]} />
      {logo ? <Image source={{ uri: logo }} resizeMode="contain" accessibilityLabel={`${model?.title || 'Media'} logo`}
        style={styles.heroLogo} /> : <Text accessibilityRole="header" style={[styles.heroTitle, { color: colors.text }]}>{model?.title || ''}</Text>}
    </Pressable>
    {photos.length || gallery.video ? <ScrollView horizontal showsHorizontalScrollIndicator={false}
      contentContainerStyle={styles.heroRail} accessibilityLabel="Artwork gallery">
      {gallery.video ? <Pressable accessibilityRole="button" accessibilityLabel="Play trailer"
        onPress={() => onOpenVideo?.(gallery.video)} style={[styles.heroThumb, styles.heroVideoThumb, { backgroundColor: colors.surfaceStrong }]}>
        <Text style={{ color: colors.text }}>▶ Trailer</Text>
      </Pressable> : null}
      {photos.map((photo, index) => <Pressable key={`${photo.url}-${index}`} accessibilityRole="button"
        accessibilityLabel={`Open artwork ${index + 1}`} onPress={() => setSelected(photo)}
        style={[styles.heroThumb, { backgroundColor: colors.surfaceStrong }]}>
        <Image source={{ uri: photo.thumb_url || photo.url }} resizeMode="cover" style={StyleSheet.absoluteFill}
          accessible={false} accessibilityElementsHidden importantForAccessibility="no-hide-descendants" />
      </Pressable>)}
    </ScrollView> : null}
    <Modal visible={Boolean(selected)} transparent animationType="fade" onRequestClose={() => setSelected(null)}>
      <View style={styles.lightbox}>
        <Pressable accessibilityRole="button" accessibilityLabel="Close artwork"
          onPress={() => setSelected(null)} style={styles.lightboxClose}>
          <Text style={{ color: '#fff', fontSize: 18 }}>×</Text>
        </Pressable>
        {selected?.url ? <Image source={{ uri: selected.url }} resizeMode="contain" style={styles.lightboxImage}
          accessibilityLabel={`${model?.title || 'Media'} artwork`} /> : null}
      </View>
    </Modal>
  </View>;
}

export function DetailMobileHero({ model, palette, onOpenItem, actions, viewport }) {
  const colors = resolvePalette(palette);
  const [seasonsOpen, setSeasonsOpen] = useState(false);
  const { width, height } = useWindowDimensions();
  const [logoAspect, setLogoAspect] = useState(model?.artwork?.logoAspect || 0);
  const [sharpEnd, setSharpEnd] = useState(null);
  const layout = detailHeroLayout(viewport || { width, height }, logoAspect);
  const artwork = model?.artwork || {};
  const backdrop = artwork.backdrop || artwork.gallery?.hero?.url || artwork.gallery?.overview?.url;
  const logo = artwork.logo || artwork.gallery?.hero?.logo_url || artwork.gallery?.overview?.logo_url;
  if (!backdrop) return null;
  return <View style={[styles.mobileHero, { marginLeft: -layout.gutterLeft, marginRight: -layout.gutterRight }]}>
    <DetailBackdrop key={backdrop} source={backdrop} layout={layout} background={colors.background} onSharpEnd={setSharpEnd}
      initialAspect={artwork.backdropAspect} />
    {/* Native action rows use a 12-point gap; web's action gap is 8px. */}
    <View style={[styles.mobileHeroContent, { paddingLeft: layout.gutterLeft, paddingRight: layout.gutterRight,
      paddingTop: Math.max(0, (logo && sharpEnd != null ? sharpEnd - layout.topBleed - 6 : layout.line) - (logo ? layout.logoHeight : 70)) }]}>
      {logo ? <Image source={{ uri: logo }} resizeMode="contain"
        style={{ width: layout.logoWidth, height: layout.logoHeight, marginBottom: 4 }}
        onLoad={event => { const size = event.nativeEvent?.source; if (size?.width && size?.height) setLogoAspect(size.width / size.height); }}
        accessibilityLabel={`${model.title} logo`} /> :
        <Text accessibilityRole="header" style={[styles.mobileHeroTitle, { minHeight: 70 }]}>{model.title}</Text>}
      {model.activityText ? <DetailActivitySubtitle text={model.activityText} color="#fff" style={styles.mobileHeroMeta} /> : null}
      {model.mobileMeta ? <Text style={styles.mobileHeroMeta}>{model.mobileMeta}</Text> : null}
      {model.seasonLabel ? <View style={styles.seasonSelector}>
        <Pressable accessibilityRole="button" accessibilityLabel="Select season"
          accessibilityState={{ expanded: seasonsOpen }} onPress={() => setSeasonsOpen(value => !value)}
          disabled={!model.seasonOptions?.length}>
          <Text style={styles.mobileHeroMeta}>{model.seasonLabel} {model.seasonOptions?.length ? '⌄' : ''}</Text>
        </Pressable>
        {seasonsOpen ? <View style={[styles.seasonMenu, { backgroundColor: colors.surface, borderColor: colors.border }]}>
          {model.seasonOptions.map(season => <Pressable key={season.key} accessibilityRole="button"
            onPress={() => { setSeasonsOpen(false); onOpenItem?.(season.identity); }} style={styles.seasonOption}>
            <Text style={{ color: colors.text }}>{season.number === 0 ? 'Specials' : `Season ${season.number}`}</Text>
          </Pressable>)}
        </View> : null}
      </View> : null}
      {actions ? <View style={styles.mobileHeroActions}>{actions}</View> : <DetailActions model={model} palette={colors} />}
    </View>
  </View>;
}

export function DetailChips({ chips = [], palette, onOpen }) {
  const colors = resolvePalette(palette);
  if (!chips.length) return null;
  return <View style={styles.chipRail} accessibilityLabel="Ratings and detail tags">
    {chips.map((chip, index) => {
      const content = <>
      {chip.logo_src ? <Image source={{ uri: chip.logo_src }} resizeMode="contain" style={styles.chipLogo}
        accessibilityLabel={`${chip.label} logo`} /> : null}
      <Text style={[styles.chipLabel, { color: colors.textMuted }]}>{chip.text || chip.label}</Text>
      {chip.text ? null : <Text style={[styles.chipValue, { color: colors.text }]}>{chip.value ?? chip.rating ?? chip.score ?? chip.tier ?? ''}</Text>}
      </>;
      const chipStyle = [styles.chip, { backgroundColor: colors.surface, borderColor: colors.border }];
      return chip.url && onOpen
        ? <Pressable key={`${chip.label}-${index}`} accessibilityRole="link" onPress={() => onOpen(chip.url)} style={chipStyle}>{content}</Pressable>
        : <View key={`${chip.label}-${index}`} style={chipStyle}>{content}</View>;
    })}
  </View>;
}

export function DetailSynopsis({ model, labels, palette, hideWhenEmpty = false }) {
  const text = resolveLabels(labels);
  const colors = resolvePalette(palette);
  const synopsis = model?.synopsis || {};
  const { width } = useWindowDimensions();
  const compact = width < 640;
  const body = compact ? (synopsis.text || '').replace(/\s+/g, ' ').trim() : synopsis.text || '';
  const previewLines = synopsis.previewLines ?? 3;
  const [expanded, setExpanded] = useState(false);
  const [measuredWidth, setMeasuredWidth] = useState(0);
  const [measurement, setMeasurement] = useState(null);
  useEffect(() => { setExpanded(false); setMeasurement(null); }, [body, previewLines, compact, measuredWidth]);
  if (!body) return hideWhenEmpty ? null : <Text style={[styles.emptySynopsis, { color: colors.textMuted }]}>{text.noSynopsis}</Text>;
  const showToggle = synopsis.clamped === undefined
    ? measurement?.clamped ?? (body.length > previewLines * 42 || body.split('\n').length > previewLines)
    : Boolean(synopsis.clamped);
  const testing = measurement?.testing;
  const measureText = testing == null ? body : `${body.slice(0, testing).trimEnd()}... ${text.readMore}`;
  const onMeasure = event => {
    const lines = event.nativeEvent?.lines;
    if (!lines?.length || measurement?.done) return;
    const fits = lines.length <= previewLines;
    if (testing == null) {
      if (fits || !compact) { setMeasurement({ clamped: !fits, done: true }); return; }
      setMeasurement({ clamped: true, low: 0, high: body.length, best: 0, testing: Math.floor(body.length / 2) });
      return;
    }
    const low = fits ? testing + 1 : measurement.low;
    const high = fits ? measurement.high : testing - 1;
    const best = fits ? testing : measurement.best;
    if (low > high) {
      const excerpt = body.slice(0, best).trimEnd();
      const boundary = excerpt.lastIndexOf(' ');
      setMeasurement({ clamped: true, done: true, preview: (boundary > 0 ? excerpt.slice(0, boundary) : excerpt).replace(/[\s.,;:!?-]+$/u, '') });
    } else setMeasurement({ ...measurement, low, high, best, testing: Math.floor((low + high) / 2) });
  };
  const inline = compact && showToggle && !expanded;
  const preview = measurement?.preview || body.slice(0, previewLines * 42).replace(/\s+\S*$/, '');
  return <View style={styles.synopsis} onLayout={event => setMeasuredWidth(event.nativeEvent.layout.width)}>
    {/* Measure native line wrapping without exposing a second copy to accessibility.
        The PWA adapter owns its equivalent DOM measurement. */}
    {!measurement?.done ? <Text key={measureText} accessible={false} accessibilityElementsHidden
      importantForAccessibility="no-hide-descendants" pointerEvents="none" onTextLayout={onMeasure}
      style={[styles.synopsisText, { position: 'absolute', left: 0, right: 0, opacity: 0 }]}>{measureText}</Text> : null}
    <Text numberOfLines={expanded ? undefined : previewLines}
      style={[styles.synopsisText, { color: colors.textSecondary }]}>
      {inline ? preview : body}
      {inline ? <Text>... <Text accessibilityRole="button" accessibilityLabel={text.readMore}
        onPress={() => setExpanded(true)} style={[styles.synopsisToggleText, { fontSize: 13, color: colors.link }]}>{text.readMore}</Text></Text> : null}
    </Text>
    {(showToggle || expanded) && !inline ? <Pressable accessibilityRole="button"
      accessibilityState={{ expanded }} onPress={() => setExpanded(value => !value)} style={styles.synopsisToggle}>
      <Text style={[styles.synopsisToggleText, { color: colors.link }]}>{expanded ? text.showLess : text.readMore}</Text>
    </Pressable> : null}
  </View>;
}

export function DetailActions({ model, labels, palette }) {
  const colors = resolvePalette(palette);
  const actions = model?.actions;
  if (!actions || model?.readOnly) return null;
  const primary = actions.primary;
  const items = actions.items || [];
  if (!primary && !items.length) return null;
  const buttonPalette = {
    primary: {
      background: colors.accentStrong,
      pressed: colors.accent,
      hovered: colors.accent,
      text: colors.accentContrast,
      border: colors.accentStrong,
    },
    surface: {
      background: colors.surface,
      pressed: colors.surfaceStrong,
      hovered: colors.surfaceStrong,
      text: colors.text,
      border: colors.border,
    },
    danger: {
      background: colors.danger,
      pressed: colors.danger,
      hovered: colors.danger,
      text: colors.accentContrast,
      border: colors.danger,
    },
  };
  return <View style={styles.actions}>
    {primary ? <Button label={primary.label} tone={primary.tone || 'primary'} width="full"
      onPress={primary.onPress} palette={buttonPalette} /> : null}
    {items.map((item, index) => <Button key={`${item.label}-${index}`} label={item.label}
      tone={item.tone || 'surface'} width="full" onPress={item.onPress} palette={buttonPalette} />)}
  </View>;
}

export function DetailSection({ section, palette }) {
  const colors = resolvePalette(palette);
  if (!section || section.hidden) return null;
  return <View style={styles.section}>
    <Text style={[styles.sectionTitle, { color: colors.text }]}>{section.title}</Text>
    {section.copy ? <Text style={[styles.sectionCopy, { color: colors.textMuted }]}>{section.copy}</Text> : null}
    {section.body || null}
  </View>;
}

export function DetailComposition({ model, labels, palette, showArtwork = true }) {
  const sections = model?.sections || [];
  return <View style={styles.composition}>
    <DetailHeader model={model} labels={labels} palette={palette} showArtwork={showArtwork} />
    <DetailSynopsis model={model} labels={labels} palette={palette} />
    <DetailActions model={model} labels={labels} palette={palette} />
    {sections.map(section => <DetailSection key={section.id || section.title} section={section} palette={palette} />)}
  </View>;
}

const styles = StyleSheet.create({
  composition: { gap: 18, width: '100%' },
  hero: { width: '100%', gap: 8 },
  mobileHero: { overflow: 'visible' },
  mobileHeroContent: { alignItems: 'center', gap: 8, paddingBottom: 8 },
  mobileHeroActions: { alignSelf: 'stretch' },
  mobileHeroTitle: { color: '#fff', fontSize: 32, fontWeight: '800', textAlign: 'center' },
  mobileHeroMeta: { color: '#fff', fontSize: 14, textAlign: 'center', textShadowColor: '#000', textShadowRadius: 4 },
  seasonSelector: { alignItems: 'center', zIndex: 2 },
  seasonMenu: { position: 'absolute', top: 25, minWidth: 160, borderRadius: 8, borderWidth: 1, padding: 4 },
  seasonOption: { minHeight: 44, justifyContent: 'center', paddingHorizontal: 12 },
  heroBackdrop: { width: '100%', overflow: 'hidden', borderRadius: 10, alignItems: 'center', justifyContent: 'center' },
  heroScrim: { backgroundColor: 'rgba(0, 0, 0, 0.38)' },
  heroLogo: { width: '72%', height: '62%', maxWidth: 420 },
  heroTitle: { fontSize: 26, fontWeight: '700', textAlign: 'center', padding: 20 },
  heroRail: { gap: 8, paddingVertical: 2 },
  heroThumb: { width: 104, height: 60, overflow: 'hidden', borderRadius: 6, alignItems: 'center', justifyContent: 'center' },
  heroVideoThumb: { width: 120 },
  lightbox: { flex: 1, backgroundColor: 'rgba(0, 0, 0, 0.96)', alignItems: 'center', justifyContent: 'center', padding: 16 },
  lightboxImage: { width: '100%', height: '86%' },
  lightboxClose: { position: 'absolute', top: 32, right: 20, zIndex: 1, width: 44, height: 44, alignItems: 'center', justifyContent: 'center' },
  chipRail: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, width: '100%' },
  chip: { minHeight: 36, borderWidth: 1, borderRadius: 18, paddingHorizontal: 12, flexDirection: 'row', alignItems: 'center', gap: 6 },
  chipLogo: { width: 16, height: 16 },
  chipLabel: { fontSize: 11, fontWeight: '600' },
  chipValue: { fontSize: 13, fontWeight: '700' },
  header: { gap: 16, width: '100%' },
  headerWithArtwork: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'flex-start', gap: 20 },
  headerBody: { flex: 1, minWidth: 200, gap: 8 },
  artworkFrame: { width: 160, overflow: 'hidden', borderRadius: 10, backgroundColor: '#3f3f46' },
  artworkPoster: { aspectRatio: 2 / 3 },
  artworkLandscape: { aspectRatio: 16 / 9 },
  artworkPlaceholder: { padding: 12, fontSize: 13, lineHeight: 18 },
  subtitle: { fontSize: 13, fontWeight: '600' },
  titleRow: { flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap' },
  title: { fontSize: 28, lineHeight: 34, fontWeight: '700' },
  altWrap: { flexDirection: 'row', alignItems: 'center' },
  altButton: { width: 24, height: 24, alignItems: 'center', justifyContent: 'center', borderRadius: 12 },
  altGlyph: { fontSize: 14, fontWeight: '700', fontStyle: 'italic' },
  altPanel: { marginTop: 4, maxWidth: 260, borderWidth: 1, borderRadius: 8, padding: 10 },
  altText: { fontSize: 12, lineHeight: 17 },
  publicBadge: { alignSelf: 'flex-start' },
  facts: { flexDirection: 'row', flexWrap: 'wrap', gap: 14 },
  fact: { gap: 2 },
  factLabel: { fontSize: 10, fontWeight: '700', letterSpacing: 0.6, textTransform: 'uppercase' },
  factValue: { fontSize: 13, fontWeight: '500' },
  synopsis: { gap: 8, width: '100%' },
  synopsisText: { fontSize: 16, lineHeight: 24 },
  emptySynopsis: { fontSize: 16, lineHeight: 24 },
  synopsisToggle: { alignSelf: 'flex-start', paddingVertical: 2 },
  synopsisToggleText: { fontSize: 14, fontWeight: '500', lineHeight: 20 },
  actions: { gap: 8, width: '100%' },
  section: { gap: 8, width: '100%' },
  sectionTitle: { fontSize: 17, fontWeight: '600' },
  sectionCopy: { fontSize: 13, lineHeight: 19 },
});
