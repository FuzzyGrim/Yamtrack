import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Icon } from './Icon';
import { openModal } from './ModalHost';
import { RatingStars } from './RatingStars';
import { DropdownItem, DropdownMenu } from './DropdownMenu';
import { saveQuickStatus } from './mediaCardQuickStatus.web';

const quickStatuses = ['Planning', 'In progress', 'Paused', 'Completed', 'Dropped'];
const quickStatusColors = { Planning: 'text-sky-400', 'In progress': 'text-indigo-400',
  Paused: 'text-orange-400', Completed: 'text-emerald-400', Dropped: 'text-red-400' };
const quickPalette = { surface: 'var(--color-surface)', surfaceStrong: 'var(--color-surface)',
  surfaceMuted: 'var(--color-surface-alt)', border: 'var(--color-border)', text: 'var(--color-text)',
  textSecondary: 'var(--color-text)', overlay: 'rgba(0,0,0,.55)' };

// The shared media card body. It renders the same class names the Django card
// used, so every existing theme and layout rule keeps applying. Only the Search
// list row still places server DOM (its `meta` and fallback `actions` slots).

const aspect = { square: 'aspect-[1/1]', poster: 'aspect-[2/3] aspect-2/3', landscape: 'aspect-[2/3] aspect-2/3' };
const chipBase = 'absolute z-20 h-6 inline-flex items-center bg-gray-900/90 text-white text-[11px] leading-4 font-medium px-2 py-1 rounded-md shadow-md';
const gridVariantClass = 'search-result-card media-card-hover-outline transition-all media-card-title-ellipsis media-card-title-bleed';
const gridSquareVariantClass = 'search-result-card-square media-card-hover-outline transition-all media-card-title-ellipsis media-card-title-bleed';

function Slot({ name, node }) {
  const ref = useRef(null);
  useLayoutEffect(() => {
    if (node && ref.current && node.parentNode !== ref.current) ref.current.replaceChildren(node);
  }, [node]);
  return <div className="media-card-slot" data-card-slot={name} ref={ref} />;
}

const statusIcons = {
  Completed: <><path d="M21.801 10A10 10 0 1 1 17 3.335" /><path d="m9 11 3 3L22 4" /></>,
  'In progress': <polygon points="6 3 20 12 6 21 6 3" />,
  Paused: <><rect x="14" y="4" width="4" height="16" rx="1" /><rect x="6" y="4" width="4" height="16" rx="1" /></>,
  Planning: <><path d="m19 21-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v16z" /><line x1="12" x2="12" y1="7" y2="13" /><line x1="15" x2="9" y1="10" y2="10" /></>,
  Dropped: <><path d="m15 9-6 6" /><path d="M2.586 16.726A2 2 0 0 1 2 15.312V8.688a2 2 0 0 1 .586-1.414l4.688-4.688A2 2 0 0 1 8.688 2h6.624a2 2 0 0 1 1.414.586l4.688 4.688A2 2 0 0 1 22 8.688v6.624a2 2 0 0 1-.586 1.414l-4.688 4.688a2 2 0 0 1-1.414.586H8.688a2 2 0 0 1-1.414-.586z" /><path d="m9 9 6 6" /></>,
};

function MediaTypeChip({ chip }) {
  return <div className={`media-type-chip media-type-chip--${chip.style}`} data-media-type-chip={chip.type}
    data-media-type-chip-style={chip.style}
    style={{ '--media-type-chip-color': chip.color, '--media-type-chip-contrast': chip.contrast }}>
    <span>{chip.label}</span>
  </div>;
}

function StatusChip({ chip }) {
  return <div className="media-status-chip absolute top-2 left-2 h-6 bg-gray-900/90 text-white text-xs leading-4 px-2 py-1 rounded-md flex items-center gap-1.5 shadow-md">
    <span className="sr-only">{chip.label}</span>
    <div className={`w-4 h-4 ${chip.colorClass}`} aria-hidden="true">
      <svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
        strokeLinecap="round" strokeLinejoin="round" className="w-full h-full">{statusIcons[chip.status]}</svg>
    </div>
  </div>;
}

function ChevronRight() {
  return <svg className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m9 18 6-6-6-6" /></svg>;
}

