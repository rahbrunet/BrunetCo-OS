import { describe, expect, it, vi } from "vitest";
import { createApiClient } from "@brunetco/contracts";

describe("api client", () => {
  it("calls the health endpoint and returns the typed payload", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(
        JSON.stringify({ status: "ok", service: "brunetco-api", version: "0.7.0" }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const client = createApiClient({ baseUrl: "http://test" });
    const health = await client.health();

    expect(health.status).toBe("ok");
    expect(fetchMock).toHaveBeenCalledWith("http://test/api/v1/health", expect.any(Object));
  });
});
