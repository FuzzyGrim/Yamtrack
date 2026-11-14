import React, { useEffect, useState } from 'react';
import { mountRoot } from '../settings/root';
import { BulkActionBar } from './BulkActionBar';

// Alpine owns selection state and publishes it as `window.__bulkSelection`
// plus a `bulk-selection-change` event. This adapter owns the transport: quick
// actions post from here, Update Status and Add Plays to History open the
// BulkActions dialogs (mountBulkActions.jsx), which keep working while you browse.
const read = () => window.__bulkSelection || { active: false, ids: [] };

const notify = (message, type = 'success') => {
  if (message && typeof window.showTrackToast === 'function') window.showTrackToast({ message, type });
};

function Toolbar({ config }) {
  const [selection, setSelection] = useState(read);
  const [busy, setBusy] = useState(false);
  const [selecting, setSelecting] = useState(false);
  useEffect(() => {
    const change = () => setSelection(read());
    window.addEventListener('bulk-selection-change', change);
    change();
    return () => window.removeEventListener('bulk-selection-change', change);
  }, []);
  if (!selection.active) return null;

  const ids = selection.ids;
  const none = ids.length === 0;
  const open = detail => window.dispatchEvent(new CustomEvent('bulk-actions-open', { detail: { ...detail, itemIds: [...ids] } }));

  async function selectAll() {
    if (!config.selectAllUrl || busy || selecting) return;
    setSelecting(true);
    try {
      const params = new URLSearchParams();
      const form = document.getElementById('filter-form');
      if (form) {
        new FormData(form).forEach((value, key) => {
          if (typeof value === 'string') params.append(key, value);
        });
      }
      const response = await fetch(`${config.selectAllUrl}&${params}`);
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !Array.isArray(data.item_ids)) {
        throw new Error(data.error || 'Could not select the matching items.');
      }
      window.dispatchEvent(new CustomEvent('bulk-selection-select-all', {
        detail: { ids: data.item_ids },
      }));
    } catch (error) {
      notify(error.message || 'Could not select the matching items.', 'error');
    } finally {
      setSelecting(false);
    }
  }

  async function run(url, fields) {
    if (!ids.length || busy || !url) return;
    setBusy(true);
    try {
      const body = new URLSearchParams();
      // One field keeps select-all sizes above Django's field-count limit postable.
      body.set('item_ids', ids.join(','));
      Object.entries(fields).forEach(([key, value]) => body.append(key, value));
      const response = await fetch(url, { method: 'POST', headers: { 'X-CSRFToken': config.csrfToken }, body });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.success === false) throw new Error(data.error || 'The bulk action failed.');
      notify(data.message || 'Bulk action completed.');
      window.dispatchEvent(new CustomEvent('bulk-actions-done'));
    } catch (error) {
      notify(error.message || 'The bulk action failed.', 'error');
    } finally {
      setBusy(false);
    }
  }

  const locked = none || busy;
  const actions = [
    { key: 'status', icon: 'status', label: 'Update Status', items: [
      { heading: 'Changes status only. Completed on a show also adds plays dated today.' },
      ...config.statuses.map(status => ({ label: status.label, onSelect: () => open({ kind: 'status', status: status.value, statusLabel: status.label }) })),
    ] },
    { key: 'list', icon: 'list', label: 'Add to List', empty: 'No editable lists.',
      items: config.lists.map(list => ({ label: list.label, onSelect: () => run(config.listUrl, { custom_list_id: list.id }) })) },
    { key: 'collection', icon: 'collection', label: 'Add to Collection',
      onSelect: () => run(config.collectionUrl, {}) },
    { key: 'tag', icon: 'tag', label: 'Apply Tag', empty: 'No tags yet.',
      onSelect: () => open({
        kind: 'tag',
        tagUrl: config.tagUrl,
        csrfToken: config.csrfToken,
        tags: config.tagOptions || config.tags.map(name => ({ id: name, name })),
      }) },
    { key: 'more', icon: 'more', label: 'More actions',
      items: [{ label: 'Add Plays to History', onSelect: () => open({ kind: 'plays' }) }] },
  ];

  return <BulkActionBar count={ids.length} actions={actions} disabled={locked} deselectDisabled={busy}
    onSelectAll={config.selectAllUrl ? selectAll : undefined} selectAllDisabled={busy} selecting={selecting}
    onDeselect={() => window.dispatchEvent(new CustomEvent('bulk-actions-clear'))} />;
}

export function mountBulkToolbar(host, props) {
  return mountRoot(host, <Toolbar config={props} />);
}
