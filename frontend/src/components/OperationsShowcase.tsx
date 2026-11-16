import React from 'react';

const OPERATIONS = [
  {
    key: 'upscale_2x',
    name: '2x Super Resolution',
    credits: 2,
    badge: 'Popular',
    desc: 'Doubles linear dimensions with enhanced texture reconstruction.',
    resolution: 'Up to 4096 x 4096 px',
  },
  {
    key: 'upscale_4x',
    name: '4x Ultra Resolution',
    credits: 3,
    badge: 'High Detail',
    desc: 'Quadruples dimensions, transforming 1080p shots into print-ready 4K.',
    resolution: 'Up to 4096 x 4096 px',
  },
  {
    key: 'bg_remove',
    name: 'Background Isolation',
    credits: 1,
    badge: 'Essential',
    desc: 'Extract subjects with fine edge alpha masks or solid studio backdrops.',
    resolution: 'Up to 4096 x 4096 px',
  },
  {
    key: 'enhance',
    name: 'Perceptual Enhancement',
    credits: 2,
    badge: 'Smart Clarity',
    desc: 'Bilateral filtering and unsharp masking to pop contrast and micro-details.',
    resolution: 'Up to 4096 x 4096 px',
  },
  {
    key: 'denoise',
    name: 'Low-Light Denoise',
    credits: 2,
    badge: 'Noise Removal',
    desc: 'Clean ISO luminance and chroma noise from night photos and vintage film.',
    resolution: 'Up to 4096 x 4096 px',
  },
  {
    key: 'deblur',
    name: 'Motion & Focus Deblur',
    credits: 2,
    badge: 'Sharpness',
    desc: 'Laplacian deconvolution to recover soft camera shake and focus drift.',
    resolution: 'Up to 4096 x 4096 px',
  },
  {
    key: 'colorize',
    name: 'B&W Photo Colorization',
    credits: 3,
    badge: 'Heritage',
    desc: 'Generates authentic chromatic spectrums for monochrome archives.',
    resolution: 'Up to 4096 x 4096 px',
  },
  {
    key: 'inpaint',
    name: 'Contextual Inpainting',
    credits: 2,
    badge: 'Object Erase',
    desc: 'Fills masked defects, dust, and tears using boundary texture interpolation.',
    resolution: 'Up to 4096 x 4096 px',
  },
];

export const OperationsShowcase: React.FC = () => {
  return (
    <section id="operations" style={{ padding: '5rem 0', background: 'var(--bg-canvas)' }}>
      <div className="container">
        <div style={{ textAlign: 'center', marginBottom: '3.5rem' }}>
          <span className="badge badge-primary" style={{ marginBottom: '1rem' }}>
            Supported Operations
          </span>
          <h2 style={{ fontSize: 'clamp(2rem, 3.5vw, 2.75rem)', fontWeight: 800, letterSpacing: '-0.02em', marginBottom: '1rem' }}>
            Commercially Cleared AI Capabilities
          </h2>
          <p style={{ color: 'var(--text-secondary)', maxWidth: '620px', margin: '0 auto', fontSize: '1.05rem' }}>
            Every operation is transparently priced in credits and backed by commercially cleared licenses for safe personal and enterprise use.
          </p>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '1.5rem' }}>
          {OPERATIONS.map((op) => (
            <div
              key={op.key}
              className="glass-panel"
              style={{
                padding: '1.75rem',
                display: 'flex',
                flexDirection: 'column',
                justifyContent: 'space-between',
              }}
            >
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '1rem' }}>
                  <span className="badge badge-primary">{op.badge}</span>
                  <div
                    style={{
                      background: 'rgba(99, 102, 241, 0.1)',
                      color: '#A5B4FC',
                      padding: '0.2rem 0.6rem',
                      borderRadius: 'var(--radius-sm)',
                      fontWeight: 700,
                      fontSize: '0.8rem',
                    }}
                  >
                    {op.credits} {op.credits === 1 ? 'Credit' : 'Credits'}
                  </div>
                </div>
                <h3 style={{ fontSize: '1.15rem', fontWeight: 700, marginBottom: '0.5rem' }}>
                  {op.name}
                </h3>
                <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', lineHeight: 1.5, marginBottom: '1.25rem' }}>
                  {op.desc}
                </p>
              </div>

              <div style={{ borderTop: '1px solid var(--border-subtle)', paddingTop: '0.75rem', fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                <span>Max: {op.resolution}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
};
