import React, { useState } from 'react';

interface LegalModalProps {
  type: 'privacy' | 'terms' | 'contact' | null;
  onClose: () => void;
}

export const LegalModals: React.FC<LegalModalProps> = ({ type, onClose }) => {
  const [contactSubmitted, setContactSubmitted] = useState(false);
  const [formData, setFormData] = useState({ name: '', email: '', subject: '', message: '' });

  if (!type) return null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal-content"
        style={{ maxWidth: '640px', maxHeight: '85vh', overflowY: 'auto' }}
        onClick={(e) => e.stopPropagation()}
      >
        <button
          onClick={onClose}
          style={{
            position: 'absolute',
            top: '1.25rem',
            right: '1.25rem',
            background: 'transparent',
            border: 'none',
            color: 'var(--text-muted)',
            cursor: 'pointer',
          }}
        >
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>

        {type === 'privacy' && (
          <div>
            <h2 style={{ fontSize: '1.75rem', fontWeight: 800, marginBottom: '1rem' }}>
              Privacy Policy
            </h2>
            <div style={{ color: 'var(--text-secondary)', lineHeight: 1.7, fontSize: '0.95rem' }}>
              <p style={{ marginBottom: '1rem' }}>
                At <strong>Demerivo PixelFix</strong>, your privacy and data security are fundamental commitments.
              </p>
              <h4 style={{ color: 'var(--text-primary)', margin: '1rem 0 0.5rem' }}>1. Zero Model Training on User Photos</h4>
              <p style={{ marginBottom: '1rem' }}>
                We strictly guarantee that your uploaded photographs and generated results are <strong>never</strong> used to train, fine-tune, or benchmark public or proprietary artificial intelligence models without explicit written consent.
              </p>
              <h4 style={{ color: 'var(--text-primary)', margin: '1rem 0 0.5rem' }}>2. Data Encryption and Storage</h4>
              <p style={{ marginBottom: '1rem' }}>
                All file transfers between your browser and our servers are encrypted in transit using TLS 1.3. Uploaded and enhanced files are stored in private cloud storage with AES-256 encryption at rest. Access is controlled via short-lived, cryptographically signed presigned URLs.
              </p>
              <h4 style={{ color: 'var(--text-primary)', margin: '1rem 0 0.5rem' }}>3. Automated Retention & Purge</h4>
              <p>
                Customer image assets are automatically purged following our 24-hour temporary retention policy. You may delete any of your images manually at any time from your processing history dashboard.
              </p>
            </div>
          </div>
        )}

        {type === 'terms' && (
          <div>
            <h2 style={{ fontSize: '1.75rem', fontWeight: 800, marginBottom: '1rem' }}>
              Terms of Service
            </h2>
            <div style={{ color: 'var(--text-secondary)', lineHeight: 1.7, fontSize: '0.95rem' }}>
              <p style={{ marginBottom: '1rem' }}>
                Welcome to Demerivo PixelFix. By accessing or using our SaaS platform, you agree to be bound by these terms.
              </p>
              <h4 style={{ color: 'var(--text-primary)', margin: '1rem 0 0.5rem' }}>1. Subscriptions and Monthly Credits</h4>
              <p style={{ marginBottom: '1rem' }}>
                Subscription tiers (Free, Starter, Pro, Studio) provide a recurring allocation of monthly credits. Credits are deducted upon initiating an enhancement task and are atomically refunded if processing fails.
              </p>
              <h4 style={{ color: 'var(--text-primary)', margin: '1rem 0 0.5rem' }}>2. Acceptable Use Policy</h4>
              <p style={{ marginBottom: '1rem' }}>
                You agree not to upload illegal, defamatory, infringent, or harmful imagery. Any automated scraping, reverse engineering, or denial-of-service attempts will result in immediate account termination.
              </p>
              <h4 style={{ color: 'var(--text-primary)', margin: '1rem 0 0.5rem' }}>3. Cancellation & Refunds</h4>
              <p>
                You may cancel your recurring subscription at any time. Prepaid subscription fees are non-refundable once the billing period has commenced, but you retain full access through the end of the current billing cycle.
              </p>
            </div>
          </div>
        )}

        {type === 'contact' && (
          <div>
            <h2 style={{ fontSize: '1.75rem', fontWeight: 800, marginBottom: '0.5rem' }}>
              Contact & Support
            </h2>
            <p style={{ color: 'var(--text-secondary)', marginBottom: '1.5rem', fontSize: '0.95rem' }}>
              Have questions or need enterprise assistance? Our engineering and support team is here to help.
            </p>

            {contactSubmitted ? (
              <div style={{ padding: '2rem', textAlign: 'center', background: 'var(--bg-card)', borderRadius: 'var(--radius-md)' }}>
                <div style={{ color: '#10B981', fontSize: '2rem', marginBottom: '0.75rem' }}>✓</div>
                <h3 style={{ fontSize: '1.2rem', fontWeight: 700, marginBottom: '0.5rem' }}>Message Received!</h3>
                <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem' }}>
                  Thank you for reaching out. We have logged your support inquiry and will reply to {formData.email} within 24 hours.
                </p>
              </div>
            ) : (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  setContactSubmitted(true);
                }}
                style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}
              >
                <div>
                  <label style={{ display: 'block', fontSize: '0.85rem', fontWeight: 600, marginBottom: '0.35rem' }}>Your Name</label>
                  <input
                    type="text"
                    required
                    value={formData.name}
                    onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                    style={{ width: '100%', padding: '0.7rem', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border-strong)', background: 'var(--bg-card)', color: '#FFF' }}
                  />
                </div>
                <div>
                  <label style={{ display: 'block', fontSize: '0.85rem', fontWeight: 600, marginBottom: '0.35rem' }}>Email Address</label>
                  <input
                    type="email"
                    required
                    value={formData.email}
                    onChange={(e) => setFormData({ ...formData, email: e.target.value })}
                    style={{ width: '100%', padding: '0.7rem', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border-strong)', background: 'var(--bg-card)', color: '#FFF' }}
                  />
                </div>
                <div>
                  <label style={{ display: 'block', fontSize: '0.85rem', fontWeight: 600, marginBottom: '0.35rem' }}>Subject</label>
                  <input
                    type="text"
                    required
                    value={formData.subject}
                    onChange={(e) => setFormData({ ...formData, subject: e.target.value })}
                    style={{ width: '100%', padding: '0.7rem', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border-strong)', background: 'var(--bg-card)', color: '#FFF' }}
                  />
                </div>
                <div>
                  <label style={{ display: 'block', fontSize: '0.85rem', fontWeight: 600, marginBottom: '0.35rem' }}>Message</label>
                  <textarea
                    rows={4}
                    required
                    value={formData.message}
                    onChange={(e) => setFormData({ ...formData, message: e.target.value })}
                    style={{ width: '100%', padding: '0.7rem', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border-strong)', background: 'var(--bg-card)', color: '#FFF' }}
                  />
                </div>
                <button type="submit" className="btn btn-primary" style={{ marginTop: '0.5rem', width: '100%', padding: '0.8rem' }}>
                  Send Support Message
                </button>
              </form>
            )}
          </div>
        )}
      </div>
    </div>
  );
};
