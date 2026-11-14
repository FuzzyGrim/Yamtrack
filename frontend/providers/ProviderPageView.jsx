import React from 'react';
import { Image, Pressable, StyleSheet, Text, View } from 'react-native';
import { CatalogGrid } from '../ui/controls/Catalog';
import { Tabs } from '../ui/controls/Navigation';
import { Chip } from '../settings/Chip';
import { SettingsCard } from '../settings/SettingsCard';
import { ProviderProvider, useProvider } from './ProviderContext';

// Transport-neutral provider page. Browser-only tab bodies arrive through
// `slots`; this file deliberately imports no provider body, form or URL module.
const defaultPalette = {
  background: '#18181b',
  surface: '#27272a',
  surfaceStrong: '#3f3f46',
  surfaceMuted: '#52525b',
  border: '#52525b',
  text: '#fafafa',
  textSecondary: '#d4d4d8',
  textMuted: '#a1a1aa',
  link: '#818cf8',
  accent: '#6366f1',
  accentStrong: '#4f46e5',
  accentText: '#ffffff',
  danger: '#ef4444',
  success: '#10b981',
  warning: '#f59e0b',
  focus: '#818cf8',
  radius: 18,
  badgeBorder: '#52525b',
  badgeBackground: '#3f3f46',
  badgeText: '#d4d4d8',
  badgeHover: '#52525b',
  infoBorder: 'rgba(99, 102, 241, 0.3)',
  infoBackground: 'rgba(99, 102, 241, 0.1)',
  infoBadgeBorder: 'rgba(99, 102, 241, 0.3)',
  infoBadgeBackground: 'rgba(99, 102, 241, 0.1)',
  infoBadgeText: '#c7d2fe',
  warningBorder: 'rgba(245, 158, 11, 0.3)',
  warningBackground: 'rgba(245, 158, 11, 0.1)',
  warningBadgeBorder: 'rgba(245, 158, 11, 0.4)',
  warningBadgeBackground: 'rgba(245, 158, 11, 0.2)',
  warningBadgeText: '#fde68a',
};

const BODY_SLOTS = new Set(['connection', 'import', 'activity', 'metadata', 'tracking', 'sync']);

function resolveSlot(slot, context) {
  if (typeof slot === 'function') return slot(context);
  return slot ?? null;
}

function tonePalette(colors, tone) {
  if (tone === 'info') {
    return {
      ...colors,
      border: colors.infoBorder,
      background: colors.infoBackground,
      badgeBorder: colors.infoBadgeBorder,
      badgeBackground: colors.infoBadgeBackground,
      badgeText: colors.infoBadgeText,
    };
  }
  if (tone === 'warning') {
    return {
      ...colors,
      border: colors.warningBorder,
      background: colors.warningBackground,
      badgeBorder: colors.warningBadgeBorder,
      badgeBackground: colors.warningBadgeBackground,
      badgeText: colors.warningBadgeText,
    };
  }
  return colors;
}

/**
 * Universal provider page shell.
 *
 * `slots` may hold plain React nodes or `(context) => node` callbacks for the
 * `connection`, `import`, `activity` and `metadata` tabs. The callback context
 * is `{ model, draft, t, palette, buttonPalette, renderIcon, onSubmit,
 * onOpenUrl, onNavigate }`. A required body is only rendered when the server
 * owns it (`contentOwner === 'react'`); server-owned bodies stay outside this
 * root.
 */
export function ProviderPageView({
  model,
  draft,
  slots,
  labels,
  palette,
  buttonPalette,
  renderIcon,
  onOpenUrl,
  onSubmit,
  onNavigate,
}) {
  const colors = { ...defaultPalette, ...(palette || {}) };
  return <ProviderProvider
    model={model}
    labels={labels}
    onOpenUrl={onOpenUrl}
    onSubmit={onSubmit}
    onNavigate={onNavigate}
    palette={colors}
    buttonPalette={buttonPalette}
    renderIcon={renderIcon}
  >
    <ProviderPageBody model={model} draft={draft} slots={slots} colors={colors} />
  </ProviderProvider>;
}

