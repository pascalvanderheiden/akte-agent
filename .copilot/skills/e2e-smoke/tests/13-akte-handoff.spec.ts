/**
 * Real six-stage helper files; deterministic model/transport only.
 * These tests do not certify live agent refusals or external integrations.
 */
import { test, expect, type Page } from "@playwright/test";
import { readFileSync, unlinkSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { FRONTEND_URL } from "./helpers";
import { loadCatalog, runPython } from "./akte-fixtures";
import type { ChatMessage, Conversation, Locale } from "../../../../src/frontend/src/types";

const { en, nl }: typeof import("../../../../src/frontend/src/lib/i18n") =
  createRequire(import.meta.url)("../../../../src/frontend/src/lib/i18n.ts");
const ui = { en, nl };
const origin = new URL(FRONTEND_URL).origin;
const mount = new URL(FRONTEND_URL).pathname.replace(/\/$/, "");
const generated: string[] = [];
type Draft = { name: string; path: string; text: string };

function journey(locale: Locale): Draft[] {
  const drafts: Draft[] = runPython(`
import json, sys
from pathlib import Path
from tests.akte_handoff_fixture import create_complete_journey
locale = json.load(sys.stdin)
documents = create_complete_journey(Path("../../use-cases/akte-agent").resolve(), locale, correction=True)
print(json.dumps([{"name": item["name"], "path": item["path"], "text": item["text"]} for item in documents]))
`, locale);
  generated.push(...drafts.map((item) => item.path));
  return drafts;
}

test.afterAll(() => { for (const filename of generated) unlinkSync(filename); });

async function fixture(page: Page, drafts: Draft[]) {
  const catalog = loadCatalog();
  const state = {
    calls: [] as { locale: Locale; useCase: string; conversationId: string; message: string }[],
    messages: [] as ChatMessage[], conversations: [] as Conversation[],
    failDownload: false, failGeneration: false,
  };
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== origin) return route.abort();
    const pathname = url.pathname.slice(mount.length);
    if (pathname === "/config.json") return route.fulfill({ json: { apiUrl: `${origin}${mount}` } });
    if (!pathname.startsWith("/api/")) return route.continue();
    if (pathname === "/api/use-cases") return route.fulfill({ json: { useCases: catalog } });
    if (pathname === "/api/admin/skills") return route.fulfill({ json: { skills: [] } });
    if (pathname === "/api/admin/mcp-servers") return route.fulfill({ json: { servers: {} } });
    if (pathname === "/api/conversations") {
      if (request.method() === "POST") {
        const body = request.postDataJSON();
        const conversation: Conversation = {
          id: "synthetic-handoff", title: body.title, useCase: body.useCase, status: "active",
          createdAt: "2026-01-15T12:00:00Z", updatedAt: "2026-01-15T12:00:00Z",
        };
        state.conversations.push(conversation);
        return route.fulfill({ status: 201, json: conversation });
      }
      return route.fulfill({ json: { conversations: state.conversations } });
    }
    if (pathname.endsWith("/messages")) return route.fulfill({ json: state.messages });
    if (pathname === "/api/conversations/synthetic-handoff" && request.method() === "PATCH") {
      state.conversations[0].title = request.postDataJSON().title;
      return route.fulfill({ json: {} });
    }
    if (pathname === "/api/agent/chat") {
      const body = request.postDataJSON();
      state.calls.push(body);
      const draft = drafts[state.calls.length - 1];
      const content = state.failGeneration
        ? (body.locale === "en" ? "Generation failed; no downloadable handoff." : "Aanmaken mislukt; geen downloadbare overdracht.")
        : `${draft.text}\n\n${draft.path}`;
      const now = "2026-01-15T12:00:00Z";
      state.messages.push(
        { id: `u-${state.calls.length}`, conversationId: body.conversationId, role: "user", content: body.message, createdAt: now },
        { id: `a-${state.calls.length}`, conversationId: body.conversationId, role: "assistant", content, createdAt: now },
      );
      return route.fulfill({
        contentType: "text/event-stream",
        body: [{ type: "content", content }, { type: "done" }].map((event) => `data: ${JSON.stringify(event)}\n\n`).join(""),
      });
    }
    if (pathname.startsWith("/api/files/download/")) {
      if (state.failDownload) return route.fulfill({ status: 404, json: { code: "DOWNLOAD_ERROR" } });
      const filename = url.searchParams.get("path")!;
      expect(drafts.map((draft) => draft.path)).toContain(filename);
      return route.fulfill({ contentType: "text/markdown", body: readFileSync(filename) });
    }
    throw new Error(`Unexpected API request: ${request.method()} ${pathname}`);
  });
  return state;
}

async function open(page: Page, locale: Locale) {
  await page.addInitScript((value) => {
    if (!sessionStorage.getItem("handoff-initialized")) {
      localStorage.setItem("kratos.locale", value);
      sessionStorage.setItem("handoff-initialized", "1");
    }
  }, locale);
  await page.goto(`${FRONTEND_URL}/`);
  await page.getByRole("combobox", { name: ui[locale].selectPersona }).selectOption("akte-agent");
}

async function download(page: Page, draft: Draft) {
  const link = page.getByRole("link", { name: new RegExp(path.basename(draft.path)) }).last();
  await expect(link).toBeVisible();
  const pending = page.waitForEvent("download");
  await link.click();
  const file = await pending;
  const chunks: Buffer[] = [];
  for await (const chunk of (await file.createReadStream())!) chunks.push(Buffer.from(chunk));
  const bytes = Buffer.concat(chunks);
  expect(bytes).toEqual(readFileSync(draft.path));
  return bytes.toString("utf8");
}

