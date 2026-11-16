const STORAGE_KEY = 'floppy.auth.session.v1';
const SESSION_VERSION = 1;
// Every scope the app's screens call. A social account gets a token with these
// after approving them on the server's device page.
export const NATIVE_APP_SCOPES = [
  'scrobble:write', 'progress:read', 'progress:write', 'watchlist:read', 'watchlist:write',
  'catalog:read', 'catalog:write', 'metadata:read', 'metadata:write', 'lists:read', 'lists:write',
  'music:read', 'music:write', 'podcasts:read', 'podcasts:write', 'statistics:read', 'statistics:write',
  'imports:read', 'imports:write', 'exports:read', 'sync:read', 'sync:write', 'user:read', 'user:write',
].join(' ');

// The built-in app's server-defined device-flow client. After the person
// approves full access on the server's page, its token carries FULL_ACCOUNT_SCOPE
// and may manage the account like a password sign-in.
export const FIRST_PARTY_CLIENT_ID = 'flp_oauth_floppy_app';
export const FULL_ACCOUNT_SCOPE = 'floppy:account';

function grantsFullAccount(scope) {
  return String(scope || '').split(/\s+/).includes(FULL_ACCOUNT_SCOPE);
}

const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);
const SCOPE_DENIAL = /^You do not have permission to perform this action\.?$/;
export const PASSWORD_SIGN_IN_REQUIRED = 'Sign in with your username and password to manage this. '
  + 'App sign-in through the browser can track and browse, but cannot change account settings, credentials or connections.';

export const AUTH_ERROR_CODES = Object.freeze({
  INVALID_SERVER: 'invalid_server',
  INVALID_CREDENTIALS: 'invalid_credentials',
  SIGNED_OUT: 'signed_out',
  EXPIRED: 'expired',
  REVOKED: 'revoked',
  FORBIDDEN: 'forbidden',
  CSRF: 'csrf',
  NOT_FOUND: 'not_found',
  RATE_LIMITED: 'rate_limited',
  SERVER: 'server',
  SERVER_UNREACHABLE: 'server_unreachable',
  UNSUPPORTED: 'unsupported',
  REQUEST_FAILED: 'request_failed',
});

export class AuthError extends Error {
  constructor(message, options = {}) {
    super(message);
    this.name = 'AuthError';
    this.code = options.code || AUTH_ERROR_CODES.REQUEST_FAILED;
    this.status = options.status || 0;
  }
}

function fail(code, message, options = {}) {
  return new AuthError(message, { ...options, code });
}

function normalizeToken(value) {
  return String(value || '').trim().replace(/^Bearer\s+/i, '');
}

export function safeRedirectPath(value, fallback = '/') {
  const raw = String(value || '').trim();
  if (!raw || raw.length > 2000) return fallback;
  if (!raw.startsWith('/') || raw.startsWith('//') || raw.includes('\\')) return fallback;
  if (/[\u0000-\u001f]/.test(raw)) return fallback;
  try {
    const url = new URL(raw, 'https://floppy.invalid');
    if (url.origin !== 'https://floppy.invalid') return fallback;
    return `${url.pathname}${url.search}${url.hash}`;
  } catch {
    return fallback;
  }
}

export function buildProviderLoginUrl({ serverUrl, providerId, redirectTo = '/', process = 'login' } = {}) {
  const baseUrl = normalizeServerUrl(serverUrl);
  const provider = String(providerId || '').trim();
  if (!/^[a-z0-9][a-z0-9_-]*$/i.test(provider)) {
    throw fail(AUTH_ERROR_CODES.INVALID_SERVER, 'Choose a valid sign-in provider.');
  }
  const params = new URLSearchParams({ process });
  const next = safeRedirectPath(redirectTo, '');
  if (next) params.set('next', next);
  return `${baseUrl}/accounts/${provider}/login/?${params.toString()}`;
}

export function buildDeviceVerificationUrl({ serverUrl, userCode = '' } = {}) {
  const baseUrl = normalizeServerUrl(serverUrl);
  const params = new URLSearchParams();
  const code = String(userCode || '').trim();
  if (code) params.set('user_code', code);
  return `${baseUrl}/oauth/device${params.size ? `?${params.toString()}` : ''}`;
}

function sameServerUrl(serverUrl, candidate) {
  const baseUrl = normalizeServerUrl(serverUrl);
  if (!candidate) return '';
  try {
    const url = new URL(String(candidate), `${baseUrl}/`);
    if (url.origin !== new URL(baseUrl).origin) {
      throw fail(AUTH_ERROR_CODES.INVALID_SERVER, 'The server returned a redirect to another site.');
    }
    return url.toString();
  } catch (error) {
    if (error instanceof AuthError) throw error;
    throw fail(AUTH_ERROR_CODES.INVALID_SERVER, 'The server returned an invalid redirect.');
  }
}

export function normalizeServerUrl(input) {
  const value = String(input || '').trim();
  if (!value) {
    throw fail(AUTH_ERROR_CODES.INVALID_SERVER, 'Enter the address of your Floppy server.');
  }
  let url;
  try {
    url = new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(value) ? value : `https://${value}`);
  } catch {
    throw fail(AUTH_ERROR_CODES.INVALID_SERVER, 'Enter a valid Floppy server address.');
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    throw fail(AUTH_ERROR_CODES.INVALID_SERVER, 'The Floppy server address must use http or https.');
  }
  if (url.username || url.password) {
    throw fail(AUTH_ERROR_CODES.INVALID_SERVER, 'Do not include credentials in the server address.');
  }
  if (url.search || url.hash) {
    throw fail(AUTH_ERROR_CODES.INVALID_SERVER, 'Enter the server address without a query or fragment.');
  }
  const path = url.pathname.replace(/\/+$/, '');
  return `${url.origin}${path === '/' ? '' : path}`;
}

