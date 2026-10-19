import Modal from '../ui/Modal';
import Tag from '../ui/Tag';
import Button from '../ui/Button';
import { formatLabel } from '../../utils/format';

export default function JobDetail({ job, onClose, onVerify, verifying }) {
  if (!job) return null;
  const isIntern = /intern/i.test(job.employment_type || '') || /intern/i.test(job.title || '');

  return (
    <Modal open={Boolean(job)} onClose={onClose} title={job.title}>
      <div className="detail-head">
        <div className="detail-monogram">
          {(job.company || '?').slice(0, 1).toUpperCase()}
        </div>
        <div>
          <h3 className="detail-company">{job.company || 'Unknown'}</h3>
          <p className="detail-location">{job.location || 'Location not specified'}</p>
        </div>
      </div>

      <div className="detail-tags">
        <Tag color={isIntern ? 'sky' : 'blue'} dot>
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

      {(job.matched_skills || job.missing_skills || job.explanation) ? (
        <section className="detail-section">
          <h4 className="section-title">Why this matches you</h4>
          {Array.isArray(job.matched_skills) && job.matched_skills.length ? (
            <div className="detail-block">
              <p className="detail-label">Matched skills</p>
              <div className="detail-skillrow">
                {job.matched_skills.map((s) => <Tag key={s} color="green">{s}</Tag>)}
              </div>
            </div>
          ) : null}
          {Array.isArray(job.missing_skills) && job.missing_skills.length ? (
            <div>
              <p className="detail-label">Skills to grow</p>
              <div className="detail-skillrow">
                {job.missing_skills.map((s) => <Tag key={s} color="orange">{s}</Tag>)}
              </div>
            </div>
          ) : null}
          {job.explanation ? (
            <p className="detail-explanation">
              {job.explanation}
            </p>
          ) : null}
        </section>
      ) : null}

      {job.description ? (
        <section className="detail-section">
          <h4 className="section-title">Description</h4>
          <p className="detail-body">{job.description}</p>
        </section>
      ) : null}

      <div className="detail-actions">
        {job.apply_url ? (
          <Button variant="primary" size="lg" onClick={() => window.open(job.apply_url, '_blank', 'noopener')}>
            Apply →
          </Button>
        ) : null}
        <Button variant="secondary" size="lg" onClick={() => onVerify?.(job)} disabled={verifying}>
          {verifying ? 'Checking…' : 'Check if still live'}
        </Button>
      </div>
    </Modal>
  );
}