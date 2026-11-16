import React from 'react';
import { BeforeAfterSlider } from './BeforeAfterSlider';

interface HeroProps {
  onGetStarted: () => void;
  onExploreFeatures: () => void;
}

// Crisp, high-contrast demo images rendered via data URI for interactive demonstration
const DEMO_BEFORE = `data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="600" height="450" viewBox="0 0 600 450"><rect width="600" height="450" fill="%231E293B"/><filter id="blur"><feGaussianBlur stdDeviation="5"/></filter><g filter="url(%23blur)"><circle cx="300" cy="200" r="100" fill="%2364748B"/><path d="M150 400 Q300 250 450 400" fill="%23475569"/><rect x="250" y="150" width="100" height="40" rx="8" fill="%2394A3B8"/></g><text x="300" y="420" font-family="sans-serif" font-size="18" fill="%2394A3B8" text-anchor="middle" font-weight="bold">Low Resolution & Blurry Original</text></svg>`;

const DEMO_AFTER = `data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="600" height="450" viewBox="0 0 600 450"><rect width="600" height="450" fill="%230F172A"/><defs><linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="%23818CF8"/><stop offset="100%" stop-color="%23C084FC"/></linearGradient></defs><circle cx="300" cy="200" r="100" fill="url(%23g)" stroke="%23FFFFFF" stroke-width="4"/><path d="M150 400 Q300 250 450 400" fill="%234F46E5" stroke="%23A5B4FC" stroke-width="2"/><rect x="250" y="150" width="100" height="40" rx="8" fill="%23FFFFFF"/><text x="300" y="420" font-family="sans-serif" font-size="18" fill="%23A5B4FC" text-anchor="middle" font-weight="bold">AI Enhanced Crystal Clear 4K</text></svg>`;

export const Hero: React.FC<HeroProps> = ({ onGetStarted, onExploreFeatures }) => {
  return (
    <section className="gradient-hero" style={{ padding: '5rem 0 4rem', position: 'relative' }}>
      <div className="container" style={{ textAlign: 'center' }}>
        {/* Release Pill */}
        <div style={{ display: 'inline-flex', marginBottom: '1.5rem' }}>
          <span className="badge badge-primary" style={{ padding: '0.4rem 1rem', fontSize: '0.85rem' }}>
            <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: '#6366F1' }}></span>
            Next-Gen AI Photo Enhancement Suite
          </span>
        </div>

        {/* Hero Title */}
        <h1
          style={{
            fontSize: 'clamp(2.5rem, 5vw, 4.25rem)',
            fontWeight: 800,
            lineHeight: 1.15,
            letterSpacing: '-0.03em',
            maxWidth: '900px',
            margin: '0 auto 1.5rem',
          }}
        >
          Transform Blurry, Low-Res Photos into <span className="gradient-text">Studio-Grade 4K</span>
        </h1>

        {/* Subtitle */}
        <p
          style={{
            fontSize: 'clamp(1.1rem, 2vw, 1.3rem)',
            color: 'var(--text-secondary)',
            maxWidth: '680px',
            margin: '0 auto 2.5rem',
            lineHeight: 1.6,
          }}
        >
          Demerivo PixelFix delivers state-of-the-art AI super-resolution, background isolation,
          noise reduction, and colorization in seconds. Built for photographers, creators, and businesses worldwide.
        </p>

        {/* CTA Buttons */}
        <div style={{ display: 'flex', justifyContent: 'center', gap: '1rem', flexWrap: 'wrap', marginBottom: '3.5rem' }}>
          <button
            className="btn btn-primary"
            style={{ padding: '0.9rem 2rem', fontSize: '1.05rem', borderRadius: 'var(--radius-lg)' }}
            onClick={onGetStarted}
          >
            Start Free (5 Credits Included)
          </button>
          <button
            className="btn btn-secondary"
            style={{ padding: '0.9rem 2rem', fontSize: '1.05rem', borderRadius: 'var(--radius-lg)' }}
            onClick={onExploreFeatures}
          >
            View Live Features
          </button>
        </div>

        {/* Interactive Before/After Showcase */}
        <div
          style={{
            maxWidth: '840px',
            margin: '0 auto',
            boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.7), 0 0 35px rgba(99, 102, 241, 0.25)',
            borderRadius: 'var(--radius-lg)',
            padding: '0.5rem',
            background: 'var(--bg-surface)',
            border: '1px solid var(--border-subtle)',
          }}
        >
          <BeforeAfterSlider
            beforeImage={DEMO_BEFORE}
            afterImage={DEMO_AFTER}
            beforeLabel="Before (Compressed / Blurry)"
            afterLabel="After (Demerivo 4x Upscaled)"
            aspectRatio="16/9"
          />
          <div style={{ marginTop: '0.75rem', fontSize: '0.85rem', color: 'var(--text-muted)' }}>
            Drag the slider horizontally to compare original image vs AI-enhanced output
          </div>
        </div>
      </div>
    </section>
  );
};
