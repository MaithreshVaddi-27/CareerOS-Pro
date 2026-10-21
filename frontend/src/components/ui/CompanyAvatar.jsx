import { useState } from 'react';

/**
 * CompanyAvatar — professional company picture for a job card / detail.
 *
 * Renders a company logo image when `logoUrl` is provided (with an initials
 * fallback if the image fails to load), otherwise a deterministic coloured
 * initials avatar — no external dependency, no broken-image boxes.
 */
export default function CompanyAvatar({ company, size = 44, logoUrl }) {
  const [hasLogo, setHasLogo] = useState(Boolean(logoUrl));
  const name = company || 'Unknown';
  const init = name
    .split(/\s+/)
    .map((w) => w.charAt(0).toUpperCase())
    .slice(0, 2)
    .join('') || '?';

  // Deterministic hue from the company name so the same company always gets
  // the same brand colour, without a lookup table.
  let h = 0;
  for (let i = 0; i < name.length; i++) {
    h = ((h << 5) - h + name.charCodeAt(i)) | 0;
  }
  const hue = Math.abs(h) % 360;
  const bg = `hsl(${hue}, 62%, 56%)`;
  const fs = Math.max(10, Math.round(size * 0.34));

  return (
    <div
      className="company-avatar"
      style={{ width: size, height: size }}
      aria-label={name}
      title={name}
    >
      {logoUrl && hasLogo ? (
        <img
          className="company-avatar__img"
          src={logoUrl}
          alt=""
          width={size}
          height={size}
          onError={() => setHasLogo(false)}
        />
      ) : (
        <div
          className="company-avatar__initials"
          style={{ background: bg, fontSize: fs }}
        >
          {init}
        </div>
      )}
    </div>
  );
}
