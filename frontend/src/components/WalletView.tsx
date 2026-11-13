import React, { useState, useEffect } from 'react';
import { api } from '../api';
import { CreditTransaction } from '../types';

interface WalletViewProps {
  balance: number;
  onNavigate: (tab: string) => void;
}

export const WalletView: React.FC<WalletViewProps> = ({ balance, onNavigate }) => {
  const [history, setHistory] = useState<CreditTransaction[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.getCreditHistory()
      .then((txs) => setHistory(txs))
      .catch((err) => console.error('Failed to load credit history:', err))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>
      {/* Wallet Balance Header */}
      <div
        className="glass-panel"
        style={{
          padding: '2.5rem',
          background: 'linear-gradient(135deg, rgba(99, 102, 241, 0.15) 0%, rgba(139, 92, 246, 0.05) 100%)',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: '1.5rem',
        }}
      >
        <div>
          <span className="badge badge-primary" style={{ marginBottom: '0.75rem' }}>
            Atomic Credit Ledger
          </span>
          <h1 style={{ fontSize: '2.5rem', fontWeight: 800, letterSpacing: '-0.02em', marginBottom: '0.5rem' }}>
            {balance} <span style={{ fontSize: '1.25rem', color: 'var(--text-secondary)', fontWeight: 500 }}>Credits Available</span>
          </h1>
          <p style={{ color: 'var(--text-secondary)', fontSize: '0.95rem', maxWidth: '540px' }}>
            Credits power your AI enhancements. Monthly subscription credits replenish automatically on renewal.
          </p>
        </div>

        <button className="btn btn-primary" onClick={() => onNavigate('billing')} style={{ padding: '0.85rem 1.75rem', fontWeight: 700 }}>
          Get More Credits
        </button>
      </div>

      {/* Immutable Transaction History Table */}
      <div className="glass-panel" style={{ padding: '2rem' }}>
        <h2 style={{ fontSize: '1.3rem', fontWeight: 700, marginBottom: '1.5rem' }}>
          Credit Ledger & Audit Trail
        </h2>

        {loading ? (
          <div style={{ textAlign: 'center', padding: '2rem', color: 'var(--text-muted)' }}>
            Loading ledger transactions...
          </div>
        ) : history.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '3rem 1rem', color: 'var(--text-secondary)' }}>
            <p>No credit transactions recorded yet.</p>
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '0.9rem' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border-subtle)', color: 'var(--text-secondary)' }}>
                  <th style={{ padding: '0.75rem 1rem' }}>Type</th>
                  <th style={{ padding: '0.75rem 1rem' }}>Amount</th>
                  <th style={{ padding: '0.75rem 1rem' }}>Balance After</th>
                  <th style={{ padding: '0.75rem 1rem' }}>Description</th>
                  <th style={{ padding: '0.75rem 1rem' }}>Timestamp</th>
                </tr>
              </thead>
              <tbody>
                {history.map((tx) => {
                  const isAddition = tx.transaction_type === 'allocation' || tx.transaction_type === 'refund';
                  return (
                    <tr key={tx.id} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                      <td style={{ padding: '1rem' }}>
                        <span className={`badge ${isAddition ? 'badge-success' : 'badge-primary'}`}>
                          {tx.transaction_type.toUpperCase()}
                        </span>
                      </td>
                      <td style={{ padding: '1rem', fontWeight: 700, color: isAddition ? '#10B981' : '#EF4444' }}>
                        {isAddition ? `+${tx.amount}` : `-${tx.amount}`}
                      </td>
                      <td style={{ padding: '1rem', fontWeight: 600 }}>{tx.balance_after}</td>
                      <td style={{ padding: '1rem', color: 'var(--text-secondary)' }}>
                        {tx.description || 'System operation'}
                      </td>
                      <td style={{ padding: '1rem', color: 'var(--text-muted)', fontSize: '0.8rem' }}>
                        {new Date(tx.created_at).toLocaleString()}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};
