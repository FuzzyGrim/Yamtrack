import React, { useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { ProviderActivityList, ProviderImportPanel } from '../providers/ProviderImportPanel';
import { ProviderLinkCard } from '../providers/ProviderLinkCard';
import { AccountSettings } from '../providers/AccountSettings';
import { ProviderMetadataView } from '../providers/ProviderMetadataView';
import { ImportPreferencesCard } from '../providers/ImportSettings';
import { ProviderFileImportView } from '../providers/ProviderFileImportView';
import { ProviderPageView } from '../providers/ProviderPageView';
import { ProviderSettingsFormView } from '../providers/ProviderSettingsFormView';
import { ProviderSettingsView, accountSettingsModel, setupGuideProps } from '../providers/ProviderSettingsView';
import { SettingsCard } from '../settings/SettingsCard';
import { Button } from '../ui/Button';
import { SetupGuide } from '../ui/SetupGuide';

export const providerDetailCopy = {
  browserTitle: 'Connect in your browser',
  browserCopy: 'This provider signs in through its own page. Finish there, then come back and refresh.',
  browserAction: 'Open in browser',
  statusTitle: 'Connection status',
  testTitle: 'Test connection',
  testCopy: 'Check that the saved server still accepts the connection.',
  testAction: 'Test connection',
  testing: 'Testing…',
  syncTitle: 'Sync',
  syncCopy: 'Watched-state sync directions and conflicts are managed in Connections.',
  syncAction: 'Open Connections',
  noTracking: 'Tracking for this provider is set up on the server.',
  formSaved: 'Saved.',
  formFailed: 'Could not save these settings.',
  importQueued: 'Import queued. Progress appears under Activity.',
  uploading: 'Uploading',
  viewActivity: 'View activity',
  importFailed: 'Could not start the import.',
  readOnly: 'This account is view-only, so provider settings cannot be changed.',
};

// Option forms the detail payload lists under `forms`, by the tab that shows them
// on the web. Each saves through the adapter and reports its own result.
const FORMS_BY_TAB = {
  connection: ['koreader', 'seerr_settings'],
  tracking: ['jellyfin_events', 'plex_usernames', 'seerr_settings', 'group_scrobbled_anime'],
  import: ['plex_mark_watched', 'hardcover_sync'],
};
const FORM_TITLES = {
  jellyfin_events: 'Jellyfin Webhook Events',
  plex_usernames: 'Plex Webhook Filter',
  plex_mark_watched: 'Plex Watched Changes',
  koreader: 'KOReader',
  seerr_settings: 'Seerr Settings',
  hardcover_sync: 'Hardcover sync',
  group_scrobbled_anime: 'Media Server Scrobbles',
};

function FormCard({ formKey, form, copy, onSaveForm, preferences, palette, buttonPalette, plain = false }) {
  const [status, setStatus] = useState({ message: '', ok: true });
  async function save({ values }) {
    setStatus({ message: '', ok: true });
    try {
      await onSaveForm?.(formKey, values);
      setStatus({ message: copy.formSaved, ok: true });
    } catch (reason) {
      setStatus({ message: reason?.message || copy.formFailed, ok: false });
    }
  }
  return <View style={styles.stack}>
    <ProviderSettingsFormView form={form} title={plain ? undefined : FORM_TITLES[formKey] || form.title}
      preferences={preferences} palette={{ ...palette, background: palette.surface ?? palette.background }}
      buttonPalette={buttonPalette} onSubmit={save}
      onSubmitError={reason => setStatus({ message: reason?.message || copy.formFailed, ok: false })} />
    {status.message ? <Text accessibilityLiveRegion="polite"
      style={[styles.copy, { color: status.ok ? palette.success : palette.danger }]}>{status.message}</Text> : null}
  </View>;
}

// The shared import-preference card, fed the same options the web island builds.
function importPreferencesModel(initial) {
  const t = text => initial.labels?.[text] ?? text;
  const options = list => list.map(option => ({ ...option, label: t(option.label) }));
  return {
    title: t('Import preferences'),
    profile: initial.profile,
    editable: initial.editable,
    fixed: initial.fixed,
    fixedFrequency: initial.fixedFrequency,
    fixedFrequencyHelp: initial.fixedFrequencyHelp,
    values: initial.values || {},
    statuses: initial.statuses || [],
    tags: initial.tags || [],
    tagNames: initial.tagNames || [],
    modeOptions: options([{ value: 'history', label: 'History' }, { value: 'watchlist', label: 'Watchlist' },
      { value: 'update_collection', label: 'Collection' }]),
    overrideOptions: options([{ value: 'new', label: 'New Items Only' }, { value: 'overwrite', label: 'Overwrite Existing' }]),
    frequencyOptions: options([{ value: 'once', label: 'One Time Import' }, { value: 'daily', label: 'Every Day' },
      { value: '2days', label: 'Every 2 Days' }]),
    labels: initial.labels || {},
  };
}

// The metadata tab with a result line for its save, move and proxy actions.
function MetadataActions({ model, copy, onSaveSource, onMove, onSaveProxy, palette, buttonPalette, onOpenUrl }) {
  const [status, setStatus] = useState({ message: '', ok: true });
  const run = action => async (...args) => {
    setStatus({ message: '', ok: true });
    try {
      const result = await action?.(...args);
      setStatus({ message: result?.messages?.[0]?.text || result?.messages?.[0] || copy.formSaved, ok: true });
    } catch (reason) {
      setStatus({ message: reason?.message || copy.formFailed, ok: false });
    }
  };
  return <View style={styles.stack}>
    <ProviderMetadataView model={model} palette={palette} buttonPalette={buttonPalette} onOpenUrl={onOpenUrl}
      onSaveSource={action => run(onSaveSource)(action)} onMove={action => run(onMove)(action)}
      onSaveProxy={url => run(onSaveProxy)(url)} />
    {status.message ? <Text accessibilityLiveRegion="polite"
      style={[styles.copy, { color: status.ok ? palette.success : palette.danger }]}>{status.message}</Text> : null}
  </View>;
}

function formatSize(size) {
  if (!Number.isFinite(size) || size <= 0) return '';
  if (size < 1024 * 1024) return `${Math.max(1, Math.round(size / 1024))} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function FileImportCard({ model, copy, onImportFile, pickFiles, onOpenUrl, onOpenActivity, palette, buttonPalette }) {
  const [status, setStatus] = useState({ message: '', ok: true, busy: false, done: false });
  async function submit({ values }) {
    const picked = values?.[model.fileField];
    const file = Array.isArray(picked) ? picked[0] : null;
    if (!file) return;
    const size = formatSize(file.size);
    setStatus({ message: `${copy.uploading} ${file.name}${size ? ` (${size})` : ''}…`, ok: true, busy: true, done: false });
    try {
      await onImportFile?.({ name: file.name, content: file.text, contentBase64: file.base64, mode: values.mode });
      setStatus({ message: copy.importQueued, ok: true, busy: false, done: true });
    } catch (reason) {
      setStatus({ message: reason?.message || copy.importFailed, ok: false, busy: false, done: false });
    }
  }
  const fail = reason => setStatus({ message: reason?.message || copy.importFailed, ok: false, busy: false, done: false });
  return <View style={styles.stack}>
    <ProviderFileImportView model={{ ...model, description: '' }} title={model.description} pickFiles={pickFiles} onSubmit={submit}
      onOpenUrl={onOpenUrl} onPickError={fail} onSubmitError={fail}
      palette={{ ...palette, background: palette.surface ?? palette.background }} buttonPalette={buttonPalette} />
    {status.message ? <Text accessibilityLiveRegion="polite"
      style={[styles.copy, { color: status.ok ? (status.done ? palette.success : palette.textMuted) : palette.danger }]}>{status.message}</Text> : null}
    {status.done && onOpenActivity ? <View style={styles.row}>
      <Button label={copy.viewActivity} tone="surface" onPress={onOpenActivity} palette={buttonPalette} />
    </View> : null}
  </View>;
}

function FormStack({ tab, forms, copy, onSaveForm, preferences, palette, buttonPalette, plain }) {
  const keys = (FORMS_BY_TAB[tab] || []).filter(key => forms?.[key]);
  return keys.map(key => <FormCard key={key} formKey={key} form={forms[key]} copy={copy}
    onSaveForm={onSaveForm} preferences={preferences} palette={palette} buttonPalette={buttonPalette} plain={plain} />);
}

function TestCard({ copy, onTest, palette, buttonPalette }) {
  const [state, setState] = useState({ busy: false, message: '', ok: true });
  async function run() {
    setState({ busy: true, message: '', ok: true });
    try {
      const result = await onTest?.();
      setState({ busy: false, message: result?.message || '', ok: result?.ok !== false });
    } catch (reason) {
      setState({ busy: false, message: reason?.message || 'The check failed. Try again.', ok: false });
    }
  }
  return <SettingsCard title={copy.testTitle} palette={{ ...palette, background: palette.surface ?? palette.background }}>
    <Text style={[styles.copy, { color: palette.textMuted }]}>{copy.testCopy}</Text>
    <View style={styles.row}>
      <Button label={state.busy ? copy.testing : copy.testAction} busy={state.busy} disabled={state.busy}
        tone="surface" onPress={run} palette={buttonPalette} />
    </View>
    {state.message ? <Text accessibilityLiveRegion="polite"
      style={[styles.copy, { color: state.ok ? palette.success : palette.danger }]}>{state.message}</Text> : null}
  </SettingsCard>;
}

/**
 * Native provider detail: the shared provider page shell with every tab body
 * supplied from the provider detail API model. Handlers return promises so the
 * shared cards can show their own saving, error and retry states.
 */
export function ProviderDetailView({
  detail,
  tab,
  labels,
  palette = {},
  buttonPalette = {},
  onTab,
  onConnect,
  onDisconnect,
  onSaveCredentials,
  onClearCredentials,
  onSaveForm,
  onImportFile,
  onSavePreferences,
  pickFiles,
  onOpenUrl,
  onCopy,
  onStartLink,
  onPollLink,
  onCancelLink,
  onLinked,
  onTest,
  onSync,
  onImport,
  onDeleteSchedule,
  onOpenConnections,
  onOpenActivity,
  onSaveSource,
  onMove,
  onSaveProxy,
  stremioInstallContent,
}) {
  const copy = { ...providerDetailCopy, ...(labels || {}) };
  const { page, connection, settingsModel, schedules, activity, readOnly, slug } = detail;
  const model = { ...page, tab };
  const linkable = Boolean(connection.canLink && onStartLink);

  function selectTab(url, item) {
    const keys = Object.keys(page.urls || {});
    const key = item?.key && keys.includes(item.key) ? item.key : keys.find(name => page.urls[name] === url);
    if (key) onTab?.(key);
  }

  const guideList = (settingsModel.setupGuides || []).map((guide, index) => <SetupGuide key={`guide-${index}`}
    {...setupGuideProps(guide, settingsModel.guideCopy, page.labels)} onCopy={onCopy} onOpenLink={onOpenUrl}
    palette={palette} buttonPalette={buttonPalette} />);

  const formProps = { forms: detail.forms, copy, onSaveForm, preferences: detail.importPreferences?.values, palette, buttonPalette };
  const browserAccount = connection.mode === 'browser' && linkable ? <ProviderLinkCard
    label={page.provider?.label} needsServerUrl={slug === 'storyteller'} palette={palette} buttonPalette={buttonPalette}
    onStart={onStartLink} onPoll={onPollLink} onCancel={onCancelLink} onOpenUrl={onOpenUrl} onCopy={onCopy}
    onConnected={onLinked} initialDevice={connection.account?.device || undefined}
    renderIdle={connection.account && slug !== 'storyteller' ? ({ onConnect: connect, error }) => <View style={styles.stack}>
      <AccountSettings model={accountSettingsModel(connection.account, page.labels)}
        onConnect={connect} onDisconnect={onDisconnect} palette={palette} buttonPalette={buttonPalette} />
      {error ? <Text accessibilityRole="alert" style={[styles.copy, { color: palette.danger }]}>{error}</Text> : null}
    </View> : undefined} /> : null;
  const accountContent = slug === 'koreader' && connection.account ? <AccountSettings
    model={accountSettingsModel(connection.account, page.labels)} onDisconnect={onDisconnect}
    formContent={<FormStack tab="connection" {...formProps} plain />}
    palette={palette} buttonPalette={buttonPalette} /> : browserAccount;
  const seerrForm = slug === 'seerr' ? <SettingsCard title="Seerr Integration"
    palette={{ ...palette, background: palette.surfaceStrong }}>
    <Text style={[styles.copy, { color: palette.textMuted }]}>Use a Seerr webhook to automatically add requested/approved media into Floppy.</Text>
    <FormStack tab={tab} {...formProps} palette={{ ...palette, surface: palette.surfaceDim || palette.surface }} />
  </SettingsCard> : null;
  const slots = {
    connection: <View style={styles.stack}>
      {readOnly ? <Text style={[styles.copy, { color: palette.warning }]}>{copy.readOnly}</Text> : null}
      {connection.mode === 'browser' && !linkable ? <SettingsCard title={copy.browserTitle} palette={{ ...palette, background: palette.surface ?? palette.background }}>
        <Text style={[styles.copy, { color: palette.textMuted }]}>{copy.browserCopy}</Text>
        <View style={styles.row}>
          <Button label={copy.browserAction} onPress={() => onOpenUrl?.(connection.browserUrl)} palette={buttonPalette} />
        </View>
      </SettingsCard> : null}
      {slug === 'seerr' ? seerrForm : slug === 'koreader' ? null : <FormStack tab="connection" {...formProps} />}
      {connection.canTest ? <TestCard copy={copy} onTest={onTest} palette={palette} buttonPalette={buttonPalette} /> : null}
      <ProviderSettingsView model={settingsModel} accountContent={accountContent}
        tab="connection" palette={palette} buttonPalette={buttonPalette}
        onSave={onSaveCredentials} onClear={onClearCredentials} onConnect={onConnect} onDisconnect={onDisconnect}
        onOpenLink={onOpenUrl} onCopy={onCopy} />
      {!connection.account ? browserAccount : null}
    </View>,
    tracking: <View style={styles.stack}>
      {settingsModel.guidesSessionRequired ? <Text style={[styles.copy, { color: palette.textMuted }]}>Sign in with your username and password to manage this.</Text> : guideList.length ? guideList : (detail.forms && FORMS_BY_TAB.tracking.some(key => detail.forms[key]) ? null
        : <Text style={[styles.copy, { color: palette.textMuted }]}>{copy.noTracking}</Text>)}
      {slug === 'seerr' ? seerrForm : <FormStack tab="tracking" {...formProps} />}
      {slug === 'stremio' ? stremioInstallContent : null}
    </View>,
    import: <View style={styles.stack}>
      {detail.importPreferences && onSavePreferences ? <ImportPreferencesCard model={importPreferencesModel(detail.importPreferences)}
        onSave={onSavePreferences} palette={palette} buttonPalette={buttonPalette} /> : null}
      <ProviderImportPanel slug={slug} connection={connection} schedules={schedules} hasFileImport={Boolean(detail.fileImport)}
        defaults={detail.importPreferences?.values} palette={palette} buttonPalette={buttonPalette}
        onSync={onSync} onImport={onImport} onDeleteSchedule={onDeleteSchedule} />
      {detail.fileImport ? <FileImportCard model={detail.fileImport} copy={copy} onImportFile={onImportFile}
        pickFiles={pickFiles} onOpenUrl={onOpenUrl} onOpenActivity={onOpenActivity ? () => onOpenActivity('activity') : undefined}
        palette={palette} buttonPalette={buttonPalette} /> : null}
      <FormStack tab="import" {...formProps} />
    </View>,
    activity: <ProviderActivityList activity={activity} schedules={schedules} palette={palette} />,
    sync: <SettingsCard title={copy.syncTitle} palette={{ ...palette, background: palette.surface ?? palette.background }}>
      <Text style={[styles.copy, { color: palette.textMuted }]}>{copy.syncCopy}</Text>
      <View style={styles.row}>
        <Button label={copy.syncAction} tone="surface" onPress={onOpenConnections} palette={buttonPalette} />
      </View>
    </SettingsCard>,
    metadata: <MetadataActions model={model} copy={copy} palette={palette} buttonPalette={buttonPalette} onOpenUrl={onOpenUrl}
      onSaveSource={onSaveSource} onMove={onMove} onSaveProxy={onSaveProxy} />,
  };

  return <ProviderPageView model={model} slots={slots} palette={palette} buttonPalette={buttonPalette}
    onNavigate={selectTab} onOpenUrl={onOpenUrl} />;
}

const styles = StyleSheet.create({
  stack: { gap: 12 },
  row: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  copy: { fontSize: 13, lineHeight: 19 },
});
