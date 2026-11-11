import React, { useState } from 'react';
import { Plan } from '../types';

interface PricingTableProps {
  plans: Plan[];
  currentPlanCode?: string;
  onSelectPlan: (planCode: string) => void;
  isAuthenticated: boolean;
}

export const PricingTable: React.FC<PricingTableProps> = ({
  plans,
  currentPlanCode = 'free',
  onSelectPlan,
  isAuthenticated,
}) => {
  const [isAnnual, setIsAnnual] = useState(false);

  // If plans haven't loaded yet from backend, provide standard fallback catalog
  const displayPlans = plans.length > 0 ? plans : [
    {
      code: 'free',
      name: 'Free Explorer',
      description: 'Test out our AI models with free starter credits.',
      price_paise: 0,
      price_inr: 0,
      monthly_credits: 5,
      currency: 'INR',
      features: ['5 Monthly Credits', 'Upscale 2x & 4x', 'Background Removal', 'Standard Queue', 'Community Support'],
    },
    {
      code: 'starter',
      name: 'Starter Creator',
      description: 'Essential toolkit for content creators and photo lovers.',
      price_paise: 19900,
      price_inr: 199,
      monthly_credits: 40,
      currency: 'INR',
      features: ['40 Monthly Credits', 'All Enhancement Operations', 'Solid & Alpha BG Replacement', 'Priority Queue', 'Email Support'],
    },
    {
      code: 'pro',
      name: 'Pro Photographer',
      description: 'Full creative freedom for photographers and digital designers.',
      price_paise: 49900,
      price_inr: 499,
      monthly_credits: 150,
      currency: 'INR',
      features: ['150 Monthly Credits', 'All Operations + Colorization', 'High-Throughput Concurrency', 'Ultra HD 4K Output', 'Priority Support'],
    },
    {
      code: 'studio',
      name: 'Studio Enterprise',
      description: 'High-volume horsepower for studios, agencies, and teams.',
      price_paise: 99900,
      price_inr: 999,
      monthly_credits: 400,
      currency: 'INR',
      features: ['400 Monthly Credits', 'Max Concurrent Execution', 'Batch Enhancement Workflows', 'Commercial Usage License', 'Dedicated Support'],
    },
  ];

  return (
    <section id="pricing" style={{ padding: '5rem 0', background: 'var(--bg-surface)', borderTop: '1px solid var(--border-subtle)', borderBottom: '1px solid var(--border-subtle)' }}>
      <div className="container">
        <div style={{ textAlign: 'center', marginBottom: '3.5rem' }}>
          <span className="badge badge-primary" style={{ marginBottom: '1rem' }}>
            Flexible Subscription Plans
          </span>
          <h2 style={{ fontSize: 'clamp(2rem, 3.5vw, 2.75rem)', fontWeight: 800, letterSpacing: '-0.02em', marginBottom: '1rem' }}>
            Simple, Transparent Pricing
          </h2>
          <p style={{ color: 'var(--text-secondary)', maxWidth: '580px', margin: '0 auto 2rem', fontSize: '1.05rem' }}>
            Pay only for what you process. Upgrade, downgrade, or cancel anytime with zero lock-in contracts.
          </p>

          {/* Billing Cycle Switch */}
          <div
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              background: 'var(--bg-card)',
              padding: '0.35rem',
              borderRadius: 'var(--radius-full)',
              border: '1px solid var(--border-subtle)',
            }}
          >
            <button
              onClick={() => setIsAnnual(false)}
              style={{
                background: !isAnnual ? 'var(--primary)' : 'transparent',
                color: !isAnnual ? '#FFFFFF' : 'var(--text-secondary)',
                border: 'none',
                padding: '0.45rem 1.25rem',
                borderRadius: 'var(--radius-full)',
                fontWeight: 600,
                fontSize: '0.85rem',
                cursor: 'pointer',
                transition: 'all var(--transition-fast)',
              }}
            >
              Monthly Billing
            </button>
            <button
              onClick={() => setIsAnnual(true)}
              style={{
                background: isAnnual ? 'var(--primary)' : 'transparent',
                color: isAnnual ? '#FFFFFF' : 'var(--text-secondary)',
                border: 'none',
                padding: '0.45rem 1.25rem',
                borderRadius: 'var(--radius-full)',
                fontWeight: 600,
                fontSize: '0.85rem',
                cursor: 'pointer',
                transition: 'all var(--transition-fast)',
                display: 'flex',
                alignItems: 'center',
                gap: '0.4rem',
              }}
            >
              Annual Billing
              <span style={{ fontSize: '0.7rem', background: '#10B981', color: '#090D16', padding: '0.1rem 0.4rem', borderRadius: '4px', fontWeight: 800 }}>
                SAVE 20%
              </span>
            </button>
          </div>
        </div>

        {/* Pricing Cards Grid */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '1.75rem', alignItems: 'stretch' }}>
          {displayPlans.map((plan) => {
            const isCurrent = currentPlanCode === plan.code;
            const isPopular = plan.code === 'pro';
            const price = isAnnual && plan.price_inr > 0 ? Math.round(plan.price_inr * 0.8) : plan.price_inr;

            return (
              <div
                key={plan.code}
                className="glass-panel"
                style={{
                  padding: '2.25rem 2rem',
                  display: 'flex',
                  flexDirection: 'column',
                  justifyContent: 'space-between',
                  position: 'relative',
                  border: isPopular ? '2px solid #818CF8' : '1px solid var(--border-subtle)',
                  background: isPopular ? 'linear-gradient(180deg, rgba(99, 102, 241, 0.08) 0%, var(--bg-card) 100%)' : 'var(--bg-card)',
                  transform: isPopular ? 'scale(1.02)' : 'none',
                }}
              >
                {isPopular && (
                  <div
                    style={{
                      position: 'absolute',
                      top: '-12px',
                      left: '50%',
                      transform: 'translateX(-50%)',
                      background: 'linear-gradient(135deg, #6366F1 0%, #8B5CF6 100%)',
                      color: '#FFF',
                      fontSize: '0.75rem',
                      fontWeight: 800,
                      padding: '0.25rem 0.85rem',
                      borderRadius: 'var(--radius-full)',
                      letterSpacing: '0.05em',
                      textTransform: 'uppercase',
                    }}
                  >
                    Most Popular
                  </div>
                )}

                <div>
                  <h3 style={{ fontSize: '1.35rem', fontWeight: 800, marginBottom: '0.5rem' }}>
                    {plan.name}
                  </h3>
                  <p style={{ color: 'var(--text-secondary)', fontSize: '0.875rem', lineHeight: 1.5, minHeight: '40px', marginBottom: '1.5rem' }}>
                    {plan.description}
                  </p>

                  <div style={{ marginBottom: '1.75rem' }}>
                    <span style={{ fontSize: '2.5rem', fontWeight: 800, letterSpacing: '-0.02em' }}>
                      ₹{price}
                    </span>
                    <span style={{ color: 'var(--text-muted)', fontSize: '0.9rem', marginLeft: '0.35rem' }}>
                      / month
                    </span>
                    <div style={{ fontSize: '0.85rem', color: '#818CF8', fontWeight: 600, marginTop: '0.25rem' }}>
                      {plan.monthly_credits} Monthly Credits Included
                    </div>
                  </div>

                  {/* Feature Checklist */}
                  <ul style={{ listStyle: 'none', display: 'flex', flexDirection: 'column', gap: '0.75rem', marginBottom: '2rem' }}>
                    {plan.features.map((feat, fIdx) => (
                      <li key={fIdx} style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', fontSize: '0.9rem', color: 'var(--text-secondary)' }}>
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#10B981" strokeWidth="2.5"><polyline points="20 6 9 17 4 12"/></svg>
                        <span>{feat}</span>
                      </li>
                    ))}
                  </ul>
                </div>

                <button
                  className={`btn ${isPopular ? 'btn-primary' : 'btn-secondary'}`}
                  style={{ width: '100%', padding: '0.85rem', fontWeight: 700 }}
                  disabled={isCurrent}
                  onClick={() => onSelectPlan(plan.code)}
                >
                  {isCurrent ? 'Current Plan' : plan.price_inr === 0 ? 'Get Started' : `Upgrade to ${plan.name.split(' ')[0]}`}
                </button>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
};
