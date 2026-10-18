import { motion } from 'framer-motion';
import { useEffect, useState } from 'react';

const VIEWS = [
  { value: 'discover', label: 'Discover' },
  { value: 'saved', label: 'Saved' },
  { value: 'profile', label: 'Profile' },
];

export default function Header({ view, onViewChange, llmProvider, providers = [], onProviderChange }) {
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  return (
    <motion.header
      initial={{ y: -80, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      transition={{ type: 'spring', bounce: 0, duration: 0.5 }}
      className={`app-header${scrolled ? ' is-scrolled' : ''}`}
    >
      <div
        className={`glass-bar app-header__bar${scrolled ? ' is-scrolled' : ''}`}
      >
        <div className="app-header__brand">
          <div className="brand-mark">✦</div>
          <span className="brand-name">CareerOS</span>
        </div>

        <nav className="app-header__nav">
          {VIEWS.map((v) => (
            <motion.button
              key={v.value}
              onClick={() => onViewChange?.(v.value)}
              whileTap={{ scale: 0.95 }}
              whileHover={{ scale: 1.02 }}
              transition={{ type: 'spring', bounce: 0, duration: 0.25 }}
              aria-current={view === v.value ? 'page' : undefined}
              className="nav-btn"
            >
              {v.label}
            </motion.button>
          ))}
        </nav>

        {providers.length > 0 && (
          <select
            value={llmProvider || ''}
            onChange={(e) => onProviderChange?.(e.target.value)}
            aria-label="LLM provider"
            className="input select app-header__provider"
          >
            {providers.map((p) => (
              <option key={p} value={p}>{p === 'auto' ? 'Auto-model' : p}</option>
            ))}
          </select>
        )}
      </div>
    </motion.header>
  );
}