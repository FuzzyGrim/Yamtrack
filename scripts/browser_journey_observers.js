// Opt-in benchmark only; never loaded by production templates.
(() => {
  const supported = PerformanceObserver.supportedEntryTypes || [];
  const state = {supported, lcp: null, long_tasks: [], dropped_long_tasks: 0,
    layout_shift: 0, layout_shift_entries: 0, marks: {}};
  const observers = [];
  const record = (type, entries) => {
    for (const entry of entries) {
      if (type === "largest-contentful-paint") {
        state.lcp = {start_ms: entry.startTime, render_ms: entry.renderTime,
          load_ms: entry.loadTime, size: entry.size,
          element: entry.element ? entry.element.tagName.toLowerCase() : null};
      } else if (type === "layout-shift") {
        if (!entry.hadRecentInput) {
          state.layout_shift += entry.value;
          state.layout_shift_entries++;
        }
      } else if (state.long_tasks.length < 10000) {
        state.long_tasks.push({start_ms: entry.startTime, duration_ms: entry.duration});
      } else {
        state.dropped_long_tasks++;
      }
    }
  };
  for (const type of ["largest-contentful-paint", "longtask", "layout-shift"]) {
    if (supported.includes(type)) {
      const observer = new PerformanceObserver(list => record(type, list.getEntries()));
      observer.observe({type, buffered: true});
      observers.push({observer, type});
    }
  }
  // Readiness marks: the first time each selector matches, stamped on the next
  // frame (content is in the DOM and has had a chance to paint). The runner sets
  // window.__floppyReadySelectors before this script runs.
  const wanted = Object.entries(window.__floppyReadySelectors || {});
  const pending = new Set(wanted.map(([name]) => name));
  const check = () => {
    for (const [name, selector] of wanted) {
      if (pending.has(name) && document.querySelector(selector)) {
        pending.delete(name);
        const seen = performance.now();
        requestAnimationFrame(() => {
          state.marks[name] = {seen_ms: seen, frame_ms: performance.now()};
        });
      }
    }
    if (!pending.size && mutations) mutations.disconnect();
  };
  const mutations = pending.size ? new MutationObserver(check) : null;
  if (mutations) {
    mutations.observe(document, {childList: true, subtree: true, attributes: true,
      attributeFilter: ["data-react-mounted", "data-ready", "class"]});
  }
  window.__floppyBenchmark = () => {
    for (const {observer, type} of observers) record(type, observer.takeRecords());
    check();
    return {...state, observed_ms: performance.now(),
      unmatched_marks: Array.from(pending)};
  };
})();
