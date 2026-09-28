import { execFileSync } from "node:child_process";
import { test, expect, request } from "@playwright/test";
import { BACKEND_URL, chatOnce, CHAT_TIMEOUT_MS } from "./helpers";

test.describe("chat round-trip", () => {
  test.setTimeout(CHAT_TIMEOUT_MS * 2 + 90_000);

  test("agent responds and persists both messages for the Akte use-case", async () => {
    const startedAt = new Date();
    // Best-effort warmup; tolerate cold-start.
    await chatOnce("ping", "akte-agent", 30_000).catch(() => undefined);

    const api = await request.newContext();
    const created = await api.post(`${BACKEND_URL}/api/conversations`, {
      data: { useCase: "akte-agent", title: "e2e-smoke persistence" },
    });
    expect(created.status(), "create smoke conversation status").toBe(201);
    const { id: conversationId } = await created.json();
    expect(conversationId, "smoke conversation id").toBeTruthy();

    try {
      const prompt = "Reply with exactly one short sentence confirming you are online.";
      const { text, ok, status } = await chatOnce(
        prompt,
        "akte-agent",
        CHAT_TIMEOUT_MS,
        conversationId,
      );

      expect(
        ok,
        `chat should succeed (status=${status}, text snippet="${text.slice(0, 200)}")`,
      ).toBe(true);
      expect(
        text.trim().length,
        "assistant response should be non-empty",
      ).toBeGreaterThan(0);

      const resp = await api.get(
        `${BACKEND_URL}/api/conversations/${encodeURIComponent(conversationId)}/messages`,
      );
      expect(resp.status(), "conversation history status").toBe(200);
      const messages: { role: string; content: string }[] = await resp.json();
      expect(messages, "user message persisted").toEqual(
        expect.arrayContaining([expect.objectContaining({ role: "user", content: prompt })]),
      );
      expect(
        messages.some((message) => message.role === "assistant" && message.content.trim().length > 0),
        "assistant message persisted",
      ).toBe(true);

      const appId = process.env.KRATOS_APP_INSIGHTS_ID;
      if (!appId) {
        const reason = "Cosmos firewall telemetry check skipped: KRATOS_APP_INSIGHTS_ID is unavailable (Azure telemetry access required)";
        if (process.env.CI) throw new Error(reason);
        test.info().annotations.push({ type: "skip-reason", description: reason });
        console.log(reason);
      } else {
        // Allow the asynchronous App Insights exporter time to ingest this run's logs.
        await new Promise((resolve) => setTimeout(resolve, 15_000));
        const endedAt = new Date();
        const query = `
          union isfuzzy=true
            (traces | project timestamp, text=strcat(message, " ", tostring(customDimensions))),
            (exceptions | project timestamp, text=strcat(outerMessage, " ", innermostMessage, " ", tostring(details)))
          | where timestamp between (datetime(${startedAt.toISOString()}) .. datetime(${endedAt.toISOString()}))
          | where text has "firewall" and text has "Cosmos"
          | count
        `;
        const output = execFileSync(
          "az",
          ["monitor", "app-insights", "query", "--app", appId, "--analytics-query", query, "--output", "json"],
          { encoding: "utf8", timeout: 30_000 },
        );
        const result = JSON.parse(output);
        const count = result.tables?.[0]?.rows?.[0]?.[0];
        expect(typeof count, "Cosmos firewall telemetry query must return a count").toBe("number");
        expect(count, "Cosmos firewall-denial telemetry during smoke run").toBe(0);
      }
    } finally {
      await api.delete(`${BACKEND_URL}/api/conversations/${encodeURIComponent(conversationId)}`);
      await api.dispose();
    }
  });
});
