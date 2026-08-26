/** Backend enum values are snake_case (e.g. 'no_experience', 'full_time').
 * Render them as readable labels instead of dumping raw underscores in the UI. */
export function formatLabel(value) {
  if (!value) return value;
  return value
    .split('_')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}
