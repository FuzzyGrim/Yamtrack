document.addEventListener('DOMContentLoaded', function() {
  const mediaTypeSelect = document.getElementById('id_media_type');
  const seasonNumberField = document.getElementById('id_season_number');
  const episodeNumberField = document.getElementById('id_episode_number');

  function updateFieldVisibility() {
      const selectedMediaType = mediaTypeSelect.value;
      
      if (selectedMediaType === 'season') {
          console.log("isSeason");
          seasonNumberField.parentNode.style.display = 'block';
          seasonNumberField.required = true;
      } else if (selectedMediaType === 'episode') {
          console.log("isEpisode");
          seasonNumberField.parentNode.style.display = 'block';
          episodeNumberField.parentNode.style.display = 'block';
          seasonNumberField.required = true;
          episodeNumberField.required = true;
      } else {
          seasonNumberField.parentNode.style.display = 'none';
          episodeNumberField.parentNode.style.display = 'none';
          seasonNumberField.required = false;
          episodeNumberField.required = false;
      }
  }

  mediaTypeSelect.addEventListener('change', updateFieldVisibility);
  updateFieldVisibility(); // Call once to set initial state
});