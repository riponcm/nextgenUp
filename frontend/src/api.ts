/**
 * Demerivo PixelFix — Typed API Client.
 * Handles JWT authentication, active tenant scoping, error parsing, and direct uploads.
 */

import {
  AICapability,
  CreditTransaction,
  Invoice,
  Plan,
  ProcessingJob,
  Subscription,
  Tenant,
  User,
  Wallet,
} from './types';

const API_BASE = '/api/v1';

class ApiError extends Error {
  status: number;
  data: any;

  constructor(message: string, status: number, data?: any) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.data = data;
  }
}

export class ApiClient {
  private token: string | null = null;
  private activeTenantId: string | null = null;

  constructor() {
    this.token = localStorage.getItem('demerivo_token');
    this.activeTenantId = localStorage.getItem('demerivo_tenant_id');
  }

  setToken(token: string | null) {
    this.token = token;
    if (token) {
      localStorage.setItem('demerivo_token', token);
    } else {
      localStorage.removeItem('demerivo_token');
    }
  }

  getToken(): string | null {
    return this.token;
  }

  setTenantId(tenantId: string | null) {
    this.activeTenantId = tenantId;
    if (tenantId) {
      localStorage.setItem('demerivo_tenant_id', tenantId);
    } else {
      localStorage.removeItem('demerivo_tenant_id');
    }
  }

  getTenantId(): string | null {
    return this.activeTenantId;
  }

  private async request<T>(
    endpoint: string,
    options: RequestInit = {}
  ): Promise<T> {
    const headers: Record<string, string> = {
      ...(options.headers as Record<string, string>),
    };

    if (this.token) {
      headers['Authorization'] = `Bearer ${this.token}`;
    }

    if (this.activeTenantId) {
      headers['X-Tenant-ID'] = this.activeTenantId;
    }

    if (!(options.body instanceof FormData) && !headers['Content-Type']) {
      headers['Content-Type'] = 'application/json';
    }

    const response = await fetch(`${API_BASE}${endpoint}`, {
      ...options,
      headers,
    });

    if (!response.ok) {
      let errorData;
      try {
        errorData = await response.json();
      } catch {
        errorData = { detail: response.statusText };
      }
      const message = errorData.detail || errorData.error || 'An unexpected error occurred';
      throw new ApiError(message, response.status, errorData);
    }

    if (response.status === 204) {
      return {} as T;
    }

    return response.json();
  }

  // --- Auth APIs ---
  async register(email: string, password: string, displayName?: string): Promise<{ user: User; token: string }> {
    const res = await this.request<{ user: User; session: { access_token: string } }>('/auth/register', {
      method: 'POST',
      body: JSON.stringify({ email, password, display_name: displayName }),
    });
    this.setToken(res.session.access_token);
    return { user: res.user, token: res.session.access_token };
  }

