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
    <div className="match-ring">
      <svg width="44" height="44" viewBox="0 0 44 44">
        <circle cx="22" cy="22" r={r} fill="none" stroke="var(--color-border-strong)" strokeWidth="4" />
        <circle
          cx="22" cy="22" r={r} fill="none"
          stroke="var(--color-accent)" strokeWidth="4" strokeLinecap="round"
          strokeDasharray={c} strokeDashoffset={c - (c * pct) / 100}
          transform="rotate(-90 22 22)"
          className="match-ring__arc"
        />
      </svg>
      <span className="match-ring__value">{pct}</span>
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
        <div className="jobcard-save-hint">
          Save ✓
        </div>
      </motion.div>

      <div className="glass-card jobcard-body">
        <div className="jobcard-head">
          {/* Company monogram */}
          <div className="jobcard-monogram">
            {(job.company || '?').slice(0, 1).toUpperCase()}
          </div>

          <div className="jobcard-main">
            <div className="jobcard-titlerow">
              <div className="jobcard-titlebox">
                <h3 className="jobcard-title">{job.title}</h3>
                <p className="jobcard-company">
                  {job.company || 'Unknown'}
                </p>
              </div>
              {typeof job.match_score === 'number' && <MatchRing score={job.match_score} />}
            </div>

            {/* Tag row */}
            <div className="jobcard-tags">
              <Tag color={isIntern ? 'sky' : 'blue'} dot>
                {isIntern ? 'Internship' : job.employment_type || 'Full-time'}
              </Tag>
              {job.experience_level && <Tag>{formatLabel(job.experience_level)}</Tag>}
              {job.is_remote && <Tag color="green" dot>Remote</Tag>}
              {job.location && job.location !== 'Unknown' && <Tag>{job.location}</Tag>}
              {job.salary && <Tag color="orange">{job.salary}</Tag>}
              {job.source && <Tag color="teal">{job.source}</Tag>}
            </div>

            {/* Footer */}
            <div className="jobcard-footer">
              <span className="jobcard-time">
                {timeAgo(job.posted_at)}
              </span>
              <motion.span
                initial={false}
                whileTap={{ scale: 0.9 }}
                whileHover={{ scale: 1.02 }}
                onClick={(e) => { e.stopPropagation(); setCardSaved(!cardSaved); onSave?.(job); }}
                className={`jobcard-save${cardSaved ? ' is-saved' : ''}`}
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