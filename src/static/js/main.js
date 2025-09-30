// Main application JavaScript functions

function openPosterModal(url) {
    fetch(url)
        .then(response => response.text())
        .then(html => {
            document.getElementById('poster-modal-container').innerHTML = html;
        });
}

function submitDiaryLog(button) {
    const modal = button.closest('.fixed');
    const form = modal.querySelector('form') || modal;
    
    // Get form data
    const formData = new FormData();
    
    // Get watch date
    const watchDate = form.querySelector('#watch-date');
    if (watchDate) {
        formData.append('watch_date', watchDate.value);
    }
    
    // Get rating from hidden input
    const ratingInput = form.querySelector('input[name="rating"]');
    if (ratingInput) {
        formData.append('rating', ratingInput.value || 0);
    }
    
    // Get review
    const review = form.querySelector('textarea[name="review"]');
    if (review) {
        formData.append('review', review.value);
    }
    
    // Get liked status from hidden input
    const likedInput = form.querySelector('input[name="liked"]');
    if (likedInput) {
        formData.append('liked', likedInput.value === 'true');
    }
    
    // Get rewatch status
    const rewatchCheckbox = form.querySelector('input[name="is_rewatch"]');
    if (rewatchCheckbox) {
        formData.append('is_rewatch', rewatchCheckbox.checked);
    }
    
    // Get auto_mark_consumed (default to true for logging)
    formData.append('auto_mark_consumed', 'true');
    
    // Get CSRF token
    const csrfToken = form.querySelector('[name=csrfmiddlewaretoken]');
    if (csrfToken) {
        formData.append('csrfmiddlewaretoken', csrfToken.value);
    }
    
    // Get the URL from the modal
    const logUrl = modal.querySelector('[data-log-url]').dataset.logUrl;
    if (!logUrl) {
        console.error('No log URL found');
        return;
    }
    
    // Convert to diary-log URL
    const diaryLogUrl = logUrl.replace('/log/', '/diary-log/');
    
    console.log('Submitting diary entry to:', diaryLogUrl);
    console.log('Form data:', Object.fromEntries(formData));
    
    // Submit the form
    fetch(diaryLogUrl, {
        method: 'POST',
        body: formData,
        headers: {
            'X-CSRFToken': csrfToken ? csrfToken.value : '',
        }
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            // Close the modal
            modal.remove();
            
            // Show success message or refresh the page
            console.log('Diary entry created successfully:', data.entry_id);
            
            // Optionally refresh the page or update UI
            window.location.reload();
        } else {
            console.error('Error creating diary entry:', data.error);
            alert('Error creating diary entry: ' + data.error);
        }
    })
    .catch(error => {
        console.error('Network error:', error);
        alert('Network error occurred while creating diary entry');
    });
}
