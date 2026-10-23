import { motion } from 'framer-motion';
import SegmentedControl from '../ui/SegmentedControl';
import { formatLabel } from '../../utils/format';

/**
 * Filters — permanently visible advanced filters.
 * Uses native inputs styled per the design system. Per §1, focus states are instant.
 */
const REMOTE_OPTS = [
  { value: 'any', label: 'Any' },
  { value: 'remote', label: 'Remote' },
  { value: 'onsite', label: 'On-site' },
];

// value = backend experience_level string (must match services/normalize.py exactly,
// not just a lowercased label — that was silently breaking the Full-time/Part-time filter, see EMP_TYPES below)
const EXP_LEVELS = [
  { value: '', label: 'Any' },
  { value: 'senior', label: 'Senior' },
  { value: 'mid', label: 'Mid' },
  { value: 'entry', label: 'Entry' },
  { value: 'no_experience', label: 'No experience' },
  { value: 'fresher', label: 'Fresher' },
  { value: 'intern', label: 'Intern' },
];

// Custom order for posted within (more recent first)
const POSTED = [
  { value: '', label: 'Any time' },
  { value: '1', label: 'Last 24h' },
  { value: '3', label: 'Last 3 days' },
  { value: '7', label: 'Last week' },
  { value: '14', label: 'Last 2 weeks' },
  { value: '30', label: 'Last month' },
];

// value = backend employment_type string (must match services/normalize.py _EMPLOYMENT_MAP output —
// sending the raw label 'Full-time' to the API never matched stored 'full_time' rows, filter was dead)
const EMP_TYPES = [
  { value: 'full_time', label: 'Full-time' },
  { value: 'part_time', label: 'Part-time' },
  { value: 'internship', label: 'Internship' },
  { value: 'contract', label: 'Contract' },
  { value: 'temporary', label: 'Temporary' },
];
// Value must match the Job.source string actually stored in the DB —
// the company scraper stores 'company_scraper' (services/sourcing/company_scraper.py),
// not the 'company' name used in the JOB_SOURCES config list. Using the wrong
// value here silently filters out every result.
const SOURCES = ['jsearch', 'adzuna', 'remotive', 'remoteok', 'arbeitnow', 'company_scraper'];

export default function Filters({ filters, onChange }) {
  const set = (patch) => onChange({ ...filters, ...patch });

  return (
    <div className="glass-card filters">
              {/* Source */}
              <FilterRow label="Source">
                <div className="filter-chips">
                  {SOURCES.map((src) => {
                    const active = (filters.sources || []).includes(src);
                    return (
                      <motion.button
                        key={src}
                        type="button"
                        aria-pressed={active}
                        onClick={() => {
                          const list = filters.sources || [];
                          set({ sources: active ? list.filter((s) => s !== src) : [...list, src] });
                        }}
                        className={`btn btn-sm ${active ? 'btn-primary' : 'btn-secondary'}`}
                        whileHover={{ scale: 1.02 }}
                        whileTap={{ scale: 0.96 }}
                        transition={{ type: 'spring', bounce: 0, duration: 0.2 }}
                      >
                        {formatLabel(src)}
                      </motion.button>
                    );
                  })}
                </div>
              </FilterRow>

              {/* Employment type */}
              <FilterRow label="Employment type">
                <SegmentedControl
                  options={[{ value: '', label: 'Any' }, ...EMP_TYPES]}
                  value={filters.employment_type || ''}
                  onChange={(v) => set({ employment_type: v })}
                  ariaLabel="Employment type"
                />
              </FilterRow>

              {/* Remote */}
              <FilterRow label="Work mode">
                <SegmentedControl
                  options={REMOTE_OPTS}
                  value={filters.remote || 'any'}
                  onChange={(v) => set({ remote: v })}
                  ariaLabel="Work mode"
                />
              </FilterRow>

              {/* Experience level */}
              <FilterRow label="Experience">
                <SegmentedControl
                  options={EXP_LEVELS}
                  value={filters.experience_level || ''}
                  onChange={(v) => set({ experience_level: v })}
                  ariaLabel="Experience level"
                />
              </FilterRow>

              {/* Posted within */}
              <FilterRow label="Posted within">
                <SegmentedControl
                  options={POSTED.map((p) => ({ value: p.value, label: p.label }))}
                  value={filters.posted_within_days || ''}
                  onChange={(v) => set({ posted_within_days: v })}
                  ariaLabel="Posted within"
                />
              </FilterRow>

              {/* Salary range */}
              <div className="filter-pair">
                <FilterRow label="Min salary (₹/yr)" stacked htmlFor="salary-min">
                  <input
                    id="salary-min"
                    name="salary_min"
                    className="input"
                    type="number"
                    min="0"
                    step="50000"
                    value={filters.salary_min || ''}
                    onChange={(e) => {
                      const raw = e.target.value;
                      set({ salary_min: raw === '' ? undefined : Number(raw) });
                    }}
                    placeholder="e.g. 300000"
                  />
                </FilterRow>
                <FilterRow label="Max salary (₹/yr)" stacked htmlFor="salary-max">
                  <input
                    id="salary-max"
                    name="salary_max"
                    className="input"
                    type="number"
                    min="0"
                    step="50000"
                    value={filters.salary_max || ''}
                    onChange={(e) => {
                      const raw = e.target.value;
                      set({ salary_max: raw === '' ? undefined : Number(raw) });
                    }}
                    placeholder="e.g. 1200000"
                  />
                </FilterRow>
              </div>
            </div>
  );
}

function FilterRow({ label, children, stacked = false, htmlFor }) {
  return (
    <div className={`filter-row${stacked ? ' filter-row--stacked' : ''}`}>
      <label htmlFor={htmlFor} className="filter-label">{label}</label>
      {children}
    </div>
  );
}