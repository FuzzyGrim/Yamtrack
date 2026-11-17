import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { View } from 'react-native';
import { mountRoot } from '../settings/root';
import { MobileNav, TopBar } from './Shell';
import { rootStyle } from './rootStyle.web';

const THEME_CLASSES = [
  'light', 'dark', 'catppuccin_mocha', 'dracula', 'nord', 'gruvbox',
  'oled', 'glass', 'plex', 'projector', 'video_store', 'custom',
];

const canvas = document.createElement('canvas');
canvas.width = canvas.height = 1;
const context = canvas.getContext('2d');

function resolveColor(value, fallback) {
  if (!value) return fallback;
  if (/^(?:#|rgba?\()/i.test(value.trim())) return value.trim();
  if (!context) return fallback;
  context.clearRect(0, 0, 1, 1);
  context.fillStyle = fallback;
  context.fillStyle = value;
  context.fillRect(0, 0, 1, 1);
  const [red, green, blue, alpha] = context.getImageData(0, 0, 1, 1).data;
  return `rgba(${red}, ${green}, ${blue}, ${alpha / 255})`;
}

function readPalette() {
  const css = rootStyle();
  const token = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
  const color = (name, fallback) => resolveColor(token(name, fallback), fallback);
  const accent = color('--color-accent', '#4a9eff');
  return {
    text: color('--color-text', '#fafafa'),
    textSecondary: color('--color-text-secondary', '#d1d5db'),
    textMuted: color('--color-text-muted', '#adb5bd'),
    sidebar: color('--color-sidebar', '#212529'),
    surface: color('--color-surface', '#2a2f35'),
    surfaceMuted: color('--color-surface-muted', '#343a40'),
    border: color('--color-border', '#343a40'),
    accent,
    accentStrong: '#4f46e5',
    overlay: 'rgba(0, 0, 0, 0.5)',
    focusStyle: { outlineStyle: 'solid', outlineWidth: 2, outlineColor: accent, outlineOffset: 2 },
  };
}

function readTheme() {
  const classes = document.documentElement.classList;
  const explicit = THEME_CLASSES.some(name => classes.contains(name));
  const light = classes.contains('light')
    || (!explicit && window.matchMedia('(prefers-color-scheme: light)').matches);
  return { light, palette: readPalette() };
}

function SearchSlot({ node }) {
  const ref = useRef(null);
  useLayoutEffect(() => {
    if (!node || !ref.current) return undefined;
    ref.current.appendChild(node);
    return () => {
      if (node.parentNode === ref.current) ref.current.removeChild(node);
    };
  }, [node]);
  return <View ref={ref} style={{ flex: 1, minWidth: 0, width: '100%', maxWidth: 672 }} />;
}

function ShellIsland({ host, props }) {
  const [open, setOpen] = useState(false);
  const [theme, setTheme] = useState(readTheme);
  const searchNode = useRef(host.querySelector('[data-global-search-root]')).current;

  useEffect(() => {
    const update = () => setTheme(readTheme());
    update();
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['class', 'style'],
    });
    const scheme = window.matchMedia('(prefers-color-scheme: dark)');
    scheme.addEventListener('change', update);
    return () => {
      observer.disconnect();
      scheme.removeEventListener('change', update);
    };
  }, []);

  useEffect(() => {
    window.dispatchEvent(new CustomEvent('floppy:mobile-menu', { detail: { open } }));
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    const close = event => { if (event.key === 'Escape') setOpen(false); };
    const desktop = window.matchMedia('(min-width: 1024px)');
    const closeDesktop = () => { if (desktop.matches) setOpen(false); };
    document.addEventListener('keydown', close);
    desktop.addEventListener('change', closeDesktop);
    return () => {
      document.removeEventListener('keydown', close);
      desktop.removeEventListener('change', closeDesktop);
    };
  }, [open]);

  const toggleTheme = () => {
    if (typeof window.floppyToggleTheme === 'function') window.floppyToggleTheme();
  };

  return <React.Fragment>
    <TopBar
      variant={props.variant}
      menuOpen={open}
      onToggleMenu={() => setOpen(value => !value)}
      showMenu={props.showMenu}
      showSearch={props.showSearch}
      search={props.showSearch ? <SearchSlot node={searchNode} /> : null}
      logo={props.logo}
      logoText={props.logoText}
      signIn={props.signIn ? { ...props.signIn, platformProps: { href: props.signIn.href } } : null}
      light={theme.light}
      showThemeButton={props.themeToggle}
      onToggleTheme={toggleTheme}
      labels={props.labels}
      palette={theme.palette}
    />
    <MobileNav open={open} onClose={() => setOpen(false)}
      labels={props.labels} palette={theme.palette} />
  </React.Fragment>;
}

export function mountShell(host, props) {
  return mountRoot(host, <ShellIsland host={host} props={props} />);
}
