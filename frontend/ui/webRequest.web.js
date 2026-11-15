// Browser `request(path, { method, body, query, signal })` for web adapters
// that drive a shared transport core (for example createRemoteHomeActions).
// Web-only: the page is signed in with Django's cookie session, so requests
// stay same-origin and unsafe methods carry the CSRF token. Native supplies
// its own request from authTransportCore. Keep this small: endpoint logic
// belongs in the shared core, and there is deliberately no cache or retry.
const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

// Same encoding as the native transport: empty values are left out.
export function webQueryString(query = {}) {
  const parts = Object.entries(query || {})
    .filter(([, value]) => value !== undefined && value !== null && value !== '')
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`);
  return parts.length ? `?${parts.join('&')}` : '';
}

export function createWebRequest({ csrfToken = '', fetchImpl, onTiming } = {}) {
  const send = fetchImpl || ((url, init) => window.fetch(url, init));
  // Identical GETs already on the way share one answer; nothing is kept after.
  const inFlight = new Map();

  async function perform(url, path, method, options) {
    const started = Date.now();
    const headers = { Accept: 'application/json' };
    let body;
    if (options.body !== undefined) {
      headers['Content-Type'] = 'application/json';
      body = typeof options.body === 'string' ? options.body : JSON.stringify(options.body);
    }
    if (!SAFE_METHODS.has(method)) headers['X-CSRFToken'] = csrfToken || '';
    const response = await send(url, { method, headers, body, credentials: 'same-origin', signal: options.signal });
    const text = await response.text();
    if (onTiming) onTiming({ path, method, ms: Date.now() - started, bytes: new TextEncoder().encode(text).length });
    if (!response.ok) {
      const error = new Error(`Request failed: ${response.status}`);
      error.status = response.status;
      throw error;
    }
    return JSON.parse(text);
  }

  return function request(path, options = {}) {
    const method = String(options.method || 'GET').toUpperCase();
    const url = `${path}${webQueryString(options.query)}`;
    // A caller that may cancel gets its own request, so cancelling never fails another caller.
    if (method !== 'GET' || options.signal) return perform(url, path, method, options);
    if (!inFlight.has(url)) {
      inFlight.set(url, perform(url, path, method, options).finally(() => inFlight.delete(url)));
    }
    return inFlight.get(url);
  };
}
