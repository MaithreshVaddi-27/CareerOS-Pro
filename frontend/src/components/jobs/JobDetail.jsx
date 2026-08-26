import Modal from '../ui/Modal';
import Tag from '../ui/Tag';
import Button from '../ui/Button';
import { formatLabel } from '../../utils/format';

export default function JobDetail({ job, onClose, onVerify, verifying }) {
  if (!job) return null;
  const isIntern = /intern/i.test(job.employment_type || '') || /intern/i.test(job.title || '');

  return (
    <Modal open={Boolean(job)} onClose={onClose} title={job.title}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-4)', marginBottom: 'var(--space-5)' }}>
        <div style={{
          width: 56, height: 56, borderRadius: 'var(--r-3)',
          background: 'linear-gradient(135deg, var(--bg-card-hover), var(--surface-base))',
          border: '1px solid var(--border)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontWeight: 600, fontSize: '1.3rem', color: 'var(--text-secondary)',
        }}>
          {(job.company || '?').slice(0, 1).toUpperCase()}
        </div>
        <div>
          <h3 style={{ fontSize: '1.25rem', fontWeight: 600, color: 'var(--text)' }}>{job.company || 'Unknown'}</h3>
          <p style={{ color: 'var(--text-secondary)', fontSize: '0.9375rem' }}>{job.location || 'Location unspecified'}</p>
        </div>
      </div>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-2)', marginBottom: 'var(--space-5)' }}>
        <Tag color={isIntern ? 'purple' : 'blue'} dot>
          {isIntern ? 'Internship' : job.employment_type || 'Full-time'}
        </Tag>
        {job.experience_level ? <Tag>{formatLabel(job.experience_level)}</Tag> : null}
        {job.is_remote ? <Tag color="green" dot>Remote</Tag> : null}
        {job.salary ? <Tag color="orange">{job.salary}</Tag> : null}
        {job.source ? <Tag color="teal">{job.source}</Tag> : null}
        {typeof job.match_score === 'number' ? (
          <Tag color="blue" dot>Match {Math.round(job.match_score * 100)}%</Tag>
        ) : null}
      </div>

      {(job.matched_skills || job.missing_skills) ? (
        <section style={{ marginBottom: 'var(--space-5)' }}>
          <h4 className="section-title" style={{ marginBottom: 'var(--space-3)' }}>AI Analysis</h4>
          {Array.isArray(job.matched_skills) && job.matched_skills.length ? (
            <div style={{ marginBottom: 'var(--space-3)' }}>
              <p style={{ fontSize: '0.8125rem', color: 'var(--color-text-tertiary)', marginBottom: 'var(--space-2)' }}>MATCHED</p>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-2)' }}>
                {job.matched_skills.map((s) => <Tag key={s} color="green">{s}</Tag>)}
              </div>
            </div>
          ) : null}
          {Array.isArray(job.missing_skills) && job.missing_skills.length ? (
            <div>
              <p style={{ fontSize: '0.8125rem', color: 'var(--color-text-tertiary)', marginBottom: 'var(--space-2)' }}>GAP</p>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-2)' }}>
                {job.missing_skills.map((s) => <Tag key={s} color="orange">{s}</Tag>)}
              </div>
            </div>
          ) : null}
          {job.explanation ? (
            <p style={{ marginTop: 'var(--space-3)', fontSize: '0.9375rem', color: 'var(--color-text-secondary)', lineHeight: 1.6 }}>
              {job.explanation}
            </p>
          ) : null}
        </section>
      ) : null}

      {job.description ? (
        <section style={{ marginBottom: 'var(--space-5)' }}>
          <h4 className="section-title" style={{ marginBottom: 'var(--space-3)' }}>Description</h4>
          <p style={{
            color: 'var(--color-text-secondary)',
            fontSize: '0.9375rem',
            lineHeight: 1.7,
            whiteSpace: 'pre-wrap',
          }}>{job.description}</p>
        </section>
      ) : null}

      <div style={{ display: 'flex', gap: 'var(--space-3)', marginTop: 'var(--space-5)' }}>
        {job.apply_url ? (
          <Button variant="primary" size="lg" onClick={() => window.open(job.apply_url, '_blank', 'noopener')}>
            Apply →
          </Button>
        ) : null}
        <Button variant="secondary" size="lg" onClick={() => onVerify?.(job)} disabled={verifying}>
          {verifying ? 'Checking…' : 'Verify posting'}
        </Button>
      </div>
    </Modal>
  );
}