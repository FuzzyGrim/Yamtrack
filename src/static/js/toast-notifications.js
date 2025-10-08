/**
 * Toast Notification System
 * Shows feedback messages when actions affect multiple levels (TV shows, seasons, episodes)
 */

(function() {
  'use strict';

  // Create toast container if it doesn't exist
  function getToastContainer() {
    let container = document.getElementById('toast-container');
    if (!container) {
      container = document.createElement('div');
      container.id = 'toast-container';
      container.className = 'fixed bottom-4 right-4 z-50 flex flex-col gap-2 pointer-events-none';
      container.style.maxWidth = '400px';
      document.body.appendChild(container);
    }
    return container;
  }

  // Show a toast notification
  function showToast(message, type = 'info', duration = 4000) {
    const container = getToastContainer();
    
    const toast = document.createElement('div');
    toast.className = `pointer-events-auto transform transition-all duration-300 ease-in-out opacity-0 translate-x-full`;
    
    // Color schemes for different types
    const typeStyles = {
      success: 'bg-green-900 border-green-700 text-green-100',
      error: 'bg-red-900 border-red-700 text-red-100',
      info: 'bg-blue-900 border-blue-700 text-blue-100',
      warning: 'bg-yellow-900 border-yellow-700 text-yellow-100'
    };
    
    // Icons for different types
    const typeIcons = {
      success: '<svg class="w-5 h-5" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd"></path></svg>',
      error: '<svg class="w-5 h-5" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clip-rule="evenodd"></path></svg>',
      info: '<svg class="w-5 h-5" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clip-rule="evenodd"></path></svg>',
      warning: '<svg class="w-5 h-5" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clip-rule="evenodd"></path></svg>'
    };
    
    toast.innerHTML = `
      <div class="flex items-start gap-3 px-4 py-3 rounded-lg border ${typeStyles[type] || typeStyles.info} shadow-lg">
        <div class="flex-shrink-0 mt-0.5">
          ${typeIcons[type] || typeIcons.info}
        </div>
        <div class="flex-1 min-w-0">
          <p class="text-sm font-medium">${message}</p>
        </div>
        <button onclick="this.closest('.pointer-events-auto').remove()" class="flex-shrink-0 ml-2 hover:opacity-70 transition-opacity">
          <svg class="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
            <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd"></path>
          </svg>
        </button>
      </div>
    `;
    
    container.appendChild(toast);
    
    // Trigger animation
    setTimeout(() => {
      toast.classList.remove('opacity-0', 'translate-x-full');
    }, 10);
    
    // Auto-remove after duration
    if (duration > 0) {
      setTimeout(() => {
        toast.classList.add('opacity-0', 'translate-x-full');
        setTimeout(() => toast.remove(), 300);
      }, duration);
    }
  }

  // Export to window for global access
  window.showToast = showToast;

  // Listen for HTMX responses with custom headers
  function initToastNotifications() {
    if (!document.body) {
      // DOM not ready yet, try again later
      setTimeout(initToastNotifications, 100);
      return;
    }
    
    document.body.addEventListener('htmx:afterRequest', function(event) {
      const xhr = event.detail.xhr;
      
      // Check for custom notification headers
      const notificationMessage = xhr.getResponseHeader('X-Notification-Message');
      const notificationType = xhr.getResponseHeader('X-Notification-Type') || 'info';
      
      if (notificationMessage) {
        showToast(notificationMessage, notificationType);
      }
    });
  }
  
  // Initialize when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initToastNotifications);
  } else {
    initToastNotifications();
  }

  // Helper functions for common scenarios
  window.showSuccessToast = function(message) {
    showToast(message, 'success');
  };

  window.showErrorToast = function(message) {
    showToast(message, 'error');
  };

  window.showInfoToast = function(message) {
    showToast(message, 'info');
  };

  window.showWarningToast = function(message) {
    showToast(message, 'warning');
  };

})();

