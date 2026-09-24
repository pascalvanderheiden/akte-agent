/**
 * Deterministic browser acceptance for #18. All API/model responses are
 * intercepted synthetic fixtures; these are NOT live agent evaluations.
 * Uses the existing smoke runner against a local static export, at root or a
 * base path supplied by KRATOS_FRONTEND_URL. No live API writes.
 */
import { test, expect, type Page } from "@playwright/test";
import { createRequire } from "node:module";
import { FRONTEND_URL } from "./helpers";
import type { ChatMessage, Conversation, Locale, UseCase } from "../../../../src/frontend/src/types";

const { en, nl }: typeof import("../../../../src/frontend/src/lib/i18n") =
  createRequire(import.meta.url)("../../../../src/frontend/src/lib/i18n.ts");
const ui = { en, nl };
const origin = new URL(FRONTEND_URL).origin;
const mount = new URL(FRONTEND_URL).pathname.replace(/\/$/, "");
const localeSelect = (page: Page) => page.getByRole("combobox", { name: /^(Language|Taal)$/ });

function persona(name: string, curated = true): UseCase {
  return { name, displayName: name, description: `Synthetic ${name}`, sampleQuestions: [], skillCount: 2, curated };
}

async function fixture(page: Page) {
  const generic: UseCase = {
    ...persona("generic"),
    displayName: "Generic fixture",
    localizations: {
      en: { displayName: "Generic fixture", description: "Synthetic English assistant", sampleQuestions: ["Start a synthetic task"] },
      nl: { displayName: "Algemene testassistent", description: "Synthetische Nederlandse assistent", sampleQuestions: ["Begin een synthetische taak"] },
    },
  };
  const state = {
    catalog: [generic, persona("synthetic-custom"), persona("synthetic-experimental", false)],
    conversations: [] as Conversation[],
    messages: [] as ChatMessage[],
    calls: [] as { conversationId: string; message: string; locale: Locale; useCase: string; attachments?: unknown[] }[],
    paths: [] as string[],
    imported: null as unknown,
    prompt: "User-authored instructions; do not translate.",
    fail: "" as string,
  };
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== origin) return route.abort();
    const path = url.pathname.slice(mount.length);
    if (path === "/config.json") return route.fulfill({ json: { apiUrl: `${origin}${mount}` } });
    if (!path.startsWith("/api/")) return route.continue();
    state.paths.push(url.pathname);
    const method = request.method();
    const fail = (code: string) => route.fulfill({ status: 500, json: { code, detail: "SENSITIVE_SYNTHETIC_DIAGNOSTIC" } });
    if (path === "/api/use-cases") {
      if (state.fail === "catalog") return fail("CATALOG_ERROR");
      return route.fulfill({ json: { useCases: state.catalog } });
    }
    if (path === "/api/admin/skills") return route.fulfill({ json: { skills: [] } });
    if (path === "/api/admin/mcp-servers") return route.fulfill({ json: { servers: {} } });
    if (path === "/api/admin/system-prompt") {
      if (state.fail === "prompt") return fail("PROMPT_ERROR");
      if (method === "PUT") state.prompt = request.postDataJSON().content;
      return route.fulfill({ json: { content: state.prompt, isDefault: false } });
    }
    if (path.endsWith("/export")) {
      if (state.fail === "export") return fail("EXPORT_ERROR");
      return route.fulfill({ contentType: "application/zip", body: "synthetic ZIP fixture" });
    }
    if (path === "/api/use-cases/import") {
      if (state.fail === "import") return route.fulfill({ status: 401, json: { detail: "SENSITIVE_SYNTHETIC_DIAGNOSTIC" } });
      state.imported = request.postDataJSON().manifest;
      state.catalog.push({
        ...persona("synthetic-import"),
        localizations: { nl: { displayName: "Ingevoerde testpersona" } },
      });
      return route.fulfill({ status: 201, json: { name: "synthetic-import", created: true } });
    }
    if (path === "/api/conversations") {
      if (method === "POST") {
        if (state.fail === "create") return fail("CREATE_ERROR");
        const body = request.postDataJSON();
        const now = new Date().toISOString();
        const conversation: Conversation = { id: "synthetic-conversation", title: body.title, useCase: body.useCase, status: "active", createdAt: now, updatedAt: now };
        state.conversations.push(conversation);
        return route.fulfill({ status: 201, json: conversation });
      }
      return route.fulfill({ json: { conversations: state.conversations } });
    }
    if (path.endsWith("/messages")) {
      if (state.fail === "history") return fail("HISTORY_ERROR");
      return route.fulfill({ json: state.messages });
    }
    if (path.startsWith("/api/conversations/")) {
      if (method === "DELETE") {
        if (state.fail === "delete") return fail("DELETE_ERROR");
        state.conversations = [];
      } else if (method === "PATCH") {
        state.conversations[0].title = request.postDataJSON().title;
      }
      return route.fulfill({ json: {} });
    }
    if (path === "/api/agent/chat") {
      const body = request.postDataJSON();
      state.calls.push(body);
      if (state.fail === "chat") return fail("PROXY_ERROR");
      if (state.fail === "sse") return route.fulfill({
        contentType: "text/event-stream",
        body: 'event: error\ndata: {"code":"AGENT_ERROR","message":"SENSITIVE_SYNTHETIC_DIAGNOSTIC"}\n\nevent: done\ndata: {}\n\n',
      });
      const language = body.message.includes("in English") ? "en" : body.locale;
      const answer = language === "nl" ? "Synthetisch antwoord." : "Synthetic response.";
      const question = language === "nl" ? "Nog een synthetische vraag?" : "Another synthetic question?";
      const content = `${answer}\n\n[synthetic.txt](${origin}${mount}/api/files/download/synthetic.txt)`;
      const now = new Date().toISOString();
      state.messages.push(
        { id: `user-${state.calls.length}`, conversationId: body.conversationId, role: "user", content: body.message, createdAt: now, attachments: body.attachments },
        { id: `assistant-${state.calls.length}`, conversationId: body.conversationId, role: "assistant", content, createdAt: now },
      );
      const events = [
        { type: "content", content },
        { type: "follow_up_questions", questions: [question] },
        { type: "done", totalDurationMs: 1250, totalTokens: 1234, promptTokens: 1000, completionTokens: 234 },
      ];
      return route.fulfill({ contentType: "text/event-stream", body: events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join("") });
    }
    if (path.startsWith("/api/files/download/")) {
      if (state.fail === "download") return fail("DOWNLOAD_ERROR");
      return route.fulfill({ contentType: "text/plain", body: "SYNTHETIC SOURCE DOCUMENT - unchanged" });
    }
    throw new Error(`Unexpected fixture API request: ${method} ${path}`);
  });
  return state;
}

