# Debugging Poster Color Issues

## Changes Made

1. **CSS Updates** (`src/static/css/main.css`):

   - Added maximum specificity selectors for `.poster-accent-chip` and `.poster-accent-button`
   - All use `!important` flags to override any inline styles
   - Force white colors on genre chips (no border, white text)
   - Force white borders and text on buttons

2. **JavaScript Override** (`src/static/js/fix-poster-colors.js`):
   - Dynamically sets white colors on all chips and buttons
   - Watches for DOM changes and re-applies colors
   - Runs multiple times to catch dynamically loaded content

## Debugging Steps

### 1. Check Browser Console

Open browser console (F12) and run these commands:

```javascript
// Check all genre chips
const chips = document.querySelectorAll(".poster-accent-chip");
console.log("Found", chips.length, "genre chips");
chips.forEach((chip, i) => {
  const styles = window.getComputedStyle(chip);
  console.log(`Chip ${i}:`, {
    color: styles.color,
    borderColor: styles.borderColor,
    backgroundColor: styles.backgroundColor,
    inlineStyle: chip.style.cssText,
  });
});

// Check all buttons
const buttons = document.querySelectorAll(".poster-accent-button");
console.log("Found", buttons.length, "buttons");
buttons.forEach((button, i) => {
  const styles = window.getComputedStyle(button);
  console.log(`Button ${i}:`, {
    color: styles.color,
    borderColor: styles.borderColor,
    backgroundColor: styles.backgroundColor,
    inlineStyle: button.style.cssText,
  });
});

// Check CSS variables
const root = document.getElementById("media-detail-root");
if (root) {
  const accent = getComputedStyle(root).getPropertyValue("--poster-accent");
  console.log("CSS Variable --poster-accent:", accent);
}
```

### 2. Check CSS File Loaded

```javascript
// Check if fix-poster-colors.js loaded
console.log(
  "fix-poster-colors.js loaded:",
  typeof forceWhiteColors !== "undefined"
);
```

### 3. Manual Override Test

Run this to manually force colors:

```javascript
// Force override all colors
document.querySelectorAll(".poster-accent-chip").forEach((chip) => {
  chip.style.setProperty("color", "#ffffff", "important");
  chip.style.setProperty("border", "none", "important");
  chip.style.setProperty("background-color", "transparent", "important");
});

document.querySelectorAll(".poster-accent-button").forEach((button) => {
  button.style.setProperty("border-color", "#ffffff", "important");
  button.style.setProperty("color", "#ffffff", "important");
  button.style.setProperty("background-color", "transparent", "important");
});
```

### 4. Check Server Logs

The CSS file should be loaded with a cache-busting query string:

```
/css/main.css?v=...
```

If you see the same version repeatedly, the cache might not be updating.

### 5. Hard Refresh

1. Press `Ctrl+Shift+R` (Windows/Linux) or `Cmd+Shift+R` (Mac)
2. Or open DevTools → Network tab → Check "Disable cache" → Reload

### 6. Check Static Files

Make sure static files are being served correctly. In Django shell:

```python
from django.contrib.staticfiles import finders
css_path = finders.find('css/main.css')
print('CSS file found at:', css_path)
```

## What to Report

If colors are still wrong, please copy and paste:

1. **Browser Console Output** from step 1 above
2. **Any errors** in the browser console
3. **Network tab** showing if `fix-poster-colors.js` is loading (status 200)
4. **Computed styles** from an element inspector:
   - Right-click a genre chip → Inspect
   - In Styles tab, show which CSS rules are being applied
   - Take a screenshot

## Files Modified

- `src/static/css/main.css` - Added high-specificity CSS rules
- `src/static/js/fix-poster-colors.js` - JavaScript color override
- `src/templates/base.html` - Added script tag for fix-poster-colors.js

