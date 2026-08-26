import { motion, useMotionValue, useTransform } from 'framer-motion';
import { useState, useEffect } from 'react';
import Tag from '../ui/Tag';
import { useDragVelocity } from '../../hooks/useDragVelocity';
import { project } from '../../utils/appleDesign';
import { formatLabel } from '../../utils/format';

/**
 * JobCard — a single job/internship posting.
 * Premium dark glass card with:
 *  - Swipe-right-to-save gesture with momentum projection (§6) and rubber-band (§9).
 *  - Instant press feedback (§1).
 *  - Match-score ring filled with accent.
 *  - Source / employment-type / location tags.
 */
function timeAgo(postedAt) {
  if (!postedAt) return '';
  const d = new Date(postedAt);
  if (isNaN(d)) return '';
  const diffH = (Date.now() - d.getTime()) / 36e5;
  if (diffH < 1) return 'just now';
  if (diffH < 24) return `${Math.floor(diffH)}h ago`;
  if (diffH < 168) return `${Math.floor(diffH / 24)}d ago`;
  return `${Math.floor(diffH / 24 / 7)}w ago`;
}

function MatchRing({ score }) {
  const pct = Math.round((score || 0) * 100);
  const r = 18, c = 2 * Math.PI * r;
  return (
    <div style={{ position: 'relative', width: 44, height: 44, flexShrink: 0 }}>
      <svg width="44" height="44" viewBox="0 0 44 44">
        <circle cx="22" cy="22" r={r} fill="none" stroke="var(--color-border-strong)" strokeWidth="4" />
        <circle
          cx="22" cy="22" r={r} fill="none"
          stroke="var(--color-accent)" strokeWidth="4" strokeLinecap="round"
          strokeDasharray={c} strokeDashoffset={c - (c * pct) / 100}
          transform="rotate(-90 22 22)"
          style={{ transition: 'stroke-dashoffset 0.6s cubic-bezier(0.25,0.1,0.25,1)' }}
        />
      </svg>
      <span style={{
        position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: '0.7rem', fontWeight: 600, color: 'var(--color-text)',
      }}>{pct}</span>
    </div>
  );
}

export default function JobCard({ job, onOpen, onSave, saved = false }) {
  const isIntern = /intern/i.test(job.employment_type || '') || /intern/i.test(job.title || '');
  const x = useMotionValue(0);
  const saveOpacity = useTransform(x, [0, 120], [0, 1]);
  const [cardSaved, setCardSaved] = useState(saved);

  // Keep local state in sync with external `saved` prop
  useEffect(() => {
    setCardSaved(saved);
  }, [saved]);

  const onRelease = (offset, velocity) => {
    // Project momentum forward (§6), decide save vs snap back
    const projected = offset + project(velocity, 0.99) * 0.5;
    if (projected > 80 && !cardSaved) {
      setCardSaved(true);
      onSave?.(job);
    }
    // Spring back to rest — velocity carried by framer (§5)
    x.set(0);
  };

  const { onPointerDown, onPointerMove, onPointerUp } = useDragVelocity({ onRelease, axis: 'x' });

  return (
    <motion.div
      className="job-card"
      tabIndex={0}
      role="button"
      aria-label={job.title ? `View details for ${job.title}` : 'View job details'}
      style={{ x }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerLeave={onPointerUp}
      initial={false}
      transition={{ type: 'spring', bounce: 0.2, duration: 0.4 }}
      onClick={() => onOpen?.(job)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          e.stopPropagation();
          onOpen?.(job);
        }
      }}
    >
      {/* "Saved" hint revealed as the card is swiped */}
      <motion.div
        style={{ opacity: saveOpacity }}
        aria-hidden="true"
      >
        <div style={{
          position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'flex-end',
          paddingRight: 'var(--space-5)', color: 'var(--color-green)', fontWeight: 600, pointerEvents: 'none',
        }}>
          Save ✓
        </div>
      </motion.div>

      <div className="glass-card" style={{
        borderRadius: 'var(--r-3)',
        padding: 'var(--space-5)',
        cursor: 'pointer',
        position: 'relative',
        overflow: 'hidden',
      }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 'var(--space-4)' }}>
          {/* Company monogram */}
          <div
            style={{
              width: 44, height: 44, borderRadius: 'var(--r-3)', flexShrink: 0,
              background: 'var(--accent-soft)', color: 'var(--accent-1)',
              border: '1px solid var(--accent-soft)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontWeight: 700, fontSize: '1.05rem',
            }}
          >
            {(job.company || '?').slice(0, 1).toUpperCase()}
          </div>

          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 'var(--space-3)' }}>
              <div style={{ minWidth: 0 }}>
                <h3 style={{
                  fontSize: '1.0625rem', fontWeight: 600, letterSpacing: '-0.01em',
                  lineHeight: 1.3, color: 'var(--color-text)',
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}>{job.title}</h3>
                <p style={{ fontSize: '0.9375rem', color: 'var(--color-text-secondary)', marginTop: 2 }}>
                  {job.company || 'Unknown'}
                </p>
              </div>
              {typeof job.match_score === 'number' && <MatchRing score={job.match_score} />}
            </div>

            {/* Tag row */}
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-2)', marginTop: 'var(--space-3)' }}>
              <Tag color={isIntern ? 'purple' : 'blue'} dot>
                {isIntern ? 'Internship' : job.employment_type || 'Full-time'}
              </Tag>
              {job.experience_level && <Tag>{formatLabel(job.experience_level)}</Tag>}
              {job.is_remote && <Tag color="green" dot>Remote</Tag>}
              {job.location && job.location !== 'Unknown' && <Tag>{job.location}</Tag>}
              {job.salary && <Tag color="orange">{job.salary}</Tag>}
              {job.source && <Tag color="teal">{job.source}</Tag>}
            </div>

            {/* Footer */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 'var(--space-4)', paddingTop: 'var(--space-3)', borderTop: '1px solid var(--color-border)' }}>
              <span style={{ fontSize: '0.8125rem', color: 'var(--color-text-faint)' }}>
                {timeAgo(job.posted_at)}
              </span>
              <motion.span
                initial={false}
                whileTap={{ scale: 0.9 }}
                whileHover={{ scale: 1.02 }}
                onClick={(e) => { e.stopPropagation(); setCardSaved(!cardSaved); onSave?.(job); }}
                style={{
                  fontSize: '1.25rem', lineHeight: 1, cursor: 'pointer',
                  color: cardSaved ? 'var(--color-pink)' : 'var(--color-text-faint)',
                }}
                role="button"
                tabIndex={0}
                aria-label={cardSaved ? 'Unsave' : 'Save'}
                aria-pressed={cardSaved}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    e.stopPropagation();
                    setCardSaved(!cardSaved);
                    onSave?.(job);
                  }
                }}
              >
                {cardSaved ? '♥' : '♡'}
              </motion.span>
            </div>
          </div>
        </div>
      </div>
    </motion.div>
  );
}