import React from 'react';
import { StyleSheet, View } from 'react-native';
import { PageHeading, PageState } from './PageChrome';

// One library/media-list composition for web and native.
//
// The server table and Alpine filter form remain the web platform
// implementation of `content`/`filterForm`; native passes the shared
// `MediaTable` and filter slots. The page owns the order of the heading,
// filters, selection toolbar, grouping banner, table/grid area and bulk bar,
// plus the loading, empty and error states.
export function LibraryPageView({
  title = 'Library',
  subtitle,
  count,
  countLabel,
  titleStyle,
  countStyle,
  labels = {},
  palette = {},
  search,
  heading,
  headerActions,
  filterForm,
  filters,
  selection,
  groupBanner,
  content,
  footer,
  bulkBar,
  state = 'ready',
  empty,
  error,
  onRetry,
  onEmptyAction,
  style,
  testID,
  renderScroll,
}) {
  const top = <>
    {search || null}
    {heading || <PageHeading
      title={title}
      subtitle={subtitle}
      count={count}
      countLabel={countLabel}
      titleStyle={titleStyle}
      countStyle={countStyle}
      actions={headerActions}
      palette={palette} />}
    {filterForm || null}
    {filters || null}
    {selection || null}
    {groupBanner || null}
    <PageState
      state={state}
      labels={labels}
      palette={palette}
      empty={empty}
      error={error}
      onRetry={onRetry}
      onEmptyAction={onEmptyAction} />
  </>;
  // A platform whose list scrolls itself (native windows the grid in a
  // FlatList) takes the parts above and below the content as the list's
  // header and footer, with the same 16 pt gaps the stacked page has. The
  // caller renders the content only when `state` is 'ready'.
  if (renderScroll) {
    return renderScroll({
      header: <View style={[styles.root, state === 'ready' && styles.header, style]} testID={testID}>{top}</View>,
      footer: bulkBar || footer
        ? <View style={[styles.root, styles.footer]}>{bulkBar || null}{footer || null}</View>
        : null,
      ready: state === 'ready',
    });
  }
  return <View style={[styles.root, style]} testID={testID}>
    {top}
    {state === 'ready' ? content : null}
    {bulkBar || null}
    {footer || null}
  </View>;
}

const styles = StyleSheet.create({
  root: { gap: 16, minWidth: 0 },
  header: { paddingBottom: 16 },
  footer: { paddingTop: 16 },
});
