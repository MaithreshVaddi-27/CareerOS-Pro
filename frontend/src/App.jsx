import { useState, useCallback } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import Header from './components/layout/Header';
import SearchBar from './components/jobs/SearchBar';
import Filters from './components/jobs/Filters';
import JobCard from './components/jobs/JobCard';
import JobDetail from './components/jobs/JobDetail';
import SkeletonGrid from './components/ui/SkeletonGrid';
import Tag from './components/ui/Tag';
import { searchJobs, verifyJob as apiVerifyJob } from './services/api';
import { defaultSpring } from './utils/appleDesign';
import { useReducedMotion } from './hooks/useReducedMotion';
import './styles/globals.css';

const LLM_PROVIDERS = ['auto', 'gemini', 'none'];

export default function App() {
  const [view, setView] = useState('discover');
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [selectedJob, setSelectedJob] = useState(null);
  const [saved, setSaved] = useState([]);
  const [llmProvider, setLLMProvider] = useState('auto');
  const [filters, setFilters] = useState({});
  const [verifyingId, setVerifyingId] = useState(null);
  const [error, setError] = useState('');
  const [searched, setSearched] = useState(false);

  const [showSkeleton, setShowSkeleton] = useState(false);
  const reducedMotion = useReducedMotion();

  const doSearch = useCallback(async ({ query, location, jobType }) => {
    if (!query?.trim()) return;
    setSearched(true);
    setLoading(true);
    setError('');
    setShowSkeleton(true);
    try {
      const data = await searchJobs({
        query: query.trim(),
        // Blank location means "anywhere". It used to default to the string
        // 'India', which the backend then treated as a LIKE filter — every
        // non-India posting vanished with no visible control explaining why.
        location: location?.trim() || undefined,
        // The advanced Employment-type control wins; the search-bar toggle is
        // the fallback. 'any' must map to undefined, never to a filter value.
        employment_type: filters.employment_type || (jobType === 'any' ? undefined : jobType),
        remote: filters.remote || 'any',
        experience_level: filters.experience_level || undefined,
        salary_min: filters.salary_min || undefined,
        salary_max: filters.salary_max || undefined,
        posted_within_days: filters.posted_within_days || undefined,
        sources: filters.sources || [],
        llm_provider: llmProvider !== 'auto' ? llmProvider : undefined,
      });
      setResults(Array.isArray(data) ? data : (data.results || []));
    } catch (e) {
      const msg = e.message || '';
      setError(
        msg.includes('401') || msg.includes('403')
          ? 'You need to sign in to search. Please check your API key.'
          : msg.includes('Failed to fetch') || msg.includes('Network')
          ? 'Unable to reach the search service. Check your connection and try again.'
          : msg.includes('500')
          ? 'The search service had a hiccup. Please try again in a moment.'
          : msg || 'Something went wrong while searching.'
      );
      setResults([]);
    } finally {
      setLoading(false);
      setShowSkeleton(false);
    }
  }, [filters, llmProvider]);

  const saveJob = useCallback((job) => {
    setSaved((prev) => (prev.some((j) => j.id === job.id) ? prev.filter((j) => j.id !== job.id) : [...prev, job]));
  }, []);

  const openJob = useCallback((job) => setSelectedJob(job), []);
  const closeJob = useCallback(() => setSelectedJob(null), []);
  const verifyJob = useCallback(async (job) => {
    setVerifyingId(job.id);
    try {
      const json = await apiVerifyJob(job.id);
      // Trust the server's verdict. This used to append `verified: true` after
      // the spread, which overrode whatever the backend actually said — so a
      // posting the verifier found closed or unconfirmed rendered as Verified in
      // the modal while the row was deactivated server-side. Spec 2.3: never
      // guess. An `is_active` flip also has to reach the list, not just the modal.
      setSelectedJob((prev) => (prev?.id === job.id ? { ...prev, ...json } : prev));
      setResults((prev) =>
        prev.map((r) => (r.id === job.id ? { ...r, ...json } : r))
      );
    } catch (e) {
      setError(e.message);
    } finally {
      setVerifyingId(null);
    }
  }, []);

  const visible = view === 'saved' ? saved : results;

  return (
    <div className="app-shell">
      <Header view={view} onViewChange={setView} llmProvider={llmProvider} providers={LLM_PROVIDERS} onProviderChange={setLLMProvider} />

      <main className="app-main">
        <motion.div
          initial={{ opacity: 0, y: reducedMotion ? 0 : 6 }}
          animate={{ opacity: 1, y: reducedMotion ? 0 : 0 }}
          transition={reducedMotion ? { opacity: { duration: 0.2 } } : defaultSpring}
        >
          <SearchBar onSearch={doSearch} loading={loading} />
        </motion.div>

        {view !== 'profile' && (
          <>
            <motion.div className="app-filters" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={reducedMotion ? { delay: 0.05, duration: 0.2 } : { delay: 0.05, ...defaultSpring }}>
              <Filters filters={filters} onChange={setFilters} />
            </motion.div>

            <motion.div
              className="app-resultshead"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={reducedMotion ? { delay: 0.1, duration: 0.2 } : { delay: 0.1, ...defaultSpring }}
            >
              <h2 className="section-title">
                {view === 'saved' ? 'Your saved roles' : 'Recommended roles'}
              </h2>
              <span className="app-resultcount">
                {visible.length} result{visible.length !== 1 ? 's' : ''}
                {view !== 'saved' && saved.length ? ` · ${saved.length} saved` : ''}
              </span>
            </motion.div>

            <AnimatePresence>
              {error ? (
                <motion.div
                  initial={{ opacity: 0, y: reducedMotion ? 0 : 4 }}
                  animate={{ opacity: 1, y: reducedMotion ? 0 : 0 }}
                  exit={{ opacity: 0, y: reducedMotion ? 0 : -4 }}
                  transition={reducedMotion ? { opacity: { duration: 0.2 } } : defaultSpring}
                  className="app-error"
                >
                  <span role="img" aria-hidden="true">⚠️</span>
                  <span>{error}</span>
                </motion.div>
              ) : null}
            </AnimatePresence>

            <section className="app-list">
              <AnimatePresence mode="popLayout">
                {showSkeleton && <SkeletonGrid />}
                {visible.map((job, idx) => (
                  <motion.div
                    key={job.id || job.source_id}
                    layout
                    initial={{ opacity: 0, y: reducedMotion ? 0 : 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: reducedMotion ? 0 : -6 }}
                    transition={reducedMotion ? { opacity: { duration: 0.2 }, delay: Math.min(idx, 8) * 0.04 } : { ...defaultSpring, delay: Math.min(idx, 8) * 0.04 }}
                  >
                    <JobCard
                      job={job}
                      onOpen={openJob}
                      onSave={saveJob}
                      saved={saved.some((s) => s.id === job.id)}
                    />
                  </motion.div>
                ))}
              </AnimatePresence>

              {!loading && !visible.length ? (
                <motion.div layout className="glass-card app-empty">
                  {!searched ? (
                    <>
                      <p className="text-secondary">
                        Start by searching for roles, stacks, or keywords — for example
                        {' '}<Tag color="blue">Software intern</Tag>{' '}
                        <Tag color="sky">Machine learning</Tag>{' '}
                        <Tag color="teal">Remote</Tag>.
                      </p>
                      <p className="text-secondary hint">
                        Try “react developer remote” or “python internship Berlin”.
                      </p>
                    </>
                  ) : (
                    <>
                      <p className="text-secondary">
                        No roles matched those filters. Try widening the location,
                        salary, or experience range — or clear a filter and search again.
                      </p>
                    </>
                  )}
                </motion.div>
              ) : null}
            </section>
          </>
        )}

        <AnimatePresence mode="wait">
          {view === 'profile' && (
            <motion.section
              key="profile"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={defaultSpring}
              className="glass-card app-profile"
            >
              <h2 className="section-title">Profile</h2>
              <p className="text-secondary">Resume parsing, skills, and application tracker will appear here.</p>
            </motion.section>
          )}
        </AnimatePresence>
      </main>

      <footer className="app-footer">
        CareerOS · Designed like it matters
      </footer>

      <JobDetail job={selectedJob} onClose={closeJob} onVerify={verifyJob} verifying={verifyingId === selectedJob?.id} />
    </div>
  );
}