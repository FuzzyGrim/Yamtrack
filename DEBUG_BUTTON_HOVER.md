# Button Hover Debugging Guide

## Steps to Debug

### 1. Open Browser Console

- **Chrome/Edge**: Press `F12` or `Cmd+Option+I` (Mac) / `Ctrl+Shift+I` (Windows)
- **Firefox**: Press `F12` or `Cmd+Option+K` (Mac) / `Ctrl+Shift+K` (Windows)
- **Safari**: Enable Developer menu first, then `Cmd+Option+C`

### 2. Check Console Logs

After refreshing the page, you should see logs starting with:

- `[DEBUG] Book actions template loaded - "In progress" section`
- `[fix-poster-colors] === forceWhiteColors() called ===`
- `[fix-poster-colors] Found X buttons with .poster-accent-button`
- `[DEBUG] Found X elements with .book-accent-hover class`

### 3. Run This Debugging Script

Copy and paste this entire block into the console:

```javascript
(function () {
  console.log("=== COMPREHENSIVE BUTTON DEBUG ===");

  // Find all buttons
  const allButtons = document.querySelectorAll(".poster-accent-button");
  const bookButtons = document.querySelectorAll(".book-accent-hover");
  const bookAccentButtons = document.querySelectorAll(
    ".poster-accent-button.book-accent-hover"
  );

  console.log("\n1. Button Counts:");
  console.log(`  - Total .poster-accent-button: ${allButtons.length}`);
  console.log(`  - Total .book-accent-hover: ${bookButtons.length}`);
  console.log(
    `  - Total .poster-accent-button.book-accent-hover: ${bookAccentButtons.length}`
  );

  console.log("\n2. Checking each button:");
  bookAccentButtons.forEach((btn, i) => {
    const classes = Array.from(btn.classList);
    const computed = window.getComputedStyle(btn);
    const inline = {
      border: btn.style.border,
      borderWidth: btn.style.borderWidth,
      borderColor: btn.style.borderColor,
      backgroundColor: btn.style.backgroundColor,
      color: btn.style.color,
    };

    console.log(`\n  Button ${i} (${btn.tagName}):`);
    console.log(`    Classes: ${classes.join(", ")}`);
    console.log(`    Text: "${btn.textContent.trim()}"`);
    console.log(`    Computed border: ${computed.border}`);
    console.log(`    Computed borderWidth: ${computed.borderWidth}`);
    console.log(`    Computed borderColor: ${computed.borderColor}`);
    console.log(`    Computed backgroundColor: ${computed.backgroundColor}`);
    console.log(`    Computed color: ${computed.color}`);
    console.log(`    Inline styles:`, inline);

    // Check for event listeners
    const listeners = getEventListeners
      ? getEventListeners(btn)
      : "getEventListeners not available";
    console.log(`    Event listeners:`, listeners);
  });

  console.log("\n3. Checking CSS rules:");
  const styleSheets = Array.from(document.styleSheets);
  let foundRules = [];
  styleSheets.forEach((sheet, sheetIndex) => {
    try {
      const rules = Array.from(sheet.cssRules || []);
      rules.forEach((rule, ruleIndex) => {
        if (
          rule.selectorText &&
          rule.selectorText.includes("book-accent-hover")
        ) {
          foundRules.push({
            sheet: sheetIndex,
            selector: rule.selectorText,
            cssText: rule.cssText,
          });
        }
      });
    } catch (e) {
      // Cross-origin stylesheet, skip
    }
  });
  console.log(
    `  Found ${foundRules.length} CSS rules with 'book-accent-hover':`
  );
  foundRules.forEach((rule, i) => {
    console.log(`    Rule ${i}: ${rule.selector}`);
    console.log(`      ${rule.cssText.substring(0, 200)}...`);
  });

  console.log("\n4. Testing hover manually:");
  console.log("   Hover over a button now and check what changes...");

  // Set up temporary hover watcher
  bookAccentButtons.forEach((btn, i) => {
    const watcher = () => {
      const computed = window.getComputedStyle(btn);
      console.log(`\n[HOVER] Button ${i} hover state:`, {
        border: computed.border,
        borderWidth: computed.borderWidth,
        borderColor: computed.borderColor,
        backgroundColor: computed.backgroundColor,
        color: computed.color,
        inlineStyle: btn.style.cssText,
      });
    };
    btn.addEventListener("mouseenter", watcher);
  });

  console.log("\n=== Debug complete ===");
  console.log("Now hover over the buttons and watch the console for changes.");
})();
```

### 4. What to Look For

**Expected Behavior:**

- ✅ `[fix-poster-colors]` should show buttons being SKIPPED (not processed)
- ✅ `[DEBUG]` should show 3 buttons found with `.book-accent-hover`
- ✅ Computed styles should show `border: 0px none` or `border: 0px`
- ✅ When hovering, `borderWidth` should remain `0px`
- ✅ When hovering, `backgroundColor` should change to `rgb(255, 255, 255)`
- ✅ No `mouseenter` logs from `fix-poster-colors.js` for these buttons

**Problem Signs:**

- ❌ Buttons are NOT being skipped in `fix-poster-colors.js`
- ❌ `borderWidth` is greater than `0px` on hover
- ❌ Inline styles show border being set
- ❌ `[fix-poster-colors] mouseenter` logs appear for these buttons

### 5. Quick Check Commands

Run these one at a time in the console:

```javascript
// Check if buttons exist
document.querySelectorAll(".book-accent-hover").length;

// Check if they have the class
document
  .querySelectorAll(".book-accent-hover")[0]
  .classList.contains("book-accent-hover");

// Check computed border
window.getComputedStyle(document.querySelectorAll(".book-accent-hover")[0])
  .border;

// Check inline styles
document.querySelectorAll(".book-accent-hover")[0].style.border;
```

### 6. Take Screenshots

1. Screenshot of the console output showing all logs
2. Screenshot of the Elements inspector showing the button's computed styles (when NOT hovered)
3. Screenshot of the Elements inspector showing the button's computed styles (when HOVERED)

### 7. Report Back

Copy and paste:

1. All console logs (especially `[fix-poster-colors]` and `[DEBUG]` messages)
2. Output from the debugging script above
3. Screenshots if possible
4. What you see visually when hovering