  async login(email: string, password: string): Promise<{ user: User; token: string }> {
    const res = await this.request<{ user: User; session: { access_token: string } }>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    });
    this.setToken(res.session.access_token);
    return { user: res.user, token: res.session.access_token };
  }

  async getCurrentUser(): Promise<User> {
    const res = await this.request<{ user: User }>('/profile/me');
    return res.user;
  }

  async updateProfile(displayName: string, avatarUrl?: string): Promise<User> {
    const res = await this.request<{ user: User }>('/profile/me', {
      method: 'PATCH',
      body: JSON.stringify({ display_name: displayName, avatar_url: avatarUrl }),
    });
    return res.user;
  }

  // --- Tenants & Workspaces ---
  async getTenants(): Promise<Tenant[]> {
    const res = await this.request<{ tenants: Tenant[] }>('/tenants');
    return res.tenants;
  }

  // --- Credit Wallet ---
  async getWallet(): Promise<Wallet> {
    const res = await this.request<{ wallet: Wallet }>('/credits/wallet');
    return res.wallet;
  }

  async getCreditHistory(): Promise<CreditTransaction[]> {
    const res = await this.request<{ transactions: CreditTransaction[] }>('/credits/history');
    return res.transactions;
  }

  // --- AI Studio & Processing Capabilities ---
  async getCapabilities(): Promise<AICapability[]> {
    const res = await this.request<any>('/jobs/capabilities');
    return Array.isArray(res) ? res : (res && res.capabilities) || [];
  }

  async getJobDownloadUrl(jobId: string): Promise<string> {
    const res = await this.request<{ job_id: string; download_url: string; expires_in: number }>(`/jobs/${jobId}/download`);
    return res.download_url;
  }

  async uploadImage(file: File): Promise<{ file_key: string }> {
    const formData = new FormData();
    formData.append('file', file);
    return this.request<{ file_key: string }>('/uploads/direct', {
      method: 'POST',
      body: formData,
    });
  }

  async createJob(operationType: string, inputKey: string, parameters: Record<string, any> = {}): Promise<ProcessingJob> {
    return this.request<ProcessingJob>('/jobs', {
      method: 'POST',
      body: JSON.stringify({
        operation_type: operationType,
        input_key: inputKey,
        parameters,
      }),
    });
  }

  async getJob(jobId: string): Promise<ProcessingJob> {
    return this.request<ProcessingJob>(`/jobs/${jobId}`);
  }

  async listJobs(): Promise<ProcessingJob[]> {
    const res = await this.request<{ jobs: ProcessingJob[] }>('/jobs');
    return res.jobs;
  }

  async cancelJob(jobId: string): Promise<ProcessingJob> {
    return this.request<ProcessingJob>(`/jobs/${jobId}/cancel`, {
      method: 'POST',
    });
  }

  async getDownloadUrl(fileKey: string): Promise<string> {
    // If local storage provider, points to /api/v1/storage/file/{key}
    return `/api/v1/storage/file/${fileKey}`;
  }

  // --- Subscription Billing & Payments ---
  async getPlans(): Promise<Plan[]> {
    const res = await this.request<{ plans: Plan[] }>('/billing/plans');
    return res.plans;
  }

  async getSubscription(): Promise<{ subscription: Subscription; plan: Plan | null }> {
    return this.request<{ subscription: Subscription; plan: Plan | null }>('/billing/subscription');
  }

  async createCheckout(planCode: string): Promise<any> {
    return this.request<any>('/billing/subscription', {
      method: 'POST',
      body: JSON.stringify({ plan_code: planCode }),
    });
  }

  async verifyPayment(orderId: string, paymentId: string, signature: string, planCode: string): Promise<any> {
    return this.request<any>('/billing/verify-payment', {
      method: 'POST',
      body: JSON.stringify({
        order_id: orderId,
        payment_id: paymentId,
        signature,
        plan_code: planCode,
      }),
    });
  }

  async cancelSubscription(immediately: boolean = false): Promise<any> {
    return this.request<any>('/billing/subscription/cancel', {
      method: 'POST',
      body: JSON.stringify({ immediately }),
    });
  }

  async getBillingHistory(): Promise<Invoice[]> {
    const res = await this.request<{ history: Invoice[] }>('/billing/history');
    return res.history;
  }

  // --- Platform Administration APIs ---
  async getAdminOverview(range: string = '30d', startDate?: string, endDate?: string): Promise<any> {
    let url = `/admin/analytics/overview?range=${encodeURIComponent(range)}`;
    if (startDate) url += `&start_date=${encodeURIComponent(startDate)}`;
    if (endDate) url += `&end_date=${encodeURIComponent(endDate)}`;
    return this.request<any>(url);
  }

  async getAdminRevenue(range: string = '30d', startDate?: string, endDate?: string): Promise<any> {
    let url = `/admin/analytics/revenue?range=${encodeURIComponent(range)}`;
    if (startDate) url += `&start_date=${encodeURIComponent(startDate)}`;
    if (endDate) url += `&end_date=${encodeURIComponent(endDate)}`;
    return this.request<any>(url);
  }

  async getAdminCredits(): Promise<any> {
    return this.request<any>('/admin/analytics/credits');
  }

  async getAdminUsers(page: number = 1, pageSize: number = 20, search?: string, statusFilter?: string): Promise<any> {
    let url = `/admin/users?page=${page}&page_size=${pageSize}`;
    if (search) url += `&search=${encodeURIComponent(search)}`;
    if (statusFilter && statusFilter !== 'all') url += `&status=${encodeURIComponent(statusFilter)}`;
    return this.request<any>(url);
  }

  async inspectAdminUser(userId: string): Promise<any> {
    return this.request<any>(`/admin/users/${userId}`);
  }

  async updateAdminUserStatus(userId: string, action: string, reason: string): Promise<any> {
    return this.request<any>(`/admin/users/${userId}/status`, {
      method: 'POST',
      body: JSON.stringify({ action, reason }),
    });
  }

  async getAdminTenants(page: number = 1, pageSize: number = 20, search?: string): Promise<any> {
    let url = `/admin/tenants?page=${page}&page_size=${pageSize}`;
    if (search) url += `&search=${encodeURIComponent(search)}`;
    return this.request<any>(url);
  }

  async inspectAdminTenant(tenantId: string): Promise<any> {
    return this.request<any>(`/admin/tenants/${tenantId}`);
  }

  async getAdminJobs(page: number = 1, pageSize: number = 20, statusFilter?: string, operationType?: string): Promise<any> {
    let url = `/admin/jobs?page=${page}&page_size=${pageSize}`;
    if (statusFilter && statusFilter !== 'all') url += `&status=${encodeURIComponent(statusFilter)}`;
    if (operationType && operationType !== 'all') url += `&operation_type=${encodeURIComponent(operationType)}`;
    return this.request<any>(url);
  }

  async inspectAdminJob(jobId: string): Promise<any> {
    return this.request<any>(`/admin/jobs/${jobId}`);
  }

  async getAdminSubscriptions(page: number = 1, pageSize: number = 20, planCode?: string, statusFilter?: string): Promise<any> {
    let url = `/admin/subscriptions?page=${page}&page_size=${pageSize}`;
    if (planCode && planCode !== 'all') url += `&plan_code=${encodeURIComponent(planCode)}`;
    if (statusFilter && statusFilter !== 'all') url += `&status=${encodeURIComponent(statusFilter)}`;
    return this.request<any>(url);
  }

  async modifyAdminSubscription(subscriptionId: string, planCode: string, reason: string, cancelAtPeriodEnd?: boolean): Promise<any> {
    return this.request<any>(`/admin/subscriptions/${subscriptionId}/modify`, {
      method: 'POST',
      body: JSON.stringify({ plan_code: planCode, reason, cancel_at_period_end: cancelAtPeriodEnd }),
    });
  }

  async getAdminPayments(page: number = 1, pageSize: number = 20, statusFilter?: string, search?: string): Promise<any> {
    let url = `/admin/payments?page=${page}&page_size=${pageSize}`;
    if (statusFilter && statusFilter !== 'all') url += `&status=${encodeURIComponent(statusFilter)}`;
    if (search) url += `&search=${encodeURIComponent(search)}`;
    return this.request<any>(url);
  }

  async getAdminAuditLogs(page: number = 1, pageSize: number = 20, action?: string, actorEmail?: string): Promise<any> {
    let url = `/admin/audit-logs?page=${page}&page_size=${pageSize}`;
    if (action) url += `&action=${encodeURIComponent(action)}`;
    if (actorEmail) url += `&actor_email=${encodeURIComponent(actorEmail)}`;
    return this.request<any>(url);
  }

  async getAdminSystemHealth(): Promise<any> {
    return this.request<any>('/admin/system/health');
  }
}

export const api = new ApiClient();
