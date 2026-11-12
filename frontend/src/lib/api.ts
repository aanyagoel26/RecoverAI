const BASE = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, "") ?? "";
const TOKEN_KEY = "recoverai.token";

let memoryToken: string | null = null;
export function getToken(): string | null {
  if (memoryToken) return memoryToken;
  try { memoryToken = localStorage.getItem(TOKEN_KEY); } catch { /* storage unavailable */ }
  return memoryToken;
}
export function setToken(t: string | null) {
  memoryToken = t;
  try { if (t) localStorage.setItem(TOKEN_KEY, t); else localStorage.removeItem(TOKEN_KEY); } catch { /* ignore */ }
}

export class ApiError extends Error {
  status: number;
  data: unknown;
  constructor(status: number, message: string, data: unknown) { super(message); this.status = status; this.data = data; }
}

function messageFrom(data: any, status: number): string {
  const d = data?.detail;
  if (typeof d === "string") return d;
  if (d && typeof d === "object" && d.message) return `${d.issue ? d.issue + ": " : ""}${d.message}`;
  if (Array.isArray(d)) return d.map((x: any) => x.msg ?? x.message).join(", ");
  return status === 0 ? "Cannot reach the RecoverAI API" : `Request failed (${status})`;
}

export async function api<T = any>(path: string, opts: { method?: string; body?: unknown; auth?: boolean } = {}): Promise<T> {
  const headers: Record<string, string> = {};
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  const tok = getToken();
  if (opts.auth !== false && tok) headers.Authorization = `Bearer ${tok}`;
  let res: Response;
  try {
    res = await fetch(BASE + path, { method: opts.method ?? (opts.body !== undefined ? "POST" : "GET"), headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined });
  } catch {
    throw new ApiError(0, "Cannot reach the RecoverAI API. Is the backend running?", null);
  }
  const text = await res.text();
  let data: any = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) {
    if (res.status === 401 && opts.auth !== false && tok) { setToken(null); window.dispatchEvent(new Event("recoverai:logout")); }
    throw new ApiError(res.status, messageFrom(data, res.status), data);
  }
  return data as T;
}

export async function downloadCsv(path: string, filename: string) {
  const res = await fetch(BASE + path, { headers: { Authorization: `Bearer ${getToken() ?? ""}` } });
  if (!res.ok) throw new ApiError(res.status, "Export failed", null);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
