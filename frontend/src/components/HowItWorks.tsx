import React from 'react';

const STEPS = [
  {
    step: '01',
    title: 'Drop Your Photos',
    description: 'Upload any standard format (JPG, PNG, WebP) directly from desktop, tablet, or your smartphone camera roll.',
  },
  {
    step: '02',
    title: 'Choose AI Transformation',
    description: 'Select Super Resolution, Background Isolation, Denoise, or Colorization with real-time credit cost visibility.',
  },
  {
    step: '03',
    title: 'Export in Crisp 4K',
    description: 'Preview before/after with our interactive comparison slider and download high-resolution results instantly.',
  },
];

export const HowItWorks: React.FC = () => {
  return (
    <section id="how-it-works" style={{ padding: '5rem 0', background: 'var(--bg-surface)', borderTop: '1px solid var(--border-subtle)', borderBottom: '1px solid var(--border-subtle)' }}>
      <div className="container">
        <div style={{ textAlign: 'center', marginBottom: '3.5rem' }}>
          <span className="badge badge-primary" style={{ marginBottom: '1rem' }}>
            Intuitive Process
          </span>
          <h2 style={{ fontSize: 'clamp(2rem, 3.5vw, 2.75rem)', fontWeight: 800, letterSpacing: '-0.02em', marginBottom: '1rem' }}>
            How Demerivo PixelFix Works
          </h2>
          <p style={{ color: 'var(--text-secondary)', maxWidth: '540px', margin: '0 auto', fontSize: '1.05rem' }}>
            Three simple steps to professional image restoration without complex photo-editing software.
          </p>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '2rem' }}>
          {STEPS.map((s, idx) => (
            <div
              key={idx}
              className="glass-panel"
              style={{
                padding: '2.5rem 2rem',
                position: 'relative',
                background: 'var(--bg-card)',
                border: '1px solid var(--border-subtle)',
              }}
            >
              <div
                style={{
                  fontSize: '2rem',
                  fontWeight: 800,
                  fontFamily: 'var(--font-mono)',
                  color: '#818CF8',
                  marginBottom: '1rem',
                }}
              >
                {s.step}
              </div>
              <h3 style={{ fontSize: '1.3rem', fontWeight: 700, marginBottom: '0.75rem' }}>
                {s.title}
              </h3>
              <p style={{ color: 'var(--text-secondary)', fontSize: '0.95rem', lineHeight: 1.6 }}>
                {s.description}
              </p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
};
