import { motion } from 'framer-motion';
import { useCallback, useLayoutEffect, useRef, useState } from 'react';
import { defaultSpring } from '../../utils/appleDesign';

/**
 * SegmentedControl — signature Apple control.
 * The selection pill slides between segments using a spring (interruptible §3),
 * starts from the live presentation value, and slides along the same path (§7).
 */
export default function SegmentedControl({ options, value, onChange, ariaLabel }) {
  const refs = useRef({});
  const [pill, setPill] = useState({ left: 0, width: 0 });

  // Measure the active segment and mirror it into state.
  //
  // This was reading refs.current[value] straight out of render, which is wrong
  // twice over: on first render the refs are still null so the pill renders at
  // zero width, and on a value change nothing re-renders, so the pill stays put
  // and only framer's `layout` projection papers over it. Measuring in a layout
  // effect (and on resize) is the honest version — the pill geometry is real
  // state that the next render can use.
  const measure = useCallback(() => {
    const el = refs.current[value];
    setPill(el ? { left: el.offsetLeft, width: el.offsetWidth } : { left: 0, width: 0 });
  }, [value]);

  useLayoutEffect(measure, [measure]);

  useLayoutEffect(() => {
    window.addEventListener('resize', measure);
    return () => window.removeEventListener('resize', measure);
  }, [measure]);

  return (
    <div
      className="glass-card segmented"
      role="radiogroup"
      aria-label={ariaLabel}
    >
      {/* Animated pill — layout projection handles position/size */}
      <motion.div
        layout
        transition={defaultSpring}
        className="segmented-pill"
        style={pill}
      />
      {options.map((opt) => (
        <motion.button
          key={opt.value}
          ref={(el) => { refs.current[opt.value] = el; }}
          type="button"
          role="radio"
          name={ariaLabel}
          aria-checked={opt.value === value}
          onClick={() => onChange(opt.value)}
          className="segmented-option"
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.96 }}
          transition={{ type: 'spring', bounce: 0, duration: 0.2 }}
        >
          {opt.label}
        </motion.button>
      ))}
    </div>
  );
}