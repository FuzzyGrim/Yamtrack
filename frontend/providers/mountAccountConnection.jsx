import React from 'react';
import { mountRoot } from '../settings/root';
import { Text } from 'react-native';
import { AccountSettings } from './AccountSettings';
import { ProviderLinkCard } from './ProviderLinkCard';
import { ProviderSettingsView, accountSettingsModel } from './ProviderSettingsView';
import { copyText, linkRequest, openExternal, readThemePalette, submitNativeForm } from './transport.web';

function draftFromFallback(host, model) {
  const draft = {};
  for (const field of model.fields || []) {
    const input = host.querySelector(`[name="${field.name}"]`);
    if (input && input.value !== input.defaultValue) draft[field.name] = input.value;
  }
  return draft;
}

export function mountAccountConnection(host, model) {
  const draft = draftFromFallback(host, model);
  const colors = readThemePalette();
  const account = {
    ...model,
    fields: (model.fields || []).map(field => ({
      ...field,
      value: draft[field.name] ?? field.value,
    })),
  };

  const disconnect = instanceId => submitNativeForm({
    action: model.disconnectUrl,
    csrf: model.csrf,
    returnTo: model.next,
    values: instanceId ? { instance_id: instanceId } : {},
  });
  // Device-code providers sign in through the same link card the app uses; the
  // idle state is the usual account card.
  const link = model.link;
  const accountContent = link ? <ProviderLinkCard
    label={link.label}
    palette={colors}
    initialDevice={model.device || undefined}
    onStart={() => linkRequest({ url: link.start, csrf: model.csrf })}
    onPoll={() => linkRequest({ url: link.poll, method: 'GET', csrf: model.csrf })}
    onCancel={() => linkRequest({ url: link.cancel, csrf: model.csrf })}
    onOpenUrl={openExternal}
    onCopy={copyText}
    onConnected={() => window.location.reload()}
    renderIdle={({ onConnect, error }) => <>
      <AccountSettings model={accountSettingsModel(account, model.labels)} onConnect={onConnect}
        onDisconnect={disconnect} palette={colors} />
      {error ? <Text accessibilityRole="alert" style={{ color: colors.danger, fontSize: 12 }}>{error}</Text> : null}
    </>} /> : undefined;

  return mountRoot(host, <ProviderSettingsView
    accountContent={accountContent}
    model={{ account, labels: model.labels }}
    tab="account"
    labels={model.labels}
    palette={colors}
    onConnect={values => submitNativeForm({
      action: model.connectUrl,
      csrf: model.csrf,
      returnTo: model.next,
      values,
    })}
    onDisconnect={disconnect}
    onOpenLink={url => { if (url) window.location.assign(url); }}
    onFollowUp={url => { if (url) window.location.assign(url); }}
  />);
}
