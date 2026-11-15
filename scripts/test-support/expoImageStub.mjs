// Test-only stand-in for expo-image: reports an error for URLs containing
// "missing" (a provider 404) and a successful load otherwise.
import React, { useEffect } from 'react';
export function Image({ source, onLoad, onError }) {
  const uri = typeof source === 'string' ? source : source?.uri;
  // Asynchronous, like a real network load.
  useEffect(() => {
    const timer = setTimeout(() => {
      if (String(uri).includes('missing')) onError?.({ error: 'HTTP 404' });
      else onLoad?.({ source: { width: 528, height: 704 } });
    }, 0);
    return () => clearTimeout(timer);
  }, [uri]);
  return React.createElement('img', { 'data-src': uri });
}
