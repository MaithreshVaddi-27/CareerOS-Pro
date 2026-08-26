import { motion } from 'framer-motion';
import { useState } from 'react';
import SegmentedControl from '../ui/SegmentedControl';

/**
 * SearchBar — the hero of CareerOS. Translucent glass, large input.
 * Segmented control switches between Jobs and Internships (your use case).
 * Per §1, feedback is instant; per §7, the bar stays spatially anchored.
 */
// value must match backend employment_type strings (see services/normalize.py _EMPLOYMENT_MAP) —
// 'full-time' (hyphen) never matched stored 'full_time' rows, this filter was silently a no-op
const JOB_TYPE = [
  { value: 'any', label: 'Any' },
  { value: 'internship', label: 'Internships' },
  { value: 'full_time', label: 'Jobs' },
];

export default function SearchBar({ onSearch, loading }) {
  const [query, setQuery] = useState('');
  const [location, setLocation] = useState('');
  const [jobType, setJobType] = useState('internship');

  const submit = (e) => {
    e?.preventDefault();
    onSearch({ query, location, jobType });
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ type: 'spring', bounce: 0, duration: 0.5 }}
      className="glass-card"
      style={{
        borderRadius: 'var(--r-4)',
        padding: 'var(--space-5)',
        boxShadow: 'var(--shadow-lg)',
      }}
    >
      <div className="section-title text-secondary" style={{ marginBottom: 'var(--space-3)' }}>
        Find your next role
      </div>
      <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
        <div style={{ display: 'flex', gap: 'var(--space-3)', flexWrap: 'wrap', alignItems: 'center' }}>
          <div style={{ position: 'relative', flex: 1, minWidth: 200 }}>
            <span className="text-tertiary" style={{ position: 'absolute', left: 14, top: '50%', transform: 'translateY(-50%)', fontSize: '1.1rem', pointerEvents: 'none', opacity: 0.9 }}>⌕</span>
            <input
              className="input"
              style={{ paddingLeft: 40, height: 52, fontSize: '1.0625rem' }}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search roles, skills, keywords…"
              aria-label="Search query"
            />
          </div>
          <div style={{ position: 'relative', flexBasis: 220, flexGrow: 1, height: 52 }}>
            <span className="text-tertiary" style={{ position: 'absolute', left: 14, top: '50%', transform: 'translateY(-50%)', fontSize: '1.1rem', pointerEvents: 'none', opacity: 0.9 }}>⌖</span>
            <input
              className="input"
              value={location}
              onChange={(e) => setLocation(e.target.value)}
              placeholder="Location (India default)"
              aria-label="Location"
              style={{ paddingLeft: 40 }}
            />
          </div>
          <motion.button
            type="submit"
            disabled={loading}
            whileTap={{ scale: 0.96 }}
            whileHover={{ scale: 1.02 }}
            transition={{ type: 'spring', bounce: 0, duration: 0.25 }}
            className="btn btn-search lg"
            style={{ height: 52, whiteSpace: 'nowrap' }}
          >
            {loading ? 'Searching…' : 'Search'}
          </motion.button>
        </div>

        <div style={{ display: 'flex', justifyContent: 'center' }}>
          <SegmentedControl options={JOB_TYPE} value={jobType} onChange={setJobType} ariaLabel="Job type" />
        </div>
      </form>
    </motion.div>
  );
}