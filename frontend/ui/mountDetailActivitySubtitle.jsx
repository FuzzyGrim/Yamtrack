import React from 'react';
import { mountRoot } from '../settings/root';
import { DetailActivitySubtitle } from './DetailActivitySubtitle';

export function mountDetailActivitySubtitle(host) {
  const text = [host.dataset.dates, host.dataset.duration].filter(Boolean).join(' • ') || host.dataset.primary || '';
  return mountRoot(host, <DetailActivitySubtitle text={text} color="inherit"
    style={{ fontSize: 'inherit', fontWeight: 'inherit', textAlign: 'inherit' }} />);
}
