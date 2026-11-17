(function () {
// Keep a floating chart popup inside the viewport. A popup centered on a bar near
// the screen edge otherwise hangs off the page and makes it scroll sideways.
function placeChartTooltip(tooltipEl, context) {
  const margin = 8;
  const rect = context.chart.canvas.getBoundingClientRect();
  const viewportWidth = document.documentElement.clientWidth;
  // Fixed width first, so measuring does not depend on where the popup sits.
  tooltipEl.style.width = "max-content";
  tooltipEl.style.maxWidth = viewportWidth - margin * 2 + "px";
  const half = tooltipEl.offsetWidth / 2;
  const centerX = Math.min(
    Math.max(rect.left + context.tooltip.caretX, margin + half),
    viewportWidth - margin - half
  );
  tooltipEl.style.opacity = 1;
  tooltipEl.style.position = "absolute";
  tooltipEl.style.left = centerX + window.scrollX + "px";
  tooltipEl.style.top = rect.top + window.scrollY + context.tooltip.caretY + "px";
  tooltipEl.style.transform = "translate(-50%, -100%)";
  tooltipEl.style.pointerEvents = "none";
}

// React islands ([data-props-id] hosts) replace the legacy chart markup inside
// them as soon as they mount, and island-reveal.css keeps that markup hidden
// until then. Drawing those charts on every visit was wasted work (14 Chart.js
// charts, the heatmap and the rhythm grid, thrown away a moment later). They
// are drawn only if an island has still not mounted when its fallback is
// revealed (the four-second fail-safe), so a page whose bundle fails to load
// still shows its charts.
var STATISTICS_ISLANDS = "[data-statistics-charts-root], [data-statistics-sections-root], [data-statistics-summary-root]";

function statisticsChartLookup(scope) {
  return function (id) {
    const element = document.getElementById(id);
    // Data blocks (<script type="application/json">) are always readable.
    if (!element || element.tagName === "SCRIPT") return element;
    const host = element.closest(STATISTICS_ISLANDS);
    if (scope === "fallback") {
      return host && !host.hasAttribute("data-react-mounted") ? element : null;
    }
    return host ? null : element;
  };
}

function initStatisticsCharts(scope) {
  // "page" draws everything outside the React islands; "fallback" draws only
  // inside islands that never mounted (see startStatisticsCharts below).
  const page = scope !== "fallback";
  const byId = statisticsChartLookup(scope);
  if (typeof Chart === "undefined") {
    return;
  }
  Chart.register(ChartDataLabels);
  // Labels are also backend data keys: translate only at the display boundary.
  function chartDisplayLabel(value) {
    const labels = {
      "TV Show": gettext("TV Show"),
      "TV Shows": gettext("TV Shows"),
      "TV Season": gettext("TV Season"),
      "Movie": gettext("Movie"),
      "Movies": gettext("Movies"),
      "Anime": gettext("Anime"),
      "Music": gettext("Music"),
      "Podcast": gettext("Podcast"),
      "Podcasts": gettext("Podcasts"),
      "Book": gettext("Book"),
      "Books": gettext("Books"),
      "Comic": gettext("Comic"),
      "Comics": gettext("Comics"),
      "Comic Issue": gettext("Comic Issue"),
      "Board Game": gettext("Board Game"),
      "Board Games": gettext("Board Games"),
      "Game": gettext("Game"),
      "Games": gettext("Games"),
      "Manga": gettext("Manga"),
      "Completed": gettext("Completed"),
      "In progress": gettext("In progress"),
      "Planning": gettext("Planning"),
      "Paused": gettext("Paused"),
      "Dropped": gettext("Dropped"),
      "Repeating": gettext("Repeating"),
      "Other": gettext("Other"),
      "Unknown": gettext("Unknown"),
      "Items": gettext("Items"),
      "Plays": gettext("Plays"),
      "Episodes": gettext("Episodes"),
      "Chapters": gettext("Chapters"),
      "Pages": gettext("Pages"),
      "Hours": gettext("Hours"),
      "Minutes": gettext("Minutes"),
      "Movie Plays": gettext("Movie Plays"),
      "Episode Plays": gettext("Episode Plays"),
      "Anime Plays": gettext("Anime Plays"),
      "Music Plays": gettext("Music Plays"),
      "Podcast Plays": gettext("Podcast Plays"),
      "Pages Read": gettext("Pages Read"),
      "Chapters Read": gettext("Chapters Read"),
    };
    return labels[value] || value;
  }
  function chartEscapeHtml(value) {
    return String(value).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }


  // Resolved once per chart-init pass so custom HTML tooltips (built via
  // inline styles, not Tailwind classes) follow the current light/dark theme.
  function chartThemeColor(varName, fallback) {
    var value = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
    return value || fallback;
  }
  var CHART_TOOLTIP_BG = chartThemeColor("--color-panel", "#1f2937");
  var CHART_TOOLTIP_BORDER = chartThemeColor("--color-surface-border", "rgba(255,255,255,0.1)");
  var CHART_TOOLTIP_TEXT = chartThemeColor("--color-text", "#f3f4f6");

  // Destroy charts from a previous visit (their canvases were detached by a
  // boosted body swap) before creating new instances.
  if (page) (window.__floppyStatsCharts || []).forEach(function (chart) {
    try {
      chart.destroy();
    } catch (error) {
      console.debug("[stats] failed to destroy stale chart", error);
    }
  });
  if (page) window.__floppyStatsCharts = [];

  // Custom external tooltip for bar charts
  function customBarTooltip(context) {
    // External custom tooltip
    let tooltipEl = document.getElementById("chartjs-tooltip");

    // Create element if it doesn't exist
    if (!tooltipEl) {
      tooltipEl = document.createElement("div");
      tooltipEl.id = "chartjs-tooltip";
      document.body.appendChild(tooltipEl);
    }

    // Hide if no tooltip
    const tooltipModel = context.tooltip;
    if (tooltipModel.opacity === 0) {
      tooltipEl.style.opacity = 0;
      return;
    }

    // Set Text
    if (tooltipModel.body) {
      const chart = context.chart;
      const dataIndex = tooltipModel.dataPoints[0].dataIndex;
      const title = tooltipModel.title[0] || "";

      // Format title based on chart type
      let formattedTitle = title;
      if (chart.canvas.id === "scoreStackedChart") {
        const score = parseInt(title);
        const scoreMax =
          (chart.options &&
            chart.options.plugins &&
            chart.options.plugins.scoreScaleMax) ||
          10;
        if (score === scoreMax) {
          formattedTitle = interpolate(gettext("Score: %(score)s"), { score: scoreMax }, true);
        } else {
          formattedTitle = interpolate(gettext("Score: %(lower)s\u2013%(upper)s"), { lower: score.toLocaleString(document.documentElement.lang || undefined, { minimumFractionDigits: 1 }), upper: (score + 0.9).toLocaleString(document.documentElement.lang || undefined, { minimumFractionDigits: 1 }) }, true);
        }
      }

      function fmt(v) {
        const n = Number(v) || 0;
        return n.toLocaleString(document.documentElement.lang || undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
      }

      let html = '<div style="font-weight:600;margin-bottom:6px;color:' + CHART_TOOLTIP_TEXT + '">' + chartEscapeHtml(formattedTitle) + "</div>";
      chart.data.datasets.forEach((dataset) => {
        const raw = Number(dataset.data[dataIndex]) || 0;
        if (raw > 0) {
          const bgColor = dataset.backgroundColor;
          const label = chartDisplayLabel(dataset.label || "");
          html +=
            '<div style="display:flex;align-items:center;gap:6px;margin-top:4px">' +
            '<span style="width:10px;height:10px;border-radius:2px;background:' +
            bgColor +
            ';flex-shrink:0"></span>' +
            "<span>" + chartEscapeHtml(label) + ": " + fmt(raw) + "</span>" +
            "</div>";
        }
      });

      tooltipEl.innerHTML = html;
    }

    placeChartTooltip(tooltipEl, context);
  }

  // Common configuration for bar charts
  const barChartConfig = {
    responsive: true,
    maintainAspectRatio: false,
    scales: {
      x: {
        stacked: true,
        grid: { color: "rgba(255, 255, 255, 0.1)" },
        ticks: { color: "#D1D5DB" },
      },
      y: {
        stacked: true,
        beginAtZero: true,
        grid: { color: "rgba(255, 255, 255, 0.1)" },
        ticks: { color: "#D1D5DB", precision: 0 },
      },
    },
    plugins: {
      legend: {
        position: "bottom",
        labels: {
          color: "#D1D5DB",
          padding: 20,
          boxWidth: 12,
          boxHeight: 12,
          usePointStyle: true,
          pointStyle: "rectRounded",
          textAlign: "center",
          font: {
            size: 12,
            lineHeight: 0.1,
          },
        },
      },
      tooltip: {
        enabled: false, // Disable default tooltip
        mode: "index",
        external: customBarTooltip,
      },
      // Disable datalabels for bar charts
      datalabels: {
        display: false,
      },
    },
    interaction: {
      mode: "index",
      intersect: false,
    },
  };

  // Helper function to process stacked bar data
  function processBarData(chartData) {
    return {
      labels: chartData.labels.map(chartDisplayLabel),
      datasets: chartData.datasets
        .map((dataset) => ({
          label: chartDisplayLabel(dataset.label),
          source_label: dataset.label,
          media_type: dataset.media_type,
          data: dataset.data,
          backgroundColor: dataset.background_color,
          borderColor: "rgba(255, 255, 255, 0.1)",
          borderRadius: 6,
          borderWidth: 1,
        }))
        .filter((dataset) => dataset.data.some((value) => value > 0)),
    };
  }

  // Helper function to safely initialize charts
  function initializeChartIfExists(elementId, chartType, data, options) {
    const element = byId(elementId);
    if (element) {
      const chart = new Chart(element.getContext("2d"), {
        type: chartType,
        data: data,
        options: options,
      });
      window.__floppyStatsCharts.push(chart);
      return chart;
    }
    return null;
  }

  function initializeSingleSeriesBarChart(canvasId, dataElementId) {
    const dataElement = byId(dataElementId);
    if (!dataElement) {
      return null;
    }

    const rawData = JSON.parse(dataElement.textContent || "null");
    if (!rawData || !rawData.labels || rawData.labels.length === 0) {
      return null;
    }

    const chartOptions = JSON.parse(JSON.stringify(barChartConfig));
    chartOptions.scales.x.stacked = false;
    chartOptions.scales.y.stacked = false;
    if (chartOptions.plugins && chartOptions.plugins.legend) {
      chartOptions.plugins.legend.display = false;
    }

    return initializeChartIfExists(
      canvasId,
      "bar",
      processBarData(rawData),
      chartOptions,
    );
  }

  // Ensure the copied score chart wrapper matches Activity History height
  function matchScoreCopyHeight() {
    // Size reference only: the hidden heatmap fallback keeps its layout box.
    const activityEl = document.getElementById("activityHistory");
    const scoreCopyWrapper = byId("scoreCopyWrapper");
    const scoreCanvasWrapper = byId("scoreCopyCanvasWrapper");
    const scoreCanvas = byId("scoreStackedChartCopy");

    if (!scoreCopyWrapper || !scoreCanvasWrapper) return 0;

    // If Activity History is hidden (stacked view), fall back to a generous baseline height
    const minHeight = 320; // ~2x the default 150px canvas height
    const activityHeight = activityEl
      ? Math.max(activityEl.clientHeight || 0, activityEl.offsetHeight || 0)
      : 0;
    const desiredHeight = Math.max(
      minHeight,
      activityHeight ? Math.round(activityHeight * 2) : 0
    );

    scoreCopyWrapper.style.minHeight = desiredHeight + "px";
    scoreCanvasWrapper.style.minHeight = desiredHeight + "px";
    scoreCanvasWrapper.style.height = desiredHeight + "px";

    // Ensure the canvas element fills its parent (Chart.js may set inline size attributes)
    if (scoreCanvas) {
      scoreCanvas.style.height = "100%";
      scoreCanvas.style.width = "100%";
      scoreCanvas.style.minHeight = desiredHeight + "px";
      scoreCanvas.height = desiredHeight;
    }

    return desiredHeight;
  }

  // Create Score Stacked Bar Chart
  const scoreDistributionElement =
    byId("score_distribution");
  if (scoreDistributionElement) {
    const scoreData = JSON.parse(scoreDistributionElement.textContent);
    const scoreChartOptions = JSON.parse(JSON.stringify(barChartConfig)); // Deep clone
    const scoreScaleMax = scoreData.scale_max || 10;

    // Add score-specific configurations
    scoreChartOptions.scales.x.title = {
      display: true,
      text: gettext("Score"),
      color: "#D1D5DB",
      padding: { top: 10, bottom: 0 },
    };

    scoreChartOptions.scales.y.title = {
      display: true,
      text: gettext("Number of Items"),
      color: "#D1D5DB",
      padding: { top: 0, left: 10 },
    };

    scoreChartOptions.plugins.title = {
      display: true,
      text: interpolate(ngettext("Average Score: %(score)s / %(max)s (%(count)s item)", "Average Score: %(score)s / %(max)s (%(count)s items)", scoreData.total_scored), { score: scoreData.average_score, max: scoreScaleMax, count: scoreData.total_scored }, true),
      color: "#D1D5DB",
      padding: { bottom: 10 },
      font: { size: 14 },
    };
    scoreChartOptions.plugins.scoreScaleMax = scoreScaleMax;

    // Ensure tooltip is properly configured for score chart
    scoreChartOptions.plugins.tooltip = {
      enabled: false,
      mode: "index",
      intersect: false,
      external: customBarTooltip,
    };

    // Ensure copy wrapper is sized to match Activity History BEFORE initializing the copy
    matchScoreCopyHeight();

    // Debug: log element presence and sizes to help diagnose blank chart issues
    try {
      const activityEl = byId("activityHistory");
      const copyWrapper = byId("scoreCopyWrapper");
      const copyCanvasWrapper = byId("scoreCopyCanvasWrapper");
      const copyCanvas = byId("scoreStackedChartCopy");
      console.debug("[stats] activityEl:", !!activityEl, "height:", activityEl ? activityEl.clientHeight : null);
      console.debug("[stats] copyWrapper:", !!copyWrapper, "minHeight:", copyWrapper ? copyWrapper.style.minHeight : null);
      console.debug("[stats] copyCanvasWrapper:", !!copyCanvasWrapper, "height:", copyCanvasWrapper ? copyCanvasWrapper.clientHeight : null);
      console.debug("[stats] copyCanvas:", !!copyCanvas, "clientH/clientW:", copyCanvas ? [copyCanvas.clientHeight, copyCanvas.clientWidth] : null);
    } catch (e) {
      // swallow debug errors
      console.debug("[stats] debug error", e);
    }

    // Prefer a daily-hours dataset for the copy (if provided by backend)
    const dailyHoursEl = byId("daily_hours_by_media_type");
    if (dailyHoursEl) {
      const dailyData = JSON.parse(dailyHoursEl.textContent || "null");
      if (dailyData && dailyData.labels && dailyData.labels.length > 0 && dailyData.datasets && dailyData.datasets.length > 0) {
        // Determine bucket size based on selected date range
        let startIso = null;
        let endIso = null;
        try {
          const startEl = byId("stats_start_date");
          const endEl = byId("stats_end_date");
          startIso = startEl ? JSON.parse(startEl.textContent || '""') : null;
          endIso = endEl ? JSON.parse(endEl.textContent || '""') : null;
        } catch (e) {
          console.debug('[stats] failed to read start/end JSON', e);
        }

        function chooseBucket(startIso, endIso, labels) {
          // Choose a bucket (day/week/month/year) by finding the
          // coarsest granularity that keeps the number of bars
          // reasonably small (target ~36 bars).
          // If start/end ISO are not provided (All Time), try to infer
          // them from the provided labels array (first/last date strings).
          let startIsoLocal = startIso;
          let endIsoLocal = endIso;
          if ((!startIsoLocal || !endIsoLocal) && Array.isArray(labels) && labels.length) {
            startIsoLocal = labels[0];
            endIsoLocal = labels[labels.length - 1];
          }
          if (!startIsoLocal || !endIsoLocal) {
            // If we only have labels and it's a short range, still favor day buckets
            if (labels && labels.length && labels.length <= 45) return 'day';
            return 'month';
          }
          const start = new Date(startIsoLocal);
          const end = new Date(endIsoLocal);
          const msPerDay = 24 * 60 * 60 * 1000;
          const spanDays = Math.ceil((end - start) / msPerDay) + 1;

          const maxBars = 36;

          // If the backend already gave us daily labels and there aren't many,
          // keep the day granularity even if timezone math nudges spanDays upward.
          if (labels && labels.length && labels.length <= 45) return 'day';

          // Day: one label per day
          if (spanDays <= 31) return 'day';

          // Week: one label per ISO week (approx 7 days)
          const spanWeeks = Math.ceil(spanDays / 7);
          if (spanWeeks <= maxBars) return 'week';

          // Month: compute month diff inclusive
          const spanMonths = (end.getFullYear() - start.getFullYear()) * 12 + (end.getMonth() - start.getMonth()) + 1;
          if (spanMonths <= maxBars) return 'month';

          // Otherwise fall back to years
          return 'year';
        }

        function getWeekStartIso(d) {
          const date = parseIsoDateLocal(d);
          // ISO week start: Monday
          const day = date.getDay(); // 0 Sun .. 6 Sat
          const diff = (day + 6) % 7; // days since Monday
          const wk = new Date(date);
          wk.setDate(date.getDate() - diff);
          wk.setHours(0, 0, 0, 0);
          return wk.toISOString().slice(0, 10);
        }

        function getMonthIso(d) {
          const date = parseIsoDateLocal(d);
          const year = date.getFullYear();
          const month = String(date.getMonth() + 1).padStart(2, "0");
          return `${year}-${month}`; // YYYY-MM
        }

        function getYearIso(d) {
          const date = parseIsoDateLocal(d);
          return String(date.getFullYear());
        }

        function parseIsoDateLocal(iso) {
          const parts = iso.split("-");
          const y = Number(parts[0]);
          const m = Number(parts[1]);
          const d = Number(parts[2] || 1);
          return new Date(y, m - 1, d); // Local time, avoids TZ shifting backward
        }

        function formatBucketLabel(bucket, key, startIso, endIso) {
          const nowYear = new Date().getFullYear();
          let startYear = null;
          let endYear = null;
          try {
            if (startIso) startYear = new Date(startIso).getFullYear();
            if (endIso) endYear = new Date(endIso).getFullYear();
          } catch (e) {
            // ignore
          }

          if (bucket === 'day') {
            const d = parseIsoDateLocal(key);
            const opts = { month: 'short', day: 'numeric' };
            // include year if span crosses years or not current year
            if (startYear && endYear && startYear !== endYear) {
              opts.year = 'numeric';
            } else if (d.getFullYear() !== nowYear) {
              opts.year = 'numeric';
            }
            return d.toLocaleDateString(document.documentElement.lang || undefined, opts);
          }

          if (bucket === 'week') {
            // key is ISO date for week start (YYYY-MM-DD)
            const d = parseIsoDateLocal(key);
            const opts = { month: 'short', day: 'numeric' };
            if (startYear && endYear && startYear !== endYear) {
              opts.year = 'numeric';
            } else if (d.getFullYear() !== nowYear) {
              opts.year = 'numeric';
            }
            // Show a short date for the week (no "Week of" prefix)
            return d.toLocaleDateString(document.documentElement.lang || undefined, opts);
          }

          if (bucket === 'month') {
            // key is YYYY-MM
            const [yy, mm] = key.split('-');
            const date = new Date(Number(yy), Number(mm) - 1, 1);
            // If the selected range is within the current year, show full month name only
            if (startYear && endYear && startYear === endYear && startYear === nowYear) {
              return date.toLocaleDateString(document.documentElement.lang || undefined, { month: 'long' });
            }
            // Otherwise show abbreviated month + year
            return date.toLocaleDateString(document.documentElement.lang || undefined, { month: 'short', year: 'numeric' });
          }

          if (bucket === 'year') {
            return String(key);
          }

          return key;
        }

        function aggregateDailyToBucket(dailyData, bucket) {
          const labels = dailyData.labels || [];

          // If we're using daily buckets, format the labels but keep the data as-is
          if (bucket === 'day') {
            const newLabels = labels.map((k) => formatBucketLabel('day', k, startIso, endIso));
            const newDatasets = dailyData.datasets.map((ds) => ({
              label: ds.label,
              media_type: ds.media_type,
              data: ds.data.map((v) => Number(v) || 0),
              background_color: ds.background_color || ds.backgroundColor || ds.backgroundColor,
            }));

            return { labels: newLabels, datasets: newDatasets };
          }

          const bucketMap = new Map();

          labels.forEach((lbl, idx) => {
            let key;
            if (bucket === 'week') key = getWeekStartIso(lbl);
            else if (bucket === 'month') key = getMonthIso(lbl);
            else if (bucket === 'year') key = getYearIso(lbl);
            else key = lbl;

            if (!bucketMap.has(key)) {
              bucketMap.set(key, Array(dailyData.datasets.length).fill(0));
            }

            dailyData.datasets.forEach((ds, dsIndex) => {
              const value = Number(ds.data[idx]) || 0;
              const arr = bucketMap.get(key);
              arr[dsIndex] = +(arr[dsIndex] + value).toFixed(4);
            });
          });

          const rawKeys = Array.from(bucketMap.keys()).sort();
          const newLabels = rawKeys.map((k) => formatBucketLabel(bucket, k, startIso, endIso));
          const newDatasets = dailyData.datasets.map((ds, i) => ({
            label: ds.label,
            media_type: ds.media_type,
            data: rawKeys.map((k) => bucketMap.get(k)[i] || 0),
            background_color: ds.background_color || ds.backgroundColor || ds.backgroundColor,
          }));

          return { labels: newLabels, datasets: newDatasets };
        }

        const bucket = chooseBucket(startIso, endIso, dailyData.labels);
        const aggregated = aggregateDailyToBucket(dailyData, bucket);
        const dailyOptions = JSON.parse(JSON.stringify(barChartConfig));
        dailyOptions.scales.x.stacked = true;
        dailyOptions.scales.y.stacked = true;
        // Remove x-axis title for the copy chart (we use the page heading instead)
        if (dailyOptions.scales && dailyOptions.scales.x) {
          dailyOptions.scales.x.title = { display: false };
        }
        dailyOptions.scales.y.title = {
          display: true,
          text: gettext("Hours"),
          color: "#D1D5DB",
          padding: { top: 0, left: 10 },
        };
        // Don't add an in-chart title for the copy chart; the page heading above
        // already displays "Played Hours by Media Type" in larger type.
        dailyOptions.plugins.tooltip = {
          enabled: false,
          mode: "index",
          intersect: false,
          external: customBarTooltip,
        };

        const dailyChart = initializeChartIfExists(
          "scoreStackedChartCopy",
          "bar",
          processBarData(aggregated),
          dailyOptions
        );

        if (dailyChart && typeof dailyChart.resize === "function") {
          dailyChart.resize();
        }
      }
    } else {
      // Fallback: initialize copy using score distribution data (legacy behavior)
      // Use the score chart options as a base but override title for the copy
      const fallbackOptions = JSON.parse(JSON.stringify(scoreChartOptions));
      fallbackOptions.plugins = fallbackOptions.plugins || {};
      // Don't set an in-chart title for the fallback; the page heading is used

      const scoreCopyChart = initializeChartIfExists(
        "scoreStackedChartCopy",
        "bar",
        processBarData(scoreData),
        fallbackOptions
      );

      if (scoreCopyChart && typeof scoreCopyChart.resize === "function") {
        scoreCopyChart.resize();
      }
    }
  }

  initializeSingleSeriesBarChart(
    "tvEpisodesByYearChart",
    "tv_episodes_by_year"
  );

  initializeSingleSeriesBarChart(
    "animeEpisodesByYearChart",
    "anime_episodes_by_year"
  );

  initializeSingleSeriesBarChart(
    "moviePlaysByYearChart",
    "movie_plays_by_year"
  );
  initializeSingleSeriesBarChart(
    "bookFinishedByYearChart",
    "book_finished_by_year"
  );
  initializeSingleSeriesBarChart(
    "bookReleasedByYearChart",
    "book_released_by_year"
  );
  initializeSingleSeriesBarChart(
    "bookCompletedLengthChart",
    "book_completed_length"
  );
  initializeSingleSeriesBarChart(
    "comicFinishedByYearChart",
    "comic_finished_by_year"
  );
  initializeSingleSeriesBarChart(
    "comicReleasedByYearChart",
    "comic_released_by_year"
  );
  initializeSingleSeriesBarChart(
    "comicCompletedLengthChart",
    "comic_completed_length"
  );
  initializeSingleSeriesBarChart(
    "mangaFinishedByYearChart",
    "manga_finished_by_year"
  );
  initializeSingleSeriesBarChart(
    "mangaReleasedByYearChart",
    "manga_released_by_year"
  );
  initializeSingleSeriesBarChart(
    "mangaCompletedLengthChart",
    "manga_completed_length"
  );
  initializeSingleSeriesBarChart(
    "gameHoursByYearChart",
    "game_hours_by_year"
  );
  initializeSingleSeriesBarChart(
    "gameHoursByMonthChart",
    "game_hours_by_month"
  );
  // Custom initialization for gameDailyAverageChart with band-level game tooltip
  (function initGameDailyAverageChart() {
    const dataEl = byId("game_daily_average");
    if (!dataEl) return;
    const rawData = JSON.parse(dataEl.textContent || "null");
    if (!rawData || !rawData.labels || rawData.labels.length === 0) return;

    const topGamesByBand = rawData.top_games_per_band || {};

    function gameDailyAverageTooltip(context) {
      let tooltipEl = document.getElementById("chartjs-tooltip");
      if (!tooltipEl) {
        tooltipEl = document.createElement("div");
        tooltipEl.id = "chartjs-tooltip";
        document.body.appendChild(tooltipEl);
      }

      const tooltipModel = context.tooltip;
      if (tooltipModel.opacity === 0) {
        tooltipEl.style.opacity = 0;
        return;
      }

      if (tooltipModel.body) {
        const bandLabel = tooltipModel.title[0] || "";
        const bandGames = topGamesByBand[bandLabel] || [];

        let html = '<div style="font-weight:600;margin-bottom:6px;color:' + CHART_TOOLTIP_TEXT + '">' + chartEscapeHtml(interpolate(gettext("Avg/day: %(band)s"), { band: bandLabel }, true)) + "</div>";
        bandGames.forEach(function (game, idx) {
          html +=
            '<div style="display:flex;justify-content:space-between;align-items:center;gap:12px;margin-top:4px">' +
            '<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:160px">' +
            (idx + 1) +
            ". " +
            chartEscapeHtml(game.title || gettext("Unknown")) +
            "</span>" +
            '<span style="font-weight:600;white-space:nowrap">' +
            (game.formatted_daily_average || "") +
            "</span>" +
            "</div>";
        });

        tooltipEl.innerHTML = html;
      }

      placeChartTooltip(tooltipEl, context);
    }

    const chartOptions = JSON.parse(JSON.stringify(barChartConfig));
    chartOptions.scales.x.stacked = false;
    chartOptions.scales.y.stacked = false;
    if (chartOptions.plugins && chartOptions.plugins.legend) {
      chartOptions.plugins.legend.display = false;
    }
    chartOptions.plugins.tooltip = {
      enabled: false,
      mode: "index",
      intersect: false,
      external: gameDailyAverageTooltip,
    };

    initializeChartIfExists(
      "gameDailyAverageChart",
      "bar",
      processBarData(rawData),
      chartOptions
    );
  })();

  // Music consumption charts
  initializeSingleSeriesBarChart(
    "musicPlaysByYearChart",
    "music_plays_by_year"
  );

  // Podcast charts
  initializeSingleSeriesBarChart(
    "podcastPlaysByYearChart",
    "podcast_plays_by_year"
  );

  // Video charts
  initializeSingleSeriesBarChart(
    "videoPlaysByYearChart",
    "video_plays_by_year"
  );

  // The selected media types from the URL, in dropdown order. Empty = "All media".
  function getCurrentMediaTypes() {
    try {
      return StatsMediaMerge.parseMediaTypeParam(
        new URL(window.location.href).searchParams.get("media-type"),
        Object.keys(MEDIA_SLUG_TO_LABEL),
      );
    } catch (_) {
      return [];
    }
  }

  // Maps the page's media-type filter slug to the readable label used as a
  // chart "type" key throughout status_distribution / score_distribution /
  // media_type_distribution payloads (e.g. "tv" -> "TV Show").
  const MEDIA_SLUG_TO_LABEL = {
    tv: "TV Show", movie: "Movie", anime: "Anime", music: "Music",
    podcast: "Podcast", video: "Video", book: "Book", comic: "Comic",
    boardgame: "Board Game", game: "Game", manga: "Manga",
  };

  // ─── Activity History heatmap ──────────────────────────────────────────────
  // The server renders every day for all media types; cells with activity carry
  // their per-type counts so the heatmap can follow the media-type filter.
  const HEATMAP_LEVEL_CLASSES = ["bg-[var(--color-surface-muted)]", "bg-indigo-700", "bg-indigo-600", "bg-indigo-500", "bg-indigo-400"];

  // Same thresholds as stats_activity.get_level.
  function heatmapLevel(count) {
    if (count <= 0) return 0;
    if (count <= 3) return 1;
    if (count <= 6) return 2;
    if (count <= 9) return 3;
    return 4;
  }

  function updateActivityHeatmap() {
    const heatmap = byId("activityHistory");
    const cells = heatmap ? heatmap.querySelectorAll("[data-heatmap-counts]") : [];
    if (!cells.length) return;
    const types = getCurrentMediaTypes();
    cells.forEach(function (cell) {
      let count = 0;
      cell.dataset.heatmapCounts.split(",").forEach(function (pair) {
        const [type, n] = pair.split(":");
        if (types.length === 0 || types.includes(type)) count += Number(n) || 0;
      });
      cell.classList.remove(...HEATMAP_LEVEL_CLASSES);
      cell.classList.add(HEATMAP_LEVEL_CLASSES[heatmapLevel(count)]);
    });
  }

  // Cells carry no per-cell date or title (all-time history is thousands of
  // weeks); the tooltip is built on hover from the cell's position in the grid.
  // The server rendered the translated tooltip for 0, 1 and 2 (the locale's
  // plural rule decides which wording each uses); swap in the real count first
  // (the template has no other digits), then the date.
  const heatmapRoot = byId("activityHistory");
  const heatmapGrid = heatmapRoot ? heatmapRoot.querySelector("[data-heatmap-grid]") : null;
  if (heatmapGrid) {
    const start = new Date(heatmapGrid.dataset.heatmapStart + "T00:00:00Z");
    heatmapGrid.addEventListener("mouseover", function (event) {
      const cell = event.target;
      if (!(cell instanceof HTMLElement) || !cell.classList.contains("hm")) return;
      const column = cell.parentElement;
      const offset = Array.prototype.indexOf.call(heatmapGrid.children, column) * 7 + Array.prototype.indexOf.call(column.children, cell);
      const date = new Date(start.getTime() + offset * 86400000).toISOString().slice(0, 10);
      if (cell.classList.contains("hm-f")) {
        cell.title = date;
        return;
      }
      const types = getCurrentMediaTypes();
      let count = 0;
      (cell.dataset.heatmapCounts || "").split(",").forEach(function (pair) {
        const [type, n] = pair.split(":");
        if (type && (types.length === 0 || types.includes(type))) count += Number(n) || 0;
      });
      const marker = count > 2 ? "2" : String(count);
      const heatmap = byId("activityHistory");
      cell.title = heatmap.getAttribute("data-title-" + marker).replace(marker, String(count)).replace("__DATE__", date);
    });
  }

  updateActivityHeatmap();
  window.addEventListener("stats-media-type-changed", updateActivityHeatmap);

  // ─── Activity Rhythm SVG dot matrix ────────────────────────────────────────
  const weekdayHourEl = byId("weekday_hour_chart_data");
  const rhythmContainer = byId("activityRhythmContainer");
  if (weekdayHourEl && rhythmContainer) {
    const rhythmData = JSON.parse(weekdayHourEl.textContent || "{}");

    const DAY_LABELS = Array.from({ length: 7 }, (_, day) =>
      new Date(2024, 0, day + 1).toLocaleDateString(document.documentElement.lang || undefined, { weekday: "short" }));
    const cellSize = 12;
    const cellGap = 4;
    const dotArea = cellSize + cellGap;
    const maxR = cellSize / 2;
    const labelW = 30;
    const labelH = 14;
    const totalW = labelW + 24 * dotArea;
    const totalH = labelH + 7 * dotArea;

    // Number of intensity tiers (tier 0 = empty cell, tiers 1..TIERS = non-zero).
    const TIERS = 6;

    // Tier → dot radius. Empty cells get the smallest dot; non-zero tiers ramp up to maxR.
    function tierRadius(tier) {
      return tier === 0 ? 1.5 : 2 + (tier / TIERS) * (maxR - 2);
    }

    // Tier → fill. Empty cells faint grey; non-zero tiers ramp indigo opacity up.
    function tierFill(tier) {
      if (tier === 0) return "rgba(255,255,255,0.05)";
      const opacity = 0.2 + 0.8 * (tier / TIERS);
      return `rgba(99,102,241,${opacity.toFixed(2)})`;
    }

    function drawRhythmChart(mediaTypes) {
      const matrix = StatsMediaMerge.sumMatrices(rhythmData, mediaTypes);
      if (!matrix) {
        rhythmContainer.innerHTML =
          '<p class="text-sm text-[var(--color-text-muted)] text-center py-6">' + chartEscapeHtml(gettext("No activity data for this range.")) + '</p>';
        return;
      }

      // Quantile bucketing: rank each cell by its percentile within the non-zero values.
      // This is robust to bulk-import outliers (a single huge day can't crush the scale).
      const nonZeroVals = [];
      for (let r = 0; r < 7; r++) {
        for (let c = 0; c < 24; c++) {
          const v = (matrix[r] && matrix[r][c]) || 0;
          if (v > 0) nonZeroVals.push(v);
        }
      }
      nonZeroVals.sort(function (a, b) { return a - b; });

      function tierFor(v) {
        if (v <= 0 || nonZeroVals.length === 0) return 0;
        // index of first value >= v → percentile rank
        let lo = 0;
        let hi = nonZeroVals.length;
        while (lo < hi) {
          const mid = (lo + hi) >> 1;
          if (nonZeroVals[mid] < v) lo = mid + 1;
          else hi = mid;
        }
        const pct = lo / nonZeroVals.length;
        return Math.min(TIERS, Math.floor(pct * TIERS) + 1);
      }

      let cells = "";
      for (let r = 0; r < 7; r++) {
        const cy = labelH + r * dotArea + cellSize / 2;
        cells +=
          `<text x="${labelW - 4}" y="${cy + 3.5}" text-anchor="end" ` +
          `font-size="9" fill="#6b7280">${DAY_LABELS[r]}</text>`;
        for (let c = 0; c < 24; c++) {
          const cx = labelW + c * dotArea + cellSize / 2;
          const count = (matrix[r] && matrix[r][c]) || 0;
          const tier = tierFor(count);
          const radius = tierRadius(tier);
          const fill = tierFill(tier);
          const title = count > 0
            ? `<title>${chartEscapeHtml(interpolate(ngettext("%(day)s %(hour)s:00 \u2014 %(count)s session", "%(day)s %(hour)s:00 \u2014 %(count)s sessions", count), { day: DAY_LABELS[r], hour: c, count }, true))}</title>`
            : "";
          cells += `<circle cx="${cx}" cy="${cy}" r="${radius.toFixed(1)}" fill="${fill}">${title}</circle>`;
        }
      }

      let hourLabels = "";
      for (const h of [0, 6, 12, 18]) {
        const cx = labelW + h * dotArea + cellSize / 2;
        const lbl = new Date(2024, 0, 1, h).toLocaleTimeString(document.documentElement.lang || undefined, { hour: "numeric" });
        hourLabels +=
          `<text x="${cx}" y="${labelH - 2}" text-anchor="middle" ` +
          `font-size="9" fill="#6b7280">${lbl}</text>`;
      }

      // "Low → High" legend rendered into the header container (top-right).
      const legendEl = byId("activityRhythmLegend");
      if (legendEl) {
        legendEl.innerHTML =
          '<span style="font-size:9px;color:#6b7280">' + chartEscapeHtml(gettext("Low")) + '</span>';
        for (let t = 1; t <= TIERS; t++) {
          const r = tierRadius(t);
          const size = (r * 2).toFixed(1) + "px";
          const dot = document.createElement("span");
          dot.style.cssText =
            `display:inline-block;width:${size};height:${size};border-radius:50%;` +
            `background:${tierFill(t)};flex-shrink:0;`;
          legendEl.appendChild(dot);
        }
        legendEl.insertAdjacentHTML(
          "beforeend",
          '<span style="font-size:9px;color:#6b7280">' + chartEscapeHtml(gettext("High")) + '</span>'
        );
      }

      rhythmContainer.innerHTML =
        `<svg width="100%" viewBox="0 0 ${totalW} ${totalH}" ` +
        `xmlns="http://www.w3.org/2000/svg" style="overflow:visible;display:block">` +
        hourLabels + cells + `</svg>`;
    }

    drawRhythmChart(getCurrentMediaTypes());
    window.addEventListener("stats-media-type-changed", function () {
      drawRhythmChart(getCurrentMediaTypes());
    });
  }

  // ─── Combined Plays charts (by_month / by_weekday / by_time_of_day) ────────
  const combinedPlaysEl = byId("combined_plays_charts");
  if (combinedPlaysEl) {
    let combinedPlaysData = null;
    try {
      combinedPlaysData = JSON.parse(combinedPlaysEl.textContent || "null");
    } catch (_) {
      combinedPlaysData = null;
    }

    const COMBINED_CHART_SPECS = [
      {
        key: "by_month",
        canvasId: "combinedPlaysByMonthChart",
        containerId: "combinedPlaysByMonthContainer",
      },
      {
        key: "by_weekday",
        canvasId: "combinedPlaysByWeekdayChart",
        containerId: "combinedPlaysByWeekdayContainer",
      },
      {
        key: "by_time_of_day",
        canvasId: "combinedPlaysByTimeChart",
        containerId: "combinedPlaysByTimeContainer",
      },
    ];
    const combinedChartInstances = {};

    const COMBINED_PLAYS_MEDIA_TYPES = ["movie", "tv", "anime", "music", "podcast"];
    const COMBINED_PLAYS_TYPE_LABELS = {
      movie: "Movies",
      tv: "TV Shows",
      anime: "Anime",
      music: "Music",
      podcast: "Podcasts",
    };

    function combinedPlaysTooltip(spec) {
      return function (context) {
        let tooltipEl = document.getElementById("combinedPlaysTooltip");
        if (!tooltipEl) {
          tooltipEl = document.createElement("div");
          tooltipEl.id = "combinedPlaysTooltip";
          tooltipEl.style.cssText =
            "position:absolute;z-index:100;pointer-events:none;opacity:0;transition:opacity 0.2s ease;" +
            "background:" + CHART_TOOLTIP_BG + ";border:1px solid " + CHART_TOOLTIP_BORDER + ";border-radius:6px;" +
            "padding:10px 12px;font-size:13px;color:" + CHART_TOOLTIP_TEXT + ";min-width:160px;" +
            "box-shadow:0 4px 12px rgba(0,0,0,0.4);";
          document.body.appendChild(tooltipEl);
        }

        const tooltipModel = context.tooltip;
        if (tooltipModel.opacity === 0) {
          tooltipEl.style.opacity = 0;
          return;
        }

        if (tooltipModel.body) {
          const dataIndex = tooltipModel.dataPoints[0].dataIndex;
          const title = tooltipModel.title[0] || "";
          const byKey = (combinedPlaysData && combinedPlaysData[spec.key]) || {};
          const currentMediaType = getCurrentMediaTypes();
          const relevantTypes =
            currentMediaType === "all" ? COMBINED_PLAYS_MEDIA_TYPES : [currentMediaType];

          const rows = relevantTypes
            .map(function (type) {
              const typeChart = byKey[type];
              const ds = typeChart && typeChart.datasets && typeChart.datasets[0];
              const value = ds ? Number(ds.data[dataIndex]) || 0 : 0;
              const color = ds ? ds.background_color : "#9ca3af";
              return {
                label: chartDisplayLabel(COMBINED_PLAYS_TYPE_LABELS[type] || type),
                value: value,
                color: color,
              };
            })
            .filter(function (row) {
              return row.value > 0;
            })
            .sort(function (a, b) {
              return b.value - a.value;
            });

          function fmtHoursValue(hrs) {
            return interpolate(gettext("%(count)sh"), { count: (Number(hrs) || 0).toLocaleString(document.documentElement.lang || undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 }) }, true);
          }

          let html = '<div style="font-weight:600;margin-bottom:6px;color:' + CHART_TOOLTIP_TEXT + '">' + title + "</div>";
          rows.forEach(function (row) {
            html +=
              '<div style="display:flex;align-items:center;gap:6px;margin-top:4px">' +
              '<span style="width:10px;height:10px;border-radius:2px;background:' +
              row.color +
              ';flex-shrink:0"></span>' +
              "<span>" + chartEscapeHtml(row.label) + ": " + fmtHoursValue(row.value) + "</span>" +
              "</div>";
          });

          tooltipEl.innerHTML = html;
        }

        placeChartTooltip(tooltipEl, context);
      };
    }

    function drawCombinedChart(spec, mediaTypes) {
      const container = byId(spec.containerId);
      if (!container) return;
      const byKey = (combinedPlaysData && combinedPlaysData[spec.key]) || {};
      const chartData = StatsMediaMerge.mergeSeriesChart(byKey, mediaTypes);

      if (!chartData || !chartData.labels || chartData.labels.length === 0) {
        if (combinedChartInstances[spec.key]) {
          combinedChartInstances[spec.key].destroy();
          combinedChartInstances[spec.key] = null;
        }
        if (container) {
          container.innerHTML =
            '<p class="text-sm text-[var(--color-text-muted)] text-center py-8 w-full">' + chartEscapeHtml(gettext("No data for this filter.")) + '</p>';
        }
        return;
      }

      if (!container.querySelector("canvas")) {
        container.innerHTML = `<canvas id="${spec.canvasId}"></canvas>`;
      }

      const chartOptions = JSON.parse(JSON.stringify(barChartConfig));
      chartOptions.scales.x.stacked = false;
      chartOptions.scales.y.stacked = false;
      chartOptions.scales.x.grid = { display: false };
      chartOptions.scales.y.border = { display: false };
      if (chartOptions.plugins && chartOptions.plugins.legend) {
        chartOptions.plugins.legend.display = false;
      }
      chartOptions.plugins.tooltip = {
        enabled: false,
        mode: "index",
        intersect: false,
        external: combinedPlaysTooltip(spec),
      };
      const processed = processBarData(chartData);

      if (combinedChartInstances[spec.key]) {
        combinedChartInstances[spec.key].data = processed;
        combinedChartInstances[spec.key].update();
      } else {
        const chart = new Chart(
          byId(spec.canvasId).getContext("2d"),
          { type: "bar", data: processed, options: chartOptions }
        );
        window.__floppyStatsCharts.push(chart);
        combinedChartInstances[spec.key] = chart;
      }
    }

    function drawAllCombinedCharts(mediaTypes) {
      COMBINED_CHART_SPECS.forEach(function (spec) {
        drawCombinedChart(spec, mediaTypes);
      });
    }

    if (combinedPlaysData && typeof combinedPlaysData === "object") {
      drawAllCombinedCharts(getCurrentMediaTypes());
      window.addEventListener("stats-media-type-changed", function () {
        drawAllCombinedCharts(getCurrentMediaTypes());
      });
    }
  }

  // ─── Time Across Your Worlds doughnut ───────────────────────────────────────
  const timeWorldsCanvas = byId("timeWorldsChart");
  const timeWorldsLegendEl = byId("timeWorldsLegend");
  const timeWorldsCenterEl = byId("timeWorldsCenter");
  const timeWorldsInfoEl = byId("timeWorldsInfo");
  const distEl = byId("media_type_distribution");

  if (timeWorldsCanvas && distEl) {
    const fullDistData = JSON.parse(distEl.textContent || "{}");

    // User's Duration Format preference (mirrors stats_utils._format_hours_minutes).
    let durationFormat = "hours_minutes";
    try {
      const dfEl = byId("stats_duration_format");
      if (dfEl) durationFormat = JSON.parse(dfEl.textContent) || "hours_minutes";
    } catch (_) { /* keep default */ }

    // Format a duration given in hours, respecting the Duration Format preference.
    // maxParts caps how many units are shown (default 2); pass Infinity for tooltips.
    function fmtHours(hrs, maxParts) {
      if (maxParts === undefined) maxParts = 2;
      let minutes = Math.round(hrs * 60);
      if (minutes <= 0) return interpolate(gettext("%(hours)sh %(minutes)smin"), { hours: 0, minutes: 0 }, true);
      if (durationFormat === "long_units") {
        if (minutes < 60) return interpolate(gettext("%(count)smin"), { count: minutes }, true);
        if (minutes < 1440) {
          return interpolate(gettext("%(hours)sh %(minutes)smin"), { hours: Math.floor(minutes / 60), minutes: minutes % 60 }, true);
        }
        const MONTH = 43800;
        const DAY = 1440;
        const HOUR = 60;
        const mo = Math.floor(minutes / MONTH);
        let rem = minutes % MONTH;
        const d = Math.floor(rem / DAY);
        rem %= DAY;
        const h = Math.floor(rem / HOUR);
        const m = rem % HOUR;
        const parts = [];
        if (mo) parts.push(interpolate(gettext("%(count)smo"), { count: mo }, true));
        if (d) parts.push(interpolate(gettext("%(count)sd"), { count: d }, true));
        if (h) parts.push(interpolate(gettext("%(count)sh"), { count: h }, true));
        if (m || !parts.length) parts.push(interpolate(gettext("%(count)smin"), { count: m }, true));
        return parts.slice(0, maxParts).join(" ");
      }
      return interpolate(gettext("%(hours)sh %(minutes)smin"), { hours: Math.floor(minutes / 60), minutes: minutes % 60 }, true);
    }

    // Palette for genre slices (cycles if there are more genres than colors).
    const GENRE_PALETTE = [
      "#6366f1", "#ec4899", "#10b981", "#f59e0b", "#3b82f6",
      "#8b5cf6", "#ef4444", "#14b8a6", "#f97316", "#a855f7",
      "#06b6d4", "#84cc16",
    ];

    // Genre data keyed by media type slug (minutes-based types only).
    function loadGenreData(slug) {
      try {
        const el = byId(slug + "_top_genres");
        // An empty list is emitted as the string "[]" (template default), so check the type.
        const genres = el ? JSON.parse(el.textContent || "[]") : [];
        return Array.isArray(genres) ? genres : [];
      } catch (_) { return []; }
    }

    // Actual (non-overlapping) total hours for a media type, mirroring the
    // fallback used by date-range.js's currentTypeSummary getter. Genre
    // buckets attribute full hours to every genre a title has, so they can't
    // be summed for a "total" — this reads the real total instead.
    function loadTypeTotalHours(slug) {
      try {
        const el = byId("summary_stats_by_type");
        const all = el ? JSON.parse(el.textContent || "{}") : {};
        const s = all[slug] || all.all || {};
        return (s.total_minutes || 0) / 60;
      } catch (_) { return 0; }
    }
    const GENRE_TYPES = { tv: 1, movie: 1, anime: 1, music: 1, game: 1 };

    // Elements for updating the card header.
    const timeWorldsTitleEl = byId("timeWorldsTitle");
    const timeWorldsSubtitleEl = byId("timeWorldsSubtitle");

    let donutChartInstance = null;

    // External HTML tooltip — lives in <body> so it's never clipped by the canvas.
    function getOrCreateTooltipEl() {
      let el = document.getElementById("timeWorldsTooltip");
      if (!el) {
        el = document.createElement("div");
        el.id = "timeWorldsTooltip";
        el.style.cssText =
          "position:fixed;z-index:9999;pointer-events:none;opacity:0;transition:opacity 0.1s;" +
          "background:" + CHART_TOOLTIP_BG + ";border:1px solid " + CHART_TOOLTIP_BORDER + ";border-radius:6px;" +
          "padding:8px 10px;font-size:12px;color:" + CHART_TOOLTIP_TEXT + ";white-space:nowrap;box-shadow:0 4px 12px rgba(0,0,0,0.4);";
        document.body.appendChild(el);
      }
      return el;
    }

    function externalTooltipHandler(context, getTotalHours) {
      const tooltipEl = getOrCreateTooltipEl();
      const tooltip = context.tooltip;

      if (tooltip.opacity === 0) {
        tooltipEl.style.opacity = "0";
        return;
      }

      if (tooltip.dataPoints && tooltip.dataPoints.length) {
        const dp = tooltip.dataPoints[0];
        const label = chartDisplayLabel(dp.label || "");
        const hrs = dp.raw;
        const total = getTotalHours();
        const pct = total > 0 ? Math.round((hrs / total) * 100) : 0;
        const color = dp.dataset.backgroundColor[dp.dataIndex];

        tooltipEl.innerHTML =
          '<div style="font-weight:600;margin-bottom:4px;color:' + CHART_TOOLTIP_TEXT + '">' + chartEscapeHtml(label) + "</div>" +
          '<div style="display:flex;align-items:center;gap:6px">' +
            '<span style="width:10px;height:10px;border-radius:2px;background:' + color + ';flex-shrink:0"></span>' +
            '<span>' + fmtHours(hrs, Infinity) + " (" + pct + "%)</span>" +
          "</div>";
      }

      const rect = timeWorldsCanvas.getBoundingClientRect();
      const x = rect.left + tooltip.caretX;
      const y = rect.top + tooltip.caretY;

      // Flip left if near right edge of viewport.
      const tipW = tooltipEl.offsetWidth || 160;
      const left = Math.max(8, x + 12 + tipW > window.innerWidth ? x - tipW - 12 : x + 12);

      tooltipEl.style.left = left + "px";
      tooltipEl.style.top = (y - 16) + "px";
      tooltipEl.style.opacity = "1";
    }

    function renderDonut(labels, data, colors, getTotalHours) {
      const externalTooltip = function (ctx) { externalTooltipHandler(ctx, getTotalHours); };

      if (donutChartInstance) {
        donutChartInstance.data.labels = labels;
        donutChartInstance.data.datasets[0].data = data;
        donutChartInstance.data.datasets[0].backgroundColor = colors;
        donutChartInstance.options.plugins.tooltip.external = externalTooltip;
        donutChartInstance.update();
      } else {
        donutChartInstance = new Chart(timeWorldsCanvas.getContext("2d"), {
          type: "doughnut",
          data: { labels: labels, datasets: [{ data: data, backgroundColor: colors }] },
          options: {
            responsive: true,
            maintainAspectRatio: true,
            cutout: "68%",
            plugins: {
              legend: { display: false },
              datalabels: { display: false },
              tooltip: { enabled: false, external: externalTooltip },
            },
            elements: { arc: { borderWidth: 1, borderColor: "rgba(0,0,0,0.15)" } },
          },
        });
        window.__floppyStatsCharts.push(donutChartInstance);
      }
    }

    function renderLegend(labels, data, colors, totalHours) {
      if (!timeWorldsLegendEl) return;
      timeWorldsLegendEl.innerHTML = "";
      const sortedIndices = labels.map(function (_, i) { return i; })
        .sort(function (a, b) { return data[b] - data[a]; });
      sortedIndices.forEach(function (i) {
        const label = chartDisplayLabel(labels[i]);
        const hrs = data[i];
        const color = colors[i];
        const pct = totalHours > 0 ? Math.round((hrs / totalHours) * 100) : 0;
        const row = document.createElement("div");
        row.style.cssText = "display:flex;align-items:center;gap:14px;font-size:11px;line-height:1.3;";
        row.innerHTML =
          '<div style="flex-shrink:0;display:flex;align-items:center;gap:6px;width:74px">' +
            '<span style="flex-shrink:0;width:10px;height:10px;border-radius:2px;background:' + color + '"></span>' +
            '<span style="flex:1;color:#d1d5db;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + chartEscapeHtml(label) + "</span>" +
          '</div>' +
          '<div style="flex:1;height:5px;border-radius:3px;background:rgba(255,255,255,0.07);overflow:hidden;min-width:40px">' +
            '<div style="height:100%;border-radius:3px;background:' + color + ';width:' + pct + '%"></div>' +
          '</div>' +
          '<span style="flex-shrink:0;width:34px;text-align:center;color:#d1d5db;font-weight:600">' + pct + "%</span>" +
          '<span style="flex-shrink:0;min-width:80px;text-align:center;color:#6b7280;font-variant-numeric:tabular-nums;white-space:nowrap">' + fmtHours(hrs) + "</span>";
        timeWorldsLegendEl.appendChild(row);
      });
    }

    function drawTimeWorldsChart(mediaTypes) {
      const container = byId("timeWorldsContainer");
      const isFiltered = mediaTypes.length > 0;
      // Genres are per type and overlap between types, so they only show for one type.
      const mediaType = mediaTypes.length === 1 ? mediaTypes[0] : null;
      const hasGenres = mediaType && GENRE_TYPES[mediaType];
      const genres = hasGenres ? loadGenreData(mediaType) : [];

      if (isFiltered && hasGenres && genres.length > 0) {
        // ── Genre mode ──────────────────────────────────────────────────────
        const labels = genres.map(function (g) { return g.name; });
        const data = genres.map(function (g) { return +(g.minutes / 60).toFixed(2); });
        const colors = genres.map(function (_, i) { return GENRE_PALETTE[i % GENRE_PALETTE.length]; });
        const totalHours = loadTypeTotalHours(mediaType);

        if (timeWorldsTitleEl) timeWorldsTitleEl.textContent = gettext("Top Genres");
        if (timeWorldsSubtitleEl) timeWorldsSubtitleEl.textContent = interpolate(gettext("Where your %(type)s hours go."), { type: chartDisplayLabel(MEDIA_SLUG_TO_LABEL[mediaType] || mediaType) }, true);
        if (timeWorldsInfoEl) timeWorldsInfoEl.classList.remove("hidden");

        if (timeWorldsCenterEl) {
          timeWorldsCenterEl.innerHTML =
            '<span style="font-size:1rem;font-weight:700;color:' + CHART_TOOLTIP_TEXT + ';line-height:1.2;text-align:center">' +
            fmtHours(totalHours) + "</span>" +
            '<span style="font-size:0.65rem;color:#9ca3af;line-height:1.2">' + chartEscapeHtml(gettext("total")) + '</span>';
        }

        renderDonut(labels, data, colors, function () { return totalHours; });
        renderLegend(labels, data, colors, totalHours);
        return;
      }

      // ── Type distribution mode (all, or filtered type with no genre data) ─
      if (timeWorldsTitleEl) timeWorldsTitleEl.textContent = gettext("Hours by Media Type");
      if (timeWorldsSubtitleEl) timeWorldsSubtitleEl.textContent = gettext("Where your hours go.");
      if (timeWorldsInfoEl) timeWorldsInfoEl.classList.add("hidden");

      // Build the filtered view of the distribution data.
      let labels, data, colors;
      if (!isFiltered || !fullDistData.labels) {
        labels = fullDistData.labels || [];
        const ds = (fullDistData.datasets || [{}])[0] || {};
        data = ds.data || [];
        colors = ds.backgroundColor || [];
      } else {
        // Keep only the selected types' slices (also used for one type with no genre breakdown).
        const ds = fullDistData.datasets[0];
        labels = []; data = []; colors = [];
        mediaTypes.forEach(function (type) {
          const idx = MEDIA_SLUG_TO_LABEL[type] ? fullDistData.labels.indexOf(MEDIA_SLUG_TO_LABEL[type]) : -1;
          if (idx >= 0) {
            labels.push(fullDistData.labels[idx]);
            data.push(ds.data[idx]);
            colors.push(ds.backgroundColor[idx]);
          }
        });
      }

      if (!labels.length) {
        if (donutChartInstance) { donutChartInstance.destroy(); donutChartInstance = null; }
        if (container) {
          container.innerHTML =
            '<p class="text-sm text-[var(--color-text-muted)] text-center py-8 w-full">' + chartEscapeHtml(gettext("No time data available for this filter.")) + '</p>';
        }
        return;
      }

      const totalHours = data.reduce(function (a, b) { return a + b; }, 0);

      if (timeWorldsCenterEl) {
        timeWorldsCenterEl.innerHTML =
          '<span style="font-size:1rem;font-weight:700;color:' + CHART_TOOLTIP_TEXT + ';line-height:1.2;text-align:center">' +
          fmtHours(totalHours) + "</span>" +
          '<span style="font-size:0.65rem;color:#9ca3af;line-height:1.2">' + chartEscapeHtml(gettext("total")) + '</span>';
      }

      renderDonut(labels, data, colors, function () { return totalHours; });
      renderLegend(labels, data, colors, totalHours);
    }

    if (fullDistData.labels && fullDistData.labels.length > 0) {
      drawTimeWorldsChart(getCurrentMediaTypes());
      window.addEventListener("stats-media-type-changed", function () {
        drawTimeWorldsChart(getCurrentMediaTypes());
      });
    } else {
      const container = byId("timeWorldsContainer");
      if (container) {
        container.innerHTML =
          '<p class="text-sm text-[var(--color-text-muted)] text-center py-8 w-full">' + chartEscapeHtml(gettext("No time data available for this range.")) + '</p>';
      }
    }
  }

  // ─── Status Composition donut (status_distribution, aggregated or single-type) ──
  const statusCompositionDataEl = byId("status_distribution");
  if (statusCompositionDataEl) {
    const statusCompositionData = JSON.parse(statusCompositionDataEl.textContent || "{}");
    let statusCompositionChartInstance = null;

    function getOrCreateStatusCompositionTooltipEl() {
      let el = document.getElementById("statusCompositionTooltip");
      if (!el) {
        el = document.createElement("div");
        el.id = "statusCompositionTooltip";
        el.style.cssText =
          "position:fixed;z-index:9999;pointer-events:none;opacity:0;transition:opacity 0.1s;" +
          "background:" + CHART_TOOLTIP_BG + ";border:1px solid " + CHART_TOOLTIP_BORDER + ";border-radius:6px;" +
          "padding:8px 10px;font-size:12px;color:" + CHART_TOOLTIP_TEXT + ";white-space:nowrap;box-shadow:0 4px 12px rgba(0,0,0,0.4);";
        document.body.appendChild(el);
      }
      return el;
    }

    function statusCompositionTooltipHandler(context, canvas, getTotal) {
      const tooltipEl = getOrCreateStatusCompositionTooltipEl();
      const tooltip = context.tooltip;
      if (tooltip.opacity === 0) {
        tooltipEl.style.opacity = "0";
        return;
      }
      if (tooltip.dataPoints && tooltip.dataPoints.length) {
        const dp = tooltip.dataPoints[0];
        const label = chartDisplayLabel(dp.label || "");
        const count = dp.raw;
        const total = getTotal();
        const pct = total > 0 ? Math.round((count / total) * 100) : 0;
        const color = dp.dataset.backgroundColor[dp.dataIndex];
        tooltipEl.innerHTML =
          '<div style="font-weight:600;margin-bottom:4px;color:' + CHART_TOOLTIP_TEXT + '">' + chartEscapeHtml(label) + "</div>" +
          '<div style="display:flex;align-items:center;gap:6px">' +
            '<span style="width:10px;height:10px;border-radius:2px;background:' + color + ';flex-shrink:0"></span>' +
            "<span>" + count.toLocaleString(document.documentElement.lang || undefined) + " (" + pct + "%)</span>" +
          "</div>";
      }
      const rect = canvas.getBoundingClientRect();
      const x = rect.left + tooltip.caretX;
      const y = rect.top + tooltip.caretY;
      const tipW = tooltipEl.offsetWidth || 160;
      const left = Math.max(8, x + 12 + tipW > window.innerWidth ? x - tipW - 12 : x + 12);
      tooltipEl.style.left = left + "px";
      tooltipEl.style.top = (y - 16) + "px";
      tooltipEl.style.opacity = "1";
    }

    function renderStatusComposition(canvas, legendEl, centerEl, labels, data, colors) {
      const total = data.reduce(function (a, b) { return a + b; }, 0);
      const externalTooltip = function (ctx) {
        statusCompositionTooltipHandler(ctx, canvas, function () { return total; });
      };

      if (centerEl) {
        centerEl.innerHTML =
          '<span style="font-size:1.1rem;font-weight:700;color:' + CHART_TOOLTIP_TEXT + ';line-height:1.2">' +
          total.toLocaleString(document.documentElement.lang || undefined) + "</span>" +
          '<span style="font-size:0.65rem;color:#9ca3af;line-height:1.2">' + chartEscapeHtml(gettext("total items")) + '</span>';
      }

      if (statusCompositionChartInstance) {
        statusCompositionChartInstance.data.labels = labels;
        statusCompositionChartInstance.data.datasets[0].data = data;
        statusCompositionChartInstance.data.datasets[0].backgroundColor = colors;
        statusCompositionChartInstance.options.plugins.tooltip.external = externalTooltip;
        statusCompositionChartInstance.update();
      } else {
        statusCompositionChartInstance = new Chart(canvas.getContext("2d"), {
          type: "doughnut",
          data: { labels: labels, datasets: [{ data: data, backgroundColor: colors }] },
          options: {
            responsive: true,
            maintainAspectRatio: true,
            cutout: "68%",
            plugins: {
              legend: { display: false },
              datalabels: { display: false },
              tooltip: { enabled: false, external: externalTooltip },
            },
            elements: { arc: { borderWidth: 1, borderColor: "rgba(0,0,0,0.15)" } },
          },
        });
        window.__floppyStatsCharts.push(statusCompositionChartInstance);
      }

      if (legendEl) {
        legendEl.innerHTML = "";
        const sortedIndices = labels.map(function (_, i) { return i; })
          .sort(function (a, b) { return data[b] - data[a]; });
        sortedIndices.forEach(function (i) {
          const label = chartDisplayLabel(labels[i]);
          const count = data[i];
          const color = colors[i];
          const pct = total > 0 ? Math.round((count / total) * 100) : 0;
          const row = document.createElement("div");
          row.style.cssText = "display:flex;align-items:center;gap:10px;font-size:11px;min-width:250px;";
          row.innerHTML =
            '<div style="flex-shrink:0;display:flex;align-items:center;gap:6px;min-width:0;width:84px">' +
              '<span style="flex-shrink:0;width:10px;height:10px;border-radius:2px;background:' + color + '"></span>' +
              '<span style="min-width:0;color:#d1d5db;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + chartEscapeHtml(label) + "</span>" +
            "</div>" +
            '<div style="flex:1;height:5px;border-radius:3px;background:rgba(255,255,255,0.07);overflow:hidden;min-width:40px">' +
              '<div style="height:100%;border-radius:3px;background:' + color + ';width:' + pct + '%"></div>' +
            "</div>" +
            '<span style="flex-shrink:0;width:34px;text-align:center;color:' + CHART_TOOLTIP_TEXT + ';font-weight:700">' + pct + "%</span>" +
            '<span style="flex-shrink:0;min-width:60px;text-align:center;color:#6b7280;font-variant-numeric:tabular-nums">' + count.toLocaleString(document.documentElement.lang || undefined) + "</span>";
          legendEl.appendChild(row);
        });
      }
    }

    function drawStatusComposition(mediaTypes) {
      const container = byId("statusCompositionContainer");
      const subtitleEl = byId("statusCompositionSubtitle");
      const isFiltered = mediaTypes.length > 0;
      const statusLabels = (statusCompositionData.datasets || []).map(function (ds) { return ds.label; });
      const statusColors = (statusCompositionData.datasets || []).map(function (ds) { return ds.background_color; });

      let labels = [];
      let data = [];
      let colors = [];
      let targetLabel = null;

      if (!isFiltered) {
        (statusCompositionData.datasets || []).forEach(function (ds, i) {
          const total = (ds.data || []).reduce(function (a, b) { return a + (Number(b) || 0); }, 0);
          if (total > 0) {
            labels.push(statusLabels[i]);
            data.push(total);
            colors.push(statusColors[i]);
          }
        });
      } else {
        // Sum each status over the selected types' columns.
        if (mediaTypes.length === 1) targetLabel = MEDIA_SLUG_TO_LABEL[mediaTypes[0]];
        const idxs = mediaTypes
          .map(function (type) { return (statusCompositionData.labels || []).indexOf(MEDIA_SLUG_TO_LABEL[type]); })
          .filter(function (idx) { return idx >= 0; });
        (statusCompositionData.datasets || []).forEach(function (ds, i) {
          const value = idxs.reduce(function (sum, idx) { return sum + (Number((ds.data || [])[idx]) || 0); }, 0);
          if (value > 0) {
            labels.push(statusLabels[i]);
            data.push(value);
            colors.push(statusColors[i]);
          }
        });
      }

      if (subtitleEl) {
        subtitleEl.textContent = isFiltered && targetLabel
          ? interpolate(gettext("%(type)s status breakdown."), { type: chartDisplayLabel(targetLabel) }, true)
          : gettext("What portion of your library is in each state.");
      }

      if (!labels.length) {
        if (statusCompositionChartInstance) { statusCompositionChartInstance.destroy(); statusCompositionChartInstance = null; }
        if (container) {
          container.innerHTML =
            '<p class="text-sm text-[var(--color-text-muted)] text-center py-8 w-full">' + chartEscapeHtml(gettext("No status data available for this filter.")) + '</p>';
        }
        return;
      }

      if (container && !container.querySelector("canvas")) {
        container.innerHTML =
          '<div class="relative shrink-0" style="width:150px;height:150px;">' +
            '<div id="statusCompositionCenter" class="absolute inset-0 flex flex-col items-center justify-center pointer-events-none text-center"></div>' +
            '<canvas id="statusCompositionChart" style="position:relative;z-index:1"></canvas>' +
          "</div>" +
          '<div id="statusCompositionLegend" class="flex-1 min-w-0 overflow-x-auto overflow-y-hidden scrollbar-hidden space-y-1.5"></div>';
        statusCompositionChartInstance = null;
      }

      const canvas = byId("statusCompositionChart");
      const legendEl = byId("statusCompositionLegend");
      const centerEl = byId("statusCompositionCenter");
      if (canvas) {
        renderStatusComposition(canvas, legendEl, centerEl, labels, data, colors);
      }
    }

    if (statusCompositionData.labels && statusCompositionData.labels.length > 0) {
      drawStatusComposition(getCurrentMediaTypes());
      window.addEventListener("stats-media-type-changed", function () {
        drawStatusComposition(getCurrentMediaTypes());
      });
    } else {
      const container = byId("statusCompositionContainer");
      if (container) {
        container.innerHTML =
          '<p class="text-sm text-[var(--color-text-muted)] text-center py-8 w-full">' + chartEscapeHtml(gettext("No status data available for this range.")) + '</p>';
      }
    }
  }

  // ─── Rating Distribution (score_distribution, aggregated or single-type) ──
  const ratingDistributionDataEl = byId("score_distribution");
  if (ratingDistributionDataEl) {
    const ratingDistributionData = JSON.parse(ratingDistributionDataEl.textContent || "{}");
    let ratingDistributionChartInstance = null;

    function buildRatingDistribution(mediaTypes) {
      const isFiltered = mediaTypes.length > 0;
      const labels = ratingDistributionData.labels || [];
      const datasets = ratingDistributionData.datasets || [];
      const data = labels.map(function () { return 0; });
      let totalScored = 0;

      function addDataset(ds) {
        (ds.data || []).forEach(function (v, i) {
          const value = Number(v) || 0;
          data[i] += value;
          totalScored += value;
        });
      }

      let targetLabel = null;
      if (!isFiltered) {
        datasets.forEach(addDataset);
      } else {
        if (mediaTypes.length === 1) targetLabel = MEDIA_SLUG_TO_LABEL[mediaTypes[0]];
        const wanted = mediaTypes.map(function (type) { return MEDIA_SLUG_TO_LABEL[type]; });
        datasets.filter(function (ds) { return wanted.indexOf(ds.label) >= 0; }).forEach(addDataset);
      }

      return { labels: labels, data: data, totalScored: totalScored, targetLabel: targetLabel };
    }

    function drawRatingDistribution(mediaTypes) {
      const container = byId("ratingDistributionContainer");
      const subtitleEl = byId("ratingDistributionSubtitle");
      const built = buildRatingDistribution(mediaTypes);

      if (subtitleEl) {
        const summary = built.targetLabel
          ? ngettext("How your %(count)s rated item (%(type)s) is distributed.", "How your %(count)s rated items (%(type)s) are distributed.", built.totalScored)
          : ngettext("How your %(count)s rated item is distributed.", "How your %(count)s rated items are distributed.", built.totalScored);
        subtitleEl.textContent = interpolate(summary, {
          count: built.totalScored.toLocaleString(document.documentElement.lang || undefined),
          type: chartDisplayLabel(built.targetLabel || ""),
        }, true);
      }

      if (!built.totalScored) {
        if (ratingDistributionChartInstance) { ratingDistributionChartInstance.destroy(); ratingDistributionChartInstance = null; }
        if (container) {
          container.innerHTML =
            '<p class="text-sm text-[var(--color-text-muted)] text-center py-8 w-full">' + chartEscapeHtml(gettext("No ratings yet for this filter.")) + '</p>';
        }
        return;
      }

      if (container && !container.querySelector("canvas")) {
        container.innerHTML = '<canvas id="ratingDistributionChart"></canvas>';
        ratingDistributionChartInstance = null;
      }

      const canvas = byId("ratingDistributionChart");
      if (!canvas) return;

      const chartOptions = JSON.parse(JSON.stringify(barChartConfig));
      chartOptions.scales.x.stacked = false;
      chartOptions.scales.y.stacked = false;
      chartOptions.scales.x.grid = { display: false };
      chartOptions.scales.y.border = { display: false };
      // Headroom so the top-of-bar count/percentage datalabel never clips
      // against the tallest bar. grace is a percentage of the data range, so
      // it shrinks in pixel terms as the container gets shorter -- pair it
      // with fixed pixel padding that reserves room for the two-line label
      // regardless of container height.
      chartOptions.scales.y.grace = "15%";
      chartOptions.layout = { padding: { top: 26 } };
      if (chartOptions.plugins && chartOptions.plugins.legend) {
        chartOptions.plugins.legend.display = false;
      }
      chartOptions.plugins.datalabels = {
        display: true,
        anchor: "end",
        align: "end",
        color: "#9ca3af",
        font: { size: 10 },
        formatter: function (value) {
          if (!value) return "";
          const pct = built.totalScored > 0 ? ((value / built.totalScored) * 100).toFixed(1) : "0.0";
          return value.toLocaleString(document.documentElement.lang || undefined) + "\n(" + pct + "%)";
        },
      };
      chartOptions.plugins.tooltip = {
        enabled: false,
        mode: "index",
        intersect: false,
        external: function (context) {
          let tooltipEl = document.getElementById("ratingDistributionTooltip");
          if (!tooltipEl) {
            tooltipEl = document.createElement("div");
            tooltipEl.id = "ratingDistributionTooltip";
            tooltipEl.style.cssText =
              "position:absolute;z-index:100;pointer-events:none;opacity:0;transition:opacity 0.2s ease;" +
              "background:" + CHART_TOOLTIP_BG + ";border:1px solid " + CHART_TOOLTIP_BORDER + ";border-radius:6px;" +
              "padding:8px 10px;font-size:12px;color:" + CHART_TOOLTIP_TEXT + ";box-shadow:0 4px 12px rgba(0,0,0,0.4);";
            document.body.appendChild(tooltipEl);
          }
          const tooltipModel = context.tooltip;
          if (tooltipModel.opacity === 0) {
            tooltipEl.style.opacity = 0;
            return;
          }
          if (tooltipModel.body) {
            const dataIndex = tooltipModel.dataPoints[0].dataIndex;
            const value = built.data[dataIndex] || 0;
            tooltipEl.innerHTML =
              '<div style="font-weight:600;color:' + CHART_TOOLTIP_TEXT + '">' + chartEscapeHtml(interpolate(gettext("Rating %(rating)s"), { rating: built.labels[dataIndex] }, true)) + "</div>" +
              '<div style="margin-top:4px">' + chartEscapeHtml(interpolate(ngettext("%(count)s item", "%(count)s items", value), { count: value.toLocaleString(document.documentElement.lang || undefined) }, true)) + "</div>";
          }
          placeChartTooltip(tooltipEl, context);
        },
      };

      const chartData = {
        labels: built.labels,
        datasets: [{
          label: gettext("Items"),
          data: built.data,
          backgroundColor: "#6366f1",
          borderColor: "rgba(255, 255, 255, 0.1)",
          borderRadius: 6,
          borderWidth: 1,
        }],
      };

      if (ratingDistributionChartInstance) {
        ratingDistributionChartInstance.data = chartData;
        ratingDistributionChartInstance.options = chartOptions;
        ratingDistributionChartInstance.update();
      } else {
        ratingDistributionChartInstance = new Chart(canvas.getContext("2d"), {
          type: "bar",
          data: chartData,
          options: chartOptions,
        });
        window.__floppyStatsCharts.push(ratingDistributionChartInstance);
      }
    }

    if (ratingDistributionData.labels && ratingDistributionData.labels.length > 0) {
      drawRatingDistribution(getCurrentMediaTypes());
      window.addEventListener("stats-media-type-changed", function () {
        drawRatingDistribution(getCurrentMediaTypes());
      });
    } else {
      const container = byId("ratingDistributionContainer");
      if (container) {
        container.innerHTML =
          '<p class="text-sm text-[var(--color-text-muted)] text-center py-8 w-full">' + chartEscapeHtml(gettext("No ratings yet.")) + '</p>';
      }
    }
  }

  // ─── Status Breakdown (status_distribution, table by media type) ──────────
  const statusBreakdownDataEl = byId("status_distribution");
  if (statusBreakdownDataEl) {
    const statusBreakdownData = JSON.parse(statusBreakdownDataEl.textContent || "{}");

    // Reverse of MEDIA_SLUG_TO_LABEL (readable label -> slug) so a row's
    // display name can be matched back to its icon/color in MEDIA_TYPE_VISUALS.
    const LABEL_TO_MEDIA_SLUG = {};
    Object.keys(MEDIA_SLUG_TO_LABEL).forEach(function (slug) {
      LABEL_TO_MEDIA_SLUG[MEDIA_SLUG_TO_LABEL[slug]] = slug;
    });

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, function (ch) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
      });
    }

    function drawStatusBreakdown(mediaTypes) {
      const container = byId("statusBreakdownContainer");
      const isFiltered = mediaTypes.length > 0;
      const targetLabel = mediaTypes.length === 1 ? MEDIA_SLUG_TO_LABEL[mediaTypes[0]] : null;
      const selectedLabels = mediaTypes.map(function (type) { return MEDIA_SLUG_TO_LABEL[type]; });
      const subtitleText = targetLabel
        ? interpolate(gettext("%(type)s status counts."), { type: chartDisplayLabel(targetLabel) }, true)
        : gettext("Breakdown by type across all status states.");

      const typeLabels = statusBreakdownData.labels || [];
      let rowIndices = [];
      typeLabels.forEach(function (label, i) {
        if (!isFiltered || selectedLabels.indexOf(label) >= 0) rowIndices.push(i);
      });

      // Only show status columns that actually have items, mirroring the
      // donut/rating-distribution "hide empty series" convention.
      const statusColumns = (statusBreakdownData.datasets || []).filter(function (ds) {
        return (ds.total || (ds.data || []).reduce(function (a, b) { return a + (Number(b) || 0); }, 0)) > 0;
      });

      if (!rowIndices.length || !statusColumns.length) {
        if (container) {
          container.innerHTML =
            '<p class="text-xs text-gray-400 mb-3">' + escapeHtml(subtitleText) + "</p>" +
            '<p class="text-sm text-[var(--color-text-muted)] text-center py-8 w-full">' + chartEscapeHtml(gettext("No status data available for this filter.")) + '</p>';
        }
        return;
      }

      const totalFor = function (idx) {
        return statusColumns.reduce(function (sum, ds) { return sum + (Number((ds.data || [])[idx]) || 0); }, 0);
      };
      rowIndices = rowIndices.slice().sort(function (a, b) { return totalFor(b) - totalFor(a); });

      const colWidth = function (ds) { return ds.label === "Completed" ? 116 : 84; };

      let html = '<div class="statistics-card-scroll" style="overflow-x:auto;">';
      html += '<div style="min-width:' + (170 + statusColumns.reduce(function (sum, ds) { return sum + colWidth(ds); }, 0) + 56) + 'px;">';

      // Subtitle + column-header row share a single line.
      html += '<div class="flex items-center gap-3 pb-2 mb-1 border-b border-gray-700">';
      html +=
        '<p class="text-xs text-gray-400 flex-1 min-w-0" style="min-width:110px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" ' +
        'id="statusBreakdownSubtitle" title="' + escapeHtml(subtitleText) + '">' + escapeHtml(subtitleText) + "</p>";
      html += '<div class="shrink-0 text-right text-xs font-medium text-gray-400" style="width:56px">' + chartEscapeHtml(gettext("Total")) + '</div>';
      statusColumns.forEach(function (ds) {
        html +=
          '<div class="shrink-0 text-right text-xs font-medium" style="width:' + colWidth(ds) + 'px;color:' + ds.background_color + '">' +
          escapeHtml(chartDisplayLabel(ds.label)) + "</div>";
      });
      html += "</div>";

      // Data rows.
      rowIndices.forEach(function (idx) {
        const typeLabel = typeLabels[idx];
        const slug = LABEL_TO_MEDIA_SLUG[typeLabel];
        const visuals = MEDIA_TYPE_VISUALS[slug] || {};
        const total = totalFor(idx);

        html += '<div class="flex items-center gap-3 py-2 border-b border-gray-700/50 last:border-0">';
        html +=
          '<div class="flex items-center gap-2 flex-1 min-w-0" style="min-width:110px">' +
          '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" ' +
          'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" ' +
          'class="w-4 h-4 shrink-0" style="color:' + (visuals.color || "#9ca3af") + '">' + (visuals.icon || "") + "</svg>" +
          '<span class="text-sm text-gray-300 truncate">' + escapeHtml(chartDisplayLabel(typeLabel)) + "</span>" +
          "</div>";
        html +=
          '<div class="shrink-0 text-sm font-semibold text-white text-right" style="width:56px">' +
          total.toLocaleString(document.documentElement.lang || undefined) + "</div>";

        statusColumns.forEach(function (ds) {
          const value = Number((ds.data || [])[idx]) || 0;
          if (ds.label === "Completed") {
            const pct = total > 0 ? Math.max(0, Math.min(100, (value / total) * 100)) : 0;
            html +=
              '<div class="shrink-0 flex items-center gap-2" style="width:' + colWidth(ds) + 'px">' +
              '<div class="rounded-full overflow-hidden shrink-0" style="width:34px;height:4px;background:rgba(255,255,255,0.1)">' +
              '<div style="height:100%;width:' + pct + '%;background:' + ds.background_color + '"></div>' +
              "</div>" +
              '<span class="text-sm text-right flex-1" style="color:' + ds.background_color + '">' + value.toLocaleString(document.documentElement.lang || undefined) + "</span>" +
              "</div>";
          } else {
            html +=
              '<div class="shrink-0 text-sm text-right" style="width:' + colWidth(ds) + 'px;color:' + ds.background_color + '">' +
              value.toLocaleString(document.documentElement.lang || undefined) + "</div>";
          }
        });

        html += "</div>";
      });

      html += "</div></div>";

      if (container) container.innerHTML = html;
    }

    if (statusBreakdownData.labels && statusBreakdownData.labels.length > 0) {
      drawStatusBreakdown(getCurrentMediaTypes());
      window.addEventListener("stats-media-type-changed", function () {
        drawStatusBreakdown(getCurrentMediaTypes());
      });
    } else {
      const container = byId("statusBreakdownContainer");
      if (container) {
        container.innerHTML =
          '<p class="text-sm text-[var(--color-text-muted)] text-center py-8 w-full">' + chartEscapeHtml(gettext("No status data available for this range.")) + '</p>';
      }
    }
  }

  // ─── Featured Repeat Player "X% of titles" ring ──────────────────────────
  // The ring sits outside the islands; the fallback pass leaves it alone.
  if (page) (function initFeaturedPersonRing() {
    let featuredPersonRingChart = null;
    let featuredPersonRingCanvas = null;

    function readFeaturedPersonPct() {
      const el = document.getElementById("featured_person_pct");
      if (!el) return 0;
      try {
        return Number(JSON.parse(el.textContent || "0")) || 0;
      } catch (_) {
        return 0;
      }
    }

    window.renderFeaturedPersonRing = function (pct) {
      const canvas = document.getElementById("featuredPersonRingChart");
      const centerEl = document.getElementById("featuredPersonRingCenter");
      if (!canvas) {
        if (featuredPersonRingChart) {
          featuredPersonRingChart.destroy();
        }
        featuredPersonRingChart = null;
        featuredPersonRingCanvas = null;
        return;
      }
      const clamped = Math.max(0, Math.min(100, Number(pct) || 0));
      const data = [clamped, Math.max(0, 100 - clamped)];
      const colors = ["#6366f1", "rgba(255,255,255,0.07)"];

      if (centerEl) {
        centerEl.innerHTML =
          '<span style="font-size:1rem;font-weight:700;color:' + CHART_TOOLTIP_TEXT + ';line-height:1.2">' +
          clamped.toFixed(1).replace(/\.0$/, "") + "%</span>" +
          '<span style="font-size:0.6rem;color:#9ca3af;line-height:1.2">' + chartEscapeHtml(gettext("of titles")) + '</span>';
      }

      if (featuredPersonRingChart && featuredPersonRingCanvas !== canvas) {
        featuredPersonRingChart.destroy();
        featuredPersonRingChart = null;
        featuredPersonRingCanvas = null;
      }

      if (featuredPersonRingChart) {
        featuredPersonRingChart.data.datasets[0].data = data;
        featuredPersonRingChart.data.datasets[0].backgroundColor = colors;
        featuredPersonRingChart.update();
        return;
      }

      featuredPersonRingChart = new Chart(canvas.getContext("2d"), {
        type: "doughnut",
        data: { labels: ["Featured", "Other"], datasets: [{ data: data, backgroundColor: colors }] },
        options: {
          responsive: true,
          maintainAspectRatio: true,
          cutout: "68%",
          plugins: {
            legend: { display: false },
            datalabels: { display: false },
            tooltip: { enabled: false },
          },
          elements: { arc: { borderWidth: 1, borderColor: "rgba(0,0,0,0.15)" } },
        },
      });
      featuredPersonRingCanvas = canvas;
      window.__floppyStatsCharts.push(featuredPersonRingChart);
    };

    if (document.getElementById("featuredPersonRingChart")) {
      window.renderFeaturedPersonRing(readFeaturedPersonPct());
    }
  })();

  window.dispatchEvent(new CustomEvent("stats-charts-initialized"));

  // Initial sizing and on resize for the copied score chart wrapper
  matchScoreCopyHeight();
  // Re-bind through a window-level reference so the resize listener is only
  // attached once but always uses the current page's sizing function.
  window.__floppyStatsFit = matchScoreCopyHeight;
  if (!window.__floppyStatsResizeBound) {
    window.__floppyStatsResizeBound = true;
    window.addEventListener("resize", function () {
      // Debounce-ish
      clearTimeout(window._scoreCopyResizeTimer);
      window._scoreCopyResizeTimer = setTimeout(function () {
        if (window.__floppyStatsFit) {
          window.__floppyStatsFit();
        }
      }, 120);
    });
  }
}

function startStatisticsCharts() {
  initStatisticsCharts("page");
  const hosts = Array.from(document.querySelectorAll(STATISTICS_ISLANDS));
  if (!hosts.length) return;
  // Same delay as island-reveal.css. A host that was swapped away (boosted
  // navigation) is not connected any more and is ignored.
  setTimeout(function () {
    const unmounted = hosts.some(function (host) {
      return host.isConnected && !host.hasAttribute("data-react-mounted");
    });
    if (unmounted) initStatisticsCharts("fallback");
  }, 4000);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", startStatisticsCharts, { once: true });
} else {
  // Script was injected after DOM load (e.g. boosted navigation swap).
  startStatisticsCharts();
}
})();
