document.addEventListener("DOMContentLoaded", function () {
  Chart.register(ChartDataLabels);

  // Custom external tooltip for bar charts
  function customBarTooltip(context) {
    // External custom tooltip
    let tooltipEl = document.getElementById("chartjs-tooltip");

    // Create element if it doesn't exist
    if (!tooltipEl) {
      tooltipEl = document.createElement("div");
      tooltipEl.id = "chartjs-tooltip";
      tooltipEl.innerHTML = "<table></table>";
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
        if (score === 10) {
          formattedTitle = `${gettext("Score")}: 10`;
        } else {
          formattedTitle = `${gettext("Score")}: ${score}.0-${score}.9`;
        }
      }

      // Get all values for this stack
      let tableBody =
        '<thead><tr><th colspan="2">' +
        formattedTitle +
        "</th></tr></thead><tbody>";
      let stackTotal = 0;

      chart.data.datasets.forEach((dataset, i) => {
        if (dataset.data[dataIndex] && dataset.data[dataIndex] > 0) {
          const value = dataset.data[dataIndex];
          stackTotal += value;
          const bgColor = dataset.backgroundColor;
          const label = dataset.label || "";

          tableBody +=
            "<tr>" +
            '<td style="padding-right:15px;"><span style="display:inline-block;width:12px;height:12px;background:' +
            bgColor +
            ';margin-right:8px;border-radius:2px;"></span>' +
            label +
            ":</td>" +
            '<td style="text-align:right;font-weight:bold;">' +
            value +
            "</td>" +
            "</tr>";
        }
      });

      // Add total row
      tableBody +=
        '<tr class="total-row">' +
        `<td>${gettext("Total")}: </td>` +
        '<td style="text-align:right;font-weight:bold;">' +
        stackTotal +
        "</td>" +
        "</tr>";

      tableBody += "</tbody>";

      const tableRoot = tooltipEl.querySelector("table");
      tableRoot.innerHTML = tableBody;
    }

    // Position and style the tooltip
    const position = context.chart.canvas.getBoundingClientRect();

    // Set tooltip styles
    tooltipEl.style.opacity = 1;
    tooltipEl.style.position = "absolute";
    tooltipEl.style.left =
      position.left + window.scrollX + tooltipModel.caretX + "px";
    tooltipEl.style.top =
      position.top + window.scrollY + tooltipModel.caretY + "px";
    tooltipEl.style.transform = "translate(-50%, -100%)";
    tooltipEl.style.pointerEvents = "none";
  }

  // Custom external tooltip for pie charts
  function customPieTooltip(context) {
    // External custom tooltip
    let tooltipEl = document.getElementById("chartjs-pie-tooltip");

    // Create element if it doesn't exist
    if (!tooltipEl) {
      tooltipEl = document.createElement("div");
      tooltipEl.id = "chartjs-pie-tooltip";
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
      const dataPoint = tooltipModel.dataPoints[0];
      const label = dataPoint.label;
      const value = dataPoint.raw;

      // Calculate percentage
      const dataset = context.chart.data.datasets[dataPoint.datasetIndex];
      const total = dataset.data.reduce((sum, val) => sum + val, 0);
      const percentage = Math.round((value / total) * 100);

      // Create tooltip content
      let tooltipContent = `
        <div class="pie-label">${label}</div>
        <div class="pie-value">${gettext("Count")}: ${value}</div>
        <div class="pie-percent">${percentage}%</div>
      `;

      tooltipEl.innerHTML = tooltipContent;
    }

    // Position and style the tooltip
    const position = context.chart.canvas.getBoundingClientRect();

    // Set tooltip styles
    tooltipEl.style.opacity = 1;
    tooltipEl.style.position = "absolute";
    tooltipEl.style.left =
      position.left + window.scrollX + tooltipModel.caretX + "px";
    tooltipEl.style.top =
      position.top + window.scrollY + tooltipModel.caretY + "px";
    tooltipEl.style.transform = "translate(-50%, -100%)";
    tooltipEl.style.pointerEvents = "none";
  }

  // Common configuration for pie charts
  const pieChartConfig = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      datalabels: {
        color: "#D1D5DB",
        font: { size: 12 },
        formatter: (value, ctx) => {
          const total = ctx.dataset.data.reduce((acc, data) => acc + data, 0);
          const percentage = Math.round((value / total) * 100);
          const label = ctx.chart.data.labels[ctx.dataIndex];
          return percentage > 5 ? `${label}\n${percentage}%` : "";
        },
        textAlign: "center",
        textStrokeColor: "rgba(0,0,0,0.5)",
        textStrokeWidth: 2,
        textShadowBlur: 5,
        textShadowColor: "rgba(0,0,0,0.5)",
        padding: 6,
      },
      legend: {
        position: "bottom",
        labels: {
          color: "#D1D5DB",
          padding: 20,
          usePointStyle: true,
          pointStyle: "rectRounded",
          generateLabels: function (chart) {
            const original =
              Chart.overrides.pie.plugins.legend.labels.generateLabels;
            const labels = original.call(this, chart);
            labels.forEach((label, i) => {
              label.text = `${label.text} (${chart.data.datasets[0].data[i]})`;
              label.strokeStyle = "transparent";
            });
            return labels;
          },
        },
        margin: { top: 20 },
      },
      tooltip: {
        enabled: false,
        external: customPieTooltip,
      },
    },
    layout: { padding: { bottom: 10 } },
    elements: {
      arc: {
        borderWidth: 1,
        borderColor: "#d3d3d3",
      },
    },
  };

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
      labels: chartData.labels,
      datasets: chartData.datasets
        .map((dataset) => ({
          label: dataset.label,
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
    const element = document.getElementById(elementId);
    if (element) {
      return new Chart(element.getContext("2d"), {
        type: chartType,
        data: data,
        options: options,
      });
    }
    return null;
  }

  // Create Media Type Distribution Chart
  const mediaTypeDistributionElement = document.getElementById(
    "media_type_distribution"
  );
  if (mediaTypeDistributionElement) {
    const mediaTypeData = JSON.parse(mediaTypeDistributionElement.textContent);
    initializeChartIfExists(
      "mediaTypeChart",
      "pie",
      mediaTypeData,
      pieChartConfig
    );
  }

  // Create Status Distribution Chart
  const statusPieChartElement = document.getElementById(
    "status_pie_chart_data"
  );
  if (statusPieChartElement) {
    const statusPieData = JSON.parse(statusPieChartElement.textContent);
    initializeChartIfExists(
      "statusChart",
      "pie",
      statusPieData,
      pieChartConfig
    );
  }

  // Create Status Stacked Bar Chart
  const statusDistributionElement = document.getElementById(
    "status_distribution"
  );
  if (statusDistributionElement) {
    const statusData = JSON.parse(statusDistributionElement.textContent);
    initializeChartIfExists(
      "statusStackedChart",
      "bar",
      processBarData(statusData),
      barChartConfig
    );
  }

  // Create Score Stacked Bar Chart
  const scoreDistributionElement =
    document.getElementById("score_distribution");
  if (scoreDistributionElement) {
    const scoreData = JSON.parse(scoreDistributionElement.textContent);
    const scoreChartOptions = JSON.parse(JSON.stringify(barChartConfig)); // Deep clone

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

    const itemsText = tn("item", "items", scoreData.total_scored);
    scoreChartOptions.plugins.title = {
      display: true,
      text: `${gettext("Average Score")}: ${scoreData.average_score} (${scoreData.total_scored} ${itemsText})`,
      color: "#D1D5DB",
      padding: { bottom: 10 },
      font: { size: 14 },
    };

    // Ensure tooltip is properly configured for score chart
    scoreChartOptions.plugins.tooltip = {
      enabled: false,
      mode: "index",
      intersect: false,
      external: customBarTooltip,
    };

    initializeChartIfExists(
      "scoreStackedChart",
      "bar",
      processBarData(scoreData),
      scoreChartOptions
    );
  }
});
