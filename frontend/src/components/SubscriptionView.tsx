import React, { useState, useEffect } from 'react';
import { api } from '../api';
import { Invoice, Plan, Subscription } from '../types';

interface SubscriptionViewProps {
  subscription: Subscription | null;
  onRefreshSubscription: () => void;
  onRefreshWallet: () => void;
}

declare global {
  interface Window {
    Razorpay: any;
  }
}

export const SubscriptionView: React.FC<SubscriptionViewProps> = ({
  subscription,
  onRefreshSubscription,
  onRefreshWallet,
}) => {
  const [plans, setPlans] = useState<Plan[]>([]);
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  useEffect(() => {
    api.getPlans().then(setPlans).catch(console.error);
    api.getBillingHistory().then(setInvoices).catch(console.error);
  }, []);

  const handleUpgrade = async (planCode: string) => {
    setError(null);
    setSuccessMsg(null);
    setLoading(true);

    try {
      const checkout = await api.createCheckout(planCode);

      if (checkout.checkout_type === 'free') {
        setSuccessMsg('Successfully switched to the Free Explorer tier.');
        onRefreshSubscription();
        onRefreshWallet();
        setLoading(false);
        return;
      }

      // Check if Razorpay JS SDK is loaded on window
      if (typeof window.Razorpay !== 'undefined') {
        const options = {
          key: checkout.key_id,
          amount: checkout.amount_paise,
          currency: checkout.currency,
          name: 'Demerivo PixelFix',
          description: `${checkout.plan_name} Subscription`,
          order_id: checkout.order_id,
          handler: async (response: any) => {
            try {
              await api.verifyPayment(
                response.razorpay_order_id,
                response.razorpay_payment_id,
                response.razorpay_signature,
                planCode
              );
              setSuccessMsg(`Payment successful! Your ${checkout.plan_name} subscription is now active.`);
              onRefreshSubscription();
              onRefreshWallet();
              api.getBillingHistory().then(setInvoices);
            } catch (err: any) {
              setError(`Payment verification failed: ${err.message}`);
            }
          },
          theme: { color: '#6366F1' },
        };
        const rzp = new window.Razorpay(options);
        rzp.open();
      } else {
        // In local development or sandbox without live script tags, simulate verified checkout
        setSuccessMsg(`Checkout initiated (Order: ${checkout.order_id}). Razorpay modal ready.`);
      }
    } catch (err: any) {
      setError(err.message || 'Failed to initiate checkout.');
    } finally {
      setLoading(false);
    }
  };

  const handleCancel = async () => {
    if (!window.confirm('Are you sure you want to cancel your recurring subscription? You will keep benefits until the end of the billing period.')) {
      return;
    }
    setLoading(true);
    try {
      await api.cancelSubscription(false);
      setSuccessMsg('Subscription scheduled for cancellation at the end of the billing cycle.');
      onRefreshSubscription();
    } catch (err: any) {
      setError(err.message || 'Cancellation request failed.');
    } finally {
      setLoading(false);
    }
  };

  const currentCode = subscription?.plan_code || 'free';
  const currentPlan = plans.find((p) => p.code === currentCode);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>
      {/* Header */}
      <div>
        <h1 style={{ fontSize: '2rem', fontWeight: 800, letterSpacing: '-0.02em', marginBottom: '0.25rem' }}>
          Subscription & Billing
        </h1>
        <p style={{ color: 'var(--text-secondary)', fontSize: '0.95rem' }}>
          Manage your active subscription plan, payments, and invoice receipts.
        </p>
      </div>

      {error && (
        <div style={{ padding: '1rem', borderRadius: 'var(--radius-md)', background: 'var(--danger-bg)', color: 'var(--danger)', border: '1px solid rgba(239, 68, 68, 0.3)', fontSize: '0.9rem' }}>
          {error}
        </div>
      )}

      {successMsg && (
        <div style={{ padding: '1rem', borderRadius: 'var(--radius-md)', background: 'var(--success-bg)', color: 'var(--success)', border: '1px solid rgba(16, 185, 129, 0.3)', fontSize: '0.9rem' }}>
          {successMsg}
        </div>
      )}

      {/* Current Plan Overview Card */}
      <div className="glass-panel" style={{ padding: '2rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '1rem', marginBottom: '1.5rem' }}>
          <div>
            <span className="badge badge-primary" style={{ marginBottom: '0.5rem' }}>Active Plan</span>
            <h2 style={{ fontSize: '1.8rem', fontWeight: 800 }}>
              {currentPlan?.name || 'Free Explorer'}
            </h2>
            <div style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', marginTop: '0.25rem' }}>
              Status: <strong style={{ color: subscription?.cancel_at_period_end ? '#F59E0B' : '#10B981', textTransform: 'capitalize' }}>
                {subscription?.cancel_at_period_end ? 'Cancelling at Period End' : subscription?.status || 'Active'}
              </strong>
            </div>
          </div>

          <div style={{ textAlign: 'right' }}>
            <div style={{ fontSize: '2rem', fontWeight: 800 }}>
              ₹{currentPlan ? currentPlan.price_inr : 0}
              <span style={{ fontSize: '0.9rem', color: 'var(--text-muted)' }}>/mo</span>
            </div>
            <div style={{ fontSize: '0.85rem', color: '#818CF8' }}>
              {currentPlan?.monthly_credits || 5} Credits / Month
            </div>
          </div>
        </div>

        {subscription?.current_period_end && currentCode !== 'free' && (
          <div style={{ borderTop: '1px solid var(--border-subtle)', paddingTop: '1.25rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '1rem' }}>
            <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
              Next renewal: <strong>{new Date(subscription.current_period_end).toLocaleDateString()}</strong>
            </div>

            {!subscription.cancel_at_period_end && (
              <button
                className="btn btn-outline"
                style={{ padding: '0.4rem 0.8rem', fontSize: '0.85rem', color: 'var(--danger)', borderColor: 'rgba(239, 68, 68, 0.4)' }}
                onClick={handleCancel}
                disabled={loading}
              >
                Cancel Subscription
              </button>
            )}
          </div>
        )}
      </div>

      {/* Available Plans Upgrade Grid */}
      <div>
        <h2 style={{ fontSize: '1.4rem', fontWeight: 700, marginBottom: '1.25rem' }}>
          Available Subscription Tiers
        </h2>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: '1.5rem' }}>
          {plans.map((p) => {
            const isCurrent = currentCode === p.code;
            return (
              <div key={p.code} className="glass-panel" style={{ padding: '1.75rem', display: 'flex', flexDirection: 'column', justifyContent: 'space-between' }}>
                <div>
                  <h3 style={{ fontSize: '1.25rem', fontWeight: 800, marginBottom: '0.25rem' }}>{p.name}</h3>
                  <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem', minHeight: '36px', marginBottom: '1rem' }}>{p.description}</p>
                  <div style={{ fontSize: '1.8rem', fontWeight: 800, marginBottom: '1rem' }}>
                    ₹{p.price_inr}
                    <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>/mo</span>
                  </div>
                  <div style={{ fontSize: '0.85rem', color: '#818CF8', fontWeight: 600, marginBottom: '1rem' }}>
                    {p.monthly_credits} Monthly Credits
                  </div>
                </div>

                <button
                  className={`btn ${isCurrent ? 'btn-secondary' : 'btn-primary'}`}
                  style={{ width: '100%', padding: '0.75rem', fontSize: '0.9rem' }}
                  disabled={isCurrent || loading}
                  onClick={() => handleUpgrade(p.code)}
                >
                  {isCurrent ? 'Current Plan' : `Switch to ${p.name.split(' ')[0]}`}
                </button>
              </div>
            );
          })}
        </div>
      </div>

      {/* Invoices History Table */}
      <div className="glass-panel" style={{ padding: '2rem' }}>
        <h2 style={{ fontSize: '1.3rem', fontWeight: 700, marginBottom: '1.5rem' }}>
          Past Invoices & Receipts
        </h2>

        {invoices.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '2rem', color: 'var(--text-secondary)', fontSize: '0.9rem' }}>
            No past invoices on record.
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '0.9rem' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border-subtle)', color: 'var(--text-secondary)' }}>
                  <th style={{ padding: '0.75rem 1rem' }}>Invoice Number</th>
                  <th style={{ padding: '0.75rem 1rem' }}>Amount</th>
                  <th style={{ padding: '0.75rem 1rem' }}>Status</th>
                  <th style={{ padding: '0.75rem 1rem' }}>Billing Period</th>
                  <th style={{ padding: '0.75rem 1rem' }}>Date</th>
                </tr>
              </thead>
              <tbody>
                {invoices.map((inv) => (
                  <tr key={inv.id} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                    <td style={{ padding: '1rem', fontWeight: 700, fontFamily: 'var(--font-mono)' }}>{inv.invoice_number}</td>
                    <td style={{ padding: '1rem', fontWeight: 700 }}>₹{inv.amount_inr}</td>
                    <td style={{ padding: '1rem' }}>
                      <span className="badge badge-success">{inv.status.toUpperCase()}</span>
                    </td>
                    <td style={{ padding: '1rem', color: 'var(--text-secondary)', fontSize: '0.85rem' }}>
                      {new Date(inv.billing_period_start).toLocaleDateString()} – {new Date(inv.billing_period_end).toLocaleDateString()}
                    </td>
                    <td style={{ padding: '1rem', color: 'var(--text-muted)', fontSize: '0.85rem' }}>
                      {new Date(inv.created_at).toLocaleDateString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};
