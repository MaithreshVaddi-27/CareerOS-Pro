import { motion } from 'framer-motion';
import { useReducedMotion } from '../../hooks/useReducedMotion';

/**
 * SkeletonGrid — loading placeholders that pulse gently while results are loading.
 * Premium dark version aligned with the new design tokens. Geometry and the
 * shimmer live in globals.css; only framer-motion props stay in JS.
 */
export default function SkeletonGrid({ count = 5 }) {
  const rows = Array.from({ length: count });
  const reducedMotion = useReducedMotion();
  const pulse = (delay) => (reducedMotion
    ? { repeat: Infinity, repeatType: 'reverse', duration: 0.2, delay }
    : { repeat: Infinity, repeatType: 'reverse', duration: 1.1, delay });

  return (
    <div className={`skeleton-grid${reducedMotion ? ' is-paused' : ''}`}>
      {rows.map((_, idx) => (
        <motion.div
          key={idx}
          initial={{ opacity: 0.45, y: reducedMotion ? 0 : 6 }}
          animate={{ opacity: 0.8 }}
          transition={pulse(idx * 0.08)}
          className="glass-card skeleton-row"
        >
          <div className="skeleton-avatar" />
          <div className="skeleton-body">
            <div className="skeleton-bar skeleton-bar--title" />
            <div className="skeleton-bar skeleton-bar--meta" />
            <div className="skeleton-bar skeleton-bar--tail" />
          </div>
        </motion.div>
      ))}
    </div>
  );
}
