import { useEffect, useState } from "react";
import type { HealthResponse } from "@brunetco/contracts";

import { api } from "./api/client";

// Minimal SPA that round-trips the typed health payload — proves the
// OpenAPI -> contracts -> SPA loop end to end (WP 0.7 acceptance).
export default function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.health().then(setHealth).catch((e: Error) => setError(e.message));
  }, []);

  return (
    <main style={{ fontFamily: "system-ui", maxWidth: 640, margin: "4rem auto", padding: "0 1rem" }}>
      <h1>BrunetCo OS</h1>
      <p style={{ color: "#666" }}>WP 0.7 scaffold — API contract loop</p>
      {error && <pre style={{ color: "crimson" }}>API error: {error}</pre>}
      {health ? (
        <pre style={{ background: "#f4f4f5", padding: "1rem", borderRadius: 8 }}>
          {JSON.stringify(health, null, 2)}
        </pre>
      ) : (
        !error && <p>Loading health…</p>
      )}
    </main>
  );
}
