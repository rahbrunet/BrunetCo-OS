import { useEffect, useState } from "react";
import type { HealthResponse } from "@brunetco/contracts";

import { api } from "./api/client";
import PermissionsAdmin from "./PermissionsAdmin";

// WP 0.7 proved the typed contract loop (health); WP 0.8 adds the permissions-admin screen.
export default function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<"home" | "permissions">("home");

  useEffect(() => {
    api.health().then(setHealth).catch((e: Error) => setError(e.message));
  }, []);

  return (
    <main style={{ fontFamily: "system-ui", maxWidth: 900, margin: "3rem auto", padding: "0 1rem" }}>
      <h1>BrunetCo OS</h1>
      <nav style={{ display: "flex", gap: 12, marginBottom: 16 }}>
        <button onClick={() => setView("home")}>Home</button>
        <button onClick={() => setView("permissions")}>Permissions admin</button>
      </nav>
      {error && <pre style={{ color: "crimson" }}>API error: {error}</pre>}
      {view === "home" &&
        (health ? (
          <pre style={{ background: "#f4f4f5", padding: "1rem", borderRadius: 8 }}>
            {JSON.stringify(health, null, 2)}
          </pre>
        ) : (
          !error && <p>Loading health…</p>
        ))}
      {view === "permissions" && <PermissionsAdmin />}
    </main>
  );
}
