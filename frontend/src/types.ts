/**
 * TypeScript Data Models for Demerivo PixelFix.
 * Strongly typed contracts matching FastAPI v1 schemas.
 */

export type OperationType =
  | 'upscale_2x'
  | 'upscale_4x'
  | 'enhance'
  | 'bg_remove'
  | 'denoise'
  | 'deblur'
  | 'colorize'
  | 'inpaint';

export type JobStatus =
  | 'pending'
  | 'queued'
  | 'processing'
  | 'completed'
  | 'failed'
  | 'cancelled';

export interface User {
  id: string;
  email: string;
  display_name: string | null;
  avatar_url: string | null;
  is_active: boolean;
  is_platform_admin?: boolean;
  email_verified: boolean;
  created_at: string;
}

export interface Tenant {
  id: string;
  name: string;
  slug: string;
  is_personal: boolean;
  owner_id: string;
  role?: 'owner' | 'admin' | 'member';
}

export interface Wallet {
  id: string;
  tenant_id: string;
  balance: number;
}

export interface CreditTransaction {
  id: string;
  tenant_id: string;
  transaction_type: 'allocation' | 'reservation' | 'consumption' | 'refund' | 'cancellation';
  amount: number;
  balance_after: number;
  description: string | null;
  created_at: string;
}

export interface AICapability {
  operation_type: OperationType;
  display_name: string;
  description: string;
  credit_cost: number;
  model_name: string;
  max_resolution: [number, number];
  commercial_license_cleared: boolean;
  parameters_schema: Record<string, any>;
}

export interface ProcessingJob {
  id: string;
  tenant_id: string;
  user_id: string;
  status: JobStatus;
  operation_type: OperationType;
  input_key: string;
  output_key: string | null;
  progress: number;
  parameters: Record<string, any>;
  credit_cost: number;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
  download_url?: string;
  result_url?: string;
}

export interface Plan {
  code: string;
  name: string;
  description: string;
  price_paise: number;
  price_inr: number;
  monthly_credits: number;
  currency: string;
  features: string[];
}

export interface Subscription {
  id: string;
  tenant_id: string;
  plan_code: string;
  status: 'trialing' | 'active' | 'past_due' | 'cancelled' | 'expired' | 'paused';
  provider: string;
  current_period_start: string;
  current_period_end: string;
  cancel_at_period_end: boolean;
}

export interface Invoice {
  id: string;
  invoice_number: string;
  amount_paise: number;
  amount_inr: number;
  currency: string;
  status: 'paid' | 'unpaid' | 'void';
  billing_period_start: string;
  billing_period_end: string;
  created_at: string;
}

export type ViewTab =
  | 'overview'
  | 'studio'
  | 'history'
  | 'wallet'
  | 'billing'
  | 'settings'
  | 'admin';

export type AdminSubTab =
  | 'overview'
  | 'users'
  | 'tenants'
  | 'jobs'
  | 'subscriptions'
  | 'revenue'
  | 'credits'
  | 'system'
  | 'audit';

export interface AdminOverviewMetrics {
  date_range: {
    preset: string;
    start: string;
    end: string;
  };
  users: {
    total: number;
    active: number;
    suspended: number;
  };
  workspaces: {
    total: number;
  };
  subscriptions: {
    active: number;
  };
  jobs: {
    total: number;
    successful: number;
    failed: number;
    pending_and_processing: number;
    success_rate_percent: number;
  };
  credits: {
    consumed_in_window: number;
  };
  financials: {
    captured_amount_paise: number;
    captured_amount_inr: number;
    refunded_amount_paise: number;
    refunded_amount_inr: number;
    net_amount_paise: number;
    net_amount_inr: number;
    currency: string;
  };
}

export interface AdminUserItem {
  id: string;
  email: string;
  display_name: string | null;
  is_active: boolean;
  is_platform_admin: boolean;
  email_verified: boolean;
  created_at: string;
  workspaces_count: number;
  credit_balance: number;
  subscription_plan: string;
  jobs_count: number;
}

export interface AdminTenantItem {
  id: string;
  name: string;
  slug: string;
  is_personal: boolean;
  owner_id: string;
  owner_email: string;
  member_count: number;
  wallet_balance: number;
  subscription_plan: string;
  subscription_status: string;
  jobs_count: number;
  created_at: string;
}

export interface AdminJobItem {
  id: string;
  tenant_id: string;
  user_id: string;
  operation_type: string;
  status: string;
  progress: number;
  credit_cost: number;
  duration_seconds: number | null;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface AdminSubscriptionItem {
  id: string;
  tenant_id: string;
  tenant_name: string;
  plan_code: string;
  plan_name: string;
  price_inr: number;
  status: string;
  provider: string;
  current_period_start: string | null;
  current_period_end: string | null;
  cancel_at_period_end: boolean;
  created_at: string;
}

export interface AdminPaymentItem {
  id: string;
  tenant_id: string;
  tenant_name: string;
  provider: string;
  provider_payment_id: string | null;
  provider_order_id: string | null;
  amount_inr: number;
  currency: string;
  status: string;
  error_description: string | null;
  created_at: string;
}

export interface AdminAuditLogItem {
  id: string;
  actor_id: string | null;
  actor_email: string;
  action: string;
  target_type: string | null;
  target_id: string | null;
  reason: string | null;
  status: string;
  metadata: Record<string, any>;
  created_at: string;
}

export interface AdminSystemHealth {
  timestamp: string;
  overall_health: string;
  components: {
    fastapi: { status: string; environment: string; debug: boolean; api_version: string };
    database: { status: string; engine: string; latency_ms: number };
    redis: { status: string; queue_backend: string; rate_limiter_mode: string; note?: string };
    storage: { status: string; provider: string; directory?: string; bucket?: string };
    queue: { status: string; pending_jobs: number; processing_jobs: number; max_concurrent_workers: number };
    job_failure_telemetry_24h: { total_jobs_24h: number; failed_jobs_24h: number; failure_rate_percent: number; status: string };
  };
}

