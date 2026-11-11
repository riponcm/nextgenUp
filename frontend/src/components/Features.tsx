import React from 'react';

const FEATURES = [
  {
    title: '4x AI Super Resolution',
    description: 'Enlarge small, grainy images up to 400% with sharp edges, recovered textures, and zero pixelation.',
    icon: (
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#818CF8" strokeWidth="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/><line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/></svg>
    ),
  },
  {
    title: 'Precise Background Removal',
    description: 'Isolate subjects with pixel-perfect hair and boundary detection. Export with transparent alpha or solid color fills.',
    icon: (
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#C084FC" strokeWidth="2"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
    ),
  },
  {
    title: 'Low-Light Denoising',
    description: 'Remove sensor noise, ISO grain, and digital artifacts while protecting vital micro-contrast and subject focus.',
    icon: (
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#60A5FA" strokeWidth="2"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>
    ),
  },
  {
    title: 'Intelligent Deblurring',
    description: 'Restore accidental camera shake, motion blur, and out-of-focus optics with multi-scale deep deconvolution.',
    icon: (
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#34D399" strokeWidth="2"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/></svg>
    ),
  },
  {
    title: 'Vibrant Photo Colorization',
    description: 'Infuse vintage black & white family photographs and historical archives with realistic, culturally accurate color palettes.',
    icon: (
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#F472B6" strokeWidth="2"><circle cx="12" cy="12" r="10"/><path d="M14.31 8l5.74 9.94M9.69 8h11.48M7.38 12l5.74-9.94M9.69 16L3.95 6.06M14.31 16H2.83m13.79-4l-5.74 9.94"/></svg>
    ),
  },
  {
    title: 'Smart Object Inpainting',
    description: 'Seamlessly erase watermarks, scratches, blemishes, and unwanted objects with contextual texture synthesis.',
    icon: (
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#FBBF24" strokeWidth="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
    ),
  },
];

export const Features: React.FC = () => {
  return (
    <section id="features" style={{ padding: '5rem 0', background: 'var(--bg-canvas)' }}>
      <div className="container">
        <div style={{ textAlign: 'center', marginBottom: '3.5rem' }}>
          <span className="badge badge-primary" style={{ marginBottom: '1rem' }}>
            Core Capabilities
          </span>
          <h2 style={{ fontSize: 'clamp(2rem, 3.5vw, 2.75rem)', fontWeight: 800, letterSpacing: '-0.02em', marginBottom: '1rem' }}>
            Built for High-Fidelity Image Restoration
          </h2>
          <p style={{ color: 'var(--text-secondary)', maxWidth: '600px', margin: '0 auto', fontSize: '1.05rem' }}>
            Every AI pipeline is tuned for maximum perceptual fidelity, preserving natural textures rather than synthetic plastic artifacts.
          </p>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '1.75rem' }}>
          {FEATURES.map((feat, idx) => (
            <div
              key={idx}
              className="glass-panel"
              style={{
                padding: '2rem',
                transition: 'transform var(--transition-normal), border-color var(--transition-normal)',
                cursor: 'default',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.transform = 'translateY(-4px)';
                e.currentTarget.style.borderColor = 'var(--border-strong)';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.transform = 'translateY(0)';
                e.currentTarget.style.borderColor = 'var(--border-subtle)';
              }}
            >
              <div
                style={{
                  width: '3rem',
                  height: '3rem',
                  borderRadius: 'var(--radius-md)',
                  background: 'var(--bg-card)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  marginBottom: '1.25rem',
                  border: '1px solid var(--border-subtle)',
                }}
              >
                {feat.icon}
              </div>
              <h3 style={{ fontSize: '1.25rem', fontWeight: 700, marginBottom: '0.75rem' }}>
                {feat.title}
              </h3>
              <p style={{ color: 'var(--text-secondary)', fontSize: '0.95rem', lineHeight: 1.6 }}>
                {feat.description}
              </p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
};
