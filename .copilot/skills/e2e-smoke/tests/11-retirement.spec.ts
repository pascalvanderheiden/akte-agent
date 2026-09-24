/** Cloud-free upgrade acceptance. Synthetic API fixtures, not live model evaluations. */
import { test, expect, type Page } from "@playwright/test";
import { createRequire } from "node:module";
import { FRONTEND_URL } from "./helpers";
import type { Conversation, Locale, UseCase } from "../../../../src/frontend/src/types";

const { en, nl }: typeof import("../../../../src/frontend/src/lib/i18n") =
  createRequire(import.meta.url)("../../../../src/frontend/src/lib/i18n.ts");
const ui = { en, nl };
const origin = new URL(FRONTEND_URL).origin;
const mount = new URL(FRONTEND_URL).pathname.replace(/\/$/, "");

async function fixture(page: Page, locale: Locale, singleAkte = false) {
  const catalog: UseCase[] = [{ name: "akte-agent", displayName: "Akte Agent", description: "Synthetic", curated: true, skillCount: 0, sampleQuestions: [] }];
  if (!singleAkte) {
    catalog.push(
      { name: "synthetic-import", displayName: "Imported fixture", description: "Synthetic", curated: false, skillCount: 0, sampleQuestions: [] },
      { name: "synthetic-hidden", displayName: "Non-curated fixture", description: "Synthetic", curated: false, skillCount: 0, sampleQuestions: [] },
    );
  }
  const now = "2020-03-21T12:00:00Z";
  const conversations: Conversation[] = [{
    id: "synthetic-old", title: "Preserved retired history", useCase: "generic",
    status: "active", createdAt: now, updatedAt: now,
  }];
  const writes: string[] = [];
  const adminReads: string[] = [];
  await page.addInitScript((locale) => localStorage.setItem("kratos.locale", locale), locale);
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== origin) return route.abort();
    const path = url.pathname.slice(mount.length);
    if (path === "/config.json") return route.fulfill({ json: { apiUrl: `${origin}${mount}` } });
    if (!path.startsWith("/api/")) return route.continue();
    if (request.method() !== "GET") writes.push(path);
    if (path === "/api/use-cases") return route.fulfill({ json: { useCases: catalog } });
    if (path === "/api/models") return route.fulfill({ json: { models: [] } });
    if (path === "/api/conversations") return route.fulfill({ json: { conversations } });
    if (path.endsWith("/messages")) return route.fulfill({ json: [{
      id: "synthetic-message", conversationId: "synthetic-old", role: "assistant",
      content: `SYNTHETIC saved response\n\n[historical.txt](${origin}${mount}/api/files/download/historical.txt)`,
      createdAt: now,
    }, {
      id: "synthetic-unanswered", conversationId: "synthetic-old", role: "user",
      content: "SYNTHETIC unanswered question", createdAt: now,
    }] });
    if (path === "/api/admin/skills") {
      adminReads.push(url.searchParams.get("use_case") ?? "");
      return route.fulfill({ json: { skills: [] } });
    }
    if (path === "/api/files/download/historical.txt") return route.fulfill({
      contentType: "text/plain", body: "SYNTHETIC historical artifact; unchanged",
    });
    throw new Error(`Unexpected fixture API request: ${request.method()} ${path}`);
  });
  return { writes, adminReads, conversations };
}

