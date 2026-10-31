export async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...(body === undefined ? {} : { method: 'POST', body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' } }),
    cache: 'no-store',
  });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const error = await response.json();
      message = typeof error.detail === 'string' ? error.detail : JSON.stringify(error.detail ?? error);
    } catch { /* Keep the status if the server response is not JSON. */ }
    throw new Error(message);
  }
  return response.json();
}
export const money = (n: number | null | undefined, digits = 2) => n == null ? '—' : new Intl.NumberFormat('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(n);
export const signed = (n: number | null | undefined, suffix = '') => n == null ? '—' : `${n >= 0 ? '+' : '−'}${money(Math.abs(n))}${suffix}`;
export const date = (ts: number, detailed = false) => new Date(ts * 1000).toLocaleString('en-GB', { timeZone: 'UTC', day: '2-digit', month: 'short', ...(detailed ? { hour: '2-digit', minute: '2-digit' } : { year: 'numeric' }) });
