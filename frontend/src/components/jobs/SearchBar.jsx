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
  // Default to 'any'. Defaulting to 'internship' sent employment_type on every
  // first search and hid every non-internship row, with no control appearing
  // to explain the empty result set.
  const [jobType, setJobType] = useState('any');

  const submit = (e) => {
    e?.preventDefault();
    onSearch({ query, location, jobType });
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ type: 'spring', bounce: 0, duration: 0.5 }}
      className="glass-card searchbar"
    >
      <div className="section-title text-secondary searchbar-title">
        Find your next role
      </div>
      <form onSubmit={submit} className="searchbar-form">
        <div className="searchbar-row">
          <div className="searchbar-field searchbar-field--query">
            <span className="searchbar-glyph">⌕</span>
            <input
              className="input searchbar-input"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search roles, skills, keywords…"
              aria-label="Search query"
            />
          </div>
          <div className="searchbar-field searchbar-field--location">
            <span className="searchbar-glyph">⌖</span>
            <input
              className="input searchbar-input"
              value={location}
              onChange={(e) => setLocation(e.target.value)}
              placeholder="Location (India default)"
              aria-label="Location"
            />
          </div>
          <motion.button
            type="submit"
            disabled={loading}
            whileTap={{ scale: 0.96 }}
            whileHover={{ scale: 1.02 }}
            transition={{ type: 'spring', bounce: 0, duration: 0.25 }}
            className="btn btn-search lg searchbar-submit"
          >
            {loading ? 'Searching…' : 'Search'}
          </motion.button>
        </div>

        <div className="searchbar-toggle">
          <SegmentedControl options={JOB_TYPE} value={jobType} onChange={setJobType} ariaLabel="Job type" />
        </div>
      </form>
    </motion.div>
  );
}