function CornerChip({ chip, labels }) {
  if (chip.kind === 'runtime') {
    return <span className="absolute bottom-3 right-2 z-10 bg-gray-900/90 text-white text-xs px-2 py-1 rounded-md shadow-md">{chip.text}</span>;
  }
  if (chip.kind === 'runtime-top') {
    return <div className="absolute top-2 right-2 bg-gray-900/90 text-white text-xs px-2 py-1 rounded-md shadow-md">{chip.text}</div>;
  }
  if (chip.kind === 'local-only') {
    return <div className="absolute bottom-2 left-2 rounded-md border border-amber-500/30 bg-amber-500/15 px-2 py-1 text-[11px] font-medium text-amber-100 shadow-md">{chip.text}</div>;
  }
  if (chip.kind === 'collected') {
    return <div className="absolute bottom-2 right-2 rounded-md border border-amber-500/30 bg-amber-500/15 px-2 py-1 text-[11px] font-medium text-amber-100 shadow-md" title={chip.title}>{chip.text}</div>;
  }
  const inner = chip.kind === 'next-episode'
    ? <><ChevronRight />{chip.compactText
      ? <><span className="media-card-next-episode-full">{chip.text}</span>
        <span className="media-card-next-episode-compact">{chip.compactText}</span></>
      : <span>{chip.text}</span>}</>
    : <><span>{chip.text}</span><ChevronRight /></>;
  if (!chip.href) {
    return <span className={`${chipBase} top-2 right-2`} data-episode-queue="true">{chip.text}</span>;
  }
  return <a href={chip.href} {...{ 'hx-disable': '' }}
    className={`${chipBase} top-2 right-2 hover:bg-indigo-600 flex items-center gap-0.5 transition-colors`}
    data-next-episode={chip.kind === 'next-episode' ? 'true' : undefined}
    data-episode-queue={chip.kind === 'queue' ? 'true' : undefined}
    title={labels.nextEpisode}
    aria-label={chip.kind === 'next-episode' ? labels.nextEpisode : undefined}>{inner}</a>;
}

function AltTitle({ text, label, inline }) {
  const [open, setOpen] = useState(false);
  const [style, setStyle] = useState(null);
  const trigger = useRef(null);
  const panel = useRef(null);
  function place() {
    const rect = trigger.current?.getBoundingClientRect();
    if (!rect) return;
    const width = Math.min(panel.current?.offsetWidth || 224, window.innerWidth - 32);
    const height = panel.current?.offsetHeight || 0;
    const left = Math.min(Math.max(16, rect.left + rect.width / 2 - width / 2), window.innerWidth - width - 16);
    const top = rect.top >= height + 24 ? rect.top - height - 8 : rect.bottom + 8;
    setStyle({ left, top, width });
  }
  useLayoutEffect(() => {
    if (!open) return undefined;
    place();
    const close = () => setOpen(false);
    const onKey = (event) => { if (event.key === 'Escape') close(); };
    window.addEventListener('keydown', onKey);
    window.addEventListener('scroll', place, true);
    window.addEventListener('resize', place);
    return () => {
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('scroll', place, true);
      window.removeEventListener('resize', place);
    };
  }, [open]);
  return <span className={inline ? 'inline-flex items-center ml-1 align-middle relative' : 'relative shrink-0'}>
    <button ref={trigger} type="button" aria-label={label}
      className="text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors cursor-pointer inline-flex items-center"
      onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)} onBlur={() => setOpen(false)}
      onClick={(event) => { event.preventDefault(); event.stopPropagation(); setOpen((value) => !value); }}>
      <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
        strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <circle cx="12" cy="12" r="10" /><path d="M12 16v-4" /><path d="M12 8h.01" />
      </svg>
    </button>
    {open && createPortal(
      <div ref={panel} role="tooltip" className="fixed z-50 pointer-events-none rounded-lg p-3 text-xs text-white"
        style={{ background: 'rgba(0, 0, 0, 0.85)', boxShadow: '0 4px 10px rgba(0, 0, 0, 0.3)', ...style }}>
        <p>{text}</p>
      </div>, document.body)}
  </span>;
}

