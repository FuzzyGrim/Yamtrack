// Global keyboard shortcut for search
document.addEventListener('keydown', (e) => {
  // Ignore if typing in an input, textarea, or contenteditable
  const activeEl = document.activeElement;
  const isTyping = activeEl.tagName === 'INPUT' ||
                   activeEl.tagName === 'TEXTAREA' ||
                   activeEl.isContentEditable;

  if (isTyping) return;

  if (e.key === '/') {
    e.preventDefault();
    const searchInput = document.getElementById('global-search');
    if (searchInput) {
      searchInput.focus();
      searchInput.select();
    }
  }
});
