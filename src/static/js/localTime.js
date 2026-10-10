// Localizes the UTC instants the server renders.
//
// The server never picks a wall clock: it emits `<time datetime="...Z"
// data-yt="datetime">` and this file rewrites the text using the browser's own
// timezone. That is the only place a timezone is chosen for display, so it
// cannot disagree with anything else -- and it follows the device, so the same
// row reads correctly on a laptop in one zone and a phone in another.
//
// It also converts date entry the other way: `datetime-local` inputs hold a
// naive wall clock, so before a form is sent the value is resolved against the
// browser zone and written to a hidden `<name>_utc` companion as an explicit
// instant. The wire is UTC in both directions.
(function () {
  const PAD = (n) => String(n).padStart(2, "0");

  // Month and weekday names come from Intl rather than a table of our own.
  const NAMES = {
    month: new Intl.DateTimeFormat("en-US", { month: "short", timeZone: "UTC" }),
    weekday: new Intl.DateTimeFormat("en-US", { weekday: "long", timeZone: "UTC" }),
  };

  // Names depend only on the calendar date, so look them up from a UTC instant
  // built out of the wall clock we already resolved.
  function nameOf(kind, year, month, day) {
    return NAMES[kind].format(new Date(Date.UTC(year, month - 1, day)));
  }

  function formats() {
    return window.YAMTRACK_FORMATS || { date: "Y-m-d", time: "H:i", trackTime: true };
  }

  // The wall clock of an instant in a given zone, as plain numbers.
  function wallClock(date, timeZone) {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: timeZone,
      hourCycle: "h23",
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    }).formatToParts(date);

    const v = {};
    for (const part of parts) {
      v[part.type] = part.value;
    }
    return {
      year: Number(v.year), month: Number(v.month), day: Number(v.day),
      hour: Number(v.hour), minute: Number(v.minute), second: Number(v.second),
    };
  }

  // The subset of Django's date-format tokens the preference choices use.
  function applyFormat(pattern, wc) {
    let out = "";
    for (const token of pattern) {
      switch (token) {
        case "Y": out += wc.year; break;
        case "m": out += PAD(wc.month); break;
        case "d": out += PAD(wc.day); break;
        case "M": out += nameOf("month", wc.year, wc.month, wc.day); break;
        case "j": out += wc.day; break;
        case "H": out += PAD(wc.hour); break;
        case "i": out += PAD(wc.minute); break;
        case "g": out += wc.hour % 12 === 0 ? 12 : wc.hour % 12; break;
        case "A": out += wc.hour < 12 ? "AM" : "PM"; break;
        default: out += token;
      }
    }
    return out;
  }

  function localize(element) {
    const iso = element.getAttribute("datetime");
    const date = iso ? new Date(iso) : null;
    if (!date || Number.isNaN(date.getTime())) {
      // Marked ready anyway: the CSS that hides un-localized stamps would
      // otherwise leave the UTC fallback text permanently invisible.
      element.dataset.ytReady = "";
      return;
    }

    const kind = element.dataset.yt;
    const { date: dateFormat, time: timeFormat, trackTime } = formats();
    const wc = wallClock(date, undefined);
    const day = applyFormat(dateFormat, wc);
    const time = applyFormat(timeFormat, wc);

    let text;
    if (kind === "date") {
      text = day;
    } else if (kind === "time") {
      text = time;
    } else if (kind === "weekday-time") {
      // A recurring broadcast slot: the weekday shifts with the zone too.
      text = `${nameOf("weekday", wc.year, wc.month, wc.day)} ${time}`;
    } else if (kind === "natural-day") {
      // "Today"/"Tomorrow" are relative to the viewer's day, not the server's.
      const today = wallClock(new Date(), undefined);
      const asUTC = (w) => Date.UTC(w.year, w.month - 1, w.day);
      const days = Math.round((asUTC(wc) - asUTC(today)) / 86400000);
      const label = days === 0 ? "Today" : days === 1 ? "Tomorrow" : day;
      text = `${label} ${time}`;
    } else {
      text = trackTime ? `${day} ${time}` : day;
    }

    element.textContent = text;
    element.dataset.ytReady = "";
  }

  function localizeAll(root) {
    const scope = root && root.querySelectorAll ? root : document;

    // Placement runs before formatting: it clones release nodes out of an
    // inert <template>, and those clones carry stamps of their own that the
    // pass below has to reach.
    for (const grid of scope.querySelectorAll("[data-calendar-grid]")) {
      fillCalendarGrid(grid);
    }
    for (const list of scope.querySelectorAll("[data-calendar-list]")) {
      fillCalendarList(list);
    }
    const journal = document.getElementById("journal-feed");
    if (journal) {
      buildJournalDays(journal);
    }
    fillTodayLinks(scope);

    for (const element of scope.querySelectorAll("time[data-yt]")) {
      localize(element);
    }
    // Tooltips that just repeat truncated text, filled once the text is final.
    for (const element of scope.querySelectorAll("[data-yt-title-from-text]")) {
      element.title = element.textContent.trim();
    }
    // Date inputs seeded from a stored instant, filled in the browser's zone.
    for (const input of scope.querySelectorAll("input[data-yt-value]")) {
      // Only once: a later htmx swap must not overwrite what someone typed.
      if (input.dataset.ytReady === undefined) {
        input.value = utcToInputValue(input.dataset.ytValue, input.type !== "date");
        input.dataset.ytReady = "";
      }
    }
    // Tooltips carrying their own instant.
    for (const element of scope.querySelectorAll("time[data-yt-title]")) {
      const stamp = document.createElement("time");
      stamp.setAttribute("datetime", element.getAttribute("datetime"));
      stamp.dataset.yt = element.dataset.ytTitle;
      localize(stamp);
      element.title = stamp.textContent;
    }
  }

  // A stored instant as the naive wall clock the input element expects.
  function utcToInputValue(iso, withTime) {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) {
      return "";
    }

    const wc = wallClock(date, undefined);
    const day = `${wc.year}-${PAD(wc.month)}-${PAD(wc.day)}`;
    return withTime ? `${day}T${PAD(wc.hour)}:${PAD(wc.minute)}` : day;
  }

  // Journal day separators. The server sends entries newest-first with their
  // instants; which day each belongs to -- and whether that day is "Today" --
  // is the viewer's question, so the separators are built here.
  function buildJournalDays(container) {
    const entries = Array.from(
      container.querySelectorAll("[data-journal-instant]"),
    );
    if (!entries.length) {
      return;
    }

    // Rebuilt from scratch each pass so an appended page, or a deleted entry
    // that empties a day, cannot leave a stale or duplicated heading.
    for (const heading of container.querySelectorAll("[data-journal-day]")) {
      heading.remove();
    }

    const { date: dateFormat } = formats();
    const today = wallClock(new Date(), undefined);
    const dayNumber = (w) => Date.UTC(w.year, w.month - 1, w.day);
    let previous = null;

    for (const entry of entries) {
      const date = new Date(entry.dataset.journalInstant);
      if (Number.isNaN(date.getTime())) {
        continue;
      }

      const wc = wallClock(date, undefined);
      const key = dayNumber(wc);
      if (key === previous) {
        continue;
      }
      previous = key;

      const days = Math.round((dayNumber(today) - key) / 86400000);
      let label = applyFormat(dateFormat, wc);
      if (days === 0) {
        label = "Today";
      } else if (days === 1) {
        label = "Yesterday";
      }

      const heading = document.createElement("div");
      heading.dataset.journalDay = String(key);
      heading.className =
        "col-span-full flex items-center gap-3 pt-3 first:pt-0";
      heading.innerHTML =
        '<h2 class="shrink-0 text-sm font-semibold text-gray-300"></h2>' +
        '<div class="h-px flex-1 bg-white/10"></div>';
      heading.firstChild.textContent = label;
      entry.parentNode.insertBefore(heading, entry);
    }
  }

  // Calendar placement. The server sends every release the month can touch,
  // in one flat template; which day cell each lands in is decided here.
  function releasesByDay(container) {
    const pool = container.parentNode.querySelector(
      "template[data-calendar-releases]",
    );
    if (!pool) {
      return null;
    }

    const month = Number(container.dataset.month);
    const year = Number(container.dataset.year);
    const byDay = new Map();

    for (const node of pool.content.querySelectorAll("[data-release-instant]")) {
      const date = new Date(node.dataset.releaseInstant);
      if (Number.isNaN(date.getTime())) {
        continue;
      }

      const wc = wallClock(date, undefined);
      // The ±1 day the server added lands outside the month for most zones.
      if (wc.year !== year || wc.month !== month) {
        continue;
      }

      if (!byDay.has(wc.day)) {
        byDay.set(wc.day, []);
      }
      byDay.get(wc.day).push(node);
    }

    return { byDay, month, year };
  }

  const TODAY_CELL = ["ring-2", "ring-indigo-500"];
  const TODAY_NUM = [
    "bg-indigo-500", "text-white", "rounded-full", "w-7", "h-7",
    "flex", "items-center", "justify-center", "ml-auto",
  ];

  function fillCalendarGrid(container) {
    const placed = releasesByDay(container);
    if (!placed) {
      return;
    }

    const today = wallClock(new Date(), undefined);
    for (const cell of container.querySelectorAll("[data-calendar-cell]")) {
      const day = Number(cell.dataset.calendarCell);
      const slot = cell.querySelector("[data-calendar-slot]");
      slot.replaceChildren(
        ...(placed.byDay.get(day) || []).map((node) => node.cloneNode(true)),
      );

      if (
        day === today.day &&
        placed.month === today.month &&
        placed.year === today.year
      ) {
        cell.classList.add(...TODAY_CELL);
        cell.querySelector("[data-calendar-daynum]").classList.add(...TODAY_NUM);
      }
    }
  }

  function fillCalendarList(container) {
    const placed = releasesByDay(container);
    if (!placed) {
      return;
    }

    const today = wallClock(new Date(), undefined);
    const monthName = container.dataset.monthName;
    const sections = [];

    for (const day of [...placed.byDay.keys()].sort((a, b) => a - b)) {
      const isToday =
        day === today.day &&
        placed.month === today.month &&
        placed.year === today.year;
      const weekday = nameOf("weekday", placed.year, placed.month, day);

      const section = document.createElement("div");
      section.className = "bg-[#343a40] rounded-lg overflow-hidden";

      const header = document.createElement("div");
      header.className =
        "px-4 py-3 font-medium flex items-center justify-between " +
        (isToday ? "bg-indigo-600" : "bg-gray-600");
      const title = document.createElement("span");
      title.textContent = `${weekday}, ${monthName} ${day}`;
      header.append(title);
      if (isToday) {
        const badge = document.createElement("span");
        badge.className =
          "text-sm bg-white text-indigo-600 px-2 py-0.5 rounded-full";
        badge.textContent = "Today";
        header.append(badge);
      }

      const body = document.createElement("div");
      body.className = "p-4 space-y-3";
      body.append(...placed.byDay.get(day).map((node) => node.cloneNode(true)));

      section.append(header, body);
      sections.push(section);
    }

    container.replaceChildren(...sections);
  }

  // "Jump to today" targets the viewer's current month, not the server's.
  function fillTodayLinks(scope) {
    const today = wallClock(new Date(), undefined);
    for (const link of scope.querySelectorAll("[data-calendar-today-link]")) {
      const url = new URL(link.href, window.location.origin);
      url.searchParams.set("month", String(today.month));
      url.searchParams.set("year", String(today.year));
      link.href = url.pathname + url.search;
    }
  }

  window.yamtrackLocalizeTimes = localizeAll;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => localizeAll(document));
  } else {
    localizeAll(document);
  }

  // htmx fires several of these for one swap, so coalesce into a single pass
  // per tick rather than re-cloning and re-grouping everything three times.
  let pending = null;
  function scheduleLocalize(target) {
    // Any swap can move content between containers, so re-run over the whole
    // document rather than trying to merge disjoint targets.
    pending = pending === null ? target : document;
    queueMicrotask(() => {
      if (pending === null) {
        return;
      }
      const scope = pending;
      pending = null;
      localizeAll(scope);
    });
  }

  for (const event of ["htmx:load", "htmx:afterSwap", "htmx:afterSettle"]) {
    document.addEventListener(event, (e) => scheduleLocalize(e.target || document));
  }

  // ---- Date entry: naive wall clock in, explicit UTC instant out ----

  // "YYYY-MM-DDTHH:MM" read as a wall clock in the browser zone -> UTC ISO.
  window.yamtrackInputToUTC = function (wallClockValue) {
    const match = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/.exec(wallClockValue);
    if (!match) {
      return null;
    }

    const [, year, month, day, hour, minute] = match.map(Number);
    // Local-time construction resolves the browser zone's offset, including
    // DST, without any offset arithmetic of our own.
    const date = new Date(year, month - 1, day, hour, minute);
    return Number.isNaN(date.getTime()) ? null : date.toISOString();
  };

  // Returns "YYYY-MM-DDTHH:MM" when withTime, otherwise "YYYY-MM-DD".
  window.yamtrackNowForInput = function (withTime, offsetMs) {
    const wc = wallClock(new Date(Date.now() + (offsetMs || 0)), undefined);
    const day = `${wc.year}-${PAD(wc.month)}-${PAD(wc.day)}`;
    return withTime ? `${day}T${PAD(wc.hour)}:${PAD(wc.minute)}` : day;
  };

  function mirrorForm(form) {
    for (const input of form.querySelectorAll('input[type="datetime-local"]')) {
      const mirror = form.querySelector(
        `input[type="hidden"][name="${CSS.escape(input.name)}_utc"]`,
      );
      if (!mirror) {
        continue;
      }
      // An intentionally cleared date must clear the companion too, or the
      // stale instant would be submitted in its place.
      mirror.value = input.value ? window.yamtrackInputToUTC(input.value) || "" : "";
    }
    return form;
  }

  // Mirrored on the way out rather than on every keystroke, so the companion
  // can never be staler than the visible field.
  document.addEventListener(
    "submit",
    (event) => {
      if (event.target instanceof HTMLFormElement) {
        mirrorForm(event.target);
      }
    },
    true,
  );

  document.addEventListener("htmx:configRequest", (event) => {
    const form = event.detail?.elt?.closest?.("form");
    if (!form) {
      return;
    }

    // htmx snapshots parameters before this event, so update them in place.
    for (const input of mirrorForm(form).querySelectorAll(
      'input[type="hidden"][name$="_utc"]',
    )) {
      event.detail.parameters[input.name] = input.value;
    }
  });
})();
