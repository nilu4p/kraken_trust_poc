/** ISO date (YYYY-MM-DD) ``lagDays`` days ago: default date of a tile / API test (yesterday's data is published). */
export function latestDate(lagDays = 1) {
  const date = new Date(Date.now() - lagDays * 24 * 3600 * 1000);
  return date.toISOString().slice(0, 10);
}
