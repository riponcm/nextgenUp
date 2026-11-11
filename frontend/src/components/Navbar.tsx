import React, { useState } from 'react';
import { User, Tenant } from '../types';

interface NavbarProps {
  user: User | null;
  tenant: Tenant | null;
  walletBalance: number;
  activeTab: string;
  onNavigate: (tab: string) => void;
  onOpenAuth: (mode: 'login' | 'register') => void;
  onLogout: () => void;
  theme: 'dark' | 'light';
  onToggleTheme: () => void;
}

export const Navbar: React.FC<NavbarProps> = ({
  user,
  walletBalance,
  activeTab,
  onNavigate,
  onOpenAuth,
  onLogout,
  theme,
  onToggleTheme,
}) => {
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  return (
    <header
      style={{
        position: 'sticky',
        top: 0,
        zIndex: 40,
        background: 'rgba(9, 13, 22, 0.85)',
        backdropFilter: 'blur(12px)',
        borderBottom: '1px solid var(--border-subtle)',
      }}
    >
      <div className="container" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', height: '4.5rem' }}>
        {/* Brand Logo */}
        <div
          onClick={() => onNavigate(user ? 'overview' : 'landing')}
          style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', cursor: 'pointer' }}
        >
          <div
            style={{
              width: '2.5rem',
              height: '2.5rem',
              borderRadius: '0.625rem',
              background: 'linear-gradient(135deg, #6366F1 0%, #8B5CF6 100%)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              boxShadow: '0 0 15px rgba(99, 102, 241, 0.4)',
            }}
          >
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#FFFFFF" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>
            </svg>
          </div>
          <div>
            <span style={{ fontSize: '1.25rem', fontWeight: 800, letterSpacing: '-0.02em' }}>
              Demerivo <span style={{ color: '#818CF8' }}>PixelFix</span>
            </span>
          </div>
        </div>

        {/* Public or Authenticated Navigation Links */}
        <nav className="hide-on-mobile" style={{ display: 'flex', alignItems: 'center', gap: '1.75rem' }}>
          {!user ? (
            <>
              <a href="#features" style={{ color: 'var(--text-secondary)', textDecoration: 'none', fontWeight: 500, fontSize: '0.9rem' }}>
                Features
              </a>
              <a href="#how-it-works" style={{ color: 'var(--text-secondary)', textDecoration: 'none', fontWeight: 500, fontSize: '0.9rem' }}>
                How It Works
              </a>
              <a href="#operations" style={{ color: 'var(--text-secondary)', textDecoration: 'none', fontWeight: 500, fontSize: '0.9rem' }}>
                AI Operations
              </a>
              <a href="#pricing" style={{ color: 'var(--text-secondary)', textDecoration: 'none', fontWeight: 500, fontSize: '0.9rem' }}>
                Pricing
              </a>
              <a href="#faq" style={{ color: 'var(--text-secondary)', textDecoration: 'none', fontWeight: 500, fontSize: '0.9rem' }}>
                FAQ
              </a>
            </>
          ) : (
            <>
              <button
                className={`btn btn-outline ${activeTab === 'overview' ? 'badge-primary' : ''}`}
                style={{ padding: '0.4rem 0.8rem', fontSize: '0.85rem' }}
                onClick={() => onNavigate('overview')}
              >
                Overview
              </button>
              <button
                className={`btn btn-outline ${activeTab === 'studio' ? 'badge-primary' : ''}`}
                style={{ padding: '0.4rem 0.8rem', fontSize: '0.85rem' }}
                onClick={() => onNavigate('studio')}
              >
                AI Studio
              </button>
              <button
                className={`btn btn-outline ${activeTab === 'history' ? 'badge-primary' : ''}`}
                style={{ padding: '0.4rem 0.8rem', fontSize: '0.85rem' }}
                onClick={() => onNavigate('history')}
              >
                My Images
              </button>
              <button
                className={`btn btn-outline ${activeTab === 'billing' ? 'badge-primary' : ''}`}
                style={{ padding: '0.4rem 0.8rem', fontSize: '0.85rem' }}
                onClick={() => onNavigate('billing')}
              >
                Subscription
              </button>
              {user.is_platform_admin && (
                <button
                  className={`btn ${activeTab === 'admin' ? 'btn-primary' : 'btn-outline'}`}
                  style={{
                    padding: '0.4rem 0.8rem',
                    fontSize: '0.85rem',
                    borderColor: '#818CF8',
                    color: activeTab === 'admin' ? '#FFFFFF' : '#A5B4FC',
                  }}
                  onClick={() => onNavigate('admin')}
                >
                  Admin Portal
                </button>
              )}
            </>
          )}
        </nav>

        {/* Right Action Controls */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
          {/* Theme Toggle */}
          <button
            onClick={onToggleTheme}
            style={{
              background: 'transparent',
              border: 'none',
              color: 'var(--text-secondary)',
              cursor: 'pointer',
              padding: '0.5rem',
              display: 'flex',
              alignItems: 'center',
            }}
            title="Toggle theme"
          >
            {theme === 'dark' ? (
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>
            ) : (
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>
            )}
          </button>

          {!user ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
              <button className="btn btn-secondary" onClick={() => onOpenAuth('login')}>
                Sign In
              </button>
              <button className="btn btn-primary" onClick={() => onOpenAuth('register')}>
                Get Started
              </button>
            </div>
          ) : (
            <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
              {/* Credit Balance Pill */}
              <div
                onClick={() => onNavigate('wallet')}
                style={{
                  background: 'rgba(99, 102, 241, 0.15)',
                  border: '1px solid rgba(99, 102, 241, 0.4)',
                  padding: '0.35rem 0.85rem',
                  borderRadius: '9999px',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.5rem',
                  cursor: 'pointer',
                  fontSize: '0.85rem',
                  fontWeight: 700,
                  color: '#A5B4FC',
                }}
                title="Available Credits"
              >
                <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M12 2l2.4 7.2h7.6l-6.1 4.5 2.3 7.3-6.2-4.6-6.2 4.6 2.3-7.3-6.1-4.5h7.6z"/>
                </svg>
                <span>{walletBalance} Credits</span>
              </div>

              {/* User Dropdown / Logout */}
              <button
                className="btn btn-secondary"
                style={{ padding: '0.4rem 0.8rem', fontSize: '0.85rem' }}
                onClick={() => onNavigate('settings')}
              >
                Settings
              </button>
              <button
                className="btn btn-outline"
                style={{ padding: '0.4rem 0.8rem', fontSize: '0.85rem' }}
                onClick={onLogout}
              >
                Log Out
              </button>
            </div>
          )}

          {/* Mobile Menu Hamburger */}
          <button
            onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
            style={{
              display: 'none',
              background: 'transparent',
              border: 'none',
              color: 'var(--text-primary)',
              cursor: 'pointer',
              padding: '0.5rem',
            }}
            className="mobile-hamburger"
          >
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="18" x2="21" y2="18"/></svg>
          </button>
        </div>
      </div>
    </header>
  );
};