function queryString(query = {}) {
  const parts = Object.entries(query)
    .filter(([, value]) => value !== undefined && value !== null && value !== '')
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`);
  return parts.length ? `?${parts.join('&')}` : '';
}

function requestUrl(baseUrl, path, query) {
  const value = String(path || '');
  if (!value.startsWith('/') || value.startsWith('//')) {
    throw fail(AUTH_ERROR_CODES.REQUEST_FAILED, 'The request path must be relative to the Floppy server.');
  }
  return `${baseUrl}${value}${queryString(query)}`;
}

function headerValue(headers, name) {
  if (!headers) return '';
  if (typeof headers.get === 'function') {
    return headers.get(name) || '';
  }
  const key = Object.keys(headers).find(entry => entry.toLowerCase() === name.toLowerCase());
  return key ? headers[key] : '';
}

function setCookies(response) {
  if (!response || !response.headers) return [];
  if (typeof response.headers.getSetCookie === 'function') {
    return response.headers.getSetCookie();
  }
  const raw = headerValue(response.headers, 'set-cookie');
  if (!raw) return [];
  if (Array.isArray(raw)) return raw;
  return String(raw).split(/,(?=\s*[^;,=\s]+=)/);
}

function cookieName(raw) {
  const pair = String(raw || '').split(';')[0];
  const index = pair.indexOf('=');
  if (index <= 0) return '';
  return pair.slice(0, index).trim();
}

// iOS joins repeated Set-Cookie headers with a comma, so one value can arrive
// as "A,csrftoken=B". Django cookie values never hold a comma: keep the newest.
function cleanCookieValue(value) {
  return String(value || '').split(/,\s*[^;,=\s]+=/).pop().trim();
}

function cookieValue(raw) {
  const pair = String(raw || '').split(';')[0];
  const index = pair.indexOf('=');
  return index >= 0 ? cleanCookieValue(pair.slice(index + 1)) : '';
}

function mergeCookies(cookies, response) {
  const next = { ...cookies };
  setCookies(response).forEach(raw => {
    const name = cookieName(raw);
    if (!name) return;
    const value = cookieValue(raw);
    if (!value || /max-age=0/i.test(String(raw))) {
      delete next[name];
      return;
    }
    next[name] = value;
  });
  return next;
}

function cleanCookieText(text) {
  return String(text || '').split(';')
    .map(part => part.trim())
    .filter(Boolean)
    .map(part => {
      const index = part.indexOf('=');
      return index > 0 ? `${part.slice(0, index)}=${cleanCookieValue(part.slice(index + 1))}` : part;
    })
    .join('; ');
}

function cookieHeader(cookies) {
  return Object.entries(cookies)
    .filter(([name]) => name === 'sessionid' || name === 'csrftoken')
    .map(([name, value]) => `${name}=${cleanCookieValue(value)}`)
    .join('; ');
}

function extractDetail(data) {
  if (!data) return '';
  if (typeof data === 'string') {
    const detail = data.trim();
    if (/<(?:!doctype|html|head|body|title|meta|style|script)\b/i.test(detail)) return '';
    return detail.slice(0, 300);
  }
  const value = data.detail || data.error || data.message;
  return typeof value === 'string' ? value.trim().slice(0, 300) : '';
}

function firstFormError(data) {
  if (!data || typeof data !== 'object' || !data.errors) return '';
  for (const value of Object.values(data.errors)) {
    if (Array.isArray(value) && value.length) return String(value[0]).trim().slice(0, 300);
    if (typeof value === 'string' && value.trim()) return value.trim().slice(0, 300);
  }
  return '';
}

function redact(message, session) {
  let text = String(message || '');
  const secrets = [session && session.token, session && session.csrfToken];
  if (session && session.cookies) {
    Object.values(session.cookies).forEach(value => secrets.push(value));
  }
  secrets.filter(Boolean).forEach(secret => {
    text = text.split(String(secret)).join('[redacted]');
  });
  return text;
}

function isCsrfFailure(data) {
  const text = `${extractDetail(data)} ${typeof data === 'string' ? data : ''}`;
  return /csrf/i.test(text);
}

async function readBody(response) {
  try {
    const text = typeof response.text === 'function' ? await response.text() : '';
    if (!text) return null;
    try {
      return JSON.parse(text);
    } catch {
      return text;
    }
  } catch {
    return null;
  }
}

function classifyFailure(response, data, session) {
  const detail = extractDetail(data);
  if (response.status === 401) {
    const expired = /expired/i.test(detail);
    if (session && session.authType === 'token' && !expired) {
      return fail(AUTH_ERROR_CODES.REVOKED, 'The access token was revoked or is no longer valid.', { status: response.status });
    }
    return fail(AUTH_ERROR_CODES.EXPIRED, 'The saved session has expired. Sign in again.', { status: response.status });
  }
  if (response.status === 403) {
    // DRF's session authentication reports a missing/expired cookie as 403.
    // A permission denial must keep the session; only this auth error expires it.
    if (session?.authType === 'session' && detail === 'Authentication credentials were not provided.') {
      return fail(AUTH_ERROR_CODES.EXPIRED, 'The saved session has expired. Sign in again.', { status: response.status });
    }
    if (isCsrfFailure(data)) {
      return fail(AUTH_ERROR_CODES.CSRF, 'The security token expired. Try the request again.', { status: response.status });
    }
    // DRF's generic denial is what HasScope returns. A scoped token (a pasted
    // one, or a device sign-in the server granted without full access) never
    // reaches session-only endpoints (credentials, tokens, connections, admin),
    // so say how to get there instead of a raw refusal. A full-access app token
    // is refused only for a real reason, which the detail already states.
    if (session && session.authType === 'token' && !session.fullAccess && SCOPE_DENIAL.test(detail)) {
      return fail(AUTH_ERROR_CODES.FORBIDDEN, PASSWORD_SIGN_IN_REQUIRED, { status: response.status });
    }
    return fail(AUTH_ERROR_CODES.FORBIDDEN, detail || 'The server denied this request.', { status: response.status });
  }
  if (response.status === 404) {
    return fail(AUTH_ERROR_CODES.NOT_FOUND, detail || 'The server does not expose this endpoint.', { status: response.status });
  }
  if (response.status === 429) {
    return fail(AUTH_ERROR_CODES.RATE_LIMITED, 'Too many requests. Wait a moment and try again.', { status: response.status });
  }
  if (response.status >= 500) {
    return fail(AUTH_ERROR_CODES.SERVER, 'The Floppy server returned an error. Try again later.', { status: response.status });
  }
  return fail(AUTH_ERROR_CODES.REQUEST_FAILED, detail || `The server rejected the request (${response.status}).`, { status: response.status });
}

function publicSession(session) {
  if (!session) return null;
  return {
    serverUrl: session.serverUrl,
    authType: session.authType,
    user: session.user || null,
    lastVerifiedAt: session.lastVerifiedAt || null,
  };
}

function publicState(state) {
  return {
    status: state.status,
    serverUrl: state.serverUrl,
    authType: state.authType,
    user: state.user,
    hasCredential: state.status === 'authenticated',
    error: state.error,
    redirectTo: state.redirectTo || null,
  };
}

function sessionFacade(session, api) {
  if (!session) return null;
  return {
    serverUrl: session.serverUrl,
    authType: session.authType,
    user: session.user || null,
    request: (path, options) => api.request(path, options),
    getState: () => publicState(api.state),
    invalidate: () => api.invalidate(AUTH_ERROR_CODES.SIGNED_OUT),
  };
}

// `browser` is the web adapter: `{ serverUrl(), readCsrfToken() }`. The credential
// is then the same-origin session cookie, which script can neither read nor
// store, so sessions carry only a marker and the CSRF value is read per request.
export function createAuthTransport({ storage, fetchImpl, onSessionChange, browser = null, now = () => Date.now() } = {}) {
  if (!storage || typeof storage.getItem !== 'function') {
    throw new TypeError('createAuthTransport requires a storage adapter.');
  }
  const fetcher = fetchImpl || ((...args) => fetch(...args));
  const state = {
    status: 'signed_out',
    serverUrl: '',
    authType: '',
    user: null,
    error: null,
    redirectTo: null,
  };
  let session = null;

  function publish() {
    if (onSessionChange) {
      onSessionChange(sessionFacade(session, api));
    }
  }

  function setState(next) {
    Object.assign(state, next);
  }

  async function persist(next) {
    if (next && next.browser) return;
    await storage.setItem(STORAGE_KEY, JSON.stringify(next));
  }

  async function removeStored() {
    await storage.removeItem(STORAGE_KEY);
  }

  async function invalidate(code) {
    await removeStored().catch(() => {});
    setState({
      status: code === AUTH_ERROR_CODES.REVOKED ? 'revoked' : code === AUTH_ERROR_CODES.EXPIRED ? 'expired' : 'signed_out',
      authType: '',
      user: state.user,
      error: code,
      redirectTo: null,
    });
    session = null;
    publish();
    return publicState(state);
  }

  async function perform(url, options) {
    try {
      return await fetcher(url, options);
    } catch (error) {
      // A request the caller cancelled is not an unreachable server.
      if (options?.signal?.aborted) throw error;
      throw fail(AUTH_ERROR_CODES.SERVER_UNREACHABLE, 'Could not reach the Floppy server. Check the address and connection.');
    }
  }

  function resolveBase(serverUrl) {
    return browser ? normalizeServerUrl(browser.serverUrl()) : normalizeServerUrl(serverUrl);
  }

  function credentialsMode(current) {
    if (current.authType === 'token') return 'omit';
    // Native sends its cookies by hand (authHeaders); let iOS add none of its own, which it joins with a comma.
    return browser ? 'same-origin' : 'omit';
  }

  function authHeaders(target, method, current) {
    const headers = { Accept: 'application/json' };
    if (current.authType === 'token') {
      headers.Authorization = `Bearer ${current.token}`;
    } else if (current.browser) {
      const csrfToken = browser.readCsrfToken();
      if (csrfToken && !SAFE_METHODS.has(method)) headers['X-CSRFToken'] = csrfToken;
    } else if (current.cookieText) {
      headers.Cookie = cleanCookieText(current.cookieText);
      if (current.csrfToken) headers['X-CSRFToken'] = cleanCookieValue(current.csrfToken);
      if (!SAFE_METHODS.has(method)) {
        headers.Origin = target;
        headers.Referer = `${target}/`;
      }
    }
    return headers;
  }

  async function refreshCsrf(current) {
    const target = current.serverUrl;
    const response = await perform(`${target}/accounts/login/`, {
      method: 'GET',
      headers: { Accept: 'text/html' },
    });
    if (!response.ok) {
      throw fail(AUTH_ERROR_CODES.CSRF, 'Could not refresh the security token. Sign in again.', { status: response.status });
    }
    current.cookies = mergeCookies(current.cookies || {}, response);
    current.csrfToken = current.cookies.csrftoken || '';
    current.cookieText = cookieHeader(current.cookies);
    if (!current.csrfToken) {
      throw fail(AUTH_ERROR_CODES.CSRF, 'The server did not provide a security token. Sign in again.');
    }
    if (current === session) await persist(session).catch(() => {});
    return current;
  }

  async function send(current, path, options = {}) {
    const method = String(options.method || 'GET').toUpperCase();
    const target = current.serverUrl;
    const headers = {
      ...authHeaders(target, method, current),
      ...(options.headers || {}),
    };
    let body;
    if (options.body !== undefined || options.json !== undefined) {
      headers['Content-Type'] = 'application/json';
      body = JSON.stringify(options.body !== undefined ? options.body : options.json);
    } else if (options.form !== undefined) {
      headers['Content-Type'] = 'application/x-www-form-urlencoded';
      const params = new URLSearchParams();
      Object.entries(options.form).forEach(([key, value]) => {
        if (Array.isArray(value)) {
          value.forEach(item => params.append(key, String(item)));
        } else if (value !== undefined && value !== null) {
          params.append(key, String(value));
        }
      });
      body = params.toString();
    }
    const response = await perform(requestUrl(target, path, options.query), {
      method,
      headers,
      body,
      credentials: credentialsMode(current),
      ...(options.signal ? { signal: options.signal } : {}),
    });
    if (current.authType === 'session' && !current.browser) {
      const cookies = mergeCookies(current.cookies || {}, response);
      if (JSON.stringify(cookies) !== JSON.stringify(current.cookies || {})) {
        current.cookies = cookies;
        current.csrfToken = cookies.csrftoken || current.csrfToken || '';
        current.cookieText = cookieHeader(cookies);
        if (current === session) await persist(session).catch(() => {});
      }
    }
    const data = response.ok && options.responseType === 'text' ? await response.text() : await readBody(response);
    if (response.status === 403 && current.authType === 'session' && !current.browser && isCsrfFailure(data) && !options.csrfRetried) {
      await refreshCsrf(current);
      return send(current, path, { ...options, csrfRetried: true });
    }
    if (response.status === 401 && current.authType === 'token' && current.refreshToken && !options.tokenRefreshed) {
      if (await refreshAccessToken(current)) return send(current, path, { ...options, tokenRefreshed: true });
    }
    if (!response.ok) {
      const error = classifyFailure(response, data, current);
      error.message = redact(error.message, current);
      if (data && typeof data === 'object' && data.errors && typeof data.errors === 'object') {
        error.errors = data.errors;
      }
      if (current === session && (error.code === AUTH_ERROR_CODES.EXPIRED || error.code === AUTH_ERROR_CODES.REVOKED)) {
        await invalidate(error.code);
      }
      throw error;
    }
    return data;
  }

  let refreshing = null;

  // Access tokens from the device flow last an hour; the refresh token rotates,
  // so only one refresh may be in flight and the new pair must be stored.
  function refreshAccessToken(current) {
    if (!refreshing) {
      refreshing = (async () => {
        try {
          const response = await perform(`${current.serverUrl}/oauth/token`, {
            method: 'POST',
            headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
            body: JSON.stringify({
              grant_type: 'refresh_token',
              client_id: current.clientId,
              refresh_token: current.refreshToken,
            }),
            credentials: 'omit',
          });
          const data = await readBody(response);
          if (!response.ok || !data || !data.access_token) return false;
          current.token = data.access_token;
          if (data.refresh_token) current.refreshToken = data.refresh_token;
          if (typeof data.scope === 'string') current.fullAccess = current.clientId === FIRST_PARTY_CLIENT_ID && grantsFullAccount(data.scope);
          if (current === session) await persist(session).catch(() => {});
          return true;
        } catch {
          return false;
        } finally {
          refreshing = null;
        }
      })();
    }
    return refreshing;
  }

  async function verifyCredential(current, path = '/api/v1/user/sidebar/') {
    return send(current, path);
  }

  // The native client's account payload carries isSuperuser and isDemo. Token
  // sessions cannot read it (session-only route), so a failure is not an error.
  async function attachAccountUser(current) {
    if (current.authType !== 'session') return;
    try {
      const data = await send(current, '/settings/account', { headers: { 'X-Floppy-Client': 'native' } });
      if (data && typeof data === 'object' && data.user && data.user.username) {
        current.user = { ...(current.user || {}), ...data.user };
      }
    } catch {
      // Keep the username from sign-in.
    }
  }

  function newSessionCandidate({ baseUrl, cookies = {}, csrfToken = '', user = null }) {
    if (browser) {
      return {
        version: SESSION_VERSION,
        serverUrl: baseUrl,
        authType: 'session',
        browser: true,
        cookies: {},
        cookieText: '',
        csrfToken: '',
        user,
        createdAt: now(),
        lastVerifiedAt: null,
      };
    }
    return {
      version: SESSION_VERSION,
      serverUrl: baseUrl,
      authType: 'session',
      cookies,
      csrfToken: cookies.csrftoken || csrfToken,
      cookieText: cookieHeader(cookies),
      user,
      createdAt: now(),
      lastVerifiedAt: null,
    };
  }

  // Headers for a form post made before a session exists. A browser sends the
  // cookie jar itself and forbids setting Cookie, Origin and Referer.
  function formPostOptions(baseUrl, path, formToken, extraHeaders = {}) {
    const headers = {
      Accept: 'application/json',
      'Content-Type': 'application/x-www-form-urlencoded',
      'X-CSRFToken': formToken.csrfToken,
      ...extraHeaders,
    };
    if (!browser) {
      headers.Cookie = cookieHeader(formToken.cookies);
      headers.Origin = baseUrl;
      headers.Referer = `${baseUrl}${path}`;
    }
    return { headers, credentials: browser ? 'same-origin' : 'omit' };
  }

  async function loadFormToken(baseUrl, path) {
    const page = await perform(`${baseUrl}${path}`, {
      method: 'GET',
      headers: { Accept: 'text/html' },
      ...(browser ? { credentials: 'same-origin' } : {}),
    });
    if (!page.ok) {
      throw fail(AUTH_ERROR_CODES.INVALID_SERVER, 'The server form could not be loaded.', { status: page.status });
    }
    const cookies = browser ? {} : mergeCookies({}, page);
    const csrfToken = browser ? browser.readCsrfToken() : (cookies.csrftoken || '');
    if (!csrfToken) {
      throw fail(AUTH_ERROR_CODES.CSRF, 'The server did not provide a security token.');
    }
    return { cookies, csrfToken };
  }

  async function fetchAuthConfig({ serverUrl, signal } = {}) {
    const baseUrl = resolveBase(serverUrl);
    const response = await perform(`${baseUrl}/accounts/auth-config/`, {
      method: 'GET',
      headers: { Accept: 'application/json' },
      ...(signal ? { signal } : {}),
    });
    const data = await readBody(response);
    if (!response.ok) throw classifyFailure(response, data, null);
    const providers = Array.isArray(data?.providers) ? data.providers.map(provider => {
      let loginUrl = '';
      try {
        loginUrl = sameServerUrl(baseUrl, provider.loginUrl);
      } catch {
        loginUrl = '';
      }
      return {
        id: String(provider.id || ''),
        name: String(provider.name || provider.id || ''),
        loginUrl,
      };
    }).filter(provider => provider.id && provider.name) : [];
    return {
      registrationEnabled: data?.registrationEnabled !== false,
      socialOnly: data?.socialOnly === true,
      firstRun: data?.firstRun === true,
      serverUrl: baseUrl,
      providers,
      connectionsUrl: sameServerUrl(baseUrl, data?.connectionsUrl),
      deviceUrl: sameServerUrl(baseUrl, data?.deviceUrl) || buildDeviceVerificationUrl({ serverUrl: baseUrl }),
    };
  }

  async function signUp({ serverUrl, username, password1, password2, setupAuthenticator = false, redirectTo = '/setup/' } = {}) {
    const baseUrl = resolveBase(serverUrl);
    const login = String(username || '').trim();
    const secret = String(password1 || '');
    const confirmation = String(password2 || '');
    if (!login || !secret) {
      throw fail(AUTH_ERROR_CODES.INVALID_CREDENTIALS, 'Enter a username and password.');
    }
    if (secret !== confirmation) {
      throw fail(AUTH_ERROR_CODES.INVALID_CREDENTIALS, 'The passwords do not match.');
    }
    // A new account is taken into guided setup first, like the web signup.
    const next = setupAuthenticator ? '/settings/account' : safeRedirectPath(redirectTo, '/setup/');
    setState({ status: 'authenticating', serverUrl: baseUrl, authType: 'session', user: { username: login }, error: null, redirectTo: null });
    publish();

    let formToken;
    try {
      formToken = await loadFormToken(baseUrl, '/accounts/signup/');
    } catch (error) {
      setState({ status: 'signed_out', error: error.code });
      publish();
      throw error;
    }
    const form = new URLSearchParams({
      username: login,
      password1: secret,
      password2: confirmation,
      csrfmiddlewaretoken: formToken.csrfToken,
      next,
    });
    const response = await perform(`${baseUrl}/accounts/signup/`, {
      method: 'POST',
      ...formPostOptions(baseUrl, '/accounts/signup/', formToken, { 'X-Floppy-Client': 'native' }),
      body: form.toString(),
    });
    const data = await readBody(response);
    const cookies = mergeCookies(formToken.cookies, response);
    if (!response.ok || !data || data.ok !== true) {
      const message = firstFormError(data) || extractDetail(data) || 'Could not create the account. Check the details and try again.';
      setState({ status: 'signed_out', authType: '', user: null, error: AUTH_ERROR_CODES.INVALID_CREDENTIALS });
      publish();
      throw fail(AUTH_ERROR_CODES.INVALID_CREDENTIALS, message, { status: response.status });
    }
    const candidate = newSessionCandidate({ baseUrl, cookies, csrfToken: formToken.csrfToken, user: { username: login } });
    session = candidate;
    try {
      await verifyCredential(candidate);
    } catch (error) {
      session = null;
      setState({ status: 'signed_out', authType: '', user: null, error: error.code });
      publish();
      throw error;
    }
    await attachAccountUser(candidate);
    candidate.lastVerifiedAt = now();
    await persist(candidate);
    setState({ status: 'authenticated', authType: 'session', user: candidate.user, error: null, redirectTo: next });
    publish();
    return publicState(state);
  }

  async function recoverPassword({ serverUrl, username, authenticatorCode = '', recoveryCode = '', newPassword1, newPassword2 } = {}) {
    const baseUrl = resolveBase(serverUrl);
    const login = String(username || '').trim();
    const code = String(authenticatorCode || '').trim();
    const recovery = String(recoveryCode || '').trim();
    const secret = String(newPassword1 || '');
    if (!login || (!code && !recovery) || !secret || secret !== String(newPassword2 || '')) {
      throw fail(AUTH_ERROR_CODES.INVALID_CREDENTIALS, 'Enter your username, recovery code and matching new passwords.');
    }
    const formToken = await loadFormToken(baseUrl, '/accounts/password/recover/');
    const form = new URLSearchParams({
      username: login,
      csrfmiddlewaretoken: formToken.csrfToken,
    });
    if (recovery) form.set('recovery_code', recovery);
    else form.set('authenticator_code', code);
    form.set('new_password1', secret);
    form.set('new_password2', String(newPassword2 || ''));
    const response = await perform(`${baseUrl}/accounts/password/recover/`, {
      method: 'POST',
      ...formPostOptions(baseUrl, '/accounts/password/recover/', formToken, { 'X-Floppy-Client': 'native' }),
      body: form.toString(),
    });
    const data = await readBody(response);
    if (!response.ok || !data || data.ok !== true) {
      const message = firstFormError(data) || extractDetail(data) || 'Unable to verify recovery details.';
      throw fail(AUTH_ERROR_CODES.INVALID_CREDENTIALS, message, { status: response.status });
    }
    return { ok: true };
  }

  async function signInWithToken({ serverUrl, token, user, refreshToken = '', clientId = '', fullAccess = false } = {}) {
    const baseUrl = resolveBase(serverUrl);
    const value = normalizeToken(token);
    if (!value) {
      throw fail(AUTH_ERROR_CODES.INVALID_CREDENTIALS, 'Enter an access token or sign in with a username and password.');
    }
    setState({ status: 'authenticating', serverUrl: baseUrl, authType: 'token', user: user || null, error: null, redirectTo: null });
    publish();
    const candidate = {
      version: SESSION_VERSION,
      serverUrl: baseUrl,
      authType: 'token',
      token: value,
      ...(refreshToken && clientId ? { refreshToken, clientId } : {}),
      ...(fullAccess ? { fullAccess: true } : {}),
      user: user || null,
      createdAt: now(),
      lastVerifiedAt: null,
    };
    session = candidate;
    try {
      await verifyCredential(candidate);
    } catch (error) {
      session = null;
      setState({ status: 'signed_out', authType: '', user: null, error: error.code });
      publish();
      throw error;
    }
    candidate.lastVerifiedAt = now();
    await persist(candidate);
    setState({ status: 'authenticated', authType: 'token', user: candidate.user, error: null, redirectTo: '/' });
    publish();
    return publicState(state);
  }

  // OAuth device flow (RFC 8628): the way a social or OIDC account signs in
  // from an app. The person approves on the server's own page, in a browser
  // where the provider login works. The app signs in as the first-party client,
  // whose approval grants full account access; a server that does not know that
  // client yet gets the old registration and a scoped token.
  async function startDeviceSignIn({ serverUrl, clientName = 'Floppy app', scope = NATIVE_APP_SCOPES } = {}) {
    const baseUrl = resolveBase(serverUrl);
    const post = async (path, body) => {
      const response = await perform(`${baseUrl}${path}`, {
        method: 'POST',
        headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        credentials: 'omit',
      });
      const data = await readBody(response);
      if (!response.ok || !data || typeof data !== 'object') {
        const text = data && typeof data === 'object' ? (data.error_description || data.error) : '';
        const error = fail(AUTH_ERROR_CODES.REQUEST_FAILED, String(text || 'The server could not start device sign-in.').slice(0, 300), { status: response.status });
        error.oauthError = data && typeof data === 'object' ? String(data.error || '') : '';
        throw error;
      }
      return data;
    };
    const authorize = clientId => post('/oauth/device/authorization', {
      client_id: clientId,
      ...(scope ? { scope } : {}),
    });
    let clientId = FIRST_PARTY_CLIENT_ID;
    let grant;
    try {
      grant = await authorize(clientId);
    } catch (error) {
      if (error.oauthError !== 'invalid_client') throw error;
      const client = await post('/oauth/register', { client_name: clientName });
      clientId = String(client.client_id);
      grant = await authorize(clientId);
    }
    return {
      serverUrl: baseUrl,
      clientId,
      deviceCode: String(grant.device_code),
      userCode: String(grant.user_code),
      verificationUri: sameServerUrl(baseUrl, grant.verification_uri),
      verificationUriComplete: sameServerUrl(baseUrl, grant.verification_uri_complete),
      interval: Math.max(1, Number(grant.interval) || 5),
      expiresIn: Math.max(1, Number(grant.expires_in) || 600),
    };
  }

  async function pollDeviceSignIn({ serverUrl, clientId, deviceCode, interval = 5, expiresIn = 600, sleep, signal } = {}) {
    const baseUrl = resolveBase(serverUrl);
    const wait = sleep || (ms => new Promise(resolve => setTimeout(resolve, ms)));
    let delay = Math.max(1, Number(interval) || 5);
    const deadline = now() + Math.max(1, Number(expiresIn) || 600) * 1000;
    while (now() < deadline) {
      if (signal && signal.aborted) throw fail(AUTH_ERROR_CODES.SIGNED_OUT, 'Sign-in was cancelled.');
      await wait(delay * 1000);
      if (signal && signal.aborted) throw fail(AUTH_ERROR_CODES.SIGNED_OUT, 'Sign-in was cancelled.');
      const response = await perform(`${baseUrl}/oauth/token`, {
        method: 'POST',
        headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
        body: JSON.stringify({
          grant_type: 'urn:ietf:params:oauth:grant-type:device_code',
          client_id: clientId,
          device_code: deviceCode,
        }),
        credentials: 'omit',
      });
      const data = await readBody(response);
      if (response.ok && data && data.access_token) {
        return signInWithToken({
          serverUrl: baseUrl,
          token: data.access_token,
          refreshToken: data.refresh_token || '',
          clientId,
          fullAccess: clientId === FIRST_PARTY_CLIENT_ID && grantsFullAccount(data.scope),
        });
      }
      const code = data && typeof data === 'object' ? data.error : '';
      if (code === 'authorization_pending') continue;
      if (code === 'slow_down') {
        delay += 5;
        continue;
      }
      if (code === 'access_denied') throw fail(AUTH_ERROR_CODES.FORBIDDEN, 'The sign-in request was denied on the server.', { status: response.status });
      if (code === 'expired_token') break;
      throw fail(AUTH_ERROR_CODES.REQUEST_FAILED, 'The server rejected the sign-in request.', { status: response.status });
    }
    throw fail(AUTH_ERROR_CODES.EXPIRED, 'The sign-in code expired. Start again.');
  }

  async function signIn({ serverUrl, username, password, remember = true, redirectTo = '/' } = {}) {
    const baseUrl = resolveBase(serverUrl);
    const login = String(username || '').trim();
    const secret = String(password || '');
    if (!login || !secret) {
      throw fail(AUTH_ERROR_CODES.INVALID_CREDENTIALS, 'Enter your username and password.');
    }
    const next = safeRedirectPath(redirectTo);
    setState({ status: 'authenticating', serverUrl: baseUrl, authType: 'session', user: { username: login }, error: null, redirectTo: null });
    publish();

    const loginPage = await perform(`${baseUrl}/accounts/login/`, {
      method: 'GET',
      headers: { Accept: 'text/html' },
      ...(browser ? { credentials: 'same-origin' } : {}),
    });
    if (!loginPage.ok) {
      setState({ status: 'signed_out', error: AUTH_ERROR_CODES.INVALID_SERVER });
      publish();
      throw fail(AUTH_ERROR_CODES.INVALID_SERVER, 'The server login page could not be loaded.', { status: loginPage.status });
    }
    let cookies = browser ? {} : mergeCookies({}, loginPage);
    let csrfToken = browser ? browser.readCsrfToken() : (cookies.csrftoken || '');
    if (!csrfToken) {
      setState({ status: 'signed_out', error: AUTH_ERROR_CODES.CSRF });
      publish();
      throw fail(AUTH_ERROR_CODES.CSRF, 'The server did not provide a login security token.');
    }

    async function postLogin() {
      const form = new URLSearchParams({
        login,
        password: secret,
        csrfmiddlewaretoken: csrfToken,
      });
      if (remember) form.set('remember', 'on');
      if (next !== '/') form.set('next', next);
      const options = formPostOptions(baseUrl, '/accounts/login/', { cookies, csrfToken }, { Accept: 'text/html, application/json' });
      return perform(`${baseUrl}/accounts/login/`, {
        method: 'POST',
        ...options,
        body: form.toString(),
        // Django answers a successful login with a 302 that carries the
        // session cookie. Native reads that redirect itself because following
        // it would hide the Set-Cookie. A browser stores the cookie either way
        // and cannot read a redirect, so it follows and verifies below.
        ...(browser ? {} : { redirect: 'manual' }),
      });
    }

    let response = await postLogin();
    if (response.status === 403) {
      cookies = browser ? {} : mergeCookies(cookies, response);
      csrfToken = browser ? browser.readCsrfToken() : (cookies.csrftoken || '');
      if (csrfToken) response = await postLogin();
    }
    if (response.status === 200 && !browser) {
      setState({ status: 'signed_out', error: AUTH_ERROR_CODES.INVALID_CREDENTIALS });
      publish();
      throw fail(AUTH_ERROR_CODES.INVALID_CREDENTIALS, 'Incorrect username or password.', { status: response.status });
    }
    if (!browser) cookies = mergeCookies(cookies, response);
    const candidate = newSessionCandidate({ baseUrl, cookies, csrfToken, user: { username: login } });
    session = candidate;
    try {
      await verifyCredential(candidate);
    } catch (error) {
      session = null;
      setState({ status: 'signed_out', authType: '', user: null, error: error.code });
      publish();
      if (error.code === AUTH_ERROR_CODES.EXPIRED || (browser && error.code === AUTH_ERROR_CODES.FORBIDDEN)) {
        throw fail(AUTH_ERROR_CODES.INVALID_CREDENTIALS, 'Sign-in failed. Check your username and password.');
      }
      throw error;
    }
    await attachAccountUser(candidate);
    candidate.lastVerifiedAt = now();
    await persist(candidate);
    setState({ status: 'authenticated', authType: 'session', user: candidate.user, error: null, redirectTo: next });
    publish();
    return publicState(state);
  }

  // The browser holds the credential, so restoring means asking the server
  // whether the same-origin cookie is a live session. Nothing is stored.
  async function restoreBrowserSession() {
    const baseUrl = resolveBase('');
    const candidate = newSessionCandidate({ baseUrl });
    const signedOut = error => {
      session = null;
      setState({ status: 'signed_out', serverUrl: baseUrl, authType: '', user: null, error, redirectTo: null });
      publish();
      return publicState(state);
    };
    try {
      await verifyCredential(candidate);
    } catch (error) {
      const expected = error.code === AUTH_ERROR_CODES.EXPIRED || error.code === AUTH_ERROR_CODES.FORBIDDEN;
      return signedOut(expected ? null : error.code);
    }
    await attachAccountUser(candidate);
    candidate.lastVerifiedAt = now();
    session = candidate;
    setState({ status: 'authenticated', serverUrl: baseUrl, authType: 'session', user: candidate.user, error: null, redirectTo: null });
    publish();
    return publicState(state);
  }

  async function restore() {
    if (browser) return restoreBrowserSession();
    let stored = null;
    try {
      stored = await storage.getItem(STORAGE_KEY);
    } catch {
      stored = null;
    }
    if (!stored) {
      setState({ status: 'signed_out', authType: '', user: null, error: null, redirectTo: null });
      session = null;
      publish();
      return publicState(state);
    }
    let parsed;
    try {
      parsed = JSON.parse(stored);
    } catch {
      await removeStored().catch(() => {});
      setState({ status: 'signed_out', authType: '', user: null, error: null, redirectTo: null });
      session = null;
      publish();
      return publicState(state);
    }
    if (!parsed || parsed.version !== SESSION_VERSION || !parsed.serverUrl || !['token', 'session'].includes(parsed.authType)) {
      await removeStored().catch(() => {});
      setState({ status: 'signed_out', authType: '', user: null, error: null, redirectTo: null });
      session = null;
      publish();
      return publicState(state);
    }
    session = parsed;
    setState({
      status: 'authenticated',
      serverUrl: parsed.serverUrl,
      authType: parsed.authType,
      user: parsed.user || null,
      error: null,
      redirectTo: null,
    });
    publish();
    if (parsed.authType === 'session' && !(parsed.user && 'isSuperuser' in parsed.user)) {
      // Sessions saved before the account payload was read learn their role once.
      attachAccountUser(parsed).then(() => {
        if (session !== parsed) return;
        setState({ user: parsed.user || null });
        persist(parsed).catch(() => {});
        publish();
      });
    }
    return publicState(state);
  }

  async function signOut() {
    if (session && session.authType === 'session') {
      try {
        await send(session, '/accounts/logout/', { method: 'POST', body: {} });
      } catch (error) {
        // A browser session lives in a cookie only the server can clear, so a
        // failed logout leaves the user signed in rather than pretending.
        if (browser) throw error;
        // Native sign-out still removes the saved credential.
      }
    }
    await removeStored().catch(() => {});
    session = null;
    setState({ status: 'signed_out', authType: '', user: null, error: null });
    publish();
    return publicState(state);
  }

  async function testConnection({ serverUrl, token } = {}) {
    const baseUrl = resolveBase(serverUrl);
    const ping = await perform(`${baseUrl}/ping/`, {
      method: 'GET',
      headers: { Accept: 'application/json' },
    });
    const pingData = await readBody(ping);
    if (!ping.ok || (pingData && typeof pingData === 'object' && pingData.status && pingData.status !== 'ok')) {
      throw fail(AUTH_ERROR_CODES.SERVER_UNREACHABLE, 'The address did not answer like a Floppy server.', { status: ping.status });
    }
    const value = normalizeToken(token);
    if (!value) {
      return { reachable: true, authenticated: false, serverUrl: baseUrl, user: null };
    }
    const candidate = { serverUrl: baseUrl, authType: 'token', token: value };
    const sidebar = await send(candidate, '/api/v1/user/sidebar/');
    return {
      reachable: true,
      authenticated: true,
      serverUrl: baseUrl,
      user: null,
      sidebar: sidebar || null,
    };
  }

  async function verify() {
    if (!session) return publicState(state);
    await verifyCredential(session);
    session.lastVerifiedAt = now();
    await persist(session).catch(() => {});
    setState({ status: 'authenticated', error: null });
    publish();
    return publicState(state);
  }

  async function request(path, options = {}) {
    if (!session) {
      throw fail(AUTH_ERROR_CODES.SIGNED_OUT, 'Sign in to your Floppy server first.');
    }
    return send(session, path, options);
  }

  const api = {
    request,
    state,
    invalidate,
  };

  return {
    restore,
    signIn,
    signUp,
    recoverPassword,
    signInWithToken,
    startDeviceSignIn,
    pollDeviceSignIn,
    signOut,
    testConnection,
    verify,
    fetchAuthConfig,
    request,
    getState: () => publicState(state),
    getSession: () => sessionFacade(session, api),
    invalidate,
  };
}
