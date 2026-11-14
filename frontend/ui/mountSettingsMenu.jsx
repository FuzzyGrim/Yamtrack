import React from 'react';
import { mountRoot } from '../settings/root';
import { SettingsMenu } from './SettingsMenu';
import { actionIcons } from './actionIcons';

// The Search media type menu's tokens (GlobalSearch.web.jsx), so this menu draws exactly like it.
const palette = {
  surface: 'var(--color-surface)',
  surfaceStrong: 'var(--color-surface-strong)',
  surfaceMuted: 'var(--color-surface-muted)',
  border: 'var(--color-surface-muted)',
  text: 'var(--color-text)',
  textSecondary: 'var(--color-text-secondary)',
  textMuted: 'var(--color-text-muted)',
  overlay: 'rgba(0, 0, 0, 0.5)',
};

function submitEntryGrouping(grouping) {
  const form = document.createElement('form');
  form.method = 'post';
  form.action = grouping.action;
  for (const [name, value] of [['csrfmiddlewaretoken', grouping.csrfToken],
    ['show_separate_entries', grouping.nextValue], ['next', grouping.next]]) {
    const input = document.createElement('input');
    input.type = 'hidden';
    input.name = name;
    input.value = value;
    form.appendChild(input);
  }
  document.body.appendChild(form);
  form.submit();
}

export function mountSettingsMenu(host, props) {
  const { labels, bulkSelect, entryGrouping } = props;
  let sharing = false;
  // Read the page's Alpine scope each time the menu opens: it owns the layout and the select mode.
  const data = () => window.Alpine?.$data(host) || {};

  const items = () => {
    const scope = data();
    const grid = scope.layout !== 'table';
    return [
      grid
        ? { label: labels.grid, icon: actionIcons.fourSquare, onSelect: () => window.location.assign(scope.layoutHref('table')) }
        : { label: labels.table, icon: actionIcons.table, onSelect: () => window.location.assign(scope.layoutHref('grid')) },
      { separator: true },
      { label: labels.save, icon: actionIcons.save, onSelect: () => scope.saveView() },
      ...(bulkSelect ? [{ label: scope.selectMode ? labels.cancelSelect : labels.select, icon: actionIcons.circleCheck,
        onSelect: () => scope.toggleSelectMode() }] : []),
      { label: sharing ? labels.sharing : labels.share, icon: actionIcons.share, disabled: sharing,
        onSelect: () => { sharing = true; Promise.resolve(scope.shareView()).finally(() => { sharing = false; }); } },
      ...(entryGrouping ? [{ label: labels.entryGrouping, icon: actionIcons.eye, detail: entryGrouping.stateLabel,
        onSelect: () => submitEntryGrouping(entryGrouping) }] : []),
    ];
  };

  return mountRoot(host, <SettingsMenu label={labels.settings} closeLabel={labels.close} items={items} palette={palette} />);
}