async function open(page: Page, locale: Locale = "en") {
  await page.addInitScript((value) => {
    if (!sessionStorage.getItem("fixture-initialized")) {
      localStorage.setItem("kratos.locale", value);
      sessionStorage.setItem("fixture-initialized", "1");
    }
  }, locale);
  await page.goto(`${FRONTEND_URL}/`);
  await expect(localeSelect(page)).toHaveValue(locale);
  await expect(page.getByRole("textbox", { name: ui[locale].ask })).toBeEnabled();
}

test("translation catalogs have matching keys and interpolation parameters", () => {
  expect(Object.keys(nl).sort()).toEqual(Object.keys(en).sort());
  for (const key of Object.keys(en) as (keyof typeof en)[]) {
    expect(nl[key].trim(), key).not.toBe("");
    expect(nl[key].match(/\{\w+\}/g)?.sort(), key).toEqual(en[key].match(/\{\w+\}/g)?.sort());
  }
});

for (const locale of ["en", "nl"] as const) {
  test(`${locale}: stored conversation dates use the selected locale`, async ({ page }) => {
    const state = await fixture(page);
    state.conversations.push({
      id: "synthetic-old", title: "Synthetic older conversation", useCase: "generic",
      status: "active", createdAt: "2020-03-21T12:00:00Z", updatedAt: "2020-03-21T12:00:00Z",
    });
    await open(page, locale);
    await expect(page.getByRole("button", { name: /Synthetic older conversation/ }).first())
      .toContainText(locale === "nl" ? "21 mrt" : "21 Mar");
    await expect(page.getByText(ui[locale].older, { exact: true })).toBeVisible();
  });

  test(`${locale}: landing, dynamic discovery, chat, downloads, prompt editor and failures`, async ({ page }) => {
    const state = await fixture(page);
    await open(page, locale);
    await expect(page.locator("html")).toHaveAttribute("lang", locale);
    await expect(page.getByRole("heading", { name: state.catalog[0].localizations![locale]!.displayName! })).toBeVisible();
    const select = page.getByRole("combobox", { name: ui[locale].selectPersona });
    expect(await select.locator("option").evaluateAll((nodes) => nodes.map((n) => (n as HTMLOptionElement).value)))
      .toEqual(state.catalog.filter((p) => p.curated).map((p) => p.name));
    await select.selectOption("synthetic-custom");
    await expect(page.getByRole("heading", { name: "synthetic-custom" })).toBeVisible();
    await select.selectOption("generic");
    await page.getByRole("textbox", { name: ui[locale].ask }).fill("Synthetic first message");
    await page.getByRole("button", { name: ui[locale].sendMessage, exact: true }).click();
    await expect(page.getByText(locale === "nl" ? "Synthetisch antwoord." : "Synthetic response.", { exact: true })).toBeVisible();
    expect(state.calls[0].locale).toBe(locale);
    await page.getByRole("button", { name: ui[locale]["execution.details"] }).click();
    await expect(page.getByText(locale === "nl" ? "1.234" : "1,234", { exact: true })).toBeVisible();
    await expect(page.getByText(locale === "nl" ? "1,3 sec" : "1.3 secs", { exact: true })).toBeVisible();
    const downloaded = page.waitForEvent("download");
    await page.getByRole("link", { name: /synthetic.txt.*download|Download synthetic.txt/i }).click();
    const download = await downloaded;
    expect(download.suggestedFilename()).toBe("synthetic.txt");
    const bytes: Buffer[] = [];
    for await (const chunk of (await download.createReadStream())!) bytes.push(Buffer.from(chunk));
    expect(Buffer.concat(bytes).toString()).toBe("SYNTHETIC SOURCE DOCUMENT - unchanged");
    state.fail = "download";
    await page.getByRole("link", { name: /synthetic.txt.*download|Download synthetic.txt/i }).click();
    await expect(page.getByRole("alert").filter({ hasText: ui[locale]["error.DOWNLOAD_ERROR"] })).toBeVisible();
    state.fail = "";
    await page.getByRole("button", { name: ui[locale].manager, exact: true }).click();
    await page.getByRole("button", { name: ui[locale]["prompt.title"], exact: true }).click();
    const editor = page.getByRole("textbox", { name: ui[locale]["prompt.title"], exact: true });
    await expect(editor).toHaveValue(state.prompt);
    await editor.fill("My unchanged custom instructions.");
    await page.getByRole("button", { name: ui[locale]["prompt.save"] }).click();
    await expect(page.getByRole("status").filter({ hasText: ui[locale]["prompt.saved"] })).toBeVisible();
    expect(state.prompt).toBe("My unchanged custom instructions.");
    state.fail = "prompt";
    await editor.fill("My unsaved draft");
    await page.getByRole("button", { name: ui[locale]["prompt.save"] }).click();
    await expect(page.getByRole("alert").filter({ hasText: ui[locale]["error.PROMPT_ERROR"] })).toBeVisible();
    await expect(editor).toHaveValue("My unsaved draft");
    state.fail = "export";
    await page.getByRole("button", { name: ui[locale]["export.deploy"], exact: true }).click();
    await page.getByRole("button", { name: /generic-foundry-agent.zip/ }).click();
    await expect(page.getByRole("alert").filter({ hasText: ui[locale]["error.EXPORT_ERROR"] })).toBeVisible();
    await expect(page.locator("body")).not.toContainText("SENSITIVE_SYNTHETIC_DIAGNOSTIC");
    expect(state.paths.every((path) => path.startsWith(`${mount}/api/`))).toBe(true);
  });
}

