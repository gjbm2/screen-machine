// The date sections a bucket's images are grouped into (the screens' buckets and the Generated tab), newest first.
export const DATE_GROUP_ORDER = ['Today', 'Yesterday', 'Earlier this week', 'Last week', 'Older'] as const;
export type DateGroupLabel = typeof DATE_GROUP_ORDER[number];

/** The section for a time in epoch seconds; weeks start on Monday. */
export function dateGroupLabel(timestamp?: number): DateGroupLabel {
  const date = new Date((timestamp ?? 0) * 1000);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const day = new Date(date);
  day.setHours(0, 0, 0, 0);

  const diffDays = Math.floor((today.getTime() - day.getTime()) / (24 * 60 * 60 * 1000));
  if (diffDays === 0) return 'Today';
  if (diffDays === 1) return 'Yesterday';

  const startOfWeek = new Date(today);
  const weekday = startOfWeek.getDay();
  startOfWeek.setDate(startOfWeek.getDate() - weekday + (weekday === 0 ? -6 : 1));
  if (date >= startOfWeek) return 'Earlier this week';

  const startOfLastWeek = new Date(startOfWeek);
  startOfLastWeek.setDate(startOfLastWeek.getDate() - 7);
  if (date >= startOfLastWeek) return 'Last week';

  return 'Older';
}
