import { motion } from 'framer-motion';
import { useRef } from 'react';
import { defaultSpring } from '../../utils/appleDesign';

/**
 * SegmentedControl — signature Apple control.
 * The selection pill slides between segments using a spring (interruptible §3),
 * starts from the live presentation value, and slides along the same path (§7).
 */
export default function SegmentedControl({ options, value, onChange, ariaLabel }) {
  const refs = useRef({});

  // Compute pill position from the active segment's measured box
  const getStyle = () => {
    const el = refs.current[value];
    if (!el) return { left: 0, width: 0 };
    return { left: el.offsetLeft, width: el.offsetWidth };
  };

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
        style={getStyle()}
      />
      {options.map((opt) => (
        <motion.button
          key={opt.value}
          ref={(el) => { refs.current[opt.value] = el; }}
          role="radio"
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