function Line({ line }) {
  if (line.variant === 'provenance') {
    return <p className="text-xs text-[var(--color-text-muted)] mt-0.5 line-clamp-1">{line.parts[0].text}</p>;
  }
  const className = `media-card-year text-xs text-[var(--color-text-muted)] mt-1${line.clamp ? ' line-clamp-1' : ''}${line.dormant ? ' media-card-line-dormant' : ''}`;
  if (line.placeholderItemId) {
    return <p className={`${className} release-year-placeholder`} data-item-id={line.placeholderItemId}
      data-progress-text={line.progressText || undefined} />;
  }
  const plain = line.join === 'plain';
  return <p className={className} title={line.tooltip}>
    {line.parts.map((part, index) => {
      const sep = !plain && index > 0 ? ' · ' : '';
      if (part.score) {
        return <React.Fragment key={index}>{sep}<span className="inline-flex items-center gap-0.5 text-yellow-300 align-middle">
          <svg className="w-3 h-3 text-yellow-400 fill-current" viewBox="0 0 24 24" aria-hidden="true">
            <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
          </svg><span className="font-medium">{part.text}</span></span></React.Fragment>;
      }
      if (part.link) return <React.Fragment key={index}>{sep}<span className="text-[var(--color-link)] font-medium">{part.text}</span></React.Fragment>;
      return <React.Fragment key={index}>{sep}{part.muted
        ? <span className="ml-1 text-[var(--color-text-muted)]">{part.text}</span> : part.text}</React.Fragment>;
    })}
  </p>;
}

// The Search list view: the same card content laid out as a row.
function MediaRow({ model, slots, labels, actions, onAction, readOnly }) {
  const shownActions = readOnly ? null : actions;
  return <div className="flex items-center p-4 gap-4">
    <div className="shrink-0 relative">
      <a href={model.href} {...{ 'hx-boost': 'true' }}>
        <img data-media-artwork="" loading="lazy" decoding="sync" alt={model.title}
          className="w-16 h-24 object-cover rounded shadow-md bg-[var(--color-surface-alt)]" src={model.image.src} />
      </a>
    </div>
    <div className="flex-1 min-w-0">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0">
          <h3 className="text-lg font-semibold text-[var(--color-text)] mb-2 line-clamp-2">
            <a href={model.href} {...{ 'hx-boost': 'true' }}
              className="hover:text-[var(--color-link)] transition-colors">{model.title}</a>
            {model.altTitle && <AltTitle inline text={model.altTitle} label={labels.altTitle} />}
          </h3>
          {(model.lines || []).map((line, index) => <Line key={index} line={line} />)}
          {model.matched && <p className="text-xs text-[var(--color-text-muted)] mb-2 line-clamp-1"
            title={model.matched.title}>{model.matched.text}</p>}
          <Slot name="meta" node={slots?.meta} />
        </div>
        {shownActions ? <div className="relative flex items-center justify-center space-x-2.5">
          {shownActions.items.map((action, index) => action.kind === 'link'
            ? <a key={index} href={action.href} title={action.title} className={actionClass(action.tone)}><Icon spec={action.icon} className="w-4 h-4" /></a>
            : <button key={index} type="button" title={action.title} className={actionClass(action.tone)} onClick={() => (onAction ? onAction(action) : openModal(action))}>
              <Icon spec={action.icon} className="w-4 h-4" /></button>)}
        </div> : <Slot name="actions" node={slots?.actions} />}
      </div>
    </div>
  </div>;
}

const tones = {
  indigo: 'bg-indigo-600 hover:bg-indigo-500 hover:shadow-[0_0_15px_rgba(99,102,241,0.5)]',
  emerald: 'bg-emerald-600 hover:bg-emerald-500 hover:shadow-[0_0_15px_rgba(16,185,129,0.5)]',
  amber: 'bg-amber-600 hover:bg-amber-500 hover:shadow-[0_0_15px_rgba(245,158,11,0.5)]',
  rose: 'bg-rose-600 hover:bg-rose-500 hover:shadow-[0_0_15px_rgba(244,63,94,0.45)]',
};
const actionClass = (tone) => `p-2.5 ${tones[tone]} text-white rounded-full hover:scale-110 transition-all duration-200 cursor-pointer`;
const orderClass = 'min-w-9 h-9 px-2 bg-indigo-600 text-white rounded-md hover:bg-indigo-500 transition-colors cursor-pointer text-sm font-semibold';

