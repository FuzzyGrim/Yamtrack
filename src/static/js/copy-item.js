document.addEventListener("DOMContentLoaded", function () {
  // Find all copy buttons with a data-copy-target attribute
  const copyButtons = document.querySelectorAll('[data-copy-target]');
  console.log(`Found ${copyButtons.length} copy buttons`);
  copyButtons.forEach((copyButton) => {
    const targetSelector = copyButton.getAttribute('data-copy-target');
    console.log(`Target selector: ${targetSelector}`);
    const input = document.querySelector(targetSelector);
    if (!input) {
      console.error(`Could not find input for selector: ${targetSelector}`);
      return;
    }

    // Copy button functionality
    copyButton.addEventListener("click", function () {
      const text = input.value;

      // Try to copy the text
      input.select();
      input.setSelectionRange(0, 99999); // For mobile devices

      let copied = false;

      // Try clipboard API first
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard
          .writeText(text)
          .then(() => {
            showSuccess();
          })
          .catch((err) => {
            console.error("Clipboard API failed:", err);
            tryExecCommand();
          });
      } else {
        tryExecCommand();
      }

      function tryExecCommand() {
        try {
          copied = document.execCommand("copy");
          if (copied) {
            showSuccess();
          } else {
            showError();
          }
        } catch (err) {
          console.error("execCommand failed:", err);
          showError();
        }
      }

      function showSuccess() {
        // Replace the icon with a checkmark for 3 seconds, then restore
        const svg = copyButton.querySelector('svg');
        if (!svg) return;
        const originalSvg = svg.outerHTML;
        svg.outerHTML = `<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"20\" height=\"20\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2\" stroke-linecap=\"round\" stroke-linejoin=\"round\" class=\"w-5 h-5 text-emerald-400\"><polyline points=\"20 6 9 17 4 12\"></polyline></svg>`;
        setTimeout(() => {
          copyButton.querySelector('svg').outerHTML = originalSvg;
        }, 3000);
      }

      function showError() {
        alert(
          "Could not copy to clipboard. Please select the text and press Ctrl+C/Cmd+C to copy."
        );
      }
    });

    // Make input select all text when clicked
    input.addEventListener("click", function () {
      this.select();
    });
  });
});
