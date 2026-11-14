// Runs at most `limit` async tasks at once; the rest wait their turn.
// `priority` (a number, or a function read when a slot frees) lets a waiting
// task go ahead of others: lower runs first, equal priorities keep their order.
export function createFetchGate(limit) {
  let active = 0;
  let order = 0;
  const waiting = [];
  const rank = entry => {
    const value = typeof entry.priority === 'function' ? entry.priority() : entry.priority;
    return Number.isFinite(value) ? value : 0;
  };
  const release = () => {
    active -= 1;
    if (!waiting.length) return;
    let best = 0;
    for (let index = 1; index < waiting.length; index += 1) {
      const a = rank(waiting[index]);
      const b = rank(waiting[best]);
      if (a < b || (a === b && waiting[index].order < waiting[best].order)) best = index;
    }
    const [next] = waiting.splice(best, 1);
    next.resolve();
  };
  return async function run(task, priority) {
    if (active >= limit) {
      await new Promise(resolve => waiting.push({ resolve, priority, order: order++ }));
    }
    active += 1;
    try {
      return await task();
    } finally {
      release();
    }
  };
}
