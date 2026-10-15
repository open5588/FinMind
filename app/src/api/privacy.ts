import { api } from './client';

export interface GdprExportPackage {
  schema_version: string;
  exported_at: string;
  gdpr_metadata: {
    regulation: string;
    articles: string[];
    controller: string;
    subject_hash: string;
    audit_log_id: number;
    audit_checksum: string;
  };
  record_counts: Record<string, number>;
  integrity: {
    algorithm: string;
    checksum_sha256: string;
  };
  user: {
    id: number;
    email: string;
    preferred_currency: string;
    role: string;
    created_at: string;
  };
}

export interface GdprDeleteReceipt {
  status: string;
  message: string;
  irreversible: boolean;
  deleted_at: string;
  subject_hash: string;
  purged_counts: Record<string, number>;
  audit_trail: {
    audit_log_id: number;
    action: string;
    subject_hash: string;
    checksum: string;
    created_at: string;
  };
}

export async function exportUserData(): Promise<GdprExportPackage> {
  return api<GdprExportPackage>('/api/privacy/export', { method: 'GET' });
}

export async function deleteUserData(payload: {
  confirm: boolean;
  confirmation: string;
  reason?: string;
  password?: string;
}): Promise<GdprDeleteReceipt> {
  return api<GdprDeleteReceipt>('/api/privacy/delete', {
    method: 'POST',
    body: payload,
  });
}