for (const preference of [
  { saved: "nl", languages: ["en-US"], expected: "nl" },
  { saved: "invalid", languages: ["fr-FR", "en-US", "nl-NL"], expected: "en" },
  { saved: "", languages: ["fr-FR", "nl-BE", "en-US"], expected: "nl" },
  { saved: "", languages: ["fr-FR", "de-DE"], expected: "en" },
]) {
  test(`preference: ${JSON.stringify(preference)}`, async ({ page }) => {
    await fixture(page);
    await page.addInitScript(({ saved, languages }) => {
      if (saved) localStorage.setItem("kratos.locale", saved);
      Object.defineProperty(navigator, "languages", { get: () => languages });
    }, preference);
    await page.goto(`${FRONTEND_URL}/`);
    await expect(localeSelect(page)).toHaveValue(preference.expected);
    await expect(page.locator("html")).toHaveAttribute("lang", preference.expected);
  });
}

test("unavailable storage does not crash locale, theme or embedded import", async ({ page }) => {
  const errors: Error[] = [];
  page.on("pageerror", (error) => errors.push(error));
  await fixture(page);
  await page.addInitScript(() => {
    Object.defineProperty(window, "localStorage", { get() { throw new DOMException("denied", "SecurityError"); } });
    Object.defineProperty(window, "sessionStorage", { get() { throw new DOMException("denied", "SecurityError"); } });
    Object.defineProperty(navigator, "languages", { get: () => ["nl-NL"] });
  });
  await page.goto(`${FRONTEND_URL}/?embed=1&import=1`);
  await expect(localeSelect(page)).toHaveValue("nl");
  await expect(page.getByRole("alert").filter({ hasText: nl["error.MANIFEST_MISSING"] })).toBeVisible();
  await localeSelect(page).selectOption("en");
  await expect(page.getByRole("alert").filter({ hasText: en["error.MANIFEST_MISSING"] })).toBeVisible();
  expect(errors).toEqual([]);
});

