(() => {
  const { body } = document;
  const enabled = body.dataset.autoExportEnabled === "true";
  const intervalValue = Number(body.dataset.autoExportIntervalValue);
  const intervalUnit = body.dataset.autoExportIntervalUnit;
  const exportUrl = body.dataset.autoExportUrl;
  const userId = body.dataset.autoExportUser;

  const reminderStorageKey = `yamtrack:auto-export:active-reminder:${userId}`;
  const lastPromptStorageKey = `yamtrack:auto-export:last-prompt:${userId}`;

  if (!enabled) {
    localStorage.removeItem(reminderStorageKey);
    return;
  }

  const unitMilliseconds = {
    days: 24 * 60 * 60 * 1000,
    hours: 60 * 60 * 1000,
    minutes: 60 * 1000,
    seconds: 1000,
  };
  const intervalMilliseconds = intervalValue * unitMilliseconds[intervalUnit];

  if (!Number.isFinite(intervalMilliseconds) || intervalMilliseconds < 1 || !exportUrl) {
    return;
  }

  const isDue = () => {
    const lastPrompt = Number(localStorage.getItem(lastPromptStorageKey));
    return !Number.isFinite(lastPrompt) || Date.now() - lastPrompt >= intervalMilliseconds;
  };

  const clearReminder = () => {
    localStorage.removeItem(reminderStorageKey);
    document.querySelector("#auto-export-reminder")?.remove();
  };

  document.querySelector("[data-export-csv]")?.addEventListener("submit", clearReminder);

  const showToast = () => {
    const messages = document.querySelector("#messages-list");
    if (!messages) {
      return;
    }

    if (document.querySelector("#auto-export-reminder")) {
      return;
    }

    const toast = document.createElement("div");
    toast.id = "auto-export-reminder";
    toast.className = "flex items-center gap-2 rounded-md border px-3 py-2 shadow-lg text-white toast-info transition-all duration-300 ease-out transform translate-y-[-1rem] opacity-0";
    toast.innerHTML = `<p class="text-sm font-medium flex-1">It is time to back up your Yamtrack data.</p>
      <a class="text-sm font-medium text-indigo-300 hover:text-indigo-200 underline" href="${exportUrl}">Export now</a>
      <button class="text-current opacity-70 hover:opacity-100 transition-opacity cursor-pointer" type="button" aria-label="Dismiss export reminder">×</button>`;
    toast.querySelector("a").addEventListener("click", clearReminder);
    toast.querySelector("button").addEventListener("click", () => {
      clearReminder();
    });
    messages.append(toast);
    window.setTimeout(() => toast.classList.remove("translate-y-[-1rem]", "opacity-0"), 10);
  };

  const showReminder = () => {
    if (localStorage.getItem(reminderStorageKey) || !isDue()) {
      return;
    }

    localStorage.setItem(lastPromptStorageKey, String(Date.now()));
    localStorage.setItem(reminderStorageKey, "true");
    showToast();
  };

  if (localStorage.getItem(reminderStorageKey)) {
    showToast();
  } else {
    showReminder();
  }
  window.setInterval(showReminder, Math.min(intervalMilliseconds, 60 * 60 * 1000));
})();