// Read one value from the page's Alpine scope (list order editing, bulk select) and keep it live.
function usePageValue(host, read) {
  const [value, setValue] = useState(false);
  useEffect(() => {
    const Alpine = window.Alpine;
    if (!host || !Alpine) return undefined;
    const runner = Alpine.effect(() => setValue(!!read(Alpine.$data(host))));
    return () => Alpine.release(runner);
  }, [host]);
  return value;
}

// The hover buttons. Track and list popups still live in the card's Alpine scope
// (trackOpen, listsOpen) because their server forms close themselves through it.
function CardOverlay({ actions, host, revealed, labels, onAction }) {
  const editing = usePageValue(host, (scope) => scope.isOrderEditing);

  function run(action) {
    if (onAction) {
      onAction(action);
    } else if (action.kind === 'modal') {
      openModal(action);
    } else if (action.kind === 'post') {
      window.htmx?.ajax('POST', action.url, {
        target: action.target, swap: action.swap, values: action.vals, headers: { 'X-CSRFToken': actions.csrfToken },
      });
    }
  }

  function reorder(step) {
    const form = document.getElementById('filter-form');
    const values = { ...Object.fromEntries(form ? new FormData(form) : []), item_id: String(actions.ordering.itemId), action: step };
    const indicator = document.getElementById('loading-indicator');
    indicator?.classList.add('htmx-request');
    window.htmx?.ajax('POST', actions.ordering.url, { swap: 'none', values, headers: { 'X-CSRFToken': actions.csrfToken } })
      ?.then(() => window.refreshCustomListGrid?.())
      .finally(() => indicator?.classList.remove('htmx-request'));
  }

  return <div className={`media-card-overlay absolute inset-0 flex items-center justify-center transition-opacity duration-200 ease-in-out${actions.clickable ? ' pointer-coarse:hidden' : ''}${revealed ? ' opacity-100 pointer-events-auto media-card-overlay-revealed' : ' opacity-0 pointer-events-none'}`}>
    <div className="absolute inset-0 bg-black/50 z-0" />
    <a href={actions.href} className="absolute inset-0 z-0" />
    {!editing && <div className="relative z-10 flex w-full max-w-full flex-wrap items-center justify-center gap-2.5 px-2">
      {actions.items.map((action, index) => action.kind === 'link'
        ? <a key={index} href={action.href} title={action.title} className={actionClass(action.tone)}><Icon spec={action.icon} className="w-4 h-4" /></a>
        : <button key={index} type="button" title={action.title} className={actionClass(action.tone)} onClick={() => run(action)}>
          <Icon spec={action.icon} className="w-4 h-4" /></button>)}
    </div>}
    {editing && actions.ordering && <div className="relative z-10 flex flex-col items-center gap-2">
      <div className="flex items-center justify-center gap-2">
        {[['first', '<<'], ['back', '<'], ['next', '>'], ['last', '>>']].map(([step, text]) =>
          <button key={step} type="button" title={actions.ordering.labels[step]} className={orderClass} onClick={() => reorder(step)}>{text}</button>)}
      </div>
      <div className="drag-handle cursor-grab px-4 py-1 text-[var(--color-text-muted)] hover:text-[var(--color-text)] w-full flex justify-center"
        title={actions.ordering.labels.drag}><Icon spec={actions.ordering.dragIcon} className="w-5 h-5" /></div>
    </div>}
  </div>;
}

