import React, { useCallback, useLayoutEffect, useState } from 'react';
import { mountRoot } from '../settings/root';
import { DetailBackdrop } from './DetailBackdrop';

// The server owns the surrounding hero and its responsive dimensions. Measure
// that frame here; the shared backdrop owns the artwork and its crop.
function WebDetailBackdrop({ host, source }) {
  const [layout, setLayout] = useState(null);
  const setSharpEnd = useCallback(value => {
    const grid = host.closest('.detail-carousel-grid');
    if (!grid) return;
    grid.style.setProperty('--hero-content-line', `${value}px`);
    const title = grid.querySelector('.detail-carousel-grid__title');
    const subtitle = title?.querySelector('[id^="detail-activity-subtitle-slot"]');
    if (title && subtitle) {
      grid.style.setProperty('--hero-title-offset', `${subtitle.getBoundingClientRect().top - title.getBoundingClientRect().top}px`);
    }
    grid.classList.remove('detail-hero-pending', 'detail-hero-waiting');
  }, [host]);
  useLayoutEffect(() => {
    const frame = host.parentElement;
    const measure = () => {
      const sharp = frame.querySelector('.detail-mobile-hero__sharp');
      const logo = frame.parentElement.querySelector('.detail-mobile-hero__logo');
      if (!sharp?.complete || (logo && !logo.complete)) return;
      const width = frame.clientWidth;
      const height = frame.clientHeight;
      const sharpHeight = sharp?.getBoundingClientRect().height;
      if (!width || !height || !sharpHeight) return;
      setLayout({ width, height, innerWidth: width, sideBleed: 0, topBleed: 0,
        sharpHeight, blurHeight: height,
        aspect: sharp.naturalWidth && sharp.naturalHeight ? sharp.naturalWidth / sharp.naturalHeight : 16 / 9 });
      host.dataset.ready = 'true';
    };
    const observer = new ResizeObserver(measure);
    observer.observe(frame);
    const images = [frame.querySelector('.detail-mobile-hero__sharp'), frame.parentElement.querySelector('.detail-mobile-hero__logo')].filter(Boolean);
    images.forEach(image => { image.addEventListener('load', measure); image.addEventListener('error', measure); });
    window.addEventListener('resize', measure);
    measure();
    return () => { observer.disconnect(); window.removeEventListener('resize', measure); delete host.dataset.ready;
      images.forEach(image => { image.removeEventListener('load', measure); image.removeEventListener('error', measure); });
      const grid = host.closest('.detail-carousel-grid');
      grid?.style.removeProperty('--hero-content-line'); grid?.style.removeProperty('--hero-title-offset'); };
  }, [host]);
  return layout ? <DetailBackdrop source={source} layout={layout} background="var(--color-page-bg)" onSharpEnd={setSharpEnd} initialAspect={layout.aspect} /> : null;
}

export function mountDetailBackdrop(host, source) {
  return mountRoot(host, <WebDetailBackdrop host={host} source={source} />);
}
