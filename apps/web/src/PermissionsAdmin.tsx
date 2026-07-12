import { useCallback, useEffect, useState } from "react";

import { getAccessToken } from "./auth/msal";

// Minimal permissions-admin screen (D43, WP 0.8). Deliberately lean: list users, show grants,
// grant/revoke domains, apply a role template. Authorization lives in Postgres RLS — a
// non-admin sees only themselves and gets 403 on writes; this UI just surfaces that.

const API = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";
const DOMAINS = [
  "time_entry",
  "expense_entry",
  "invoicing",
  "accounting_reporting",
  "compensation_admin",
] as const;
const TEMPLATES = ["Agent", "Paralegal", "Bookkeeper", "Principal"] as const;

interface UserGrants {
  user_id: string;
  email: string;
  display_name: string;
  role_template: string | null;
  is_active: boolean;
  domains: string[];
}

async function call(path: string, init?: RequestInit): Promise<Response> {
  const token = await getAccessToken();
  return fetch(`${API}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...init?.headers,
    },
  });
}

export default function PermissionsAdmin() {
  const [users, setUsers] = useState<UserGrants[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const resp = await call("/api/v1/admin/permissions");
    if (!resp.ok) {
      setError(`Load failed: ${resp.status}`);
      return;
    }
    setUsers(await resp.json());
    setError(null);
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function toggle(user: UserGrants, domain: string) {
    const has = user.domains.includes(domain);
    const resp = has
      ? await call(`/api/v1/admin/permissions/grants/${user.user_id}/${domain}`, { method: "DELETE" })
      : await call("/api/v1/admin/permissions/grants", {
          method: "POST",
          body: JSON.stringify({ user_id: user.user_id, domain }),
        });
    if (!resp.ok) setError(`${has ? "Revoke" : "Grant"} failed: ${resp.status}`);
    await refresh();
  }

  async function applyTemplate(user: UserGrants, template: string) {
    const resp = await call("/api/v1/admin/permissions/apply-template", {
      method: "POST",
      body: JSON.stringify({ user_id: user.user_id, template }),
    });
    if (!resp.ok) setError(`Apply template failed: ${resp.status}`);
    await refresh();
  }

  return (
    <section>
      <h2>Permissions</h2>
      {error && <p style={{ color: "crimson" }}>{error}</p>}
      <table style={{ borderCollapse: "collapse", width: "100%" }}>
        <thead>
          <tr>
            <th style={{ textAlign: "left", padding: 6 }}>User</th>
            {DOMAINS.map((d) => (
              <th key={d} style={{ padding: 6, fontSize: 12 }}>{d.replace("_", " ")}</th>
            ))}
            <th style={{ padding: 6 }}>Template</th>
          </tr>
        </thead>
        <tbody>
          {users.map((u) => (
            <tr key={u.user_id} style={{ borderTop: "1px solid #ddd" }}>
              <td style={{ padding: 6 }}>
                {u.display_name}
                <div style={{ fontSize: 12, color: "#666" }}>{u.email}</div>
              </td>
              {DOMAINS.map((d) => (
                <td key={d} style={{ textAlign: "center", padding: 6 }}>
                  <input
                    type="checkbox"
                    checked={u.domains.includes(d)}
                    onChange={() => void toggle(u, d)}
                  />
                </td>
              ))}
              <td style={{ padding: 6 }}>
                <select
                  value={u.role_template ?? ""}
                  onChange={(e) => void applyTemplate(u, e.target.value)}
                >
                  <option value="" disabled>
                    apply…
                  </option>
                  {TEMPLATES.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