// Touch screens show the hover buttons on the first tap, and follow the link on the second.
function useTapToReveal(cardRef, host, enabled, selecting, clickable) {
  const [revealed, setRevealed] = useState(false);
  const state = useRef({ revealed: false, selecting: false });
  state.current = { revealed, selecting };
  useEffect(() => {
    const element = cardRef?.current ?? host?.current ?? host;
    const card = element?.closest?.('.media-card');
    // "Hide actions on touch devices" (clickable_media_cards): a tap opens the card directly.
    if (!card || !enabled || clickable) return undefined;
    const onClick = (event) => {
      const { revealed: open, selecting: busy } = state.current;
      if (open) {
        if (event.target.closest('button, a, input, select, textarea')) return;
        const link = card.querySelector('.media-card-overlay a[href]') || card.querySelector('a.media-card-title[href]');
        if (link) { event.preventDefault(); event.stopPropagation(); link.click(); }
        return;
      }
      if (!window.matchMedia('(pointer: coarse)').matches || card.classList.contains('media-card-hide-overlay') || busy) return;
      event.preventDefault();
      event.stopPropagation();
      setRevealed(true);
      window.dispatchEvent(new CustomEvent('media-card-revealed', { detail: card }));
    };
    const onOther = (event) => { if (event.detail !== card) setRevealed(false); };
    // A popup the card opened lives outside it; using it must not hide the buttons again.
    const onOutside = (event) => {
      if (state.current.revealed && !card.contains(event.target) && !event.target.closest?.('[data-floppy-modal]')) setRevealed(false);
    };
    card.addEventListener('click', onClick, true);
    window.addEventListener('media-card-revealed', onOther);
    window.addEventListener('click', onOutside);
    return () => {
      card.removeEventListener('click', onClick, true);
      window.removeEventListener('media-card-revealed', onOther);
      window.removeEventListener('click', onOutside);
    };
  }, [cardRef, host, enabled, clickable]);
  useEffect(() => {
    const element = cardRef?.current ?? host?.current ?? host;
    element?.closest?.('.media-card')?.classList.toggle('media-card-revealed', revealed);
  }, [cardRef, host, revealed]);
  return revealed;
}

// The save and score responses still swap server fragments out of band for pages
// that render them. A card React owns ignores that swap and listens for the
// floppyCardUpdate event the same responses send.
if (typeof window !== 'undefined' && !window.__floppyCardOobGuard) {
  window.__floppyCardOobGuard = true;
  document.addEventListener('htmx:oobBeforeSwap', (event) => {
    if (event.detail?.target?.hasAttribute?.('data-react-owned')) event.preventDefault();
  });
}

function useLiveCard(model) {
  const [live, setLive] = useState(null);
  useEffect(() => {
    if (!model.cardKey) return undefined;
    const onUpdate = (event) => { if (event.detail?.cardKey === model.cardKey) setLive(event.detail); };
    document.addEventListener('floppyCardUpdate', onUpdate);
    return () => document.removeEventListener('floppyCardUpdate', onUpdate);
  }, [model.cardKey]);
  return live;
}

function liveChip(chip, live, labels) {
  if (!live) return chip;
  if (live.statusless) return { kind: 'no-status', label: labels.noStatus, hideOnMobile: chip?.hideOnMobile };
  if (live.status) {
    if (chip?.kind === 'event') return chip;
    return { kind: 'status', status: live.status, colorClass: live.statusColor, hideOnMobile: chip?.hideOnMobile };
  }
  return chip?.kind === 'event' ? chip : null;
}

const chipShell = 'media-status-chip absolute top-2 left-2 bg-gray-900/90 text-white text-xs px-2 py-1 rounded-md flex items-center gap-1.5 shadow-md max-w-[calc(100%-1rem)] overflow-hidden';
const chipLabel = 'text-xs font-medium text-white drop-shadow-sm truncate min-w-0';

function CardStatusChip({ chip }) {
  const mobile = { 'data-mobile-hide-on-mobile': chip.hideOnMobile ? 'true' : 'false' };
  if (chip.kind === 'no-status') {
    return <div className={chipShell} data-status-label="no-status" {...mobile}><span className={chipLabel}>{chip.label}</span></div>;
  }
  if (chip.kind === 'event') {
    return <div className={chipShell} {...mobile}><Icon spec={chip.icon} className="w-3.5 h-3.5 text-indigo-300" /><span className={chipLabel}>{chip.label}</span></div>;
  }
  return <div className={chipShell} {...mobile}><div className={`w-4 h-4 ${chip.colorClass}`}>
    <svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"
      strokeLinejoin="round" className="w-full h-full">{statusIcons[chip.status]}</svg></div></div>;
}

const starPalette = {
  lit: '#facc15', unlit: '#4b5563', muted: 'var(--color-text-muted)',
  focusStyle: { outlineStyle: 'solid', outlineWidth: 1, outlineColor: 'var(--color-accent)' },
};
const starShape = () => <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="currentColor"
  stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
  <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
</svg>;
const smallStar = (className) => <svg className={className} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
  <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" /></svg>;

