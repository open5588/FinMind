import { api } from './client';

export interface WeeklyDigestSpike {
  type: 'CATEGORY_SPIKE' | 'TRANSACTION_SPIKE';
  category_id: number | null;
  category_name: string;
  amount: number;
  baseline_amount: number;
  increase_pct: number;
  severity: 'MEDIUM' | 'HIGH';
  reason: string;
  expense_id?: number;
  description?: string;
  date?: string;
}

export interface WeeklyDigestResponse {
  digest_id?: number;
  persisted?: boolean;
  generated_at?: string;
  period: {
    week_start: string;
    week_end: string;
    prev_week_start: string;
    prev_week_end: string;
    days: number;
  };
  currency: string;
  summary: {
    total_income: number;
    total_expenses: number;
    net_flow: number;
    transaction_count: number;
    expense_transaction_count: number;
    daily_average_spend: number;
    savings_rate_pct: number;
    upcoming_bills_total: number;
    upcoming_bills_count: number;
  };
  trends: {
    direction: 'UP' | 'DOWN' | 'STABLE';
    trend_direction: 'UP' | 'DOWN' | 'STABLE';
    week_over_week_change_pct: number;
    wow_change_pct: number;
    week_over_week_delta: number;
    current_week_expenses: number;
    previous_week_expenses: number;
    rolling_4_week_avg: number;
    vs_4_week_avg_pct: number;
  };
  spikes: WeeklyDigestSpike[];
  top_categories: Array<{
    category_id: number | null;
    category_name: string;
    amount: number;
    share_pct: number;
    change_pct: number;
  }>;
  highlights: string[];
  recommendations: string[];
}

export async function getWeeklyDigest(params?: {
  week_start?: string;
  week_end?: string;
  date?: string;
}): Promise<WeeklyDigestResponse> {
  const qs = new URLSearchParams();
  if (params?.week_start) qs.set('week_start', params.week_start);
  if (params?.week_end) qs.set('week_end', params.week_end);
  if (params?.date) qs.set('date', params.date);
  const suffix = qs.toString() ? `?${qs.toString()}` : '';
  return api<WeeklyDigestResponse>(`/api/digests/weekly${suffix}`);
}

export async function generateWeeklyDigest(payload?: {
  week_start?: string;
  week_end?: string;
  notify?: boolean;
  channel?: 'email' | 'whatsapp';
}): Promise<WeeklyDigestResponse> {
  return api<WeeklyDigestResponse>('/api/digests/generate', {
    method: 'POST',
    body: payload || {},
  });
}
