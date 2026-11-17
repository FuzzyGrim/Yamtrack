import React, { useEffect, useState } from 'react';
import { mountRoot } from '../settings/root';
import { MarkdownNotes } from './MarkdownNotes';
import { MarkdownPreview } from './MarkdownPreview.web';
import { rootStyle } from './rootStyle.web';

const canvas = document.createElement('canvas');
canvas.width = canvas.height = 1;
const ctx = canvas.getContext('2d');

function resolveColor(value, fallback) {
  if (!value) return fallback;
  if (/^(?:#|rgba?\()/i.test(value.trim())) return value.trim();
  if (!ctx) return fallback;
  ctx.clearRect(0, 0, 1, 1);
  ctx.fillStyle = fallback;
  ctx.fillStyle = value;
  ctx.fillRect(0, 0, 1, 1);
  const [r, g, b, a] = ctx.getImageData(0, 0, 1, 1).data;
  return `rgba(${r}, ${g}, ${b}, ${a / 255})`;
}

function translate(value) {
  return typeof gettext === 'function' ? gettext(value) : value;
}

function readPalette() {
  const css = rootStyle();
  const token = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
  const color = (name, fallback) => resolveColor(token(name, fallback), fallback);
  return {
    background: color('--color-page-bg', '#18181b'),
    surface: color('--color-surface', '#27272a'),
    surfaceStrong: color('--color-surface-strong', '#3f3f46'),
    border: color('--color-gray-700', '#52525b'),
    text: color('--color-text', '#fafafa'),
    textSecondary: color('--color-text-secondary', '#d4d4d8'),
    textMuted: color('--color-text-muted', '#a1a1aa'),
    accent: color('--color-accent', '#6366f1'),
    link: color('--color-link', '#818cf8'),
    danger: color('--color-danger', '#f87171'),
    codeBackground: color('--color-surface-dim', '#1f2328'),
  };
}

function readLabels(fallback) {
  const text = selector => {
    const node = fallback?.querySelector(selector);
    return node ? node.textContent.trim() : '';
  };
  const toolbar = {};
  fallback?.querySelectorAll('[data-md-action]').forEach(button => {
    const action = button.getAttribute('data-md-action');
    const label = button.getAttribute('title') || button.textContent.trim();
    if (action && label) toolbar[action] = label;
  });
  return {
    label: text('label') || translate('Notes'),
    hint: fallback?.querySelector('[data-markdown-notes-hint]')?.textContent.trim() || translate('Markdown supported'),
    error: fallback?.querySelector('.errorlist, [data-markdown-notes-errors]')?.textContent.trim() || '',
    write: text('[data-md-tab="write"]') || translate('Write'),
    preview: text('[data-md-tab="preview"]') || translate('Preview'),
    heading: toolbar.heading || translate('Heading'),
    bold: toolbar.bold || translate('Bold'),
    italic: toolbar.italic || translate('Italic'),
    code: toolbar.code || translate('Code'),
    link: toolbar.link || translate('Link'),
    quote: toolbar.quote || translate('Quote'),
    bulletedList: toolbar.ul || translate('Bulleted list'),
    numberedList: toolbar.ol || translate('Numbered list'),
    emptyPreview: translate('Nothing to preview yet.'),
  };
}

function EditorAdapter({ host, palette }) {
  const textarea = host.querySelector('textarea');
  const fallback = host.closest('[data-markdown-editor]')?.querySelector('[data-markdown-notes-fallback]');
  const labels = readLabels(fallback);
  const [value, setValue] = useState(textarea?.value || '');

  useEffect(() => {
    if (textarea) textarea.value = value;
  }, [textarea, value]);

  return <MarkdownNotes
    value={value}
    onChangeText={setValue}
    label={labels.label}
    hint={labels.hint}
    error={labels.error}
    placeholder={textarea?.getAttribute('placeholder') || ''}
    accessibilityLabel={labels.label}
    labels={labels}
    palette={palette}
    Preview={MarkdownPreview} />;
}

function initEditor(editor) {
  if (!editor || editor.dataset.markdownMounted === 'true') return;
  const host = editor.querySelector('[data-markdown-notes-host]');
  const fallback = editor.querySelector('[data-markdown-notes-fallback]');
  const textarea = editor.querySelector('textarea');
  if (!host || !textarea) return;
  const palette = readPalette();
  const previousDisplay = fallback ? fallback.style.display : '';
  const previousTabIndex = textarea.tabIndex;
  if (fallback) {
    fallback.style.display = 'none';
    fallback.setAttribute('aria-hidden', 'true');
  }
  textarea.tabIndex = -1;
  try {
    mountRoot(host, <EditorAdapter host={editor} palette={palette} />);
    editor.dataset.markdownMounted = 'true';
  } catch (error) {
    if (fallback) {
      fallback.style.display = previousDisplay;
      fallback.removeAttribute('aria-hidden');
    }
    textarea.tabIndex = previousTabIndex;
    throw error;
  }
}

function previewValue(preview) {
  const sourceId = preview.getAttribute('data-markdown-source');
  if (!sourceId) return '';
  const source = document.getElementById(sourceId);
  if (!source) return '';
  try {
    return JSON.parse(source.textContent || '');
  } catch (error) {
    return source.textContent || '';
  }
}

function initPreview(preview) {
  if (!preview || preview.dataset.markdownMounted === 'true') return;
  const value = previewValue(preview);
  const palette = readPalette();
  try {
    mountRoot(preview, <MarkdownPreview markdown={value} palette={palette}
      labels={{ emptyPreview: translate('Nothing to preview yet.') }} />);
    preview.dataset.markdownMounted = 'true';
    window.requestAnimationFrame(() => {
      preview.dispatchEvent(new CustomEvent('markdown-preview-rendered', { bubbles: true }));
    });
  } catch (error) {
    delete preview.dataset.markdownMounted;
    throw error;
  }
}

export function initMarkdown(root) {
  const scope = root || document;
  const editors = [
    ...(scope.matches?.('[data-markdown-editor]') ? [scope] : []),
    ...(scope.querySelectorAll?.('[data-markdown-editor]') || []),
  ];
  editors.forEach(initEditor);
  const previews = [
    ...(scope.matches?.('[data-markdown-preview]') ? [scope] : []),
    ...(scope.querySelectorAll?.('[data-markdown-preview]') || []),
  ];
  previews.forEach(initPreview);
}

if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => initMarkdown(document), { once: true });
  } else {
    initMarkdown(document);
  }
  document.body?.addEventListener('htmx:afterSwap', event => {
    initMarkdown(event.detail?.elt || event.target);
  });
  document.body?.addEventListener('htmx:load', event => {
    initMarkdown(event.detail?.elt || event.target);
  });
}
