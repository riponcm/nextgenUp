import React, { useState } from 'react';

const FAQS = [
  {
    q: 'How does the credit system work?',
    a: 'Each AI operation consumes a fixed number of credits based on algorithmic complexity (for example, Background Removal is 1 credit, 2x Upscale is 2 credits, 4x Upscale is 3 credits). Monthly subscription credits replenish automatically on your renewal billing date.',
  },
  {
    q: 'What image formats and resolutions are supported?',
    a: 'Demerivo PixelFix supports JPG, PNG, WebP, BMP, and TIFF files up to 20 MB per file, with dimensions up to 4096 x 4096 pixels.',
  },
  {
    q: 'Can I use enhanced images for commercial client work?',
    a: 'Yes. All models in our production pipeline (Real-ESRGAN, BiRefNet, NAFNet, SCUNet, DDColor, LaMa) are commercially cleared. Face restoration models with non-commercial dataset restrictions are intentionally disabled to guarantee 100% legal compliance for our business customers.',
  },
  {
    q: 'What happens if an AI enhancement fails?',
    a: 'If any job encounters an unrecoverable error or worker timeout, our atomic wallet service automatically refunds the reserved credits back to your balance immediately.',
  },
  {
    q: 'How are my uploaded photos protected?',
    a: 'Photos are stored securely in encrypted private cloud storage, accessible exclusively through temporary presigned URLs. Your images are never used to train public AI models and are purged automatically according to our retention policy.',
  },
  {
    q: 'Can I cancel or switch subscription plans anytime?',
    a: 'Yes. You can cancel your subscription from your dashboard with one click. When you cancel, you retain your active benefits and remaining credits until the end of your prepaid billing period.',
  },
];

export const FAQ: React.FC = () => {
  const [openIdx, setOpenIdx] = useState<number | null>(0);

  return (
    <section id="faq" style={{ padding: '5rem 0', background: 'var(--bg-canvas)' }}>
      <div className="container" style={{ maxWidth: '840px' }}>
        <div style={{ textAlign: 'center', marginBottom: '3.5rem' }}>
          <span className="badge badge-primary" style={{ marginBottom: '1rem' }}>
            Got Questions?
          </span>
          <h2 style={{ fontSize: 'clamp(2rem, 3.5vw, 2.75rem)', fontWeight: 800, letterSpacing: '-0.02em', marginBottom: '1rem' }}>
            Frequently Asked Questions
          </h2>
          <p style={{ color: 'var(--text-secondary)', fontSize: '1.05rem' }}>
            Everything you need to know about processing, credits, commercial usage, and security.
          </p>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          {FAQS.map((faq, idx) => {
            const isOpen = openIdx === idx;
            return (
              <div
                key={idx}
                className="glass-panel"
                style={{
                  padding: '1.5rem 1.75rem',
                  cursor: 'pointer',
                  transition: 'background var(--transition-fast)',
                }}
                onClick={() => setOpenIdx(isOpen ? null : idx)}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '1rem' }}>
                  <h3 style={{ fontSize: '1.1rem', fontWeight: 700, margin: 0 }}>
                    {faq.q}
                  </h3>
                  <svg
                    width="20"
                    height="20"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    style={{
                      transform: isOpen ? 'rotate(180deg)' : 'rotate(0deg)',
                      transition: 'transform var(--transition-fast)',
                      flexShrink: 0,
                      color: '#818CF8',
                    }}
                  >
                    <polyline points="6 9 12 15 18 9"></polyline>
                  </svg>
                </div>

                {isOpen && (
                  <p style={{ marginTop: '1rem', color: 'var(--text-secondary)', lineHeight: 1.6, fontSize: '0.95rem' }}>
                    {faq.a}
                  </p>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
};
