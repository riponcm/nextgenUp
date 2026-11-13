import React, { useState, useEffect } from 'react';
import {
  LayoutDashboard,
  Users,
  Building2,
  Cpu,
  CreditCard,
  Coins,
  Search,
  RefreshCw,
  AlertTriangle,
  TrendingUp,
  FileText,
  LogOut,
  UserCheck,
  Server,
  Lock,
} from 'lucide-react';
import { api } from '../../api';
import {
  AdminOverviewMetrics,
  AdminUserItem,
  AdminTenantItem,
  AdminJobItem,
  AdminSubscriptionItem,
  AdminPaymentItem,
  AdminAuditLogItem,
  AdminSystemHealth,
  AdminSubTab,
  User,
} from '../../types';

interface AdminDashboardProps {
  currentUser: User;
  onExit: () => void;
}

export const AdminDashboard: React.FC<AdminDashboardProps> = ({ currentUser, onExit }) => {
  const [activeTab, setActiveTab] = useState<AdminSubTab>('overview');
  const [dateRange, setDateRange] = useState<string>('30d');
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  // Data states
  const [overview, setOverview] = useState<AdminOverviewMetrics | null>(null);
  const [users, setUsers] = useState<AdminUserItem[]>([]);
  const [, setUsersTotal] = useState<number>(0);
  const [usersPage] = useState<number>(1);
  const [usersSearch, setUsersSearch] = useState<string>('');
  const [usersStatus, setUsersStatus] = useState<string>('all');

  const [tenants, setTenants] = useState<AdminTenantItem[]>([]);
  const [, setTenantsTotal] = useState<number>(0);
  const [tenantsSearch, setTenantsSearch] = useState<string>('');

  const [jobs, setJobs] = useState<AdminJobItem[]>([]);
  const [, setJobsTotal] = useState<number>(0);
  const [jobsStatus, setJobsStatus] = useState<string>('all');
  const [jobsOp, setJobsOp] = useState<string>('all');

  const [subscriptions, setSubscriptions] = useState<AdminSubscriptionItem[]>([]);
  const [, setSubsTotal] = useState<number>(0);
  const [subsPlan, setSubsPlan] = useState<string>('all');

  const [revenue, setRevenue] = useState<any>(null);
  const [payments, setPayments] = useState<AdminPaymentItem[]>([]);

  const [credits, setCredits] = useState<any>(null);
  const [systemHealth, setSystemHealth] = useState<AdminSystemHealth | null>(null);
  const [auditLogs, setAuditLogs] = useState<AdminAuditLogItem[]>([]);

  // Modals & Action States
  const [selectedUser, setSelectedUser] = useState<any>(null);
  const [actionReason, setActionReason] = useState<string>('');
  const [actionLoading, setActionLoading] = useState<boolean>(false);
  const [, setSelectedTenant] = useState<any>(null);
  const [, setSelectedJob] = useState<any>(null);
  const [selectedSub, setSelectedSub] = useState<any>(null);
  const [newPlanCode, setNewPlanCode] = useState<string>('starter');

  // Load data when activeTab or dateRange changes
  const loadActiveData = async () => {
    setLoading(true);
    setError(null);
    try {
      if (activeTab === 'overview') {
        const res = await api.getAdminOverview(dateRange);
        setOverview(res);
      } else if (activeTab === 'users') {
        const res = await api.getAdminUsers(usersPage, 20, usersSearch, usersStatus);
        setUsers(res.items || []);
        setUsersTotal(res.total || 0);
      } else if (activeTab === 'tenants') {
        const res = await api.getAdminTenants(1, 20, tenantsSearch);
        setTenants(res.items || []);
        setTenantsTotal(res.total || 0);
      } else if (activeTab === 'jobs') {
        const res = await api.getAdminJobs(1, 20, jobsStatus, jobsOp);
        setJobs(res.items || []);
        setJobsTotal(res.total || 0);
      } else if (activeTab === 'subscriptions') {
        const res = await api.getAdminSubscriptions(1, 20, subsPlan, 'all');
        setSubscriptions(res.items || []);
        setSubsTotal(res.total || 0);
      } else if (activeTab === 'revenue') {
        const rev = await api.getAdminRevenue(dateRange);
        const pays = await api.getAdminPayments(1, 20);
        setRevenue(rev);
        setPayments(pays.items || []);
      } else if (activeTab === 'credits') {
        const cred = await api.getAdminCredits();
        setCredits(cred);
      } else if (activeTab === 'system') {
        const sys = await api.getAdminSystemHealth();
        setSystemHealth(sys);
      } else if (activeTab === 'audit') {
        const logs = await api.getAdminAuditLogs(1, 25);
        setAuditLogs(logs.items || []);
      }
    } catch (err: any) {
      setError(err.message || 'Failed to fetch administrative records');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadActiveData();
  }, [activeTab, dateRange, usersPage, usersStatus, jobsStatus, jobsOp, subsPlan]);

  // Actions
  const handleUserStatusToggle = async (userId: string, currentActive: boolean) => {
    if (!actionReason || actionReason.length < 5) {
      alert('Please provide a mandatory audit reason (minimum 5 characters)');
      return;
    }
    setActionLoading(true);
    try {
      await api.updateAdminUserStatus(userId, currentActive ? 'suspend' : 'activate', actionReason);
      setActionReason('');
      setSelectedUser(null);
      loadActiveData();
    } catch (err: any) {
      alert(`Action failed: ${err.message}`);
    } finally {
      setActionLoading(false);
    }
  };

  const handleModifySubscription = async (subId: string) => {
    if (!actionReason || actionReason.length < 5) {
      alert('Please provide a mandatory audit reason (minimum 5 characters)');
      return;
    }
    setActionLoading(true);
    try {
      await api.modifyAdminSubscription(subId, newPlanCode, actionReason);
      setActionReason('');
      setSelectedSub(null);
      loadActiveData();
    } catch (err: any) {
      alert(`Modification failed: ${err.message}`);
    } finally {
      setActionLoading(false);
    }
  };

  return (
    <div className="flex h-screen bg-[#0A0D14] text-slate-100 overflow-hidden font-sans">
      {/* Sidebar Navigation */}
      <aside className="w-64 bg-[#121624] border-r border-slate-800/80 flex flex-col flex-shrink-0">
        <div className="p-5 border-b border-slate-800/80 flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-tr from-indigo-600 to-violet-500 flex items-center justify-center shadow-lg shadow-indigo-500/20">
              <Lock className="w-5 h-5 text-white" />
            </div>
            <div>
              <div className="font-bold text-sm tracking-wider uppercase bg-gradient-to-r from-white to-slate-300 bg-clip-text text-transparent">
                PixelFix Admin
              </div>
              <div className="text-[10px] text-indigo-400 font-semibold flex items-center gap-1">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
                Platform Portal
              </div>
            </div>
          </div>
        </div>

        <nav className="flex-1 p-3 space-y-1 overflow-y-auto">
          <button
            onClick={() => setActiveTab('overview')}
            className={`w-full flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-medium transition-all ${
              activeTab === 'overview'
                ? 'bg-indigo-600/20 text-indigo-300 border border-indigo-500/30'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
            }`}
          >
            <LayoutDashboard className="w-4 h-4" />
            <span>Overview & KPIs</span>
          </button>

          <button
            onClick={() => setActiveTab('users')}
            className={`w-full flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-medium transition-all ${
              activeTab === 'users'
                ? 'bg-indigo-600/20 text-indigo-300 border border-indigo-500/30'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
            }`}
          >
            <Users className="w-4 h-4" />
            <span>Customer Management</span>
          </button>

          <button
            onClick={() => setActiveTab('tenants')}
            className={`w-full flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-medium transition-all ${
              activeTab === 'tenants'
                ? 'bg-indigo-600/20 text-indigo-300 border border-indigo-500/30'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
            }`}
          >
            <Building2 className="w-4 h-4" />
            <span>Workspaces</span>
          </button>

          <button
            onClick={() => setActiveTab('jobs')}
            className={`w-full flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-medium transition-all ${
              activeTab === 'jobs'
                ? 'bg-indigo-600/20 text-indigo-300 border border-indigo-500/30'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
            }`}
          >
            <Cpu className="w-4 h-4" />
            <span>Job Monitoring</span>
          </button>

          <button
            onClick={() => setActiveTab('subscriptions')}
            className={`w-full flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-medium transition-all ${
              activeTab === 'subscriptions'
                ? 'bg-indigo-600/20 text-indigo-300 border border-indigo-500/30'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
            }`}
          >
            <CreditCard className="w-4 h-4" />
            <span>Subscriptions</span>
          </button>

          <button
            onClick={() => setActiveTab('revenue')}
            className={`w-full flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-medium transition-all ${
              activeTab === 'revenue'
                ? 'bg-indigo-600/20 text-indigo-300 border border-indigo-500/30'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
            }`}
          >
            <TrendingUp className="w-4 h-4" />
            <span>Revenue & Payments</span>
          </button>

          <button
            onClick={() => setActiveTab('credits')}
            className={`w-full flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-medium transition-all ${
              activeTab === 'credits'
                ? 'bg-indigo-600/20 text-indigo-300 border border-indigo-500/30'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
            }`}
          >
            <Coins className="w-4 h-4" />
            <span>Credit Analytics</span>
          </button>

          <button
            onClick={() => setActiveTab('system')}
            className={`w-full flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-medium transition-all ${
              activeTab === 'system'
                ? 'bg-indigo-600/20 text-indigo-300 border border-indigo-500/30'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
            }`}
          >
            <Server className="w-4 h-4" />
            <span>System Health</span>
          </button>

          <button
            onClick={() => setActiveTab('audit')}
            className={`w-full flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-xs font-medium transition-all ${
              activeTab === 'audit'
                ? 'bg-indigo-600/20 text-indigo-300 border border-indigo-500/30'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
            }`}
          >
            <FileText className="w-4 h-4" />
            <span>Audit Logs</span>
          </button>
        </nav>

        {/* Footer with exit button */}
        <div className="p-4 border-t border-slate-800/80 bg-[#0E121D]">
          <div className="text-[11px] text-slate-400 mb-2 truncate">
            Admin: <span className="text-slate-200 font-mono">{currentUser.email}</span>
          </div>
          <button
            onClick={onExit}
            className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-medium transition-colors"
          >
            <LogOut className="w-3.5 h-3.5" />
            <span>Exit to Customer App</span>
          </button>
        </div>
      </aside>

      {/* Main Content Area */}
      <main className="flex-1 flex flex-col overflow-hidden bg-[#0A0D14]">
        {/* Top Header */}
        <header className="h-16 border-b border-slate-800/80 bg-[#121624]/60 backdrop-blur-md px-6 flex items-center justify-between flex-shrink-0">
          <div className="flex items-center gap-3">
            <h1 className="text-lg font-semibold text-white capitalize">
              {activeTab === 'overview' && 'Platform Overview & Core KPIs'}
              {activeTab === 'users' && 'Customer Profiles & Management'}
              {activeTab === 'tenants' && 'Workspaces & Organization Directory'}
              {activeTab === 'jobs' && 'AI Processing Engine Monitoring'}
              {activeTab === 'subscriptions' && 'Subscription Tiers & Lifecycle'}
              {activeTab === 'revenue' && 'Revenue & Payment Gateway Analytics'}
              {activeTab === 'credits' && 'Credit Ledger & Circulation Analytics'}
              {activeTab === 'system' && 'Infrastructure & Subsystem Diagnostics'}
              {activeTab === 'audit' && 'Security & Administrative Audit Trails'}
            </h1>
          </div>

          <div className="flex items-center gap-3">
            {(activeTab === 'overview' || activeTab === 'revenue') && (
              <div className="flex items-center bg-slate-900 border border-slate-800 rounded-xl p-1">
                {['today', '7d', '30d', '90d'].map((rangeOption) => (
                  <button
                    key={rangeOption}
                    onClick={() => setDateRange(rangeOption)}
                    className={`px-3 py-1 text-xs font-medium rounded-lg transition-all ${
                      dateRange === rangeOption
                        ? 'bg-indigo-600 text-white shadow-sm'
                        : 'text-slate-400 hover:text-white'
                    }`}
                  >
                    {rangeOption === 'today' ? 'Today' : rangeOption.toUpperCase()}
                  </button>
                ))}
              </div>
            )}

            <button
              onClick={loadActiveData}
              disabled={loading}
              className="p-2 rounded-xl bg-slate-800/80 hover:bg-slate-700 text-slate-300 transition-colors"
              title="Refresh Data"
            >
              <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            </button>
          </div>
        </header>

        {/* Content Body */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {error && (
            <div className="p-4 rounded-xl bg-red-950/40 border border-red-500/40 text-red-200 text-sm flex items-center gap-3">
              <AlertTriangle className="w-5 h-5 text-red-400 flex-shrink-0" />
              <span>{error}</span>
            </div>
          )}

          {/* TAB 1: OVERVIEW */}
          {activeTab === 'overview' && overview && (
            <div className="space-y-6">
              {/* Primary KPI Cards */}
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                <div className="p-5 rounded-2xl bg-[#121624] border border-slate-800/80">
                  <div className="text-slate-400 text-xs font-medium">Total Registered Users</div>
                  <div className="text-2xl font-bold text-white mt-1">{overview.users.total}</div>
                  <div className="text-xs text-emerald-400 mt-2 flex items-center gap-1">
                    <UserCheck className="w-3.5 h-3.5" />
                    <span>{overview.users.active} Active accounts</span>
                  </div>
                </div>

                <div className="p-5 rounded-2xl bg-[#121624] border border-slate-800/80">
                  <div className="text-slate-400 text-xs font-medium">Active Subscriptions</div>
                  <div className="text-2xl font-bold text-indigo-400 mt-1">{overview.subscriptions.active}</div>
                  <div className="text-xs text-slate-400 mt-2">
                    Across {overview.workspaces.total} workspaces
                  </div>
                </div>

                <div className="p-5 rounded-2xl bg-[#121624] border border-slate-800/80">
                  <div className="text-slate-400 text-xs font-medium">Jobs In Window ({dateRange})</div>
                  <div className="text-2xl font-bold text-white mt-1">{overview.jobs.total}</div>
                  <div className="text-xs text-emerald-400 mt-2">
                    {overview.jobs.success_rate_percent}% success rate ({overview.jobs.successful} done)
                  </div>
                </div>

                <div className="p-5 rounded-2xl bg-[#121624] border border-slate-800/80">
                  <div className="text-slate-400 text-xs font-medium">Net Revenue Collected</div>
                  <div className="text-2xl font-bold text-emerald-400 mt-1">
                    ₹{overview.financials.net_amount_inr.toLocaleString()}
                  </div>
                  <div className="text-xs text-slate-400 mt-2">
                    Gross: ₹{overview.financials.captured_amount_inr} | Ref: ₹{overview.financials.refunded_amount_inr}
                  </div>
                </div>
              </div>

              {/* Secondary Breakdown */}
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div className="p-5 rounded-2xl bg-[#121624] border border-slate-800/80">
                  <h3 className="text-sm font-semibold text-slate-200 mb-3 flex items-center gap-2">
                    <Cpu className="w-4 h-4 text-indigo-400" />
                    <span>Job Pipeline Status</span>
                  </h3>
                  <div className="space-y-2 text-xs">
                    <div className="flex justify-between py-1 border-b border-slate-800">
                      <span className="text-slate-400">Completed Successfully</span>
                      <span className="text-emerald-400 font-semibold">{overview.jobs.successful}</span>
                    </div>
                    <div className="flex justify-between py-1 border-b border-slate-800">
                      <span className="text-slate-400">Failed / Errors</span>
                      <span className="text-red-400 font-semibold">{overview.jobs.failed}</span>
                    </div>
                    <div className="flex justify-between py-1 border-b border-slate-800">
                      <span className="text-slate-400">Pending / Processing</span>
                      <span className="text-amber-400 font-semibold">{overview.jobs.pending_and_processing}</span>
                    </div>
                  </div>
                </div>

                <div className="p-5 rounded-2xl bg-[#121624] border border-slate-800/80">
                  <h3 className="text-sm font-semibold text-slate-200 mb-3 flex items-center gap-2">
                    <Coins className="w-4 h-4 text-amber-400" />
                    <span>Credit Consumption</span>
                  </h3>
                  <div className="text-3xl font-bold text-white mb-2">
                    {overview.credits.consumed_in_window.toLocaleString()}
                  </div>
                  <div className="text-xs text-slate-400">
                    Credits burned on AI operations during selected window ({dateRange}).
                  </div>
                </div>

                <div className="p-5 rounded-2xl bg-[#121624] border border-slate-800/80">
                  <h3 className="text-sm font-semibold text-slate-200 mb-3 flex items-center gap-2">
                    <Building2 className="w-4 h-4 text-violet-400" />
                    <span>Workspaces</span>
                  </h3>
                  <div className="text-3xl font-bold text-white mb-2">{overview.workspaces.total}</div>
                  <div className="text-xs text-slate-400">
                    Total provisioned customer workspaces with credit isolation.
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* TAB 2: CUSTOMERS */}
          {activeTab === 'users' && (
            <div className="space-y-4">
              <div className="flex items-center justify-between gap-4">
                <div className="flex-1 relative">
                  <Search className="w-4 h-4 text-slate-400 absolute left-3 top-3" />
                  <input
                    type="text"
                    placeholder="Search by email or name..."
                    value={usersSearch}
                    onChange={(e) => setUsersSearch(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && loadActiveData()}
                    className="w-full bg-[#121624] border border-slate-800 rounded-xl pl-9 pr-4 py-2 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
                  />
                </div>
                <select
                  value={usersStatus}
                  onChange={(e) => setUsersStatus(e.target.value)}
                  className="bg-[#121624] border border-slate-800 rounded-xl px-3 py-2 text-xs text-slate-200 focus:outline-none"
                >
                  <option value="all">All Statuses</option>
                  <option value="active">Active Only</option>
                  <option value="suspended">Suspended Only</option>
                </select>
              </div>

              <div className="bg-[#121624] border border-slate-800/80 rounded-2xl overflow-hidden">
                <table className="w-full text-left text-xs text-slate-300">
                  <thead className="bg-slate-900/60 text-slate-400 border-b border-slate-800">
                    <tr>
                      <th className="py-3 px-4">User</th>
                      <th className="py-3 px-4">Status</th>
                      <th className="py-3 px-4">Plan</th>
                      <th className="py-3 px-4">Credits</th>
                      <th className="py-3 px-4">Jobs</th>
                      <th className="py-3 px-4">Registered</th>
                      <th className="py-3 px-4 text-right">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/50">
                    {users.map((u) => (
                      <tr key={u.id} className="hover:bg-slate-800/20">
                        <td className="py-3 px-4">
                          <div className="font-medium text-white">{u.display_name || 'No Name'}</div>
                          <div className="text-[11px] text-slate-400 font-mono">{u.email}</div>
                          {u.is_platform_admin && (
                            <span className="inline-block mt-1 px-1.5 py-0.5 rounded bg-indigo-500/20 text-indigo-400 text-[10px] font-semibold">
                              Platform Admin
                            </span>
                          )}
                        </td>
                        <td className="py-3 px-4">
                          <span
                            className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold ${
                              u.is_active
                                ? 'bg-emerald-500/20 text-emerald-400'
                                : 'bg-red-500/20 text-red-400'
                            }`}
                          >
                            {u.is_active ? 'Active' : 'Suspended'}
                          </span>
                        </td>
                        <td className="py-3 px-4 uppercase font-semibold text-slate-300">{u.subscription_plan}</td>
                        <td className="py-3 px-4 font-mono font-medium text-amber-400">{u.credit_balance}</td>
                        <td className="py-3 px-4 text-slate-400">{u.jobs_count}</td>
                        <td className="py-3 px-4 text-slate-500">
                          {u.created_at ? new Date(u.created_at).toLocaleDateString() : 'N/A'}
                        </td>
                        <td className="py-3 px-4 text-right">
                          <button
                            onClick={() => setSelectedUser(u)}
                            className="px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-[11px] font-medium transition-colors"
                          >
                            Manage
                          </button>
                        </td>
                      </tr>
                    ))}
                    {users.length === 0 && !loading && (
                      <tr>
                        <td colSpan={7} className="py-8 text-center text-slate-500 text-xs">
                          No users matched the criteria.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* TAB 3: WORKSPACES */}
          {activeTab === 'tenants' && (
            <div className="space-y-4">
              <div className="flex items-center justify-between gap-4">
                <div className="flex-1 relative">
                  <Search className="w-4 h-4 text-slate-400 absolute left-3 top-3" />
                  <input
                    type="text"
                    placeholder="Search by workspace name or slug..."
                    value={tenantsSearch}
                    onChange={(e) => setTenantsSearch(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && loadActiveData()}
                    className="w-full bg-[#121624] border border-slate-800 rounded-xl pl-9 pr-4 py-2 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
                  />
                </div>
              </div>

              <div className="bg-[#121624] border border-slate-800/80 rounded-2xl overflow-hidden">
                <table className="w-full text-left text-xs text-slate-300">
                  <thead className="bg-slate-900/60 text-slate-400 border-b border-slate-800">
                    <tr>
                      <th className="py-3 px-4">Workspace</th>
                      <th className="py-3 px-4">Owner</th>
                      <th className="py-3 px-4">Members</th>
                      <th className="py-3 px-4">Wallet Balance</th>
                      <th className="py-3 px-4">Subscription</th>
                      <th className="py-3 px-4">Jobs</th>
                      <th className="py-3 px-4 text-right">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/50">
                    {tenants.map((t) => (
                      <tr key={t.id} className="hover:bg-slate-800/20">
                        <td className="py-3 px-4">
                          <div className="font-medium text-white">{t.name}</div>
                          <div className="text-[11px] text-slate-400 font-mono">{t.slug}</div>
                        </td>
                        <td className="py-3 px-4 text-slate-300 font-mono text-[11px]">{t.owner_email}</td>
                        <td className="py-3 px-4 text-slate-400">{t.member_count}</td>
                        <td className="py-3 px-4 font-mono font-medium text-amber-400">{t.wallet_balance} cr</td>
                        <td className="py-3 px-4 uppercase font-semibold text-slate-300">{t.subscription_plan}</td>
                        <td className="py-3 px-4 text-slate-400">{t.jobs_count}</td>
                        <td className="py-3 px-4 text-right">
                          <button
                            onClick={async () => {
                              const detail = await api.inspectAdminTenant(t.id);
                              setSelectedTenant(detail);
                            }}
                            className="px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-[11px] font-medium transition-colors"
                          >
                            Inspect
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* TAB 4: JOB MONITORING */}
          {activeTab === 'jobs' && (
            <div className="space-y-4">
              <div className="flex items-center gap-3">
                <select
                  value={jobsStatus}
                  onChange={(e) => setJobsStatus(e.target.value)}
                  className="bg-[#121624] border border-slate-800 rounded-xl px-3 py-2 text-xs text-slate-200 focus:outline-none"
                >
                  <option value="all">All States</option>
                  <option value="completed">Completed</option>
                  <option value="failed">Failed</option>
                  <option value="processing">Processing</option>
                  <option value="pending">Pending</option>
                </select>

                <select
                  value={jobsOp}
                  onChange={(e) => setJobsOp(e.target.value)}
                  className="bg-[#121624] border border-slate-800 rounded-xl px-3 py-2 text-xs text-slate-200 focus:outline-none"
                >
                  <option value="all">All Operations</option>
                  <option value="upscale_2x">Upscale 2x</option>
                  <option value="upscale_4x">Upscale 4x</option>
                  <option value="enhance">Enhance</option>
                  <option value="bg_remove">Background Removal</option>
                  <option value="colorize">Colorize</option>
                  <option value="inpaint">Inpaint</option>
                </select>
              </div>

              <div className="bg-[#121624] border border-slate-800/80 rounded-2xl overflow-hidden">
                <table className="w-full text-left text-xs text-slate-300">
                  <thead className="bg-slate-900/60 text-slate-400 border-b border-slate-800">
                    <tr>
                      <th className="py-3 px-4">Job ID</th>
                      <th className="py-3 px-4">Operation</th>
                      <th className="py-3 px-4">Status</th>
                      <th className="py-3 px-4">Credits</th>
                      <th className="py-3 px-4">Duration</th>
                      <th className="py-3 px-4">Created At</th>
                      <th className="py-3 px-4 text-right">Details</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/50">
                    {jobs.map((j) => (
                      <tr key={j.id} className="hover:bg-slate-800/20">
                        <td className="py-3 px-4 font-mono text-[11px] text-slate-400">{j.id.slice(0, 12)}...</td>
                        <td className="py-3 px-4 font-semibold text-white uppercase">{j.operation_type}</td>
                        <td className="py-3 px-4">
                          <span
                            className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold ${
                              j.status === 'completed'
                                ? 'bg-emerald-500/20 text-emerald-400'
                                : j.status === 'failed'
                                ? 'bg-red-500/20 text-red-400'
                                : 'bg-amber-500/20 text-amber-400'
                            }`}
                          >
                            {j.status}
                          </span>
                        </td>
                        <td className="py-3 px-4 font-mono text-amber-400 font-medium">{j.credit_cost} cr</td>
                        <td className="py-3 px-4 text-slate-400 font-mono">
                          {j.duration_seconds ? `${j.duration_seconds}s` : 'N/A'}
                        </td>
                        <td className="py-3 px-4 text-slate-500">
                          {j.created_at ? new Date(j.created_at).toLocaleString() : 'N/A'}
                        </td>
                        <td className="py-3 px-4 text-right">
                          <button
                            onClick={async () => {
                              const detail = await api.inspectAdminJob(j.id);
                              setSelectedJob(detail);
                            }}
                            className="px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-[11px] font-medium transition-colors"
                          >
                            Inspect
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* TAB 5: SUBSCRIPTIONS */}
          {activeTab === 'subscriptions' && (
            <div className="space-y-4">
              <div className="flex items-center gap-3">
                <select
                  value={subsPlan}
                  onChange={(e) => setSubsPlan(e.target.value)}
                  className="bg-[#121624] border border-slate-800 rounded-xl px-3 py-2 text-xs text-slate-200 focus:outline-none"
                >
                  <option value="all">All Plans</option>
                  <option value="free">Free Explorer</option>
                  <option value="starter">Starter Creator</option>
                  <option value="pro">Pro Studio</option>
                  <option value="studio">Studio Agency</option>
                </select>
              </div>

              <div className="bg-[#121624] border border-slate-800/80 rounded-2xl overflow-hidden">
                <table className="w-full text-left text-xs text-slate-300">
                  <thead className="bg-slate-900/60 text-slate-400 border-b border-slate-800">
                    <tr>
                      <th className="py-3 px-4">Workspace</th>
                      <th className="py-3 px-4">Plan Tier</th>
                      <th className="py-3 px-4">Status</th>
                      <th className="py-3 px-4">Price</th>
                      <th className="py-3 px-4">Renewal Date</th>
                      <th className="py-3 px-4 text-right">Modify</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/50">
                    {subscriptions.map((s) => (
                      <tr key={s.id} className="hover:bg-slate-800/20">
                        <td className="py-3 px-4 font-medium text-white">{s.tenant_name}</td>
                        <td className="py-3 px-4 font-semibold text-indigo-400 uppercase">{s.plan_name}</td>
                        <td className="py-3 px-4">
                          <span
                            className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold ${
                              s.status === 'active'
                                ? 'bg-emerald-500/20 text-emerald-400'
                                : 'bg-slate-500/20 text-slate-400'
                            }`}
                          >
                            {s.status}
                          </span>
                        </td>
                        <td className="py-3 px-4 font-mono text-slate-300">₹{s.price_inr}</td>
                        <td className="py-3 px-4 text-slate-400">
                          {s.current_period_end ? new Date(s.current_period_end).toLocaleDateString() : 'N/A'}
                        </td>
                        <td className="py-3 px-4 text-right">
                          <button
                            onClick={() => setSelectedSub(s)}
                            className="px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-[11px] font-medium transition-colors"
                          >
                            Change Plan
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* TAB 6: REVENUE */}
          {activeTab === 'revenue' && revenue && (
            <div className="space-y-6">
              <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                <div className="p-5 rounded-2xl bg-[#121624] border border-slate-800/80">
                  <div className="text-slate-400 text-xs font-medium">Gross Collected</div>
                  <div className="text-2xl font-bold text-white mt-1">
                    ₹{revenue.financial_summary.gross_collected_inr.toLocaleString()}
                  </div>
                  <div className="text-[11px] text-slate-400 mt-2">
                    {revenue.financial_summary.successful_transactions} successful payments
                  </div>
                </div>

                <div className="p-5 rounded-2xl bg-[#121624] border border-slate-800/80">
                  <div className="text-slate-400 text-xs font-medium">Total Refunds</div>
                  <div className="text-2xl font-bold text-red-400 mt-1">
                    ₹{revenue.financial_summary.refunds_inr.toLocaleString()}
                  </div>
                  <div className="text-[11px] text-slate-400 mt-2">Reversals & chargebacks</div>
                </div>

                <div className="p-5 rounded-2xl bg-[#121624] border border-slate-800/80">
                  <div className="text-slate-400 text-xs font-medium">Net Collected</div>
                  <div className="text-2xl font-bold text-emerald-400 mt-1">
                    ₹{revenue.financial_summary.net_collected_inr.toLocaleString()}
                  </div>
                  <div className="text-[11px] text-slate-400 mt-2">Gross less refunded</div>
                </div>

                <div className="p-5 rounded-2xl bg-[#121624] border border-slate-800/80">
                  <div className="text-slate-400 text-xs font-medium">Estimated MRR</div>
                  <div className="text-2xl font-bold text-indigo-400 mt-1">
                    ₹{revenue.mrr.estimated_monthly_recurring_revenue_inr.toLocaleString()}
                  </div>
                  <div className="text-[11px] text-slate-400 mt-2">Active paid subscriptions</div>
                </div>
              </div>

              {/* Disclaimer Banner */}
              <div className="p-4 rounded-xl bg-slate-900 border border-slate-800 text-slate-400 text-xs">
                <span className="font-semibold text-slate-300">Accounting Note:</span> {revenue.disclaimer}
              </div>

              {/* Recent Payments Table */}
              <div className="bg-[#121624] border border-slate-800/80 rounded-2xl overflow-hidden">
                <div className="p-4 border-b border-slate-800 font-semibold text-sm text-slate-200">
                  Payment Transaction History
                </div>
                <table className="w-full text-left text-xs text-slate-300">
                  <thead className="bg-slate-900/60 text-slate-400 border-b border-slate-800">
                    <tr>
                      <th className="py-3 px-4">Transaction ID</th>
                      <th className="py-3 px-4">Workspace</th>
                      <th className="py-3 px-4">Amount</th>
                      <th className="py-3 px-4">Status</th>
                      <th className="py-3 px-4">Provider</th>
                      <th className="py-3 px-4">Date</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/50">
                    {payments.map((p) => (
                      <tr key={p.id} className="hover:bg-slate-800/20">
                        <td className="py-3 px-4 font-mono text-slate-400 text-[11px]">
                          {p.provider_payment_id || p.id}
                        </td>
                        <td className="py-3 px-4 font-medium text-white">{p.tenant_name}</td>
                        <td className="py-3 px-4 font-mono font-semibold text-emerald-400">₹{p.amount_inr}</td>
                        <td className="py-3 px-4">
                          <span
                            className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold ${
                              p.status === 'captured'
                                ? 'bg-emerald-500/20 text-emerald-400'
                                : p.status === 'refunded'
                                ? 'bg-amber-500/20 text-amber-400'
                                : 'bg-red-500/20 text-red-400'
                            }`}
                          >
                            {p.status}
                          </span>
                        </td>
                        <td className="py-3 px-4 uppercase text-[11px] text-slate-400">{p.provider}</td>
                        <td className="py-3 px-4 text-slate-500">
                          {p.created_at ? new Date(p.created_at).toLocaleString() : 'N/A'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* TAB 7: CREDITS */}
          {activeTab === 'credits' && credits && (
            <div className="space-y-6">
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div className="p-5 rounded-2xl bg-[#121624] border border-slate-800/80">
                  <div className="text-slate-400 text-xs font-medium">Credits in Circulation</div>
                  <div className="text-3xl font-bold text-amber-400 mt-1">
                    {credits.circulation.credits_in_circulation.toLocaleString()}
                  </div>
                  <div className="text-xs text-slate-400 mt-2">
                    Across {credits.circulation.total_wallets} tenant wallets
                  </div>
                </div>

                <div className="p-5 rounded-2xl bg-[#121624] border border-slate-800/80">
                  <div className="text-slate-400 text-xs font-medium">Total Lifetime Consumed</div>
                  <div className="text-3xl font-bold text-white mt-1">
                    {credits.cumulative_totals.total_credits_consumed.toLocaleString()}
                  </div>
                  <div className="text-xs text-slate-400 mt-2">Executed via AI jobs</div>
                </div>

                <div className="p-5 rounded-2xl bg-[#121624] border border-slate-800/80">
                  <div className="text-slate-400 text-xs font-medium">Administrative Adjustments</div>
                  <div className="text-3xl font-bold text-indigo-400 mt-1">
                    {credits.administrative_adjustments.total_adjusted_credits.toLocaleString()}
                  </div>
                  <div className="text-xs text-slate-400 mt-2">
                    {credits.administrative_adjustments.adjustment_count} recorded adjustments
                  </div>
                </div>
              </div>

              {/* Disclosure Banner */}
              <div className="p-4 rounded-xl bg-indigo-950/30 border border-indigo-500/30 text-indigo-300 text-xs leading-relaxed">
                <span className="font-semibold text-white">System Disclosure:</span>{' '}
                {credits.accounting_architecture_disclosure}
              </div>
            </div>
          )}

          {/* TAB 8: SYSTEM HEALTH */}
          {activeTab === 'system' && systemHealth && (
            <div className="space-y-6">
              <div className="flex items-center gap-3 p-4 rounded-2xl bg-[#121624] border border-slate-800">
                <span
                  className={`w-3.5 h-3.5 rounded-full ${
                    systemHealth.overall_health === 'healthy' ? 'bg-emerald-400 animate-pulse' : 'bg-amber-400'
                  }`}
                ></span>
                <div>
                  <div className="text-sm font-semibold text-white capitalize">
                    Overall Subsystem Status: {systemHealth.overall_health}
                  </div>
                  <div className="text-xs text-slate-400">
                    Probed at: {new Date(systemHealth.timestamp).toLocaleTimeString()}
                  </div>
                </div>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {/* FastAPI */}
                <div className="p-5 rounded-2xl bg-[#121624] border border-slate-800/80">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs text-slate-400 font-medium">FastAPI Server</span>
                    <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-emerald-500/20 text-emerald-400">
                      Healthy
                    </span>
                  </div>
                  <div className="text-sm font-semibold text-white">
                    Version: {systemHealth.components.fastapi.api_version}
                  </div>
                  <div className="text-xs text-slate-400 mt-1">
                    Environment: {systemHealth.components.fastapi.environment}
                  </div>
                </div>

                {/* Database */}
                <div className="p-5 rounded-2xl bg-[#121624] border border-slate-800/80">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs text-slate-400 font-medium">Database Layer</span>
                    <span
                      className={`px-2 py-0.5 rounded text-[10px] font-semibold ${
                        systemHealth.components.database.status === 'healthy'
                          ? 'bg-emerald-500/20 text-emerald-400'
                          : 'bg-red-500/20 text-red-400'
                      }`}
                    >
                      {systemHealth.components.database.status}
                    </span>
                  </div>
                  <div className="text-sm font-semibold text-white">
                    Engine: {systemHealth.components.database.engine}
                  </div>
                  <div className="text-xs text-slate-400 mt-1">
                    Ping Latency: {systemHealth.components.database.latency_ms} ms
                  </div>
                </div>

                {/* Redis / Limiter */}
                <div className="p-5 rounded-2xl bg-[#121624] border border-slate-800/80">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs text-slate-400 font-medium">Redis & Rate Limiter</span>
                    <span
                      className={`px-2 py-0.5 rounded text-[10px] font-semibold ${
                        systemHealth.components.redis.status === 'connected'
                          ? 'bg-emerald-500/20 text-emerald-400'
                          : 'bg-amber-500/20 text-amber-400'
                      }`}
                    >
                      {systemHealth.components.redis.status}
                    </span>
                  </div>
                  <div className="text-sm font-semibold text-white">
                    Limiter Mode: {systemHealth.components.redis.rate_limiter_mode}
                  </div>
                  {systemHealth.components.redis.note && (
                    <div className="text-[11px] text-amber-300 mt-1">
                      {systemHealth.components.redis.note}
                    </div>
                  )}
                </div>

                {/* Queue Depth */}
                <div className="p-5 rounded-2xl bg-[#121624] border border-slate-800/80">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs text-slate-400 font-medium">Worker Queue Depth</span>
                    <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-emerald-500/20 text-emerald-400">
                      {systemHealth.components.queue.status}
                    </span>
                  </div>
                  <div className="text-sm font-semibold text-white">
                    Pending Jobs: {systemHealth.components.queue.pending_jobs}
                  </div>
                  <div className="text-xs text-slate-400 mt-1">
                    Processing: {systemHealth.components.queue.processing_jobs} | Max Workers:{' '}
                    {systemHealth.components.queue.max_concurrent_workers}
                  </div>
                </div>

                {/* Storage */}
                <div className="p-5 rounded-2xl bg-[#121624] border border-slate-800/80">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs text-slate-400 font-medium">Storage Subsystem</span>
                    <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-emerald-500/20 text-emerald-400">
                      {systemHealth.components.storage.status}
                    </span>
                  </div>
                  <div className="text-sm font-semibold text-white">
                    Provider: {systemHealth.components.storage.provider}
                  </div>
                  <div className="text-xs text-slate-400 mt-1 truncate">
                    Target: {systemHealth.components.storage.directory || systemHealth.components.storage.bucket}
                  </div>
                </div>

                {/* 24h Error Rate */}
                <div className="p-5 rounded-2xl bg-[#121624] border border-slate-800/80">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs text-slate-400 font-medium">24h Job Telemetry</span>
                    <span
                      className={`px-2 py-0.5 rounded text-[10px] font-semibold ${
                        systemHealth.components.job_failure_telemetry_24h.status === 'healthy'
                          ? 'bg-emerald-500/20 text-emerald-400'
                          : 'bg-amber-500/20 text-amber-400'
                      }`}
                    >
                      {systemHealth.components.job_failure_telemetry_24h.status}
                    </span>
                  </div>
                  <div className="text-sm font-semibold text-white">
                    Failure Rate:{' '}
                    {systemHealth.components.job_failure_telemetry_24h.failure_rate_percent}%
                  </div>
                  <div className="text-xs text-slate-400 mt-1">
                    Failed:{' '}
                    {systemHealth.components.job_failure_telemetry_24h.failed_jobs_24h} of{' '}
                    {systemHealth.components.job_failure_telemetry_24h.total_jobs_24h} total
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* TAB 9: AUDIT LOGS */}
          {activeTab === 'audit' && (
            <div className="space-y-4">
              <div className="bg-[#121624] border border-slate-800/80 rounded-2xl overflow-hidden">
                <table className="w-full text-left text-xs text-slate-300">
                  <thead className="bg-slate-900/60 text-slate-400 border-b border-slate-800">
                    <tr>
                      <th className="py-3 px-4">Action</th>
                      <th className="py-3 px-4">Administrator</th>
                      <th className="py-3 px-4">Target</th>
                      <th className="py-3 px-4">Reason</th>
                      <th className="py-3 px-4">Status</th>
                      <th className="py-3 px-4">Timestamp</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/50">
                    {auditLogs.map((log) => (
                      <tr key={log.id} className="hover:bg-slate-800/20">
                        <td className="py-3 px-4 font-mono font-semibold text-indigo-400">{log.action}</td>
                        <td className="py-3 px-4 font-mono text-[11px] text-slate-300">{log.actor_email}</td>
                        <td className="py-3 px-4 font-mono text-[11px] text-slate-400">
                          {log.target_type ? `${log.target_type}:${log.target_id?.slice(0, 8)}` : 'N/A'}
                        </td>
                        <td className="py-3 px-4 text-slate-300 max-w-xs truncate">{log.reason || 'None'}</td>
                        <td className="py-3 px-4">
                          <span
                            className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold ${
                              log.status === 'success'
                                ? 'bg-emerald-500/20 text-emerald-400'
                                : 'bg-red-500/20 text-red-400'
                            }`}
                          >
                            {log.status}
                          </span>
                        </td>
                        <td className="py-3 px-4 text-slate-500">
                          {log.created_at ? new Date(log.created_at).toLocaleString() : 'N/A'}
                        </td>
                      </tr>
                    ))}
                    {auditLogs.length === 0 && (
                      <tr>
                        <td colSpan={6} className="py-8 text-center text-slate-500 text-xs">
                          No audit log records recorded yet.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      </main>

      {/* USER MANAGE MODAL */}
      {selectedUser && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
          <div className="w-full max-w-md bg-[#121624] border border-slate-800 rounded-2xl p-6 space-y-4 shadow-2xl">
            <h3 className="text-base font-semibold text-white">Manage Customer Account</h3>
            <div className="space-y-1 text-xs text-slate-400">
              <div>
                User: <span className="text-white font-mono">{selectedUser.email}</span>
              </div>
              <div>
                ID: <span className="font-mono text-slate-500">{selectedUser.id}</span>
              </div>
              <div>
                Current Status:{' '}
                <span className={selectedUser.is_active ? 'text-emerald-400' : 'text-red-400 font-semibold'}>
                  {selectedUser.is_active ? 'Active' : 'Suspended'}
                </span>
              </div>
            </div>

            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1">
                Administrative Audit Reason (Mandatory)
              </label>
              <textarea
                value={actionReason}
                onChange={(e) => setActionReason(e.target.value)}
                placeholder="Reason for suspending or restoring account..."
                className="w-full bg-[#0A0D14] border border-slate-800 rounded-xl p-3 text-xs text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500 resize-none h-20"
              />
            </div>

            <div className="flex items-center justify-end gap-3 pt-2">
              <button
                onClick={() => {
                  setSelectedUser(null);
                  setActionReason('');
                }}
                className="px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-medium"
              >
                Cancel
              </button>
              <button
                disabled={actionLoading}
                onClick={() => handleUserStatusToggle(selectedUser.id, selectedUser.is_active)}
                className={`px-4 py-2 rounded-xl text-xs font-semibold text-white shadow-lg transition-all ${
                  selectedUser.is_active
                    ? 'bg-red-600 hover:bg-red-500 shadow-red-500/20'
                    : 'bg-emerald-600 hover:bg-emerald-500 shadow-emerald-500/20'
                }`}
              >
                {selectedUser.is_active ? 'Confirm Suspension' : 'Confirm Restoration'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* SUBSCRIPTION MODIFY MODAL */}
      {selectedSub && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
          <div className="w-full max-w-md bg-[#121624] border border-slate-800 rounded-2xl p-6 space-y-4 shadow-2xl">
            <h3 className="text-base font-semibold text-white">Modify Subscription Tier</h3>
            <div className="space-y-1 text-xs text-slate-400">
              <div>
                Workspace: <span className="text-white font-medium">{selectedSub.tenant_name}</span>
              </div>
              <div>
                Current Plan: <span className="text-indigo-400 uppercase font-semibold">{selectedSub.plan_code}</span>
              </div>
            </div>

            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1">Select New Plan Tier</label>
              <select
                value={newPlanCode}
                onChange={(e) => setNewPlanCode(e.target.value)}
                className="w-full bg-[#0A0D14] border border-slate-800 rounded-xl p-2.5 text-xs text-white focus:outline-none focus:border-indigo-500"
              >
                <option value="free">Free Explorer (0 INR / mo)</option>
                <option value="starter">Starter Creator (199 INR / mo)</option>
                <option value="pro">Pro Studio (499 INR / mo)</option>
                <option value="studio">Studio Agency (999 INR / mo)</option>
              </select>
            </div>

            <div>
              <label className="block text-xs font-medium text-slate-300 mb-1">
                Audit Justification (Mandatory)
              </label>
              <textarea
                value={actionReason}
                onChange={(e) => setActionReason(e.target.value)}
                placeholder="Audit reason for modifying customer subscription..."
                className="w-full bg-[#0A0D14] border border-slate-800 rounded-xl p-3 text-xs text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500 resize-none h-20"
              />
            </div>

            <div className="flex items-center justify-end gap-3 pt-2">
              <button
                onClick={() => {
                  setSelectedSub(null);
                  setActionReason('');
                }}
                className="px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-medium"
              >
                Cancel
              </button>
              <button
                disabled={actionLoading}
                onClick={() => handleModifySubscription(selectedSub.id)}
                className="px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-xs font-semibold text-white shadow-lg shadow-indigo-500/20"
              >
                Apply Plan Change
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
