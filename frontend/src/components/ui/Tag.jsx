/**
 * Tag / chip — small categorical label.
 * Color mapped to category for type-safe styling (§16 grouping/mapping).
 */
// Hues only. purple/pink were dropped when the accent moved to emerald — a tag
// palette with violet in it re-introduces exactly the tint the rest of the UI
// dropped. 'teal' is kept as the key name because JobCard uses it for job.source,
// but it now paints cyan.
const CLASSES = {
  blue: 'tag-blue',      // emerald — the brand hue
  green: 'tag-green',
  orange: 'tag-orange',
  teal: 'tag-teal',      // cyan
  sky: 'tag-sky',
  red: 'tag-red',
  yellow: 'tag-yellow',
  neutral: 'tag tag-neutral',
};

export default function Tag({ children, color = 'neutral', dot = false, className = '' }) {
  const cls = CLASSES[color] || CLASSES.neutral;
  return (
    <span className={`${cls} ${className}`.trim()}>
      {dot && <span className="tag-dot" />}
      {children}
    </span>
  );
}