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
    let diaryLogUrl;
    if (logUrl.includes('/season/')) {
        // For seasons, use the season-specific diary-log URL
        diaryLogUrl = logUrl.replace('/log/season/', '/diary-log/season/');
    } else {
        // For movies and TV shows, use the regular diary-log URL
        diaryLogUrl = logUrl.replace('/log/', '/diary-log/');
    }
    
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

// Book progress tracking functions
function initBookProgressModal(modalRoot) {
    if (!modalRoot) {
        return;
    }

    const modal = modalRoot.querySelector('[data-book-progress-modal]') || modalRoot;
    const form = modal.querySelector('#book-progress-form');
    if (!form) {
        return;
    }

    const progressValueInput = form.querySelector('input[name="progress_value"]');
    const progressUnit = form.querySelector('[data-progress-unit]');
    const progressRadios = form.querySelectorAll('input[name="progress_type"]');
    const optionLabels = form.querySelectorAll('[data-progress-option]');

    const updateProgressUI = () => {
        const selected = form.querySelector('input[name="progress_type"]:checked');
        const type = selected ? selected.value : 'pages';

        if (!progressValueInput || !progressUnit) {
            return;
        }

        if (type === 'percentage') {
            progressValueInput.max = 100;
            progressUnit.textContent = '%';
        } else {
            progressValueInput.max = 10000;
            progressUnit.textContent = 'pages';
        }

        optionLabels.forEach(label => {
            const isActive = label.dataset.progressOption === type;
            label.classList.toggle('bg-gray-600', isActive);
            label.classList.toggle('text-white', isActive);
            label.classList.toggle('text-gray-300', !isActive);
            label.classList.toggle('hover:bg-[#343a40]', !isActive);
        });
    };

    progressRadios.forEach(radio => {
        radio.addEventListener('change', updateProgressUI);
    });
    updateProgressUI();

    form.addEventListener('submit', function (e) {
        e.preventDefault();

        const formData = new FormData(form);
        const progressType = formData.get('progress_type');
        const progressValueRaw = formData.get('progress_value');
        const progressValue = progressValueRaw ? parseInt(progressValueRaw, 10) : 0;

        if (progressType === 'percentage' && progressValue > 100) {
            alert('Percentage cannot exceed 100%');
            return;
        }

        fetch(form.action, {
            method: 'POST',
            body: formData,
            headers: {
                'X-CSRFToken': formData.get('csrfmiddlewaretoken')
            }
        })
            .then(response => response.text())
            .then(html => {
                const mediaActions = document.getElementById('media-actions');
                if (mediaActions) {
                    mediaActions.innerHTML = html;
                }
                modalRoot.remove();
            })
            .catch(error => {
                console.error('Error:', error);
                alert('Error logging progress. Please try again.');
            });
    });
}

function showBookProgressModal(url) {
    fetch(url)
        .then(response => response.text())
        .then(html => {
            const wrapper = document.createElement('div');
            wrapper.innerHTML = html.trim();
            const modal = wrapper.firstElementChild;
            if (!modal) {
                return;
            }
            document.body.appendChild(modal);
            initBookProgressModal(modal);
        })
        .catch(error => {
            console.error('Error loading book progress modal:', error);
            alert('Error loading progress form. Please try again.');
        });
}

function showBookCompletedModal(url) {
    fetch(url)
        .then(response => response.text())
        .then(html => {
            document.body.insertAdjacentHTML('beforeend', html);
        })
        .catch(error => {
            console.error('Error loading book completed modal:', error);
            alert('Error loading completed form. Please try again.');
        });
}

function loadBookLogModal(url) {
    if (!url) {
        console.warn('Book log modal URL missing');
        return;
    }

    const progressModal = document.querySelector('[data-book-progress-modal]');
    if (progressModal) {
        const modalRoot = progressModal.closest('.fixed');
        if (modalRoot) {
            modalRoot.remove();
        }
    }

    fetch(url)
        .then(response => response.text())
        .then(html => {
            const container = document.getElementById('log-modal-container');
            if (container) {
                container.innerHTML = html;
            } else {
                document.body.insertAdjacentHTML('beforeend', html);
            }
        })
        .catch(error => {
            console.error('Failed to load book log modal:', error);
            alert('Could not open log modal. Please try again.');
        });
}

document.addEventListener('click', (event) => {
    const trigger = event.target.closest('[data-book-complete-url]');
    if (!trigger) {
        return;
    }

    event.preventDefault();
    const url = trigger.getAttribute('data-book-complete-url');
    loadBookLogModal(url);
});

document.addEventListener('submit', (event) => {
    const form = event.target.closest('[data-diary-log-form]');
    if (!form) {
        return;
    }

    event.preventDefault();

    const formData = new FormData(form);
    const action = form.getAttribute('action');
    const modal = form.closest('.fixed');

    fetch(action, {
        method: 'POST',
        body: formData,
        headers: {
            'X-Requested-With': 'XMLHttpRequest',
        },
    })
        .then((response) => {
            if (!response.ok) {
                throw new Error(`Request failed with status ${response.status}`);
            }
            return response.json();
        })
        .then((data) => {
            if (data?.success) {
                if (modal) {
                    modal.remove();
                }
                window.location.reload();
            } else {
                console.error('Diary entry error:', data);
                alert('Failed to create diary entry. Please try again.');
            }
        })
        .catch((error) => {
            console.error('Network error while logging diary entry:', error);
            alert('Network error occurred while logging entry.');
        });
});