// The rating in the title row: a button that opens the shared star row, or read-only text.
function CardRating({ rating, csrfToken, editable, onRate }) {
  const [value, setValue] = useState(rating.value);
  const [label, setLabel] = useState(rating.label);
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState({});
  const trigger = useRef(null);
  const popup = useRef(null);
  useEffect(() => { setValue(rating.value); setLabel(rating.label); }, [rating.value, rating.label]);
  const place = () => {
    const rect = trigger.current?.getBoundingClientRect();
    if (!rect) return;
    const width = Math.min(250, window.innerWidth - 32);
    const height = popup.current?.offsetHeight || 90;
    const left = Math.min(Math.max(16, rect.right - width), window.innerWidth - width - 16);
    const below = rect.bottom + 8 + height <= window.innerHeight;
    setPos({ left, top: below ? rect.bottom + 8 : Math.max(8, rect.top - height - 8), width });
  };
  useLayoutEffect(() => {
    if (!open) return undefined;
    place();
    const away = (event) => { if (!popup.current?.contains(event.target) && !trigger.current?.contains(event.target)) setOpen(false); };
    const key = (event) => { if (event.key === 'Escape') setOpen(false); };
    document.addEventListener('click', away);
    window.addEventListener('keydown', key);
    window.addEventListener('resize', place);
    window.addEventListener('scroll', place, true);
    return () => {
      document.removeEventListener('click', away);
      window.removeEventListener('keydown', key);
      window.removeEventListener('resize', place);
      window.removeEventListener('scroll', place, true);
    };
  }, [open]);
  function onSelect(score) {
    setValue(score);
    setLabel(score === null ? null : String(score));
    setOpen(false);
    if (onRate) onRate(score);
    else window.htmx?.ajax('POST', rating.rateUrl, {
      swap: 'none', headers: { 'X-CSRFToken': csrfToken }, values: { score: score ?? value, toggle: true },
    });
  }
  if ((!rating.rateUrl && !onRate) || !editable) {
    return label == null ? null : <span className="flex h-5 items-center gap-1 text-xs text-yellow-300 shrink-0">
      {smallStar('w-3 h-3 text-yellow-400 fill-current')}<span className="font-medium">{label}</span></span>;
  }
  return <span className="flex shrink-0">
    <button ref={trigger} type="button" title={rating.rateLabel} aria-label={rating.rateLabel} aria-haspopup="true"
      onClick={() => setOpen((current) => !current)}
      className={`media-card-rate-button -mx-1 flex h-5 items-center gap-1 px-1 text-xs text-yellow-300 cursor-pointer${value === null || value === undefined ? ' media-card-rate-empty' : ''}`}>
      <span className="media-card-rate-star">{smallStar('w-3 h-3 text-yellow-400')}</span>
      {value !== null && value !== undefined && <span className="font-medium">{label ?? value}</span>}
    </button>
    {open && createPortal(<div ref={popup} style={pos}
      className="fixed z-50 rounded-lg border border-gray-700 bg-[var(--color-surface)] p-3 shadow-lg">
      <div className="mb-1"><RatingStars scaleMax={rating.scaleMax} value={value} label={rating.rateLabel}
        renderStar={starShape} onSelect={onSelect} palette={starPalette} /></div>
    </div>, document.body)}
  </span>;
}

