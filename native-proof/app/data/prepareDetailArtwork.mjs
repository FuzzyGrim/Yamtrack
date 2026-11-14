// Bound the wait so unavailable artwork cannot prevent a detail page loading.
export async function prepareDetailArtwork(model, image, timeoutMs = 2500) {
  const artwork = model?.artwork;
  if (!artwork?.backdrop && !artwork?.logo) return model;
  let timer;
  const prepare = async uri => {
    if (!uri) return null;
    const [size] = await Promise.all([
      Promise.resolve().then(() => image.getSize(uri)).catch(() => null),
      Promise.resolve().then(() => image.prefetch(uri)).catch(() => false),
    ]);
    return size?.width > 0 && size?.height > 0 ? size.width / size.height : null;
  };
  const result = await Promise.race([
    Promise.all([prepare(artwork.backdrop), prepare(artwork.logo)]),
    new Promise(resolve => { timer = setTimeout(() => resolve(null), timeoutMs); }),
  ]);
  clearTimeout(timer);
  if (!result) return model;
  return { ...model, artwork: { ...artwork, backdropAspect: result[0], logoAspect: result[1] } };
}
