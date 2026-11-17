import React, { useEffect, useRef } from 'react';
import { SearchResultCard } from './SearchResultCard';
import { Button } from './Button';
import { ROW_LOOKAHEAD_SCREENS } from './homeRowLoading.js';
import {
  HistoryActivityDialog,
  HistoryEditDialog,
  HistoryDeleteDialog,
} from './HistoryFeed.jsx';

// The browser History feed renders the card grid the release page shipped. The
// month navigation, filtered pagination and filter sheet stay server-owned page
// chrome, so they are not repeated here.

const SQUARE_TYPES = new Set(['music', 'podcast']);
const NON_POSTER_TYPES = new Set(['music', 'podcast', 'video']);

const FALLBACK_LABELS = {
  loadMore: 'Load more',
  releases: 'releases',
  releaseOne: 'release',
  items: 'items',
  item: 'item',
  loadDayError: 'This day could not be loaded.',
  retryDay: 'Retry day',
  altTitle: 'Show alternative title',
  nextEpisode: 'Jump to next episode',
  noStatus: 'No Status',
};

function copyLabels(labels) {
  return { ...FALLBACK_LABELS, ...(labels || {}) };
}

function fallbackCard(entry) {
  const lines = [];
  const parts = [];
  if (entry.verb) parts.push({ text: entry.verb, link: true });
  if (entry.when) parts.push({ text: `${parts.length ? ' ' : ''}${entry.when}`, muted: true });
  if (parts.length) lines.push({ join: 'plain', parts });
  return {
    mediaType: entry.mediaType,
    shape: SQUARE_TYPES.has(entry.mediaType) ? 'square' : 'poster',
    video: entry.mediaType === 'video',
    href: entry.href || '',
    image: entry.image || { src: null, fallback: '', source: 'primary' },
    title: entry.title,
    altTitle: entry.altTitle || null,
    statusChip: entry.status
      ? { kind: 'status-native', status: entry.status, label: entry.status }
      : null,
    cornerChips: entry.runtime ? [{ kind: 'runtime-top', text: entry.runtime }] : [],
    titleLinks: [],
    progressBar: null,
    scoreText: entry.score == null ? null : String(entry.score),
    lines,
    actions: null,
  };
}

function HistoryCard({ entry, labels }) {
  const model = entry.card?.title ? entry.card : fallbackCard(entry);
  return <SearchResultCard
    model={{ ...model, cardKey: entry.key }}
    outline={false}
    labels={labels} />;
}

function DaySummary({ day, mode, labels }) {
  const copy = copyLabels(labels);
  if (mode === 'release') {
    const count = day.releaseCount ?? day.entryCount ?? 0;
    return <p className="text-sm text-[var(--color-text-muted)]">
      {`${count} ${count === 1 ? copy.releaseOne : copy.releases}`}
    </p>;
  }
  const count = day.entryCount ?? day.entries?.length ?? 0;
  const noun = count === 1 ? copy.item : copy.items;
  return <p className="text-sm text-[var(--color-text-muted)]">
    {`${count} ${noun}${day.runtimeLabel ? ` · ${day.runtimeLabel}` : ''}`}
  </p>;
}

function DayGrid({ entries, extraClass, labels }) {
  if (!entries.length) return null;
  return <div className={`media-grid${extraClass}`}>
    {entries.map(entry => <HistoryCard key={entry.key} entry={entry} labels={labels} />)}
  </div>;
}

function LazyDay({ day, labels, onLoadDay, onRetryDay }) {
  const copy = copyLabels(labels);
  const ref = useRef(null);
  useEffect(() => {
    if (!day.lazy || day.loading || day.error) return undefined;
    const node = ref.current;
    if (!node || typeof IntersectionObserver === 'undefined') return undefined;
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) {
        observer.disconnect();
        onLoadDay?.(day);
      }
    // Start a day one and a half screens before it scrolls in, like Home's
    // waiting rows, so the reader meets cards rather than a placeholder. The
    // margin is in pixels: a percentage resolves against the width.
    }, { rootMargin: `${Math.round((window.innerHeight || 0) * ROW_LOOKAHEAD_SCREENS) || 200}px 0px` });
    observer.observe(node);
    return () => observer.disconnect();
  }, [day.lazy, day.loading, day.error, onLoadDay, day]);

  if (day.error) {
    return <div ref={ref} className="flex flex-col items-center gap-2 py-6">
      <p className="text-xs text-[var(--color-danger)]">{copy.loadDayError}</p>
      <Button label={copy.retryDay} tone="surface" size="sm" onPress={() => onRetryDay?.(day)} />
    </div>;
  }
  return <div ref={ref} className="h-40" aria-hidden="true" />;
}

function HistoryDay({ day, mode, labels, onLoadMore, onLoadDay, onRetryDay }) {
  const copy = copyLabels(labels);
  const entries = day.entries || [];
  const standard = entries.filter(entry => !NON_POSTER_TYPES.has(entry.mediaType));
  const square = entries.filter(entry => SQUARE_TYPES.has(entry.mediaType));
  const video = entries.filter(entry => entry.mediaType === 'video');

  return <div id={`history-day-${day.key}`} className="history-day space-y-4">
    <div className="flex items-center justify-between gap-4 pb-3 border-b border-[var(--color-border)]">
      <div className="history-date-row">
        {day.weekday ? <span className="text-xl font-semibold text-[var(--color-text)]">{day.weekday}</span> : null}
        <span className={`text-xl text-[var(--color-text)]${day.weekday ? '' : ' font-semibold'}`}>
          {day.weekday ? ` ${day.dateLabel}` : day.dateLabel}
        </span>
      </div>
      <div className="text-right">
        <DaySummary day={day} mode={mode} labels={copy} />
      </div>
    </div>

    {day.lazy
      ? <LazyDay day={day} labels={copy} onLoadDay={onLoadDay} onRetryDay={onRetryDay} />
      : <>
        <DayGrid entries={standard} extraClass="" labels={copy} />
        <DayGrid entries={square} extraClass=" mt-4" labels={copy} />
        <DayGrid entries={video} extraClass=" mt-4 media-grid-video" labels={copy} />
        {day.hasMore ? <div className="flex justify-center pt-2">
          <Button
            label={day.remaining ? `${copy.loadMore} (${day.remaining} remaining)` : copy.loadMore}
            tone="surface" size="sm" busy={day.loadingMore}
            onPress={() => onLoadMore?.(day)} />
        </div> : null}
      </>}
  </div>;
}

export function HistoryFeed({
  model = {},
  labels,
  chrome = true,
  onLoadMore,
  onLoadDay,
  onRetryDay,
}) {
  const days = model.days || [];
  if (!days.length) return null;
  return <div className="space-y-10">
    {days.map(day => <HistoryDay key={day.key} day={day} mode={model.mode || 'activity'}
      labels={labels} onLoadMore={onLoadMore} onLoadDay={onLoadDay} onRetryDay={onRetryDay} />)}
  </div>;
}

export {
  HistoryActivityDialog,
  HistoryEditDialog,
  HistoryDeleteDialog,
};
