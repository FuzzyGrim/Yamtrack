// currentScript only works while the script is being initially executed
// not inside event listeners or callbacks that run later.
const data = document.currentScript.dataset;

document.addEventListener('DOMContentLoaded', function() {
  const searchForm = document.getElementById('search-form');
  // Reuse the previous source value when searching for manga
  searchForm.addEventListener('submit', function(e) {
    const mediaType = this.querySelector('select[name="media_type"]').value;
    const source = data.source;
    console.log('source', source);
    if (mediaType === 'manga' && source) {
      const hiddenInput = document.createElement('input');
      hiddenInput.type = 'hidden';
      hiddenInput.name = 'source';
      hiddenInput.value = source;
      this.appendChild(hiddenInput);
    }

    // Allow the form to submit
    return true;
  });
});