test("switching preserves conversation, history, draft, attachments, persona and theme; next turns carry locale", async ({ page }) => {
  const state = await fixture(page);
  await open(page);
  await page.getByRole("textbox", { name: en.ask }).fill("Original English message");
  await page.getByRole("button", { name: en.sendMessage, exact: true }).click();
  await expect(page.getByText("Synthetic response.", { exact: true })).toBeVisible();
  await page.getByRole("textbox", { name: en.ask }).fill("Unsent source text");
  await page.locator('input[type="file"]').setInputFiles({ name: "synthetic-source.txt", mimeType: "text/plain", buffer: Buffer.from("SYNTHETIC SOURCE - unchanged") });
  const theme = await page.locator("html").getAttribute("data-theme");
  await localeSelect(page).selectOption("nl");
  await expect(page.getByRole("textbox", { name: nl.ask })).toHaveValue("Unsent source text");
  await expect(page.getByRole("button", { name: "Bijlage verwijderen: synthetic-source.txt" })).toBeVisible();
  await expect(page.getByText("Synthetic response.", { exact: true })).toBeVisible();
  await expect(page.getByRole("combobox", { name: nl.selectPersona })).toHaveValue("generic");
  await expect(page.locator("html")).toHaveAttribute("data-theme", theme!);
  await page.getByRole("button", { name: nl.sendMessage, exact: true }).click();
  await expect(page.getByText("Synthetisch antwoord.", { exact: true })).toBeVisible();
  expect(state.calls[1]).toMatchObject({ conversationId: state.calls[0].conversationId, locale: "nl", message: "Unsent source text", useCase: "generic" });
  expect(state.calls[1].attachments).toEqual([expect.objectContaining({ displayName: "synthetic-source.txt", content: Buffer.from("SYNTHETIC SOURCE - unchanged").toString("base64") })]);
  await page.getByRole("textbox", { name: nl.ask }).fill("Write this output in English");
  await page.getByRole("button", { name: nl.sendMessage, exact: true }).click();
  await expect(page.getByRole("button", { name: "Another synthetic question?" })).toBeVisible();
  expect(state.calls[2].locale).toBe("nl");
  expect(await page.evaluate(() => localStorage.getItem("kratos.locale"))).toBe("nl");
  await page.reload();
  await expect(localeSelect(page)).toHaveValue("nl");
  await page.getByRole("button", { name: /Original English message/ }).first().click();
  await expect(page.getByText("Synthetisch antwoord.", { exact: true })).toBeVisible();
  await expect(page.getByText("Unsent source text", { exact: true })).toBeVisible();
  await expect(page.getByText("synthetic-source.txt", { exact: true })).toBeVisible();
  expect(state.calls).toHaveLength(3);
});

