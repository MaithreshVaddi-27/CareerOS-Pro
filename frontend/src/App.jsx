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

const LLM_PROVIDERS = ['auto', 'nvidia', 'openrouter', 'gemini', 'llama', 'none'];

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

  const [showSkeleton, setShowSkeleton] = useState(false);
  const reducedMotion = useReducedMotion();

  const doSearch = useCallback(async ({ query, location, jobType }) => {
    if (!query?.trim()) return;
    setLoading(true);
    setError('');
    setShowSkeleton(true);
    try {
      const data = await searchJobs({
        query: query.trim(),
        location: location?.trim() || 'India',
        employment_type: filters.employment_type || (jobType === 'any' ? '' : jobType) || undefined,
        remote: filters.remote || 'any',
        experience_level: filters.experience_level || undefined,
        salary_min: filters.salary_min || undefined,
        salary_max: filters.salary_max || undefined,
        posted_within_days: filters.posted_within_days || undefined,
        sources: filters.sources || [],
        top_k: 30,
        llm_provider: llmProvider !== 'auto' ? llmProvider : undefined,
      });
      setResults(Array.isArray(data) ? data : (data.results || []));
    } catch (e) {
      setError(e.message);
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
      setSelectedJob((prev) => (prev?.id === job.id ? { ...prev, ...json, verified: true } : prev));
    } catch {
      // keep silent; verification is best-effort
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
              <h2 className="section-title">{view === 'saved' ? 'Saved opportunities' : 'Recommended'}</h2>
              <span className="app-resultcount">{visible.length} results</span>
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
                  {error}
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
                      job={{ ...job, match_score: job.match_score ?? job.matchPercentage ?? 0 }}
                      onOpen={openJob}
                      onSave={saveJob}
                      saved={saved.some((s) => s.id === job.id)}
                    />
                  </motion.div>
                ))}
              </AnimatePresence>

              {!loading && !visible.length ? (
                <motion.div layout className="glass-card app-empty">
                  <p className="text-secondary">
                    Start by searching for roles, stacks, or keywords — for example
                    {' '}<Tag color="blue">Software intern</Tag>
                    {' '}<Tag color="sky">Machine learning</Tag>
                    {' '}<Tag color="teal">Remote</Tag>.
                  </p>
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