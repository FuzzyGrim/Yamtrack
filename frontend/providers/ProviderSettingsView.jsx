import React, { useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { AccountSettings } from './AccountSettings';
import { ConnectionSettings } from './ConnectionSettings';
import { ImportSettings } from './ImportSettings';
import { SetupGuide } from '../ui/SetupGuide';
import { Chip } from '../settings/Chip';
import { Dialog } from '../ui/controls/Dialog';

const defaultPalette = {
  text: '#fafafa',
};

// The web provider page owns one tab at a time. Connection also contains the
// account card and integration guides, matching the server template order:
// credentials, setup guides, account.
const tabSections = {
  connection: ['credentials', 'guides', 'account'],
  import: ['imports', 'guides'],
  account: ['account'],
  setup: ['guides'],
  tracking: ['guides'],
};

function translated(labels, key, fallback) {
  return labels?.[key] || fallback;
}

function combinedLabels(source, pageLabels) {
  return { ...(source?.labels || {}), ...(pageLabels || {}) };
}

// Some shared cards merge supplied labels over their defaults. Undefined keys
// would erase a default, so only forward translations that actually exist.
function compactLabels(values) {
  return Object.fromEntries(Object.entries(values).filter(([, value]) => value !== undefined && value !== null));
}

function optionalLabel(field, labels) {
  if (field.required) return field.label;
  const optional = translated(labels, '(optional)');
  return optional ? `${field.label} ${optional}` : field.label;
}

function credentialField(field, labels, personal = false) {
  return {
    name: field.name,
    label: personal ? field.label : optionalLabel(field, labels),
    type: field.type || (field.secret ? 'password' : 'text'),
    secret: field.secret,
    required: field.required,
    placeholder: personal
      ? field.placeholder
      : field.disabled ? undefined : field.fallback
        ? translated(labels, 'Leave a field blank to keep the current fallback.')
        : field.placeholder,
    help: personal ? field.help : (field.help
      || (field.environment ? `${translated(labels, 'Environment', 'Environment')}: ${field.setting}` : undefined)
      || (field.usingDefault ? translated(labels, 'Bundled default', 'Bundled default') : undefined)
      || (field.disabled ? translated(labels, 'Stored', 'Stored') : undefined)),
    disabled: field.disabled,
    environment: field.environment,
    setting: field.setting,
    stored: field.stored,
    usingDefault: field.usingDefault,
    fallback: field.fallback,
    value: field.value,
    options: field.options,
    maskedPreview: field.maskedPreview,
  };
}

/** Map the web `credentials` view model onto ConnectionSettings' contract. */
export function connectionSettingsModel(credentials, pageLabels = {}) {
  const labels = combinedLabels(credentials, pageLabels);
  const editable = credentials.editable !== false;
  const publicAccess = Boolean(credentials.publicAccess);
  const statusKey = publicAccess ? 'Public API' : editable ? 'Editable' : 'Read-only';
  const getKeyLabel = credentials.getKeyLabel
    || translated(labels, 'Get a key from {name}')
    || translated(labels, `Get a key from ${credentials.label}`);
  return {
    title: credentials.title || translated(labels, 'Instance credentials'),
    personalTitle: credentials.personalTitle || translated(labels, 'Personal credentials'),
    providerLabel: credentials.providerLabel || credentials.label,
    description: credentials.description,
    editable,
    publicAccess,
    canClear: Boolean(credentials.canClear),
    statusLabel: credentials.statusLabel || translated(labels, statusKey),
    statusTone: credentials.statusTone || (publicAccess ? 'success' : editable ? 'info' : 'warning'),
    getKeyUrl: credentials.getKeyUrl,
    getKeyLabel,
    readOnlyReason: credentials.readOnlyReason
      || translated(labels, 'Only superusers can change instance-wide credentials. Values set in the environment are read-only here and take precedence over stored credentials.')
      || translated(labels, 'Read-only: explain instance credential access'),
    fields: (credentials.fields || []).map(field => credentialField(field, labels)),
    personalFields: credentials.personalFields?.length
      ? credentials.personalFields.map(field => credentialField(field, labels, true))
      : undefined,
    defaults: credentials.defaults || [],
    setupHint: credentials.setupHint,
    slug: credentials.slug,
    labels: compactLabels({
      save: translated(labels, 'Save'),
      saving: translated(labels, 'Saving…'),
      saved: translated(labels, 'Credentials saved'),
      saveError: translated(labels, 'Could not save credentials. Try again.'),
      remove: translated(labels, 'Remove stored credentials'),
      personalSave: translated(labels, 'Save personal key'),
      personalSaving: translated(labels, 'Saving…'),
      personalSaved: translated(labels, 'Personal key saved'),
      personalRemoved: translated(labels, 'Personal key removed'),
      personalError: translated(labels, 'Could not save the personal key. Try again.'),
      instanceHelp: translated(labels, 'These credentials are shared across the instance.'),
      personalHelp: translated(labels, 'Your key may avoid shared instance or bundled rate limits.'),
      fallback: translated(labels, 'Leave a field blank to keep the current fallback.'),
      getKey: getKeyLabel,
      readOnly: translated(labels, 'Read-only'),
      editable: translated(labels, 'Editable'),
      publicApi: translated(labels, 'Public API'),
      readOnlyHelp: translated(labels, 'Read-only: explain instance credential access'),
      close: translated(labels, 'Close'),
      stored: translated(labels, 'Stored'),
      notSet: translated(labels, 'Not set'),
      your: translated(labels, 'Your'),
      environment: translated(labels, 'Environment'),
      bundled: translated(labels, 'Bundled default'),
      defaultsTitle: translated(labels, 'Make this the default provider for'),
    }),
  };
}

function importOptionLabels(labels) {
  return {
    modeOptions: [
      { value: 'history', label: translated(labels, 'History', 'History') },
      { value: 'watchlist', label: translated(labels, 'Watchlist', 'Watchlist') },
      { value: 'update_collection', label: translated(labels, 'Collection', 'Collection') },
    ],
    overrideOptions: [
      { value: 'new', label: translated(labels, 'New Items Only', 'New Items Only') },
      { value: 'overwrite', label: translated(labels, 'Overwrite Existing', 'Overwrite Existing') },
    ],
    frequencyOptions: [
      { value: 'once', label: translated(labels, 'One Time Import', 'One Time Import') },
      { value: 'daily', label: translated(labels, 'Every Day', 'Every Day') },
      { value: '2days', label: translated(labels, 'Every 2 Days', 'Every 2 Days') },
    ],
  };
}

/** Map web import preferences plus the import action onto ImportSettings. */
export function importSettingsModel(imports, pageLabels = {}) {
  const labels = combinedLabels(imports, pageLabels);
  const options = importOptionLabels(labels);
  return {
    profile: imports.profile,
    title: imports.title || translated(labels, 'Import options'),
    description: imports.description || translated(labels, 'Import anime and manga from your AniList profile.'),
    configured: Boolean(imports.configured),
    privateImport: Boolean(imports.privateImport),
    username: imports.username,
    modeUnsupported: imports.modeUnsupported
      || translated(labels, 'This import mode is not supported by AniList.')
      || translated(labels, 'This import mode is not supported by this provider.'),
    preferences: {
      title: imports.preferencesTitle || translated(labels, 'Import preferences'),
      editable: imports.editable !== false,
      values: { ...(imports.values || {}) },
      statuses: imports.statuses || [],
      tags: imports.tags || [],
      fixed: imports.fixed,
      fixedFrequency: imports.fixedFrequency,
      fixedFrequencyHelp: imports.fixedFrequencyHelp,
      modeOptions: imports.modeOptions || options.modeOptions,
      overrideOptions: imports.overrideOptions || options.overrideOptions,
      frequencyOptions: imports.frequencyOptions || options.frequencyOptions,
      labels: compactLabels({
        preferences: translated(labels, 'Import preferences'),
        editable: translated(labels, 'Editable'),
        readOnly: translated(labels, 'Read-only'),
        mode: translated(labels, 'Import Mode'),
        override: translated(labels, 'Override mode'),
        modeHelp: translated(labels, 'New items leaves existing entries unchanged. Overwrite replaces matching imported entries.'),
        frequency: translated(labels, 'Import Frequency'),
        time: translated(labels, 'Import Time'),
        watchlistStatus: translated(labels, 'Watchlist status'),
        watchlistTag: translated(labels, 'Watchlist tag'),
        saving: translated(labels, 'Saving…'),
        saved: translated(labels, 'Saved'),
        saveError: translated(labels, 'Could not save preferences. Try again.'),
        retry: translated(labels, 'Retry'),
        oneTime: translated(labels, 'One Time Import'),
      }),
    },
    labels: compactLabels({
      options: translated(labels, 'Import options'),
      description: translated(labels, 'Import anime and manga from your AniList profile.'),
      publicLabel: translated(labels, 'Public'),
      privateLabel: translated(labels, 'Private'),
      username: translated(labels, 'Public username'),
      usernamePlaceholder: translated(labels, 'Public AniList username') || translated(labels, 'Public username'),
      import: translated(labels, 'Import'),
      importing: translated(labels, 'Importing…'),
      imported: translated(labels, 'Import started'),
      importError: translated(labels, 'Could not start the import. Try again.'),
      privateRequires: translated(labels, 'Private imports require provider credentials.'),
      configure: translated(labels, 'Configure Connection'),
      modeUnsupported: imports.modeUnsupported
        || translated(labels, 'This import mode is not supported by AniList.')
        || translated(labels, 'This import mode is not supported by this provider.'),
    }),
  };
}

function accountField(field) {
  return {
    name: field.name,
    label: field.label,
    type: field.type || 'text',
    placeholder: field.placeholder,
    required: field.required !== false,
    value: field.value,
    options: field.options,
    disabled: field.disabled,
    help: field.help,
    maskedPreview: field.maskedPreview,
  };
}

/** Map the web `account_connection` view model onto AccountSettings. */
export function accountSettingsModel(account, pageLabels = {}) {
  const labels = combinedLabels(account, pageLabels);
  const connected = Boolean(account.connected);
  const attention = Boolean(account.attention);
  const statusKey = connected ? 'Connected' : attention ? 'Needs attention' : 'Not connected';
  return {
    title: account.title || translated(labels, 'Account connection'),
    description: account.description,
    connected,
    attention,
    saved: Boolean(account.saved),
    editable: account.editable !== false,
    statusLabel: account.statusLabel || translated(labels, statusKey),
    statusTone: account.statusTone || (connected ? 'success' : attention ? 'warning' : 'neutral'),
    submitLabel: account.submitLabel,
    canConnect: Boolean(account.connectUrl),
    followUp: account.followUp,
    message: account.message,
    messages: account.messages,
    badges: account.badges,
    summary: account.summary,
    instances: account.instances,
    fields: (account.fields || []).map(accountField),
    labels: compactLabels({
      connect: translated(labels, 'Connect'),
      reconnect: translated(labels, 'Reconnect'),
      addInstance: translated(labels, 'Add instance') || translated(labels, 'Add Instance'),
      saving: translated(labels, 'Connecting…'),
      saved: translated(labels, 'Connection saved'),
      saveError: translated(labels, 'Could not save the connection. Try again.'),
      disconnect: translated(labels, 'Disconnect'),
      disconnecting: translated(labels, 'Disconnecting…'),
      disconnected: translated(labels, 'Connection removed'),
      disconnectError: translated(labels, 'Could not disconnect. Try again.'),
      edit: translated(labels, 'Edit connection'),
      cancel: translated(labels, 'Cancel'),
      connected: translated(labels, 'Connected'),
      disconnectedStatus: translated(labels, 'Disconnected'),
      attention: translated(labels, 'Needs attention'),
      connectedMessage: account.messages?.connected,
      disconnectedMessage: account.messages?.disconnected,
      attentionMessage: account.messages?.attention,
      close: translated(labels, 'Close'),
    }),
  };
}

/** Map one web setup guide and its server-owned copy values onto SetupGuide. */
export function setupGuideProps(guide, copy = {}, pageLabels = {}) {
  const labels = combinedLabels(guide, pageLabels);
  return {
    guide,
    copy,
    labels: compactLabels({
      showSteps: translated(labels, 'Show steps') || guide?.stepsLabel,
      hideSteps: translated(labels, 'Hide steps') || guide?.stepsLabel,
      copy: guide?.copyLabel || translated(labels, 'Copy'),
      copied: guide?.copiedLabel || translated(labels, 'Copied'),
      copyFailed: translated(labels, 'Could not copy'),
      close: guide?.closeLabel || translated(labels, 'Close'),
    }),
  };
}

function guidesFrom(model) {
  const source = model.setupGuides ?? model.setupGuide;
  if (!source) return [];
  return Array.isArray(source) ? source : [source];
}

function SectionHeading({ children, palette }) {
  return <Text role="heading" aria-level={3} style={[styles.heading, { color: palette.text }]}>{children}</Text>;
}

/**
 * Universal provider settings composition for the connection, import, account
 * and setup surfaces. `model` accepts the web provider view model:
 * `credentials`, `imports` (or `import`), `account` (or `accountConnection`),
 * `setupGuides`/`setupGuide`, `guideCopy`/`copy`, plus `labels` and `tab`.
 *
 * All transport stays with the caller. Entered secrets, CSRF values, saved
 * URLs and token-bearing copy values never enter this component.
 */
export function ProviderSettingsView({
  model,
  tab,
  labels,
  onSave,
  onClear,
  onOpenLink,
  linkPropsForUrl,
  onSetDefault,
  onFollowUp,
  onImport,
  onSavePreferences,
  onConfigure,
  onConnect,
  onDisconnect,
  onCopy,
  accountContent,
  palette = {},
  buttonPalette = {},
}) {
  const [setupHint, setSetupHint] = useState(null);
  if (!model) return null;
  const currentTab = tab || model.tab || 'connection';
  const sections = tabSections[currentTab] || [];
  if (!sections.length) return null;

  const colors = { ...defaultPalette, ...palette };
  // The "Get a key" link is an info chip. Palettes that carry no badge tokens
  // (the web theme reader) left it with a black border and no fill, so the
  // indigo info values are the fallback rather than undefined.
  const keyPalette = {
    ...colors,
    badgeBorder: colors.infoBadgeBorder || 'rgba(99, 102, 241, 0.3)',
    badgeBackground: colors.infoBadgeBackground || 'rgba(99, 102, 241, 0.1)',
    badgeText: colors.infoBadgeText || colors.link || '#c7d2fe',
    badgeHover: colors.infoBadgeHover || 'rgba(99, 102, 241, 0.2)',
  };
  const pageLabels = { ...(model.labels || {}), ...(labels || {}) };
  const credentials = model.credentials;
  const imports = model.imports || model.import;
  const account = model.account || model.accountConnection;
  const guides = guidesFrom(model);
  const copy = model.guideCopy || model.copy || {};

  const rendered = [];
  for (const section of sections) {
    if (section === 'credentials' && credentials) {
      const card = connectionSettingsModel(credentials, pageLabels);
      const heading = credentials.heading || translated(combinedLabels(credentials, pageLabels), 'Provider credentials');
      const keyLabel = (credentials.getKeyLabel || translated(combinedLabels(credentials, pageLabels), 'Get a key from {name}'))
        .replace('{name}', credentials.label || credentials.providerLabel || 'the provider');
      const keyAction = credentials.getKeyUrl
        ? () => onOpenLink?.(credentials.getKeyUrl)
        : undefined;
      rendered.push(<React.Fragment key="credentials">
        <View style={styles.headingRow}>
          {heading ? <SectionHeading palette={colors}>{heading}</SectionHeading> : null}
          {credentials.setupHint
            ? <Chip testID="provider-key-link" label={keyLabel} link hasPopup onPress={() => setSetupHint(credentials)} palette={keyPalette} />
            : credentials.getKeyUrl
              ? <Chip testID="provider-key-link" label={keyLabel} link onPress={keyAction}
                  {...(linkPropsForUrl?.(credentials.getKeyUrl) || {})} palette={keyPalette} />
              : null}
        </View>
        <ConnectionSettings model={card} onSave={onSave} onClear={onClear} showKeyLink={false}
          onOpenLink={onOpenLink} onSetDefault={onSetDefault} palette={palette} buttonPalette={buttonPalette} />
      </React.Fragment>);
    }
    if (section === 'imports' && imports) {
      const card = importSettingsModel(imports, pageLabels);
      const heading = imports.heading || translated(combinedLabels(imports, pageLabels), 'Import settings');
      rendered.push(<React.Fragment key="imports">
        {heading ? <SectionHeading palette={colors}>{heading}</SectionHeading> : null}
        <ImportSettings model={card} onImport={onImport} onSavePreferences={onSavePreferences}
          onConfigure={onConfigure} palette={palette} buttonPalette={buttonPalette} />
      </React.Fragment>);
    }
    if (section === 'account' && account) {
      const card = accountSettingsModel(account, pageLabels);
      const heading = account.heading || translated(combinedLabels(account, pageLabels), 'Connection settings');
      rendered.push(<React.Fragment key="account">
        {heading ? <SectionHeading palette={colors}>{heading}</SectionHeading> : null}
        {accountContent || <AccountSettings model={card} onConnect={onConnect} onDisconnect={onDisconnect}
          onFollowUp={followUp => (onFollowUp || onOpenLink)?.(followUp?.url)} palette={palette} buttonPalette={buttonPalette} />}
      </React.Fragment>);
    }
    if (section === 'guides') {
      if (model.guidesSessionRequired) rendered.push(<Text key="guide-session-required" style={{ color: colors.textMuted, fontSize: 14, lineHeight: 20 }}>Sign in with your username and password to manage this.</Text>);
      guides.forEach((guide, index) => rendered.push(<SetupGuide key={`guide-${index}`}
        {...setupGuideProps(guide, copy, pageLabels)} onCopy={onCopy} onOpenLink={onOpenLink}
        palette={palette} buttonPalette={buttonPalette} />));
    }
  }

  if (!rendered.length) return null;
  return <View testID="provider-settings" style={styles.stack}>
    {rendered}
    {setupHint ? <Dialog open title={translated(combinedLabels(setupHint, pageLabels), 'Get a key')}
      closeLabel={translated(combinedLabels(setupHint, pageLabels), 'Close')} palette={palette}
      onClose={() => setSetupHint(null)}>
      <Text style={{ color: colors.textSecondary, fontSize: 13, lineHeight: 19 }}>{setupHint.setupHint}</Text>
    </Dialog> : null}
  </View>;
}

const styles = StyleSheet.create({
  stack: { gap: 12 },
  headingRow: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: 8 },
  heading: { fontSize: 18, fontWeight: '600', lineHeight: 26, flexShrink: 1, minWidth: 0 },
});