// Memoized on its props: a list re-render (a shelf arriving, a page appended)
// skips cards whose model and callbacks are unchanged. Parents pass stable
// props for this to hold; a changed model still re-renders the card.
export const MediaCard = React.memo(function MediaCard({
  model,
  slots,
  labels = {},
  selection,
  host,
  actions,
  standalone = true,
  readOnly = false,
  onAction,
  onRate,
  variant = '',
  className = '',
}) {
  const copy = {
    altTitle: 'Show alternative title',
    nextEpisode: 'Jump to next episode',
    noStatus: 'No Status',
    rate: 'Rate',
    close: 'Close',
    ...labels,
  };
  const shownActions = readOnly ? null : (actions === undefined ? model.actions : actions);
  // `grid` is the release's `.search-result-card`: the poster fills the card,
  // the body sits over its lower edge, and the subtitle stays hidden until
  // hover. Surfaces that render their own shell leave it off.
  const grid = variant === 'grid' || variant === 'grid-square';
  const cardRef = useRef(null);
  const revealed = useTapToReveal(cardRef, host, !!shownActions, !!selection?.active, !!shownActions?.clickable);
  const live = useLiveCard(model);
  const [quick, setQuick] = useState(model.quickStatus);
  const [quickOpen, setQuickOpen] = useState(false);
  const [quickActive, setQuickActive] = useState(false);
  const [quickError, setQuickError] = useState('');
  const [quickBusy, setQuickBusy] = useState(false);
  const [quickAnchor, setQuickAnchor] = useState(null);
  const quickAvailable = quick && !readOnly && !selection?.active
    && !['event', 'media-type', 'status-native'].includes(model.statusChip?.kind);
  useEffect(() => setQuick(model.quickStatus), [model.quickStatus]);
  useEffect(() => {
    if (live?.status) setQuick(current => current && { ...current, tracked: true, status: live.status });
  }, [live]);
  async function chooseStatus(status) {
    setQuickOpen(false);
    setQuickBusy(true);
    setQuickError('');
    try {
      await saveQuickStatus(quick, status, shownActions?.csrfToken);
      setQuick(current => ({ ...current, tracked: true, status, identity: { ...current.identity, instanceId: null } }));
      if (model.cardKey) document.dispatchEvent(new CustomEvent('floppyCardUpdate', {
        detail: { cardKey: model.cardKey, status, statusColor: quickStatusColors[status] },
      }));
    } catch (error) { setQuickError(error.message); }
    finally { setQuickBusy(false); }
  }
  if (model.layout === 'row') {
    return <MediaRow model={model} slots={slots} labels={copy} actions={shownActions}
      onAction={onAction} readOnly={readOnly} />;
  }
  const art = model.image;
  const imgClass = `media-card-poster w-full ${aspect[model.shape]} bg-[var(--color-surface-alt)] object-cover`;
  const aspectStyle = model.aspectRatio ? { aspectRatio: Number(model.aspectRatio) } : undefined;
  const bar = model.progressBar;
  const card = <>
    <div className={`media-card-visual relative${quickActive || quickOpen ? ' media-quick-status-active' : ''}`}>
      {model.recommend ? <div className="cursor-pointer"><img decoding="sync" loading="lazy" alt={model.title}
        className={imgClass} style={aspectStyle} src={art.src} data-image-source={art.source} /></div> : <a href={model.href} {...(model.boost ? { 'hx-boost': 'true' } : {})} onClick={(event) => {
        if (selection?.active) { event.preventDefault(); selection.toggle(); }
      }}>
        {selection?.active && <div className="absolute top-2 left-2 z-10" onClick={(event) => event.stopPropagation()}>
          <input type="checkbox" className="cursor-pointer w-5 h-5" checked={selection.selected}
            aria-label={model.title} onChange={selection.toggle} />
        </div>}
        <img decoding="sync" loading="lazy" alt={model.title} className={imgClass} style={aspectStyle} src={art.src}
          data-image-source={art.source}
          {...(model.video ? { 'data-image-fallback': art.fallback, 'data-image-fallback-on-small': 'true' } : {})} />
      </a>}
      {quickAvailable && <div className="media-quick-status"
        onMouseEnter={() => setQuickActive(true)} onMouseLeave={() => setQuickActive(false)}
        onFocus={() => setQuickActive(true)} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setQuickActive(false); }}>
        <button type="button" className="media-quick-status-button" disabled={quickBusy}
          aria-label={`Change status: ${quick.status || 'Not tracked'}`} aria-expanded={quickOpen}
          onClick={event => { event.preventDefault(); event.stopPropagation(); const rect = event.currentTarget.getBoundingClientRect();
            setQuickAnchor({ x: rect.left, y: rect.top, width: rect.width, height: rect.height }); setQuickOpen(true); }}>
          <span className={`media-quick-status-icon ${quickStatusColors[quick.status] || ''}`} aria-hidden="true">{quick.status && statusIcons[quick.status]
            ? <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">{statusIcons[quick.status]}</svg> : '+'}</span>
          <span className="media-quick-status-label">{quick.status || 'Add to tracker'}</span>
        </button>
        <DropdownMenu open={quickOpen} onClose={() => setQuickOpen(false)} anchor={quickAnchor} align="start"
          closeLabel="Close status menu" palette={quickPalette} estimatedHeight={208}>
          {quickStatuses.map(status => <DropdownItem key={status} label={status} palette={quickPalette}
            selected={quick.status === status} onPress={() => chooseStatus(status)} />)}
        </DropdownMenu>
      </div>}
      {quickError && <span className="media-quick-status-error" role="alert">{quickError}</span>}
      <div className={quickAvailable ? 'media-quick-status-original' : undefined}>{model.statusChip?.kind === 'status-native' ? <StatusChip chip={model.statusChip} />
        : model.statusChip?.kind === 'media-type' ? <MediaTypeChip chip={model.statusChip} />
          : model.statusId && <div id={model.statusId} data-react-owned="">{(() => {
            const chip = liveChip(model.statusChip, live, copy);
            return chip && <CardStatusChip chip={chip} />;
          })()}</div>}</div>
      {(model.cornerChips || []).map((chip) => <CornerChip key={chip.kind} chip={chip} labels={copy} />)}
      {bar && <div className={`media-card-progress-bar absolute bottom-0 left-0 right-0 h-1 bg-gray-900/60 z-20${bar.dormant ? ' media-card-progress-dormant' : ''}`}>
        <div className={`h-full ${bar.paused ? 'bg-indigo-500/40' : 'bg-indigo-500'} transition-[width] duration-300 ease-out`}
          style={{ width: `${bar.percent}%` }} />
      </div>}
      {shownActions && !selection?.active && <CardOverlay actions={shownActions} host={host} revealed={revealed}
        labels={copy} onAction={onAction} />}
    </div>
    <div className="media-card-body p-3 bg-card-body">
      <div className="media-card-title-row flex items-center gap-2 min-w-0">
        {model.titleLinks?.length ? <div className="min-w-0 flex-1">
          {model.titleLinks.map((part, index) => <a key={part.url} href={part.url} title={part.label}
            className={index === 0
              ? 'media-card-title block text-sm font-semibold text-[var(--color-text)] hover:text-[var(--color-link)] transition duration-300 line-clamp-1'
              : 'mt-0.5 block text-xs text-[var(--color-text-muted)] hover:text-[var(--color-link)] line-clamp-1'}>{part.label}</a>)}
        </div> : model.recommend ? <h3 title={model.title}
          className="media-card-title font-medium text-[var(--color-text)] line-clamp-1 text-sm leading-tight min-w-0">{model.title}</h3>
          : <a href={model.href} title={model.title} {...(model.boost ? { 'hx-boost': 'true' } : {})}
          className={`media-card-title text-sm font-semibold text-[var(--color-text)] ${model.publicView ? '' : 'hover:text-[var(--color-link)] '}transition duration-300 line-clamp-1 min-w-0`}>{model.title}</a>}
        {model.altTitle && <AltTitle text={model.altTitle} label={copy.altTitle} />}
        {model.scoreText != null && <span className="flex items-center gap-1 text-xs text-yellow-300 shrink-0">
          <svg className="w-3 h-3 text-yellow-400 fill-current" viewBox="0 0 24 24" aria-hidden="true">
            <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
          </svg><span className="font-medium">{model.scoreText}</span></span>}
        {model.ratingId && <span id={model.ratingId} data-react-owned="">{(() => {
          const rating = live?.rating !== undefined && live ? (live.rating || null) : model.rating;
          const cardRating = live ? { ...rating, rateUrl: rating?.rateUrl || model.rating?.rateUrl } : rating;
          return cardRating && <CardRating rating={cardRating} csrfToken={model.csrfToken}
            editable={!readOnly && model.ratingEditable} onRate={onRate} />;
        })()}</span>}
      </div>
      {(model.lines || []).map((line, index) => <Line key={index} line={line} />)}
    </div>
  </>;
  if (!standalone) return card;
  const rootClass = [
    'media-card bg-[var(--color-surface)] rounded-lg overflow-hidden shadow-lg relative group',
    variant === 'grid-square' ? gridSquareVariantClass : grid ? gridVariantClass : '',
    className,
  ].filter(Boolean).join(' ');
  return <div ref={cardRef} className={rootClass}
    style={grid ? { '--card-title-rest-lines': '1', '--card-title-hover-lines': '3' } : undefined}>
    <div data-media-card-root="">{card}</div>
  </div>;
});
