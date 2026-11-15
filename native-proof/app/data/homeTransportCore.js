// The Home transport core is shared with the web Home adapter, so it lives
// beside the shared presentation in frontend/ui. Native keeps this path.
export {
  createLocalHomeActions,
  createRemoteHomeActions,
  homeQuery,
  homeScreenFromPayload,
  mergeHomeRowPage,
} from '../../../frontend/ui/homeTransportCore.js';
