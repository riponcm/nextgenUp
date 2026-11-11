import React from 'react';
import { User, Tenant, Subscription, ProcessingJob } from '../types';

interface DashboardOverviewProps {
  user: User;
  tenant: Tenant | null;
  walletBalance: number;
  subscription: Subscription | null;
  recentJobs: ProcessingJob[];
  onNavigate: (tab: string) => void;
}

export const DashboardOverview: React.FC<DashboardOverviewProps> = ({
  user,
  walletBalance,
  subscription,
  recentJobs,
  onNavigate,
}) => {
  const planName = subscription ? subscription.plan_code.toUpperCase() : 'FREE';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>
      {/* Welcome Banner */}
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
            Workspace: {user.display_name || user.email}'s Team
          </span>
          <h1 style={{ fontSize: '2.2rem', fontWeight: 800, letterSpacing: '-0.02em', marginBottom: '0.5rem' }}>
            Welcome back, <span className="gradient-text">{user.display_name || 'Creator'}</span>
          </h1>
          <p style={{ color: 'var(--text-secondary)', fontSize: '1rem', maxWidth: '580px' }}>
            Your AI enhancement cluster is ready. Process photos, manage subscriptions, or review your historical downloads below.
          </p>
        </div>

        <button
          className="btn btn-primary"
          style={{ padding: '0.85rem 1.75rem', fontSize: '1rem', borderRadius: 'var(--radius-md)' }}
          onClick={() => onNavigate('studio')}
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
          Open AI Studio
        </button>
      </div>

      {/* Real-time KPI Stats Grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: '1.5rem' }}>
        {/* Credits KPI */}
        <div className="glass-panel" style={{ padding: '1.75rem' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', color: 'var(--text-secondary)', marginBottom: '0.75rem' }}>
            <span style={{ fontSize: '0.9rem', fontWeight: 600 }}>Available Credits</span>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="#818CF8"><path d="M12 2l2.4 7.2h7.6l-6.1 4.5 2.3 7.3-6.2-4.6-6.2 4.6 2.3-7.3-6.1-4.5h7.6z"/></svg>
          </div>
          <div style={{ fontSize: '2.5rem', fontWeight: 800, color: 'var(--text-primary)' }}>
            {walletBalance}
          </div>
          <div
            onClick={() => onNavigate('wallet')}
            style={{ fontSize: '0.85rem', color: '#818CF8', fontWeight: 600, marginTop: '0.5rem', cursor: 'pointer' }}
          >
            View Credit Ledger →
          </div>
        </div>

        {/* Plan KPI */}
        <div className="glass-panel" style={{ padding: '1.75rem' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', color: 'var(--text-secondary)', marginBottom: '0.75rem' }}>
            <span style={{ fontSize: '0.9rem', fontWeight: 600 }}>Current Subscription</span>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#C084FC" strokeWidth="2"><rect x="1" y="4" width="22" height="16" rx="2" ry="2"/><line x1="1" y1="10" x2="23" y2="10"/></svg>
          </div>
          <div style={{ fontSize: '2.5rem', fontWeight: 800, color: 'var(--text-primary)' }}>
            {planName}
          </div>
          <div
            onClick={() => onNavigate('billing')}
            style={{ fontSize: '0.85rem', color: '#C084FC', fontWeight: 600, marginTop: '0.5rem', cursor: 'pointer' }}
          >
            Manage Plan & Billing →
          </div>
        </div>

        {/* Processed Jobs KPI */}
        <div className="glass-panel" style={{ padding: '1.75rem' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', color: 'var(--text-secondary)', marginBottom: '0.75rem' }}>
            <span style={{ fontSize: '0.9rem', fontWeight: 600 }}>Total Processed Images</span>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#34D399" strokeWidth="2"><polyline points="20 6 9 17 4 12"/></svg>
          </div>
          <div style={{ fontSize: '2.5rem', fontWeight: 800, color: 'var(--text-primary)' }}>
            {recentJobs.length}
          </div>
          <div
            onClick={() => onNavigate('history')}
            style={{ fontSize: '0.85rem', color: '#34D399', fontWeight: 600, marginTop: '0.5rem', cursor: 'pointer' }}
          >
            Browse My Images →
          </div>
        </div>
      </div>

      {/* Recent Activity Table */}
      <div className="glass-panel" style={{ padding: '2rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.5rem' }}>
          <h2 style={{ fontSize: '1.3rem', fontWeight: 700 }}>Recent Image Enhancements</h2>
          <button
            className="btn btn-secondary"
            style={{ padding: '0.4rem 0.8rem', fontSize: '0.85rem' }}
            onClick={() => onNavigate('history')}
          >
            View All
          </button>
        </div>

        {recentJobs.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '3rem 1rem', color: 'var(--text-secondary)' }}>
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" style={{ margin: '0 auto 1rem', opacity: 0.5 }}>
              <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
              <circle cx="8.5" cy="8.5" r="1.5"/>
              <polyline points="21 15 16 10 5 21"/>
            </svg>
            <p style={{ fontWeight: 600, fontSize: '1.1rem', marginBottom: '0.5rem' }}>No enhanced photos yet</p>
            <p style={{ fontSize: '0.9rem', maxWidth: '400px', margin: '0 auto 1.5rem' }}>
              Upload your first photo in the AI Studio to upscale, denoise, or isolate backgrounds.
            </p>
            <button className="btn btn-primary" onClick={() => onNavigate('studio')}>
              Start First Enhancement
            </button>
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '0.9rem' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border-subtle)', color: 'var(--text-secondary)' }}>
                  <th style={{ padding: '0.75rem 1rem' }}>Operation</th>
                  <th style={{ padding: '0.75rem 1rem' }}>Status</th>
                  <th style={{ padding: '0.75rem 1rem' }}>Credit Cost</th>
                  <th style={{ padding: '0.75rem 1rem' }}>Date</th>
                  <th style={{ padding: '0.75rem 1rem' }}>Action</th>
                </tr>
              </thead>
              <tbody>
                {recentJobs.slice(0, 5).map((job) => (
                  <tr key={job.id} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                    <td style={{ padding: '1rem', fontWeight: 600 }}>{job.operation_type.toUpperCase()}</td>
                    <td style={{ padding: '1rem' }}>
                      <span className={`badge ${job.status === 'completed' ? 'badge-success' : 'badge-primary'}`}>
                        {job.status.toUpperCase()}
                      </span>
                    </td>
                    <td style={{ padding: '1rem', color: 'var(--text-secondary)' }}>{job.credit_cost} Credits</td>
                    <td style={{ padding: '1rem', color: 'var(--text-secondary)' }}>
                      {new Date(job.created_at).toLocaleDateString()}
                    </td>
                    <td style={{ padding: '1rem' }}>
                      {job.status === 'completed' && job.output_key && (
                        <a
                          href={`/api/v1/storage/file/${job.output_key}`}
                          download
                          className="btn btn-secondary"
                          style={{ padding: '0.35rem 0.75rem', fontSize: '0.8rem' }}
                        >
                          Download
                        </a>
                      )}
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
