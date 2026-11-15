import React, { useCallback, useEffect, useRef, useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { SettingsCard } from '../settings/SettingsCard';
import { Button } from '../ui/Button';
import { DeviceConnectCard } from '../ui/DeviceConnect';
import { TextField } from '../ui/TextField';

export const providerLinkCopy = {
  title: 'Connect your account',
  copy: 'Sign in to this service from the app. You approve Floppy on the service, then return here.',
  deviceCopy: 'Enter this code on the service to approve Floppy.',
  action: 'Connect',
  starting: 'Starting…',
  serverUrl: 'Server URL',
  redirectWaiting: 'Finish signing in in your browser, then return to Floppy.',
  redirectDone: 'Check connection',
  failed: 'Could not start the connection. Try again.',
};

/**
 * Connect a provider that signs in through a device code or a browser redirect.
 * The caller owns transport: `onStart` answers {kind: 'device', userCode, openUrl,
 * interval} or {kind: 'redirect', authorizeUrl}; `onPoll` answers {status,
 * message} for a device code. Props only, no platform API.
 */
export function ProviderLinkCard({
  label = '',
  needsServerUrl = false,
  labels,
  palette = {},
  buttonPalette = {},
  onStart,
  onPoll,
  onCancel,
  onOpenUrl,
  onCopy,
  onConnected,
  renderIdle,
  initialDevice,
}) {
  const copy = { ...providerLinkCopy, ...(labels || {}) };
  // A code started before a reload resumes instead of asking the person to start over.
  const [phase, setPhase] = useState(initialDevice ? 'device' : 'idle');
  const [device, setDevice] = useState(initialDevice || null);
  const [wait, setWait] = useState({ state: 'waiting', error: '' });
  const [serverUrl, setServerUrl] = useState('');
  const [error, setError] = useState('');
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);

  const start = useCallback(async () => {
    setError('');
    setPhase('starting');
    try {
      const result = await onStart?.(needsServerUrl ? { serverUrl: serverUrl.trim() } : {});
      if (!alive.current) return;
      if (result?.kind === 'device') {
        setDevice(result);
        setWait({ state: 'waiting', error: '' });
        setPhase('device');
      } else if (result?.kind === 'redirect' && result.authorizeUrl) {
        onOpenUrl?.(result.authorizeUrl);
        setPhase('redirect');
      } else {
        throw new Error(copy.failed);
      }
    } catch (reason) {
      if (!alive.current) return;
      setError(reason?.message || copy.failed);
      setPhase('idle');
    }
  }, [onStart, onOpenUrl, needsServerUrl, serverUrl, copy.failed]);

  const interval = Math.max(Number(device?.interval) || 5, 2) * 1000;
  useEffect(() => {
    if (phase !== 'device' || wait.state !== 'waiting') return undefined;
    const timer = setTimeout(async () => {
      try {
        const result = await onPoll?.();
        if (!alive.current) return;
        const status = result?.status;
        if (status === 'connected') {
          setWait({ state: 'approved', error: '' });
          onConnected?.();
        } else if (status === 'expired') setWait({ state: 'expired', error: '' });
        else if (status === 'denied' || status === 'error' || status === 'idle') {
          setWait({ state: 'error', error: result?.message || '' });
        } else setWait(current => ({ ...current }));
      } catch (reason) {
        if (alive.current) setWait({ state: 'error', error: reason?.message || '' });
      }
    }, interval);
    return () => clearTimeout(timer);
  }, [phase, wait, interval, onPoll, onConnected]);

  async function cancel() {
    try { await onCancel?.(); } catch { /* the pending code expires on its own */ }
    if (!alive.current) return;
    setPhase('idle');
    setDevice(null);
  }

  const card = { ...palette, background: palette.surface ?? palette.background };
  if (phase === 'device' && device) {
    return <DeviceConnectCard code={device.userCode} state={wait.state} error={wait.error}
      title={label ? `Connect ${label}` : copy.title} description={copy.deviceCopy}
      palette={palette} onCopy={onCopy} onOpen={() => onOpenUrl?.(device.openUrl || device.verificationUrl)}
      onCancel={cancel} onRetry={start} />;
  }
  if (phase === 'redirect') {
    return <SettingsCard title={copy.title} palette={card}>
      <Text style={[styles.copy, { color: palette.textMuted }]}>{copy.redirectWaiting}</Text>
      <View style={styles.row}>
        <Button label={copy.redirectDone} onPress={() => onConnected?.()} palette={buttonPalette} />
        <Button label="Cancel" tone="surface" onPress={() => setPhase('idle')} palette={buttonPalette} />
      </View>
    </SettingsCard>;
  }
  if (renderIdle) return renderIdle({ onConnect: start, busy: phase === 'starting', error });
  return <SettingsCard title={copy.title} palette={card}>
    <Text style={[styles.copy, { color: palette.textMuted }]}>{copy.copy}</Text>
    {needsServerUrl ? <TextField id="link-server-url" name="server_url" label={copy.serverUrl} type="url"
      value={serverUrl} onChangeText={setServerUrl} palette={palette} /> : null}
    {error ? <Text accessibilityRole="alert" style={[styles.copy, { color: palette.danger }]}>{error}</Text> : null}
    <View style={styles.row}>
      <Button label={phase === 'starting' ? copy.starting : copy.action} busy={phase === 'starting'}
        disabled={phase === 'starting' || (needsServerUrl && !serverUrl.trim())} onPress={start} palette={buttonPalette} />
    </View>
  </SettingsCard>;
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  copy: { fontSize: 13, lineHeight: 19 },
});
