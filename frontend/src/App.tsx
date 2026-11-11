import React, { useState, useEffect } from 'react';
import { api } from './api';
import {
  Invoice,
  Plan,
  ProcessingJob,
  Subscription,
  Tenant,
  User,
  ViewTab,
} from './types';
import { Navbar } from './components/Navbar';
import { Hero } from './components/Hero';
import { Features } from './components/Features';
import { HowItWorks } from './components/HowItWorks';
import { OperationsShowcase } from './components/OperationsShowcase';
import { PricingTable } from './components/PricingTable';
import { FAQ } from './components/FAQ';
import { AuthModal } from './components/AuthModal';
import { LegalModals } from './components/LegalModals';
import { DashboardOverview } from './components/DashboardOverview';
import { PhotoStudio } from './components/PhotoStudio';
import { ImageHistory } from './components/ImageHistory';
import { WalletView } from './components/WalletView';
import { SubscriptionView } from './components/SubscriptionView';
import { AccountSettings } from './components/AccountSettings';
import { AdminDashboard } from './components/admin/AdminDashboard';

export const App: React.FC = () => {
  const [theme, setTheme] = useState<'dark' | 'light'>('dark');
  const [user, setUser] = useState<User | null>(null);
  const [tenant, setTenant] = useState<Tenant | null>(null);
  const [walletBalance, setWalletBalance] = useState<number>(0);
  const [subscription, setSubscription] = useState<Subscription | null>(null);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [jobs, setJobs] = useState<ProcessingJob[]>([]);

  // Navigation and Modal State
  const [activeTab, setActiveTab] = useState<ViewTab | 'landing'>('landing');
  const [authModal, setAuthModal] = useState<'login' | 'register' | 'forgot_password' | null>(null);
  const [legalModal, setLegalModal] = useState<'privacy' | 'terms' | 'contact' | null>(null);

  // Apply theme to document root
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
  }, [theme]);

  // Load session and initial public data
  useEffect(() => {
    api.getPlans().then(setPlans).catch(console.error);

    const token = api.getToken();
    if (token) {
      loadUserData();
    }
  }, []);

  const loadUserData = async () => {
    try {
      const currentUser = await api.getCurrentUser();
      setUser(currentUser);

      const tenants = await api.getTenants();
      if (tenants.length > 0) {
        setTenant(tenants[0]);
        api.setTenantId(tenants[0].id);
      }

      const wallet = await api.getWallet();
      setWalletBalance(wallet.balance);

      const subData = await api.getSubscription();
      setSubscription(subData.subscription);

      const userJobs = await api.listJobs();
      setJobs(userJobs);

      setActiveTab('overview');
    } catch (err) {
      console.error('Session sync error:', err);
      api.setToken(null);
      setUser(null);
      setActiveTab('landing');
    }
  };

  const handleRefreshWallet = async () => {
    try {
      const wallet = await api.getWallet();
      setWalletBalance(wallet.balance);
    } catch (err) {
      console.error(err);
    }
  };

  const handleRefreshSubscription = async () => {
    try {
      const subData = await api.getSubscription();
      setSubscription(subData.subscription);
    } catch (err) {
      console.error(err);
    }
  };

  const handleLogout = () => {
    api.setToken(null);
    api.setTenantId(null);
    setUser(null);
    setTenant(null);
    setWalletBalance(0);
    setSubscription(null);
    setActiveTab('landing');
  };

  if (user && activeTab === 'admin' && user.is_platform_admin) {
    return (
      <AdminDashboard
        currentUser={user}
        onExit={() => setActiveTab('overview')}
      />
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: '100vh' }}>
      {/* Top Navigation */}
      <Navbar
        user={user}
        tenant={tenant}
        walletBalance={walletBalance}
        activeTab={activeTab}
        onNavigate={(tab) => setActiveTab(tab as any)}
        onOpenAuth={(mode) => setAuthModal(mode)}
        onLogout={handleLogout}
        theme={theme}
        onToggleTheme={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
      />

      {/* Main View Area */}
      <main style={{ flex: 1 }}>
        {!user || activeTab === 'landing' ? (
          /* Public Website View */
          <>
            <Hero
              onGetStarted={() => setAuthModal(user ? null : 'register')}
              onExploreFeatures={() => {
                const el = document.getElementById('features');
                el?.scrollIntoView({ behavior: 'smooth' });
              }}
            />
            <Features />
            <HowItWorks />
            <OperationsShowcase />
            <PricingTable
              plans={plans}
              currentPlanCode={subscription?.plan_code || 'free'}
              onSelectPlan={(code) => {
                if (!user) {
                  setAuthModal('register');
                } else {
                  setActiveTab('billing');
                }
              }}
              isAuthenticated={!!user}
            />
            <FAQ />
          </>
        ) : (
          /* Customer Dashboard View */
          <div className="container" style={{ padding: '2.5rem 1.25rem', maxWidth: '1200px' }}>
            {activeTab === 'overview' && (
              <DashboardOverview
                user={user}
                tenant={tenant}
                walletBalance={walletBalance}
                subscription={subscription}
                recentJobs={jobs}
                onNavigate={(t) => setActiveTab(t as any)}
              />
            )}

            {activeTab === 'studio' && (
              <PhotoStudio
                walletBalance={walletBalance}
                onRefreshWallet={handleRefreshWallet}
                onJobCompleted={(newJob) => setJobs([newJob, ...jobs])}
              />
            )}

            {activeTab === 'history' && (
              <ImageHistory
                jobs={jobs}
                onOpenStudio={() => setActiveTab('studio')}
              />
            )}

            {activeTab === 'wallet' && (
              <WalletView
                balance={walletBalance}
                onNavigate={(t) => setActiveTab(t as any)}
              />
            )}

            {activeTab === 'billing' && (
              <SubscriptionView
                subscription={subscription}
                onRefreshSubscription={handleRefreshSubscription}
                onRefreshWallet={handleRefreshWallet}
              />
            )}

            {activeTab === 'settings' && (
              <AccountSettings
                user={user}
                tenant={tenant}
                onUserUpdated={(updated) => setUser(updated)}
                onLogout={handleLogout}
              />
            )}
          </div>
        )}
      </main>

      {/* Public Footer */}
      <footer style={{ background: 'var(--bg-surface)', borderTop: '1px solid var(--border-subtle)', padding: '4rem 0 2rem' }}>
        <div className="container" style={{ display: 'flex', flexDirection: 'column', gap: '2.5rem' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '2rem' }}>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', marginBottom: '0.75rem' }}>
                <div style={{ width: '1.75rem', height: '1.75rem', borderRadius: '6px', background: '#6366F1' }} />
                <span style={{ fontSize: '1.2rem', fontWeight: 800 }}>Demerivo PixelFix</span>
              </div>
              <p style={{ color: 'var(--text-secondary)', fontSize: '0.875rem', maxWidth: '340px' }}>
                Enterprise-grade AI photo restoration, super-resolution, and background isolation for creators worldwide.
              </p>
            </div>

            <div style={{ display: 'flex', gap: '3rem', flexWrap: 'wrap' }}>
              <div>
                <h4 style={{ fontSize: '0.85rem', fontWeight: 700, textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: '0.75rem' }}>
                  Platform
                </h4>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', fontSize: '0.9rem' }}>
                  <a href="#features" style={{ color: 'var(--text-secondary)', textDecoration: 'none' }}>Features</a>
                  <a href="#operations" style={{ color: 'var(--text-secondary)', textDecoration: 'none' }}>AI Operations</a>
                  <a href="#pricing" style={{ color: 'var(--text-secondary)', textDecoration: 'none' }}>Pricing</a>
                </div>
              </div>

              <div>
                <h4 style={{ fontSize: '0.85rem', fontWeight: 700, textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: '0.75rem' }}>
                  Legal & Trust
                </h4>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', fontSize: '0.9rem' }}>
                  <span onClick={() => setLegalModal('privacy')} style={{ color: 'var(--text-secondary)', cursor: 'pointer' }}>Privacy Policy</span>
                  <span onClick={() => setLegalModal('terms')} style={{ color: 'var(--text-secondary)', cursor: 'pointer' }}>Terms of Service</span>
                  <span onClick={() => setLegalModal('contact')} style={{ color: 'var(--text-secondary)', cursor: 'pointer' }}>Contact Support</span>
                </div>
              </div>
            </div>
          </div>

          <div style={{ borderTop: '1px solid var(--border-subtle)', paddingTop: '1.5rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '1rem', fontSize: '0.85rem', color: 'var(--text-muted)' }}>
            <div>© {new Date().getFullYear()} Demerivo Inc. All rights reserved.</div>
            <div>Commercially Cleared Inference Pipeline (CPU/GPU-ready)</div>
          </div>
        </div>
      </footer>

      {/* Auth Modal */}
      {authModal && (
        <AuthModal
          initialMode={authModal}
          onClose={() => setAuthModal(null)}
          onSuccess={(loggedUser) => {
            setUser(loggedUser);
            loadUserData();
          }}
        />
      )}

      {/* Legal & Support Modals */}
      <LegalModals
        type={legalModal}
        onClose={() => setLegalModal(null)}
      />
    </div>
  );
};
