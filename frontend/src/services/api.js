// API service — calls CareerOS FastAPI backend.
const BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000';
// Only needed if the backend has API_KEY set in its .env (see backend .env.example).
// If the backend's API_KEY is empty, require_api_key() is a no-op and this header
// is simply ignored — safe to always send.
const API_KEY = import.meta.env.VITE_API_KEY || '';

function authHeaders(extra = {}) {
  return API_KEY ? { ...extra, 'X-API-Key': API_KEY } : extra;
}

/** Transform backend JobOut to frontend JobCard format. */
function transformJob(job) {
  // Format salary display
  let salaryDisplay = '';
  if (job.salary_min || job.salary_max) {
    const currency = job.salary_currency || 'USD';
    const min = job.salary_min ? job.salary_min.toLocaleString() : '';
    const max = job.salary_max ? job.salary_max.toLocaleString() : '';
    if (min && max) salaryDisplay = `${currency} ${min}–${max}`;
    else if (min) salaryDisplay = `${currency} ${min}+`;
    else if (max) salaryDisplay = `Up to ${currency} ${max}`;
  }

  // Map remote string to boolean for UI
  const isRemote = job.remote === 'remote';

  // Derive match_score from hybrid_score (for matched jobs) or default to 0
  const matchScore = job.hybrid_score !== undefined ? job.hybrid_score
    : job.keyword_score !== undefined ? job.keyword_score
    : 0;

  return {
    id: job.id,
    title: job.title,
    company: job.company,
    location: job.location_normalized,
    employment_type: job.employment_type,
    experience_level: job.experience_level,
    is_remote: isRemote,
    remote: job.remote, // keep original for reference
    skills_required: job.skills_required,
    salary_min: job.salary_min,
    salary_max: job.salary_max,
    salary_currency: job.salary_currency,
    salary: salaryDisplay,
    apply_url: job.apply_url,
    posted_at: job.posted_at,
    source: job.source,
    is_active: job.is_active,
    verified: job.verified,
    verified_at: job.verified_at,
    match_score: matchScore,
    // MatchedJobOut extra fields (if present)
    keyword_score: job.keyword_score,
    semantic_score: job.semantic_score,
    hybrid_score: job.hybrid_score,
    matched_skills: job.matched_skills,
    missing_skills: job.missing_skills,
    explanation: job.explanation,
    confidence: job.confidence,
  };
}

/** Call /jobs/search and return transformed jobs for frontend. */
export async function searchJobs({
  query,
  location = '',
  employment_type = '',
  remote = '',
  experience_level = '',
  salary_min = '',
  salary_max = '',
  posted_within_days = '',
  sources = [],
  llm_provider,
  model_name,
}) {
  // Map frontend filter fields to backend SearchRequest schema
  const payload = {
    query: query?.trim() || '',
    location: location?.trim() || (location ? location.trim() : undefined),
    // Filters.jsx now supplies backend-format values directly (e.g. 'full_time', 'no_experience') —
    // no more label-guessing/toLowerCase() here, that was the bug that made these filters no-ops.
    employment_type: employment_type || undefined,
    experience_level: experience_level || undefined,
    // Backend expects remote_only: true for remote-only filter
    remote_only: remote === 'remote' ? true : false,
    min_salary: salary_min ? Number(salary_min) : undefined,
    // Was destructured as `_salary_max` and never sent, so the visible "Max
    // salary" input in Filters.jsx did nothing at all. The backend grew
    // `max_salary` to match.
    max_salary: salary_max ? Number(salary_max) : undefined,
    posted_within_days: posted_within_days ? Number(posted_within_days) : undefined,
    sources: sources && sources.length ? sources : undefined,
    llm_provider: llm_provider || undefined,
    model_name: model_name || undefined,
  };

  const r = await fetch(`${BASE}/jobs/search`, {
    method: 'POST',
    headers: authHeaders({
      'Content-Type': 'application/json',
      'X-Request-ID': crypto.randomUUID()
    }),
    body: JSON.stringify(payload),
  });
  if (!r.ok) throw new Error(`Search failed: ${r.status}`);

  const data = await r.json();
  // Backend returns array directly, handle both array and object with results
  const jobs = Array.isArray(data) ? data : (data.results || []);
  return jobs.map(transformJob);
}

/** Run full match pipeline for a query and return transformed jobs. */
export async function matchJobs(payload) {
  const r = await fetch(`${BASE}/jobs/match`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json', 'X-Request-ID': crypto.randomUUID() }),
    body: JSON.stringify(payload),
  });
  if (!r.ok) throw new Error(`Match failed: ${r.status}`);
  const data = await r.json();
  // Backend returns MatchedJobOut[] array
  return data.map(transformJob);
}

/** Fetch a single job by id and transform for frontend. */
export async function getJob(id) {
  const r = await fetch(`${BASE}/jobs/${id}`, { headers: authHeaders() });
  if (!r.ok) throw new Error(`Get job failed: ${r.status}`);
  const job = await r.json();
  return transformJob(job);
}

/** Verify a posting is still live. */
export async function verifyJob(id) {
  const r = await fetch(`${BASE}/jobs/${id}/verify`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json', 'X-Request-ID': crypto.randomUUID() }),
  });
  if (!r.ok) throw new Error(`Verify failed: ${r.status}`);
  return r.json();
}

export const API_BASE = BASE;