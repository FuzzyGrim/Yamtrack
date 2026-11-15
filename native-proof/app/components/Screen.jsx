import React from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { palette, spacing } from '../theme';

// `onScroll` and `onContentSizeChange` pass through to the scrolling body (Home
// uses them to load shelves near the screen).
// `renderScroll(scrollProps)`: a page whose list scrolls itself (the native
// Library's windowed grid) draws the body with the props this ScrollView would
// have had, so its padding, safe-area and side-rail handling are the same.
export function Screen({ title, subtitle, children, scroll = true, onBack, chrome = true, contentStyle, scrollStyle, floating, artworkBleed = false, onScroll, onContentSizeChange, renderScroll }) {
  const scrollProps = {
    style: [artworkBleed ? styles.bleed : null, scrollStyle],
    contentContainerStyle: [styles.content, contentStyle],
    keyboardShouldPersistTaps: 'handled',
  };
  const body = renderScroll
    ? renderScroll(scrollProps)
    : scroll
      ? <ScrollView {...scrollProps} onScroll={onScroll} scrollEventThrottle={onScroll ? 100 : undefined}
        onContentSizeChange={onContentSizeChange}>{children}</ScrollView>
      : <View style={[styles.content, contentStyle]}>{children}</View>;
  return <View style={[styles.screen, artworkBleed && styles.bleed]}>
    {chrome && (title || subtitle || onBack) ? <View style={styles.header}>
      {onBack ? <Pressable accessibilityRole="button" accessibilityLabel="Back" onPress={onBack} style={styles.back}>
        <Text style={[styles.backText, { color: palette.accent }]}>Back</Text>
      </Pressable> : null}
      {title || subtitle ? <View style={styles.heading}>
        {title ? <Text role="heading" aria-level={1} style={[styles.title, { color: palette.text }]}>{title}</Text> : null}
        {subtitle ? <Text style={[styles.subtitle, { color: palette.textMuted }]}>{subtitle}</Text> : null}
      </View> : null}
    </View> : null}
    {body}
    {floating || null}
  </View>;
}

const styles = StyleSheet.create({
  screen: { flex: 1, minHeight: 0 },
  bleed: { overflow: 'visible' },
  header: { paddingHorizontal: spacing.lg, paddingTop: spacing.sm, paddingBottom: spacing.md, gap: spacing.sm },
  heading: { gap: 2 },
  title: { color: palette.text, fontSize: 28, fontWeight: '700' },
  subtitle: { color: palette.textMuted, fontSize: 13 },
  back: { alignSelf: 'flex-start', paddingVertical: 4, paddingRight: 8 },
  backText: { color: palette.accent, fontSize: 14, fontWeight: '600' },
  content: { paddingHorizontal: spacing.lg, paddingBottom: spacing.xl, gap: spacing.lg },
  section: { gap: spacing.sm },
  sectionTitle: { color: palette.text, fontSize: 17, fontWeight: '600' },
  sectionCopy: { color: palette.textMuted, fontSize: 12 },
});

export function Section({ title, copy, children }) {
  return <View style={styles.section}>
    <Text style={[styles.sectionTitle, { color: palette.text }]}>{title}</Text>
    {copy ? <Text style={[styles.sectionCopy, { color: palette.textMuted }]}>{copy}</Text> : null}
    {children}
  </View>;
}