for (const locale of ["en", "nl"] as const) {
  test(`${locale}: all six stages, corrections, language override and preserved history`, async ({ page }) => {
    const drafts = journey(locale);
    const state = await fixture(page, drafts);
    await open(page, locale);
    let currentLocale: Locale = locale;
    let oldMessages: string[] = [];
    for (const draft of drafts) {
      if (draft.name === "corrected-invoice") {
        oldMessages = state.messages.map((message) => message.content);
        currentLocale = locale === "en" ? "nl" : "en";
        const box = page.getByRole("textbox", { name: ui[locale].ask });
        await box.fill("SYNTHETIC unsent correction");
        await page.getByRole("combobox", { name: /^(Language|Taal)$/ }).selectOption(currentLocale);
        await expect(page.getByRole("textbox", { name: ui[currentLocale].ask })).toHaveValue("SYNTHETIC unsent correction");
        await expect(page.getByRole("combobox", { name: ui[currentLocale].selectPersona })).toHaveValue("akte-agent");
        expect(state.messages.map((message) => message.content)).toEqual(oldMessages);
      }
      const prompt = `SYNTHETIC-AKTE-LEGAL ${draft.name}; ${locale === "en" ? "write output in English" : "schrijf uitvoer in het Nederlands"}; retain raw sources.`;
      await page.getByRole("textbox", { name: ui[currentLocale].ask }).fill(prompt);
      await page.getByRole("button", { name: ui[currentLocale].sendMessage, exact: true }).click();
      const text = await download(page, draft);
      expect(text).toContain("SYNTHETIC-AKTE-LEGAL");
      expect(text).toContain(locale === "en" ? "Status: DRAFT" : "Status: CONCEPT");
      if (draft.name === "deed") {
        expect(text).toContain(locale === "en" ? "[UNKNOWN: amount]" : "[ONBEKEND: bedrag]");
        expect(text).toContain("SYNTHETIC");
      }
      if (draft.name === "invoice") {
        for (const value of locale === "en" ? ["1.75", "105 minutes", "EUR 350.00", "EUR 400.00", "NOT POSTED / NOT SENT"]
          : ["1,75", "105 minuten", "EUR 350,00", "EUR 400,00", "NIET GEBOEKT / NIET VERZONDEN"]) expect(text).toContain(value);
      }
      if (draft.name === "corrected-invoice") {
        expect(text).toContain(locale === "en" ? "EUR 450.00" : "EUR 450,00");
        expect(text).toContain(locale === "en" ? "Exclude" : "Uitsluiten");
        expect(text).toContain("SYNTHETIC correction C");
      }
      if (draft.name.endsWith("handoff")) {
        expect(text).toContain("CTR");
        expect(text).toContain(locale === "en" ? "NOT SUBMITTED" : "NIET INGEDIEND");
        expect(text).toContain(locale === "en" ? "Closure evidence: registration" : "Afsluitbewijs: registration");
        expect(text).toContain(locale === "en" ? "PENDING - obtain" : "OPEN - verkrijg");
        expect(text).toContain(locale === "en" ? "-84.00" : "-84,00");
        expect(text).toContain(locale === "en" ? "not an approved legal archive" : "geen goedgekeurd juridisch archief");
        if (draft.name === "corrected-handoff") {
          expect(text).toContain("invoice@");
          expect(text).toContain("execution@");
          expect(text).toContain(locale === "en" ? "STALE" : "VEROUDERD");
        }
      }
      expect(state.calls.at(-1)!.locale).toBe(currentLocale);
    }
    expect(new Set(state.calls.map((call) => call.conversationId)).size).toBe(1);
    expect(state.calls.every((call) => call.useCase === "akte-agent")).toBe(true);
    expect(state.messages.slice(0, oldMessages.length).map((message) => message.content)).toEqual(oldMessages);
    const original = state.messages[0].content;
    await expect(page.getByText(original, { exact: true })).toBeVisible();
    await page.reload();
    await page.getByRole("combobox", { name: ui[currentLocale].selectPersona }).selectOption("akte-agent");
    await page.getByRole("button", { name: /SYNTHETIC-AKTE-LEGAL intake/ }).first().click();
    await expect(page.getByText(original, { exact: true })).toBeVisible();
    await download(page, drafts.at(-1)!);
  });

  test(`${locale}: direct stage-six entry and explicit generation/download failures`, async ({ page }) => {
    const drafts = journey(locale).filter((item) => item.name === "invoice" || item.name === "handoff");
    const state = await fixture(page, drafts);
    await open(page, locale);
    for (const draft of drafts) {
      await page.getByRole("textbox", { name: ui[locale].ask }).fill(`SYNTHETIC direct stage6 ${draft.name} with supplied dossier facts`);
      await page.getByRole("button", { name: ui[locale].sendMessage, exact: true }).click();
      await download(page, draft);
    }
    expect(state.calls).toHaveLength(2);
    state.failDownload = true;
    await page.getByRole("link", { name: new RegExp(path.basename(drafts[1].path)) }).last().click();
    await expect(page.getByRole("alert").filter({ hasText: ui[locale]["error.DOWNLOAD_ERROR"] })).toBeVisible();
    state.failGeneration = true;
    const links = await page.getByRole("link", { name: /akte-draft-/ }).count();
    await page.getByRole("textbox", { name: ui[locale].ask }).fill("SYNTHETIC regenerate unavailable handoff");
    await page.getByRole("button", { name: ui[locale].sendMessage, exact: true }).click();
    await expect(page.getByText(locale === "en" ? "Generation failed; no downloadable handoff." : "Aanmaken mislukt; geen downloadbare overdracht.")).toBeVisible();
    expect(await page.getByRole("link", { name: /akte-draft-/ }).count()).toBe(links);
  });
}