test("explicit preference persists across reload; catalog failure never invents Generic", async ({ page }) => {
  const state = await fixture(page);
  await page.goto(`${FRONTEND_URL}/`);
  await localeSelect(page).selectOption("nl");
  await page.reload();
  await expect(localeSelect(page)).toHaveValue("nl");
  state.fail = "catalog";
  await page.reload();
  await expect(page.getByRole("alert").filter({ hasText: nl["error.CATALOG_ERROR"] })).toBeVisible();
  await expect(page.getByRole("combobox", { name: nl.selectPersona })).toHaveCount(0);
  await expect(page.getByRole("button", { name: nl.sendMessage, exact: true })).toBeDisabled();
  state.fail = "";
  await page.getByRole("button", { name: nl.retry }).click();
  await expect(page.getByRole("heading", { name: "Algemene testassistent" })).toBeVisible();
  state.fail = "create";
  await page.getByRole("textbox", { name: nl.ask }).fill("Retain this draft");
  await page.getByRole("button", { name: nl.sendMessage, exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: nl["error.CREATE_ERROR"] })).toBeVisible();
  await expect(page.getByRole("textbox", { name: nl.ask })).toHaveValue("Retain this draft");
  expect(state.calls).toHaveLength(0);
});

test("embedded import retains metadata, chosen language, persona and base path", async ({ page }) => {
  const state = await fixture(page);
  const manifest = { name: "synthetic-import", instructions: "Keep these instructions", localizations: { nl: { displayName: "Ingevoerde testpersona" } } };
  await page.addInitScript((manifest) => {
    localStorage.setItem("kratos.locale", "nl");
    sessionStorage.setItem("kratos.import", JSON.stringify(manifest));
  }, manifest);
  await page.goto(`${FRONTEND_URL}/?embed=1&import=1&theme=dark`);
  await expect(page.getByRole("heading", { name: "Ingevoerde testpersona" })).toBeVisible();
  expect(state.imported).toEqual(manifest);
  await expect(page).toHaveURL(/persona=synthetic-import/);
  await expect(page.locator("html")).toHaveClass(/dark/);
  await expect(page.getByRole("link", { name: nl.backHost })).toBeVisible();
  expect(new URL(page.url()).pathname).toBe(`${mount}/`);
});

for (const locale of ["en", "nl"] as const) {
  test(`${locale}: chat HTTP/SSE failures, history retry and deletion rollback stay visible and safe`, async ({ page }) => {
    const state = await fixture(page);
    await open(page, locale);
    state.fail = "chat";
    await page.getByRole("textbox", { name: ui[locale].ask }).fill("Synthetic failed turn");
    await page.getByRole("button", { name: ui[locale].sendMessage, exact: true }).click();
    await expect(page.getByRole("alert").filter({ hasText: ui[locale]["error.PROXY_ERROR"] })).toBeVisible();
    state.fail = "sse";
    await page.getByRole("textbox", { name: ui[locale].ask }).fill("Synthetic protocol error");
    await page.getByRole("button", { name: ui[locale].sendMessage, exact: true }).click();
    await expect(page.getByRole("alert").filter({ hasText: ui[locale]["error.AGENT_ERROR"] })).toBeVisible();
    await expect(page.locator("body")).not.toContainText("SENSITIVE_SYNTHETIC_DIAGNOSTIC");
    state.fail = "history";
    await page.reload();
    await page.getByRole("button", { name: /Synthetic failed turn/ }).first().click();
    await expect(page.getByRole("alert").filter({ hasText: ui[locale]["error.HISTORY_ERROR"] })).toBeVisible();
    state.fail = "";
    await page.getByRole("button", { name: ui[locale].retry }).click();
    await expect(page.getByText(ui[locale]["chat.empty"])).toBeVisible();
    state.fail = "delete";
    await page.getByRole("button", { name: new RegExp(`^${ui[locale].deleteConversation}:`) }).click();
    const dialog = page.getByRole("dialog", { name: ui[locale].deleteConfirm });
    await dialog.getByRole("button", { name: ui[locale].delete, exact: true }).click();
    await expect(page.getByRole("alert").filter({ hasText: ui[locale]["error.DELETE_ERROR"] })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Synthetic failed turn" })).toBeVisible();
  });
}
