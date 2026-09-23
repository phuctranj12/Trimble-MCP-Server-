export type Me =
  | { signed_in: false }
  | {
      signed_in: true;
      user: { id: string; email: string | null; name: string | null };
      trimble_connected: boolean;
      token_expires_at: number | null;
    };

export type Project = {
  id: string;
  name?: string;
  region?: string;
  updatedOn?: string;
  modifiedOn?: string;
  location?: string;
};

export type ProjectList = {
  source: string;
  fetched_at: string;
  count: number;
  projects: Project[];
  region_errors: { region: string; code: string; message: string }[];
};

export type McpKey = { id: string; label: string | null; created_at: number; last_used_at: number | null };

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const msg = body?.error?.message ?? body?.detail ?? `HTTP ${resp.status}`;
    throw new ApiError(resp.status, msg);
  }
  return body as T;
}

export const api = {
  me: () => request<Me>("/api/me"),
  projects: () => request<ProjectList>("/api/projects"),
  listKeys: () => request<{ keys: McpKey[] }>("/api/mcp-keys"),
  createKey: (label: string) =>
    request<{ id: string; key: string }>("/api/mcp-keys", { method: "POST", body: JSON.stringify({ label }) }),
  deleteKey: (id: string) => request<{ ok: boolean }>(`/api/mcp-keys/${id}`, { method: "DELETE" }),
  logout: () => request<{ ok: boolean }>("/auth/logout", { method: "POST" }),
  disconnect: () => request<{ ok: boolean }>("/auth/trimble/disconnect", { method: "POST" }),
};