for (const locale of ["en", "nl"] as const) {
  test(`${locale}: retired history remains discoverable and read-only, downloads survive`, async ({ page }) => {
    const state = await fixture(page, locale);
    await page.goto(`${FRONTEND_URL}/`);
    const catalog = await page.evaluate(async () => {
      const config = await (await fetch("config.json")).json();
      return (await (await fetch(`${config.apiUrl}/api/use-cases`)).json()).useCases as UseCase[];
    });
    await expect(page.getByRole("button", { name: /^(Approved|Goedgekeurd|All|Alle)$/i })).toHaveCount(0);
    const selector = page.getByRole("combobox", { name: ui[locale].selectPersona });
    await expect(selector.locator("option")).toHaveCount(catalog.length);
    const custom = catalog.find((persona) => persona.name === "synthetic-import")!;
    await selector.selectOption(custom.name);
    await page.getByRole("button", { name: /Preserved retired history/ }).first().click();
    await expect(selector).toHaveValue("generic");
    await expect(page.getByText(ui[locale]["error.PERSONA_UNAVAILABLE"], { exact: true })).toBeVisible();
    await expect(page.getByText("SYNTHETIC saved response", { exact: true })).toBeVisible();
    await expect(page.getByText("SYNTHETIC unanswered question", { exact: true })).toBeVisible();
    await expect(page.getByRole("textbox", { name: ui[locale].ask })).toBeDisabled();
    await expect(page.getByRole("button", { name: ui[locale].attach })).toBeDisabled();
    await expect(page.getByRole("button", { name: ui[locale].sendMessage })).toBeDisabled();
    const pending = page.waitForEvent("download");
    await page.getByRole("link", { name: /historical.txt.*download|Download historical.txt/i }).click();
    const download = await pending;
    const chunks: Buffer[] = [];
    for await (const chunk of (await download.createReadStream())!) chunks.push(Buffer.from(chunk));
    expect(Buffer.concat(chunks).toString()).toBe("SYNTHETIC historical artifact; unchanged");
    expect(state.writes).toEqual([]);
    expect(state.adminReads).not.toContain("generic");
    await page.getByRole("button", { name: ui[locale].startAvailableConversation }).click();
    await expect(page.getByRole("textbox", { name: ui[locale].ask })).toBeEnabled();
    expect(catalog.map((persona) => persona.name))
      .toContain(await selector.inputValue());
    expect(state.writes).toEqual([]);
    await page.getByRole("button", { name: /Preserved retired history/ }).first().click();
    await expect(page.getByText("SYNTHETIC saved response", { exact: true })).toBeVisible();
  });

  for (const embed of [false, true]) {
    test(`${locale}: ${embed ? "embedded" : "direct"} retired persona link never starts Generic`, async ({ page }) => {
      const state = await fixture(page, locale);
      await page.goto(`${FRONTEND_URL}/?persona=generic&prompt=Synthetic+old+prompt${embed ? "&embed=1" : ""}`);
      await expect(page.getByText(ui[locale]["error.PERSONA_UNAVAILABLE"], { exact: true })).toBeVisible();
      await expect(page.getByRole("combobox", { name: ui[locale].selectPersona })).toHaveValue("generic");
      await expect(page.getByRole("textbox", { name: ui[locale].ask })).toBeDisabled();
      expect(state.writes).toEqual([]);
      await page.getByRole("button", { name: ui[locale].startAvailableConversation }).click();
      await expect(page.getByRole("textbox", { name: ui[locale].ask })).toBeEnabled();
      expect(state.writes).toEqual([]);
    });
  }

  test(`${locale}: metadata-less history remains read-only and starts a separate Akte conversation`, async ({ page }) => {
    const state = await fixture(page, locale);
    state.conversations.push({
      id: "metadata-less",
      title: "Unknown history",
      useCase: "",
      status: "active",
      createdAt: "2020-03-21T12:00:00Z",
      updatedAt: "2020-03-21T12:00:00Z",
    });
    await page.goto(`${FRONTEND_URL}/`);
    await page.getByRole("button", { name: /Unknown history/ }).first().click();
    await expect(page.getByText(ui[locale]["error.PERSONA_UNAVAILABLE"], { exact: true })).toBeVisible();
    await expect(page.getByRole("textbox", { name: ui[locale].ask })).toBeDisabled();
    expect(state.writes).toEqual([]);
    await page.getByRole("button", { name: ui[locale].startAvailableConversation }).click();
    await expect(page.getByRole("textbox", { name: ui[locale].ask })).toBeEnabled();
    expect(state.writes).toEqual([]);
  });

  test(`${locale}: single Akte catalog preserves unavailable history without a selector`, async ({ page }) => {
    const state = await fixture(page, locale, true);
    state.conversations.push({
      id: "metadata-less",
      title: "Unknown history",
      useCase: "",
      status: "active",
      createdAt: "2020-03-21T12:00:00Z",
      updatedAt: "2020-03-21T12:00:00Z",
    });
    await page.goto(`${FRONTEND_URL}/`);
    await expect(page.getByRole("combobox", { name: ui[locale].selectPersona })).toHaveCount(0);
    await page.getByRole("button", { name: /Preserved retired history/ }).first().click();
    await expect(page.getByText(ui[locale]["error.PERSONA_UNAVAILABLE"], { exact: true })).toBeVisible();
    await expect(page.getByRole("textbox", { name: ui[locale].ask })).toBeDisabled();
    const pending = page.waitForEvent("download");
    await page.getByRole("link", { name: /historical.txt.*download|Download historical.txt/i }).click();
    const download = await pending;
    expect(await download.suggestedFilename()).toBe("historical.txt");
    await page.getByRole("button", { name: ui[locale].startAvailableConversation }).click();
    await expect(page.getByRole("textbox", { name: ui[locale].ask })).toBeEnabled();
    await page.getByRole("button", { name: /Unknown history/ }).first().click();
    await expect(page.getByText(ui[locale]["error.PERSONA_UNAVAILABLE"], { exact: true })).toBeVisible();
    await expect(page.getByRole("textbox", { name: ui[locale].ask })).toBeDisabled();
    expect(state.writes).toEqual([]);
  });
}
