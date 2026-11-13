import React, { useState } from 'react';
import { api } from '../api';
import { User, Tenant } from '../types';

interface AccountSettingsProps {
  user: User;
  tenant: Tenant | null;
  onUserUpdated: (user: User) => void;
  onLogout: () => void;
}

export const AccountSettings: React.FC<AccountSettingsProps> = ({
  user,
  tenant,
  onUserUpdated,
  onLogout,
}) => {
  const [displayName, setDisplayName] = useState(user.display_name || '');
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleUpdate = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setMsg(null);
    setError(null);
    try {
      const updated = await api.updateProfile(displayName);
      onUserUpdated(updated);
      setMsg('Profile details successfully saved.');
    } catch (err: any) {
      setError(err.message || 'Failed to update profile.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem', maxWidth: '720px' }}>
      <div>
        <h1 style={{ fontSize: '2rem', fontWeight: 800, letterSpacing: '-0.02em', marginBottom: '0.25rem' }}>
          Account & Workspace Settings
        </h1>
        <p style={{ color: 'var(--text-secondary)', fontSize: '0.95rem' }}>
          Manage your personal profile and multi-tenant workspace configurations.
        </p>
      </div>

      {msg && (
        <div style={{ padding: '0.75rem 1rem', borderRadius: 'var(--radius-md)', background: 'var(--success-bg)', color: 'var(--success)', border: '1px solid rgba(16, 185, 129, 0.3)', fontSize: '0.875rem' }}>
          {msg}
        </div>
      )}

      {error && (
        <div style={{ padding: '0.75rem 1rem', borderRadius: 'var(--radius-md)', background: 'var(--danger-bg)', color: 'var(--danger)', border: '1px solid rgba(239, 68, 68, 0.3)', fontSize: '0.875rem' }}>
          {error}
        </div>
      )}

      {/* Profile Form */}
      <div className="glass-panel" style={{ padding: '2rem' }}>
        <h3 style={{ fontSize: '1.2rem', fontWeight: 700, marginBottom: '1.25rem' }}>
          Personal Profile
        </h3>

        <form onSubmit={handleUpdate} style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
          <div>
            <label style={{ display: 'block', fontSize: '0.85rem', fontWeight: 600, marginBottom: '0.4rem' }}>
              Email Address (Authentication ID)
            </label>
            <input
              type="text"
              disabled
              value={user.email}
              style={{
                width: '100%',
                padding: '0.75rem',
                borderRadius: 'var(--radius-md)',
                background: 'rgba(255, 255, 255, 0.05)',
                border: '1px solid var(--border-subtle)',
                color: 'var(--text-muted)',
                fontSize: '0.95rem',
              }}
            />
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginTop: '0.35rem' }}>
              <span className={`badge ${user.email_verified ? 'badge-success' : 'badge-primary'}`}>
                {user.email_verified ? 'Verified Email' : 'Verification Pending'}
              </span>
            </div>
          </div>

          <div>
            <label style={{ display: 'block', fontSize: '0.85rem', fontWeight: 600, marginBottom: '0.4rem' }}>
              Display Name
            </label>
            <input
              type="text"
              required
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="Your Name"
              style={{
                width: '100%',
                padding: '0.75rem',
                borderRadius: 'var(--radius-md)',
                background: 'var(--bg-card)',
                border: '1px solid var(--border-strong)',
                color: 'var(--text-primary)',
                fontSize: '0.95rem',
              }}
            />
          </div>

          <div>
            <button type="submit" className="btn btn-primary" disabled={loading} style={{ padding: '0.75rem 1.5rem' }}>
              {loading ? 'Saving...' : 'Save Profile Changes'}
            </button>
          </div>
        </form>
      </div>

      {/* Multi-Tenant Workspace Details */}
      <div className="glass-panel" style={{ padding: '2rem' }}>
        <h3 style={{ fontSize: '1.2rem', fontWeight: 700, marginBottom: '0.5rem' }}>
          Active Workspace
        </h3>
        <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', marginBottom: '1.25rem' }}>
          Workspaces provide isolated asset storage, team collaboration, and dedicated credit balance tracking.
        </p>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', fontSize: '0.9rem' }}>
          <div>
            <strong style={{ color: 'var(--text-muted)' }}>Workspace Name:</strong>{' '}
            <span>{tenant?.name || 'Personal Workspace'}</span>
          </div>
          <div>
            <strong style={{ color: 'var(--text-muted)' }}>Workspace ID:</strong>{' '}
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: '0.85rem' }}>{tenant?.id || 'default'}</span>
          </div>
          <div>
            <strong style={{ color: 'var(--text-muted)' }}>Role:</strong>{' '}
            <span className="badge badge-primary" style={{ textTransform: 'uppercase' }}>
              {tenant?.role || 'OWNER'}
            </span>
          </div>
        </div>
      </div>

      {/* Session Management */}
      <div className="glass-panel" style={{ padding: '2rem' }}>
        <h3 style={{ fontSize: '1.2rem', fontWeight: 700, marginBottom: '0.5rem' }}>
          Session Security
        </h3>
        <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', marginBottom: '1.25rem' }}>
          Log out of your current session on this device.
        </p>
        <button
          className="btn btn-outline"
          onClick={onLogout}
          style={{ color: 'var(--danger)', borderColor: 'rgba(239, 68, 68, 0.4)' }}
        >
          Sign Out of Demerivo PixelFix
        </button>
      </div>
    </div>
  );
};
