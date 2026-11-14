// Run independent reads together; one unavailable optional source must not
// prevent the rest of the header from appearing.
export async function loadDetailHeader(identity, loaders, noTracking, timeoutMs = 5000) {
  const read = async (loader, fallback) => {
    let timer;
    try {
      return await Promise.race([
        Promise.resolve().then(() => loader(identity)).catch(() => fallback),
        new Promise(resolve => { timer = setTimeout(() => resolve(fallback), timeoutMs); }),
      ]);
    } finally {
      clearTimeout(timer);
    }
  };
  const [tracking, progress, scores, checkin] = await Promise.all([
    read(loaders.tracking, { ...noTracking, unavailable: true }),
    read(loaders.progress, null),
    read(loaders.scores, { chips: [], graphs: {}, gameLengths: null }),
    read(loaders.checkin, null),
  ]);
  return { tracking, progress, scores, checkin: tracking.tracked ? checkin?.actions || [] : [] };
}
