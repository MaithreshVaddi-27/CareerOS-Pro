import { useSyncExternalStore } from 'react';

const QUERY = '(prefers-reduced-motion: reduce)';

/**
 * useReducedMotion — returns true if the user has requested reduced motion.
 * @returns {boolean} true if prefers-reduced-motion: reduce
 *
 * useSyncExternalStore rather than useState + useEffect. matchMedia is an
 * external store, and the effect version had to setState on mount, which meant
 * every consumer re-rendered once on hydration and animations briefly ran at
 * full motion before the reduced-motion value arrived. This subscribes
 * directly, reads the current value during the first render, and tears down
 * with React instead of manually.
 */
export function useReducedMotion() {
  return useSyncExternalStore(
    (onChange) => {
      const mediaQuery = window.matchMedia(QUERY);
      mediaQuery.addEventListener('change', onChange);
      return () => mediaQuery.removeEventListener('change', onChange);
    },
    () => window.matchMedia(QUERY).matches,
    // Server render: no window, and no reason to assume reduced motion.
    () => false,
  );
}