import { motion } from 'framer-motion';
import { useEffect, useState } from 'react';
import Tag from '../ui/Tag';

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
      style={{
        position: 'sticky',
        top: 0,
        zIndex: 50,
        padding: scrolled ? 'var(--space-2) 0' : 'var(--space-3) 0',
        transition: 'padding 200ms ease',
      }}
    >
      <div
        className="glass-bar"
        style={{
          maxWidth: 1200,
          margin: '0 auto',
          borderRadius: scrolled ? 'var(--r-3)' : 'var(--r-4)',
          padding: 'var(--space-2) var(--space-4)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 'var(--space-4)',
          boxShadow: scrolled ? 'var(--shadow-md)' : 'none',
          transition: 'border-radius 200ms ease, box-shadow 200ms ease',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)' }}>
          <div className="tag tag-blue" style={{
            width: 36, height: 36, borderRadius: 'var(--r-2)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontWeight: 800, fontSize: '1.05rem',
            boxShadow: 'var(--accent-glow)',
          }}>✦</div>
          <span style={{ fontFamily: 'var(--font-display)', fontWeight: 600, letterSpacing: '-0.02em', fontSize: '1.0625rem' }}>
            CareerOS
          </span>
          <Tag color="blue">beta</Tag>
        </div>

        <nav style={{ display: 'flex', gap: 'var(--space-1)' }}>
          {VIEWS.map((v) => (
            <motion.button
              key={v.value}
              onClick={() => onViewChange?.(v.value)}
              whileTap={{ scale: 0.95 }}
              whileHover={{ scale: 1.02 }}
              transition={{ type: 'spring', bounce: 0, duration: 0.25 }}
              aria-current={view === v.value ? 'page' : undefined}
              style={{
                padding: '8px 14px',
                borderRadius: 'var(--r-2)',
                fontSize: '0.875rem',
                fontWeight: 500,
                background: view === v.value ? 'var(--bg-card-hover)' : 'transparent',
                color: view === v.value ? 'var(--text)' : 'var(--text-secondary)',
                transition: 'background 180ms ease, color 180ms ease',
              }}
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
            className="input select"
            style={{ height: 36, padding: '0 32px 0 12px', fontSize: '0.8125rem', minWidth: 140 }}
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