function ProviderPageBody({ model, draft, slots, colors }) {
  const { t, onOpenUrl, onNavigate, buttonPalette, renderIcon, onSubmit } = useProvider();
  const serverOwned = model.contentOwner === 'server';
  const provider = model.provider || {};
  const media = provider.media || [];
  const tabs = model.tabs || [];
  const slot = BODY_SLOTS.has(model.tab) ? slots?.[model.tab] : null;

  function selectTab(key, item) {
    const url = item?.url || model.urls?.[key];
    if (url) onNavigate?.(url, item);
  }

  return <View testID="provider-page" style={[serverOwned ? styles.content : styles.page, !serverOwned && {
    backgroundColor: colors.background,
    borderRadius: colors.radius,
    padding: 24,
  }]}>
    <View style={styles.identity}>
      <View style={styles.titleRow}>
        <View style={styles.name}>
          {provider.logo ? <Image source={{ uri: provider.logo }} style={styles.logo}
            accessible={false} accessibilityElementsHidden importantForAccessibility="no-hide-descendants" /> : null}
          <Text role="heading" aria-level={2} style={[styles.heading, { color: colors.text }]}>
            {provider.label}
          </Text>
        </View>
        <View role="group" accessibilityLabel={t('Media types')} style={styles.media}>
          {media.map(mediaType => <Chip key={mediaType} label={mediaType} pill palette={colors} />)}
        </View>
      </View>
      <Text style={[styles.description, { color: colors.textMuted }]}>{provider.description}</Text>
    </View>

    <View accessibilityRole="navigation" accessibilityLabel={t('Provider sections')}>
      <Tabs items={tabs} selected={model.tab} label={t('Provider sections')}
        palette={{ ...colors, accent: colors.link || colors.accent }}
        onSelect={onNavigate ? selectTab : undefined} />
    </View>

    {model.onboardingUrl ? <Pressable
      accessibilityRole={onOpenUrl ? 'link' : undefined}
      disabled={!onOpenUrl}
      onPress={onOpenUrl ? () => onOpenUrl(model.onboardingUrl) : undefined}
      style={({ pressed }) => [styles.linkPressable, pressed && styles.pressed]}>
      <Text style={[styles.link, { color: colors.link }]}>{t('Continue Guided Setup')}</Text>
    </Pressable> : null}

    {model.tab === 'overview' ? <Overview model={model} colors={colors} onNavigate={onNavigate} /> : null}
    {!serverOwned && slot
      ? resolveSlot(slot, {
        model,
        draft,
        t,
        palette: colors,
        buttonPalette,
        renderIcon,
        onSubmit,
        onOpenUrl,
        onNavigate,
      })
      : null}
  </View>;
}

function Overview({ model, colors, onNavigate }) {
  return <View testID="provider-overview"><CatalogGrid>
    {(model.overviewCards || []).map(card => <Pressable key={card.key}
      accessibilityRole={card.url ? 'link' : 'button'}
      accessibilityLabel={card.title}
      accessibilityHint={card.tooltip || undefined}
      {...(card.linkProps || {})}
      disabled={!onNavigate || !card.url}
      onPress={onNavigate && card.url ? event => {
        event?.preventDefault?.();
        onNavigate(card.url, card);
      } : undefined}
      // No outline here: the card draws its own border, and the global
      // :focus-visible rule in controls.css rings it for keyboard focus. An
      // always-on outline showed as a square on top of the rounded card.
      style={({ pressed }) => [styles.cardPressable, pressed && styles.pressed]}>
      <SettingsCard title={card.title} badge={card.badge || undefined}
        palette={tonePalette(colors, card.tone)}>
        <Text style={[styles.cardText, { color: colors.textMuted }]}>{card.subtitle}</Text>
        {card.detail ? <Text style={[styles.cardText, { color: colors.textMuted }]}>{card.detail}</Text> : null}
      </SettingsCard>
    </Pressable>)}
  </CatalogGrid></View>;
}

const styles = StyleSheet.create({
  page: { gap: 24, minWidth: 0 },
  content: { gap: 24, minWidth: 0 },
  identity: { minWidth: 0 },
  titleRow: {
    flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between',
    gap: 12, minWidth: 0,
  },
  name: { flexDirection: 'row', alignItems: 'center', gap: 12, minWidth: 0, flexShrink: 1 },
  logo: { width: 32, height: 32, resizeMode: 'contain', flexShrink: 0 },
  heading: { fontSize: 20, lineHeight: 28, fontWeight: '600', flexShrink: 1 },
  media: {
    flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'flex-end',
    gap: 8, marginLeft: 'auto', minWidth: 0, flexShrink: 1,
  },
  description: { fontSize: 14, lineHeight: 21, marginTop: 4 },
  linkPressable: { alignSelf: 'flex-start', minWidth: 0 },
  link: { fontSize: 14, lineHeight: 21 },
  cardPressable: { minWidth: 0, textDecorationLine: 'none', borderRadius: 18 },
  cardText: { fontSize: 14, lineHeight: 21 },
  pressed: { opacity: 0.85 },
});
