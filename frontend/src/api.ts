import type { TransactionDetail, TransactionListItem, Verdict } from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

async function request<T>(path: string): Promise<T> {
  const resp = await fetch(`${API_BASE_URL}${path}`);
  if (!resp.ok) {
    throw new Error(`${resp.status} ${resp.statusText}`);
  }
  return resp.json();
}

export function listTransactions(verdict?: Verdict | ""): Promise<TransactionListItem[]> {
  const query = verdict ? `?verdict=${verdict}` : "";
  return request(`/transactions${query}`);
}

export function getTransaction(id: string): Promise<TransactionDetail> {
  return request(`/transactions/${id}`);
}
