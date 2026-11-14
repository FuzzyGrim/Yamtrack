import React from 'react';
import { Button } from './Button';
import { ActionMenu } from './controls/Menu';

// The universal menu takes concrete theme values. These are the same semantic
// tokens the legacy `.ui-menu` rules read, so it follows the active theme.
const menuPalette = {
  surface: 'var(--color-surface)',
  surfaceStrong: 'var(--color-surface-strong)',
  surfaceMuted: 'var(--color-surface-muted)',
  border: 'var(--color-surface-border)',
  text: 'var(--color-text)',
  textSecondary: 'var(--color-text-secondary)',
  textMuted: 'var(--color-text-muted)',
  accent: 'var(--color-accent)',
  overlay: 'rgba(0, 0, 0, 0.55)',
};

const Glyph = ({ children }) => <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor"
  strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">{children}</svg>;

const glyphs = {
  status: <Glyph><circle cx="12" cy="12" r="10" /><path d="m9 12 2 2 4-4" /></Glyph>,
  list: <Glyph><path d="M11 12H3" /><path d="M16 6H3" /><path d="M16 18H3" />
    <path d="M18 9v6" /><path d="M21 12h-6" /></Glyph>,
  collection: <Glyph><path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z" />
    <path d="m3.3 7 8.7 5 8.7-5" /><path d="M12 22V12" /></Glyph>,
  tag: <Glyph><path d="M12.586 2.586A2 2 0 0 0 11.172 2H4a2 2 0 0 0-2 2v7.172a2 2 0 0 0 .586 1.414l8.704 8.704a2.426 2.426 0 0 0 3.42 0l6.58-6.58a2.426 2.426 0 0 0 0-3.42z" />
    <circle cx="7.5" cy="7.5" r=".5" fill="currentColor" /></Glyph>,
  more: <Glyph><circle cx="12" cy="12" r="1" /><circle cx="19" cy="12" r="1" />
    <circle cx="5" cy="12" r="1" /></Glyph>,
  check: <Glyph><path d="M20 6 9 17l-5-5" /></Glyph>,
  close: <Glyph><path d="M18 6 6 18" /><path d="m6 6 12 12" /></Glyph>,
};

export function BulkActionBar({ count, actions = [], disabled = false, deselectDisabled = false, onDeselect,
  onSelectAll, selectAllDisabled = false, selecting = false }) {
  return <div className="ui-bulk-bar" role="toolbar" aria-label="Bulk actions">
    <div className="ui-bulk-count">
      {glyphs.check}
      <span><strong>{count.toLocaleString()}</strong> {count === 1 ? 'item' : 'items'} selected</span>
    </div>
    <div className="ui-bulk-buttons">
      {actions.map(action => action.items
        ? <ActionMenu key={action.key} icon={glyphs[action.icon]} label={action.label} disabled={disabled}
          items={action.items} empty={action.empty} palette={menuPalette} />
        : <Button key={action.key} type="button" secondary disabled={disabled}
          aria-label={action.label} title={action.label} onClick={action.onSelect}>{glyphs[action.icon]}</Button>)}
    </div>
    {onSelectAll ? <button type="button" className="ui-bulk-deselect"
      disabled={selectAllDisabled || selecting} onClick={onSelectAll}>
      {selecting ? 'Selecting…' : 'Select all matching'}
    </button> : null}
    <button type="button" className="ui-bulk-deselect" disabled={deselectDisabled} onClick={onDeselect}>
      {glyphs.close} Deselect All
    </button>
  </div>;
}
