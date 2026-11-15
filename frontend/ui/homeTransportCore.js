// Home endpoints, shared by the web Home adapter (mountHome.jsx, through
// webRequest.web.js) and the Expo Home screen (homeTransport.js, through the
// signed-in auth transport). Pure: each platform injects
// `request(path, { method, body, query })`, which owns sign-in, CSRF and the
// server address.
const HOME_PATH = '/api/v1/home/';

export function homeQuery(params = {}) {
  const query = {
    presentation: 1,
    deferred: params.deferred === false ? 0 : 1,
  };
  if (params.limit !== undefined) query.limit = params.limit;
  return query;
}

export function homeScreenFromPayload(payload = {}) {
  return {
    groups: Array.isArray(payload.groups) ? payload.groups : [],
    deferredGroups: Array.isArray(payload.deferredGroups) ? payload.deferredGroups : [],
    showMediaTypeHeaders: !!payload.showMediaTypeHeaders,
    readOnly: !!payload.readOnly,
    labels: payload.labels || {},
  };
}

export function mergeHomeRowPage(row, page) {
  if (!page) return row;
  return {
    ...row,
    ...page,
    items: [...(row.items || []), ...(page.items || [])],
    loadedCount: page.loadedCount ?? (row.items || []).length + (page.items || []).length,
    total: page.total ?? row.total,
    seed: page.seed ?? row.seed,
    pollForCovers: !!(row.pollForCovers || page.pollForCovers),
  };
}

export function createRemoteHomeActions(request) {
  return {
    mode: 'server',
    load(params = {}) {
      return request(HOME_PATH, { query: homeQuery(params) });
    },
    loadRow({ rowId, offset = 0, seed = '', limit } = {}) {
      const query = { offset };
      if (seed !== '' && seed != null) query.seed = seed;
      if (limit !== undefined) query.limit = limit;
      return request(`/api/v1/home/row/${encodeURIComponent(rowId)}/`, { query })
        .then(payload => payload.row || null);
    },
    sortRow({ rowId, limit = 14 } = {}) {
      return request(
        `/api/v1/home/row/${encodeURIComponent(rowId)}/sort/`,
        { method: 'POST', body: {}, query: { limit } },
      ).then(payload => payload.row || null);
    },
    refreshCovers({ ids = [], pending = 0 } = {}) {
      return request('/api/v1/home/artwork/', {
        query: { ids: ids.join(','), pending: pending ? 1 : 0 },
      });
    },
  };
}

export function createLocalHomeActions(reason) {
  const local = async () => ({ local: true, reason });
  return {
    mode: 'local',
    reason,
    load: local,
    loadRow: local,
    sortRow: local,
    refreshCovers: local,
  };
}
