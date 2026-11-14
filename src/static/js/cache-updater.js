/**
 * Cache Updater - Polls cache status and updates pages when fresh data is available
 *
 * Usage:
 *   Initialize with: CacheUpdater.init(cacheType, options)
 *   Options: { rangeName, loggingStyle, pollInterval, timeout, requestTimeout, maxRetryInterval }
 */

// Guard against re-execution: this script is re-evaluated when pages are
// loaded via boosted (hx-boost) navigation, and a top-level class
// redeclaration would throw.
if (!window.CacheUpdater) {

class CacheUpdater {
    constructor(cacheType, options = {}) {
        this.cacheType = cacheType;
        this.rangeName = options.rangeName || null;
        this.loggingStyle = options.loggingStyle || 'repeats';
        this.mediaType = options.mediaType || null;
        this.showMore = Boolean(options.showMore);
        this.pollInterval = options.pollInterval ?? 2500; // 2.5 seconds
        this.timeout = options.timeout ?? 180000; // 180 seconds — stats rebuild can take 90s+ for large libraries
        this.requestTimeout = options.requestTimeout ?? 10000; // Bound one status request independently
        this.maxRetryInterval = options.maxRetryInterval ?? 30000; // Cap transient-failure backoff
        this.startTime = Date.now();
        this.pollTimer = null;
        this.abortController = null;
        this.isPolling = false;
        this.retryAttempt = 0;
        this.offlineSince = null;
        this.wasRefreshing = false; // Track if we were refreshing in previous poll
        this.lastBuiltAt = null; // Track built_at to detect fresh builds even if lock lingers
        this.onUpdateCallback = options.onUpdate || null;
        this.onRefreshCompleteCallback = options.onRefreshComplete || null;
        // Runs whenever polling ends by any path, so a page indicator can never outlive the poller.
        this.onStopCallback = options.onStop || null;
        // Remember which page started this updater so an orphaned poll loop
        // can't reload an unrelated page after boosted navigation away.
        this.pagePath = window.location.pathname;
        this.handleOnline = () => this.resumeAfterOffline();
        this.handleOffline = () => this.pauseForOffline();
        this.handlePageHide = (event) => {
            // A page placed in the back/forward cache can resume later with
            // the same JavaScript state. Keep the updater alive in that case.
            if (!event.persisted) {
                this.stop();
            }
        };
    }

    currentRefreshActive(data) {
        if (!data) {
            return false;
        }

        const waitingOnPendingStatisticsRange =
            this.cacheType === 'statistics' &&
            ((!data.exists || data.is_stale) && data.any_range_refreshing);

        return Boolean(
            data.is_refreshing ||
            data.refresh_scheduled ||
            data.metadata_refreshing ||
            waitingOnPendingStatisticsRange
        );
    }

    /**
     * Start polling for cache updates
     */
    start() {
        if (this.isPolling) {
            return;
        }

        this.isPolling = true;
        this.startTime = Date.now();
        this.retryAttempt = 0;
        this.offlineSince = null;
        window.addEventListener('online', this.handleOnline);
        window.addEventListener('offline', this.handleOffline);
        window.addEventListener('pagehide', this.handlePageHide);
        // Don't reset wasRefreshing here - it may have been set by the caller
        // to track the initial state (e.g., if refresh was already in progress)
        this.poll();
    }

    /**
     * Stop polling
     */
    stop() {
        const wasPolling = this.isPolling;
        this.isPolling = false;
        if (this.pollTimer) {
            clearTimeout(this.pollTimer);
            this.pollTimer = null;
        }
        if (this.abortController) {
            this.abortController.abort();
            this.abortController = null;
        }
        window.removeEventListener('online', this.handleOnline);
        window.removeEventListener('offline', this.handleOffline);
        window.removeEventListener('pagehide', this.handlePageHide);
        this.offlineSince = null;
        if (wasPolling && this.onStopCallback) {
            this.onStopCallback();
        }
    }

    schedulePoll(delay = this.pollInterval) {
        if (!this.isPolling) {
            return;
        }

        if (this.pollTimer) {
            clearTimeout(this.pollTimer);
        }
        this.pollTimer = setTimeout(() => {
            this.pollTimer = null;
            this.poll();
        }, delay);
    }

    finishTimeout() {
        this.stop();
        if (this.onRefreshCompleteCallback) {
            this.onRefreshCompleteCallback(false, 'timeout');
        }
    }

    pauseForOffline() {
        if (!this.isPolling || this.offlineSince !== null) {
            return;
        }

        this.offlineSince = Date.now();
        if (this.pollTimer) {
            clearTimeout(this.pollTimer);
            this.pollTimer = null;
        }
        if (this.abortController) {
            this.abortController.abort();
        }
        console.log('CacheUpdater: Offline, pausing cache status polling');
    }

    resumeAfterOffline() {
        if (!this.isPolling || this.offlineSince === null) {
            return;
        }

        // Offline time should not consume the active refresh timeout.
        this.startTime += Date.now() - this.offlineSince;
        this.offlineSince = null;
        this.retryAttempt = 0;
        console.log('CacheUpdater: Online, resuming cache status polling');
        this.poll();
    }

    shouldRetryStatus(status) {
        return status === 408 || status === 425 || status === 429 || status >= 500;
    }

    retryAfterMs(response) {
        if (!response.headers || typeof response.headers.get !== 'function') {
            return 0;
        }

        const value = response.headers.get('Retry-After');
        if (!value) {
            return 0;
        }

        const seconds = Number(value);
        if (Number.isFinite(seconds) && seconds >= 0) {
            return seconds * 1000;
        }

        const retryAt = Date.parse(value);
        return Number.isNaN(retryAt) ? 0 : Math.max(0, retryAt - Date.now());
    }

    scheduleRetry(retryAfter = 0) {
        if (!this.isPolling) {
            return;
        }

        const elapsed = Date.now() - this.startTime;
        const remaining = this.timeout - elapsed;
        if (remaining <= 0) {
            this.finishTimeout();
            return;
        }

        const backoff = Math.min(
            this.pollInterval * (2 ** this.retryAttempt),
            this.maxRetryInterval,
        );
        this.retryAttempt += 1;
        const delay = Math.min(Math.max(backoff, retryAfter), remaining);
        this.schedulePoll(delay);
    }

    /**
     * Poll cache status endpoint
     */
    async poll() {
        if (!this.isPolling) {
            return;
        }

        // Stop if the user navigated to a different page since this updater
        // was created (the poll timer survives boosted body swaps).
        if (window.location.pathname !== this.pagePath) {
            this.stop();
            return;
        }

        // Do not send status traffic while the browser knows it is offline.
        if (typeof navigator !== 'undefined' && navigator.onLine === false) {
            this.pauseForOffline();
            return;
        }

        // Check timeout
        if (Date.now() - this.startTime >= this.timeout) {
            this.finishTimeout();
            return;
        }

        const controller = new AbortController();
        let requestTimedOut = false;
        this.abortController = controller;
        const remaining = this.timeout - (Date.now() - this.startTime);
        const requestTimeout = Math.min(this.requestTimeout, remaining);
        const requestTimer = setTimeout(() => {
            requestTimedOut = true;
            controller.abort();
        }, requestTimeout);

        try {
            const params = new URLSearchParams({
                cache_type: this.cacheType,
            });

            if (this.cacheType === 'statistics' && this.rangeName) {
                params.append('range_name', this.rangeName);
            } else if (this.cacheType === 'history' && this.loggingStyle) {
                params.append('logging_style', this.loggingStyle);
            } else if (this.cacheType === 'discover' && this.mediaType) {
                params.append('media_type', this.mediaType);
                params.append('show_more', this.showMore ? '1' : '0');
            }

            const response = await fetch(`/api/cache-status/?${params.toString()}`, {
                signal: controller.signal,
            });

            if (!response.ok) {
                if (this.shouldRetryStatus(response.status)) {
                    console.warn(`CacheUpdater: HTTP ${response.status}, retrying with backoff`);
                    this.scheduleRetry(this.retryAfterMs(response));
                } else {
                    console.error(`CacheUpdater: HTTP ${response.status}, stopping polling`);
                    this.stop();
                    if (this.onRefreshCompleteCallback) {
                        this.onRefreshCompleteCallback(false, `http_${response.status}`);
                    }
                }
                return;
            }

            const data = await response.json();
            this.retryAttempt = 0;
            const prevBuiltAt = this.lastBuiltAt;
            const refreshActive = this.currentRefreshActive(data);
            console.log('CacheUpdater: Poll result', {
                exists: data.exists,
                is_stale: data.is_stale,
                is_refreshing: data.is_refreshing,
                recently_built: data.recently_built,
                any_range_refreshing: data.any_range_refreshing,
                wasRefreshing: this.wasRefreshing,
                wasRefreshingBefore: this.wasRefreshing, // Show before update
                built_at: data.built_at
            });

            // Check if refresh just completed (was refreshing, now not)
            const refreshJustCompleted = this.wasRefreshing && !refreshActive;
            // Detect if cache was rebuilt even though lock still looks active
            const builtAtChanged = this.wasRefreshing && data.built_at && data.built_at !== prevBuiltAt;

            // Update wasRefreshing for next poll (but keep old value for this check)
            const wasRefreshingBefore = this.wasRefreshing;
            this.wasRefreshing = refreshActive;
            // Track latest built_at
            this.lastBuiltAt = data.built_at || null;

            // Only reload if we've been polling for at least 2 seconds
            // This ensures we were actually waiting for the refresh, not just detecting
            // a refresh that completed before we started polling (which would cause loops)
            const hasBeenPolling = Date.now() - this.startTime >= 2000;

            // Reload only when we were actively waiting and the current cache
            // has transitioned to a fresh, ready state during polling.
            const currentRangeDone = hasBeenPolling && (
                refreshJustCompleted ||
                builtAtChanged
            ) && data.exists && !data.is_stale && !refreshActive;

            // Reload as soon as the current cache is rebuilt. Other ranges may still
            // be refreshing in the background, but they should not block the active page.
            const shouldUpdate = currentRangeDone;

            if (shouldUpdate) {
                console.log('CacheUpdater: Refresh completed, updating page', {
                    refreshJustCompleted,
                    recently_built: data.recently_built,
                    exists: data.exists,
                    is_stale: data.is_stale,
                    is_refreshing: data.is_refreshing,
                    any_range_refreshing: data.any_range_refreshing,
                    wasRefreshingBefore,
                    wasRefreshing: this.wasRefreshing,
                    currentRangeDone,
                    shouldUpdate
                });
                this.stop();
                if (this.onRefreshCompleteCallback) {
                    this.onRefreshCompleteCallback(true, 'complete');
                }
                // Trigger page update to show fresh data
                if (this.onUpdateCallback) {
                    this.onUpdateCallback();
                } else {
                    this.updatePage();
                }
                return;
            }

            // If still refreshing, continue polling
            if (refreshActive) {
                console.log('CacheUpdater: Still refreshing, continuing to poll');
                this.schedulePoll();
            } else if (data.exists && !data.is_stale && !refreshActive) {
                // If we were waiting for a refresh, we should have caught it above.
                // Otherwise, the current page is already up to date.
                console.log('CacheUpdater: Cache is fresh and not refreshing, stopping', {
                    wasRefreshingBefore,
                    exists: data.exists,
                    is_stale: data.is_stale,
                    is_refreshing: data.is_refreshing,
                    any_range_refreshing: data.any_range_refreshing
                });
                this.stop();
                if (this.onRefreshCompleteCallback) {
                    this.onRefreshCompleteCallback(true, 'complete');
                }
            } else if (data.exists && data.is_stale) {
                // Cache exists but is stale - only poll if a refresh is actually running
                if (this.cacheType === 'statistics' && !refreshActive && !data.any_range_refreshing) {
                    console.log('CacheUpdater: Cache is stale but not refreshing, stopping');
                    this.stop();
                    if (this.onRefreshCompleteCallback) {
                        this.onRefreshCompleteCallback(false, 'stale_no_refresh');
                    }
                } else {
                    console.log('CacheUpdater: Cache is stale, continuing to poll');
                    this.schedulePoll();
                }
            } else if (!data.exists) {
                // Cache doesn't exist and no refresh in progress, stop polling
                console.log('CacheUpdater: Cache does not exist, stopping');
                this.stop();
                if (this.onRefreshCompleteCallback) {
                    this.onRefreshCompleteCallback(false, 'no_cache');
                }
            }
        } catch (error) {
            if (!this.isPolling) {
                return;
            }

            if (typeof navigator !== 'undefined' && navigator.onLine === false) {
                this.pauseForOffline();
                return;
            }

            // stop(), offline pause, or a fast restart can intentionally abort
            // an older request. Never let that stale request schedule new work.
            if (controller.signal.aborted && !requestTimedOut) {
                return;
            }

            if (requestTimedOut) {
                console.warn('CacheUpdater: Cache status request timed out, retrying with backoff');
            } else {
                console.error('Cache status poll error:', error);
            }
            // Network failures may be temporary, but repeated fixed-interval requests
            // waste client and server resources. Back off while keeping the refresh live.
            this.scheduleRetry();
        } finally {
            clearTimeout(requestTimer);
            if (this.abortController === controller) {
                this.abortController = null;
            }
        }
    }

    /**
     * Update the page content by reloading the current page
     */
    updatePage() {
        console.log('CacheUpdater: Updating page to show fresh data');
        // Mark that this is a cache-triggered reload with timestamp to prevent loops
        if (this.cacheType === 'history') {
            sessionStorage.setItem('history_cache_reload', Date.now().toString());
        }
        // Reload the page to show fresh data
        // Using a small delay to ensure cache is fully updated
        setTimeout(() => {
            if (window.location.pathname !== this.pagePath) {
                console.log('CacheUpdater: Page changed, skipping reload');
                return;
            }
            console.log('CacheUpdater: Reloading page now');
            window.location.reload();
        }, 300);
    }

    /**
     * Static method to initialize cache updater for a page
     */
    static init(cacheType, options = {}) {
        const updater = new CacheUpdater(cacheType, options);

        // Auto-start if cache is stale or refreshing
        // This will be checked on page load via Alpine.js
        return updater;
    }
}

// Export for use in modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = CacheUpdater;
}

// Make available globally
window.CacheUpdater = CacheUpdater;

}
