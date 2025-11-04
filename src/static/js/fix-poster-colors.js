// Force override poster accent colors for genre chips and buttons
(function() {
  const processedElements = new WeakSet();
  const cleanedElements = new WeakSet(); // Track elements we've cleaned
  const DEBUG = true; // Enable debugging
  
  function log(...args) {
    if (DEBUG) {
      console.log('[fix-poster-colors]', ...args);
    }
  }
  
  // Helper to remove all event listeners (nuclear option)
  function removeAllListeners(element) {
    const newElement = element.cloneNode(true);
    if (element.parentNode) {
      element.parentNode.replaceChild(newElement, element);
    }
    return newElement;
  }
  
  function forceWhiteColors() {
    log('=== forceWhiteColors() called ===');
    
    // Find all genre chips
    const chips = document.querySelectorAll('.poster-accent-chip');
    log(`Found ${chips.length} genre chips`);
    chips.forEach(chip => {
      if (processedElements.has(chip)) return;
      processedElements.add(chip);
      
      chip.style.setProperty('color', '#ffffff', 'important');
      chip.style.setProperty('border', 'none', 'important');
      chip.style.setProperty('border-color', 'transparent', 'important');
      chip.style.setProperty('background-color', 'rgba(255, 255, 255, 0.5)', 'important');
      
      // No hover effect - keep border hidden on hover
    });

    // Find all buttons - force grey background
    const buttons = document.querySelectorAll('.poster-accent-button');
    log(`Found ${buttons.length} buttons with .poster-accent-button`);
    
    let skippedCount = 0;
    let processedCount = 0;
    
    buttons.forEach((button, index) => {
      const hasBookHover = button.classList.contains('book-accent-hover');
      const classes = Array.from(button.classList).join(', ');
      log(`Button ${index}: classes="${classes}", has book-accent-hover=${hasBookHover}`);
      
      // Skip buttons with book-accent-hover class - they have their own hover effect
      if (hasBookHover) {
        skippedCount++;
        log(`  -> SKIPPING button ${index} (has book-accent-hover class)`);
        
        // CRITICAL: Remove any inline styles that might have been set previously
        // First try to remove the properties entirely, then set them to what we want
        if (!cleanedElements.has(button)) {
          log(`  -> Cleaning inline styles on button ${index}`);
          
          // Remove border-related inline styles completely first
          button.style.removeProperty('border');
          button.style.removeProperty('border-width');
          button.style.removeProperty('border-color');
          button.style.removeProperty('opacity');
          
          // Now set them to our desired values with !important
          button.style.setProperty('border', 'none', 'important');
          button.style.setProperty('border-width', '0', 'important');
          button.style.setProperty('border-color', 'transparent', 'important');
          
          cleanedElements.add(button);
          log(`  -> Button ${index} cleaned (inline styles reset)`);
        }
        
        return;
      }
      
      if (processedElements.has(button)) {
        log(`  -> Button ${index} already processed, re-applying styles`);
        // Even if processed, force grey background again
        button.style.setProperty('background-color', 'rgba(255, 255, 255, 0.5)', 'important');
        button.style.setProperty('color', '#ffffff', 'important');
        button.style.setProperty('opacity', '0.8', 'important');
        return;
      }
      processedElements.add(button);
      processedCount++;
      
      log(`  -> Processing button ${index} (adding hover listeners)`);
      
      button.style.setProperty('border', 'none', 'important');
      button.style.setProperty('border-color', 'transparent', 'important');
      button.style.setProperty('color', '#ffffff', 'important');
      button.style.setProperty('background-color', 'rgba(255, 255, 255, 0.5)', 'important');
      button.style.setProperty('opacity', '0.8', 'important');
      
      // Add hover listener for border
      button.addEventListener('mouseenter', function() {
        log(`  -> mouseenter on button (NOT book-accent-hover)`);
        this.style.setProperty('border', '1px solid #ffffff', 'important');
        this.style.setProperty('border-color', '#ffffff', 'important');
        // Keep grey background on hover
        this.style.setProperty('background-color', 'rgba(255, 255, 255, 0.5)', 'important');
        // Full opacity on hover
        this.style.setProperty('opacity', '1', 'important');
      });
      button.addEventListener('mouseleave', function() {
        log(`  -> mouseleave on button (NOT book-accent-hover)`);
        this.style.setProperty('border', 'none', 'important');
        this.style.setProperty('border-color', 'transparent', 'important');
        // Keep grey background on leave
        this.style.setProperty('background-color', 'rgba(255, 255, 255, 0.5)', 'important');
        // Back to 0.8 opacity on leave
        this.style.setProperty('opacity', '0.8', 'important');
      });
    });
    
    log(`Summary: ${processedCount} processed, ${skippedCount} skipped`);

    // Find all status divs (Reading, Completed, Watching) - ensure they have grey background
    const statusDivs = document.querySelectorAll('[class*="poster-accent-button"]');
    statusDivs.forEach(div => {
      if (div.classList.contains('poster-accent-button')) {
        // Force grey background for all poster-accent-button elements
        div.style.setProperty('background-color', 'rgba(255, 255, 255, 0.5)', 'important');
        div.style.setProperty('color', '#ffffff', 'important');
      }
    });
  }

  // Expose function globally for manual triggering
  window.forceWhiteColors = forceWhiteColors;
  
  // Listen for custom event when book buttons are created
  document.addEventListener('bookButtonsCreated', function() {
    log('Received bookButtonsCreated event, re-running...');
    setTimeout(forceWhiteColors, 10);
  });
  
  // Run immediately
  forceWhiteColors();

  // Run on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', forceWhiteColors);
  }

  // Run after short delays to catch dynamically loaded content and override any other scripts
  setTimeout(forceWhiteColors, 50);
  setTimeout(forceWhiteColors, 100);
  setTimeout(forceWhiteColors, 200);
  setTimeout(forceWhiteColors, 500);
  setTimeout(forceWhiteColors, 1000);
  setTimeout(forceWhiteColors, 2000);

  // Watch for dynamic changes
  const observer = new MutationObserver(function(mutations) {
    // Check if any new elements with book-accent-hover were added
    const hasNewBookButtons = mutations.some(mutation => {
      return Array.from(mutation.addedNodes).some(node => {
        if (node.nodeType === 1) { // Element node
          return node.classList && node.classList.contains('book-accent-hover') ||
                 node.querySelector && node.querySelector('.book-accent-hover');
        }
        return false;
      });
    });
    
    if (hasNewBookButtons) {
      log('Detected new book-accent-hover elements, re-running...');
      setTimeout(forceWhiteColors, 10);
    } else {
      forceWhiteColors();
    }
  });

  // Only observe if document.body exists
  if (document.body) {
    observer.observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['style', 'class']
    });
  } else {
    // Wait for body to exist
    document.addEventListener('DOMContentLoaded', () => {
      observer.observe(document.body, {
        childList: true,
        subtree: true,
        attributes: true,
        attributeFilter: ['style', 'class']
      });
    });
  }
  
  // Also run once after a longer delay to catch late-loading content
  setTimeout(forceWhiteColors, 3000);
})();

