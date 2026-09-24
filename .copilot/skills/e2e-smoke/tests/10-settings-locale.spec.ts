/**
 * #20 acceptance: real UI, controlled synthetic API responses. Not live
 * settings, MCP or identity-provider integrations. Runs at root or mount
 * using the existing smoke runner; all external requests are blocked.
 */
import { test, expect, type Page } from "@playwright/test";
import { createRequire } from "node:module";
import { FRONTEND_URL } from "./helpers";
import type { Locale, MCPConfig, Skill, SkillFile } from "../../../../src/frontend/src/types";

const { en, nl }: typeof import("../../../../src/frontend/src/lib/i18n") =
  createRequire(import.meta.url)("../../../../src/frontend/src/lib/i18n.ts");
const ui = { en, nl };
const origin = new URL(FRONTEND_URL).origin;
const mount = new URL(FRONTEND_URL).pathname.replace(/\/$/, "");
const selector = (page: Page) => page.getByRole("combobox", { name: /^(Language|Taal)$/ });
const alert = (page: Page) => page.getByRole("alert").filter({ hasText: /\S/ });

async function fixture(page: Page) {
  const state = {
    settings: { configured: true, foundryEndpoint: "https://synthetic.example.test", foundryModelDeployment: "synthetic-model" },
    skills: [] as Skill[],
    files: [{ path: "notes.txt", name: "notes.txt", content: "User-authored source. Niet vertalen." }] as SkillFile[],
    servers: {} as MCPConfig["servers"],
    prompt: "User-authored system prompt.",
    writes: [] as { path: string; body: unknown; method: string }[],
    fail: "",
    status: 500,
    logicalFailure: false,
    obo: false,
  };
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== origin) return route.abort();
    const path = url.pathname.slice(mount.length);
    if (path === "/config.json") return route.fulfill({ json: {
      apiUrl: `${origin}${mount}`,
      ...(state.obo ? { auth: { clientId: "synthetic-client", tenantId: "synthetic-tenant", mcpScope: "api://synthetic/access_as_user" } } : {}),
    } });
    if (!path.startsWith("/api/")) return route.continue();
    const method = request.method();
    if (method !== "GET") state.writes.push({ path, method, body: request.postData() ? request.postDataJSON() : null });
    if (state.fail && path.includes(state.fail)) return route.fulfill({ status: state.status, json: { detail: "SENSITIVE_SYNTHETIC_DIAGNOSTIC", stderr: "SENSITIVE_SYNTHETIC_DIAGNOSTIC" } });
    if (path === "/api/use-cases") return route.fulfill({ json: { useCases: [
      { name: "akte-agent", displayName: "Akte Agent", description: "Synthetic", curated: true, sampleQuestions: [], skillCount: 0 },
      { name: "synthetic-custom", displayName: "Synthetic custom", description: "Synthetic", curated: false, sampleQuestions: [], skillCount: 0 },
    ] } });
    if (path === "/api/conversations") return route.fulfill({ json: { conversations: [] } });
    if (path === "/api/settings") {
      if (method === "POST") state.settings = { ...state.settings, ...request.postDataJSON() };
      return route.fulfill({ json: state.settings });
    }
    if (path === "/api/admin/system-prompt") {
      if (method === "PUT") state.prompt = request.postDataJSON().content;
      return route.fulfill({ json: { content: state.prompt, isDefault: false } });
    }
    if (path === "/api/admin/mcp-servers") {
      if (method === "PUT") state.servers = request.postDataJSON().servers;
      return route.fulfill({ json: { servers: state.servers } });
    }
    if (path.includes("/files")) {
      if (method === "GET") return route.fulfill({ json: { files: state.files } });
      const filePath = decodeURIComponent(path.split("/files/")[1]);
      if (method === "DELETE") state.files = state.files.filter((f) => f.path !== filePath);
      if (method === "PUT") {
        state.files = state.files.filter((f) => f.path !== filePath);
        state.files.push({ path: filePath, name: filePath.split("/").at(-1)!, content: request.postDataJSON().content });
      }
      return route.fulfill({ json: {} });
    }
    if (path.startsWith("/api/admin/skills")) {
      const name = decodeURIComponent(path.split("/").at(-1)!);
      if (method === "GET") return route.fulfill({ json: { skills: state.skills } });
      if (method === "POST") {
        const skill = { ...request.postDataJSON(), toolName: "synthetic-tool", fileCount: 1 };
        state.skills.push(skill);
        return route.fulfill({ json: skill });
      }
      if (method === "DELETE") state.skills = state.skills.filter((s) => s.name !== name);
      if (method === "PATCH") {
        const skill = state.skills.find((s) => s.name === name)!;
        Object.assign(skill, request.postDataJSON());
        return route.fulfill({ json: skill });
      }
      return route.fulfill({ json: {} });
    }
    if (path.includes("/analysis/consistency")) return route.fulfill({ json: {
      overallScore: 85, summary: "Synthetic model summary", durationMs: 1250,
      issues: [{ severity: "warning", category: "gap", title: "Synthetic gap", description: "Source description", recommendation: "Source recommendation", affectedSkills: [] }],
      strengths: ["Source strength"],
    } });
    throw new Error(`Unexpected fixture request: ${method} ${path}`);
  });
  return state;
}

async function open(page: Page, locale: Locale) {
  await page.addInitScript((value) => localStorage.setItem("kratos.locale", value), locale);
  await page.goto(`${FRONTEND_URL}/`);
  await expect(selector(page)).toHaveValue(locale);
  await expect(page.getByRole("textbox", { name: ui[locale].ask })).toBeEnabled();
}

async function manager(page: Page, locale: Locale) {
  await page.getByRole("button", { name: ui[locale].manager, exact: true }).click();
  await expect(page.getByRole("heading", { name: ui[locale].manager, exact: true })).toBeVisible();
}

for (const locale of ["en", "nl"] as const) {
  const other = locale === "en" ? "nl" : "en";
  const text = ui[locale];

  test(`${locale}: settings save, locale-preserved draft, failure and cancellation`, async ({ page }) => {
    const state = await fixture(page);
    await open(page, locale);
    await expect(page).toHaveTitle("Akte Agent");
    await page.getByRole("button", { name: text.settings, exact: true }).click();
    await expect(page.getByRole("dialog", { name: text["settings.title"] })).toBeVisible();
    await page.getByRole("textbox", { name: text["settings.model"] }).fill("unsaved-model");
    await selector(page).selectOption(other);
    await expect(page.getByRole("textbox", { name: ui[other]["settings.model"] })).toHaveValue("unsaved-model");
    expect(state.writes).toHaveLength(0);
    await page.getByRole("button", { name: ui[other]["settings.save"], exact: true }).click();
    await expect(page.getByRole("status")).toHaveText(ui[other]["settings.saved"]);
    expect(state.settings.foundryModelDeployment).toBe("unsaved-model");
    state.fail = "/settings";
    state.status = 405;
    await page.getByRole("button", { name: ui[other]["settings.save"], exact: true }).click();
    await expect(alert(page)).toHaveText(ui[other]["error.SETTINGS_READ_ONLY"]);
    state.status = 500;
    state.fail = "/settings";
    await page.getByRole("textbox", { name: ui[other]["settings.model"] }).fill("failed-draft");
    await page.getByRole("button", { name: ui[other]["settings.save"], exact: true }).click();
    await expect(alert(page)).toHaveText(ui[other]["error.SETTINGS_ERROR"]);
    await expect(page.getByText("SENSITIVE_SYNTHETIC_DIAGNOSTIC")).toHaveCount(0);
    await expect(page.getByRole("textbox", { name: ui[other]["settings.model"] })).toHaveValue("failed-draft");
    const count = state.writes.length;
    await page.getByRole("button", { name: ui[other].cancel, exact: true }).click();
    expect(state.writes).toHaveLength(count);
    expect(state.settings.foundryModelDeployment).toBe("unsaved-model");
  });

  test(`${locale}: theme and all help steps retain dialog and selected step`, async ({ page }) => {
    const state = await fixture(page);
    await open(page, locale);
    await page.getByRole("button", { name: text["theme.change"] }).click();
    await page.getByRole("button", { name: /Nord/ }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "nord");
    await selector(page).selectOption(other);
    await expect(page.getByRole("button", { name: ui[other]["theme.change"] })).toHaveAttribute("aria-expanded", "true");
    await selector(page).selectOption(locale);
    await page.getByTitle(text["theme.toDark"], { exact: true }).click();
    await expect(page.locator("html")).toHaveClass(/dark/);
    await page.getByTitle(text["theme.toLight"], { exact: true }).click();
    await expect(page.locator("html")).not.toHaveClass(/dark/);
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: text.howItWorks, exact: true }).click();
    const steps = ["sdk", "persona", "skills", "connect", "ask", "session", "plan", "act", "loop", "answer", "audit"] as const;
    for (const step of steps) {
      await page.getByRole("button", { name: new RegExp(`^${locale === "en" ? "Step" : "Stap"} \\d+: ${text[`help.${step}.title`].replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`) }).click();
      await expect(page.getByRole("heading", { name: text[`help.${step}.title`], exact: true })).toBeVisible();
      await expect(page.getByText(text[`help.${step}.detail`], { exact: true })).toBeVisible();
    }
    await selector(page).selectOption(other);
    await expect(page.getByRole("dialog", { name: ui[other].howItWorks })).toBeVisible();
    await expect(page.getByRole("heading", { name: ui[other]["help.audit.title"], exact: true })).toBeVisible();
    expect(state.writes).toHaveLength(0);
    await page.getByRole("button", { name: ui[other].dismiss, exact: true }).click();
  });

  test(`${locale}: skill CRUD, file editing, errors, and native confirmation cancellation`, async ({ page }) => {
    const state = await fixture(page);
    await open(page, locale);
    await manager(page, locale);
    await expect(page.getByText(text["skills.empty"], { exact: true })).toBeVisible();
    await page.getByRole("button", { name: text["skills.add"], exact: true }).click();
    await page.getByRole("textbox", { name: text["skills.name"], exact: true }).fill("synthetic-skill");
    await page.getByRole("textbox", { name: text["skills.description"], exact: true }).fill("User description.");
    await page.getByRole("textbox", { name: text["skills.instructions"], exact: true }).fill("Do not translate these instructions.");
    await selector(page).selectOption(other);
    await expect(page.getByRole("textbox", { name: ui[other]["skills.instructions"], exact: true })).toHaveValue("Do not translate these instructions.");
    expect(state.writes).toHaveLength(0);
    await selector(page).selectOption(locale);
    state.fail = "/admin/skills";
    await page.getByRole("button", { name: text["skills.create"], exact: true }).click();
    await expect(alert(page)).toContainText(text["error.SKILL_CHANGE_ERROR"]);
    await expect(page.getByRole("textbox", { name: text["skills.name"], exact: true })).toHaveValue("synthetic-skill");
    state.fail = "";
    await page.getByRole("button", { name: text["skills.create"], exact: true }).click();
    await page.getByRole("switch").click();
    await expect(page.getByRole("switch")).toHaveAttribute("aria-checked", "false");
    await page.getByRole("button", { name: text["skills.edit"], exact: true }).click();
    await page.getByRole("textbox", { name: text["skills.description"], exact: true }).fill("Changed source.");
    await page.getByRole("button", { name: "notes.txt", exact: true }).click();
    const file = page.getByRole("textbox", { name: /notes\.txt/ });
    await file.fill("Updated user-authored file. Ongewijzigd.");
    const writes = state.writes.length;
    await selector(page).selectOption(other);
    await expect(file).toHaveValue("Updated user-authored file. Ongewijzigd.");
    expect(state.writes).toHaveLength(writes);
    state.fail = "/files/";
    await page.getByRole("button", { name: ui[other]["skills.saveFile"], exact: true }).click();
    await expect(alert(page)).toContainText(ui[other]["error.SKILL_FILE_ERROR"]);
    state.fail = "";
    await page.getByRole("button", { name: ui[other]["skills.saveFile"], exact: true }).click();
    await expect(page.getByRole("status").filter({ hasText: ui[other]["skills.fileSaved"] })).toBeVisible();
    expect(state.files[0].content).toBe("Updated user-authored file. Ongewijzigd.");
    await file.fill("Draft replaced explicitly by upload.");
    await page.getByRole("button", { name: ui[other]["skills.upload"], exact: true }).click();
    await page.getByLabel(ui[other]["skills.chooseFile"], { exact: true }).setInputFiles({
      name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("Explicit replacement content."),
    });
    await expect(file).toHaveValue("Explicit replacement content.");
    await page.getByRole("button", { name: ui[other]["skills.upload"], exact: true }).click();
    await page.getByRole("textbox", { name: ui[other]["skills.path"] }).fill("scripts/");
    await page.getByLabel(ui[other]["skills.chooseFile"], { exact: true }).setInputFiles({
      name: "synthetic.txt", mimeType: "text/plain", buffer: Buffer.from("Synthetic supporting file."),
    });
    await expect(page.getByRole("button", { name: "scripts/synthetic.txt", exact: true })).toBeVisible();
    page.once("dialog", (dialog) => dialog.dismiss());
    const beforeFileDelete = state.writes.length;
    await page.getByRole("button", { name: `${ui[other]["skills.deleteFile"]}: scripts/synthetic.txt`, exact: true }).click();
    expect(state.writes).toHaveLength(beforeFileDelete);
    page.once("dialog", (dialog) => dialog.accept());
    await page.getByRole("button", { name: `${ui[other]["skills.deleteFile"]}: scripts/synthetic.txt`, exact: true }).click();
    await expect(page.getByRole("button", { name: "scripts/synthetic.txt", exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: ui[other]["settings.save"], exact: true }).click();
    await expect(page.getByText("Changed source.", { exact: true })).toBeVisible();
    page.once("dialog", async (dialog) => {
      expect(dialog.message()).toBe(ui[other]["skills.deleteConfirm"].replace("{name}", "synthetic-skill"));
      await dialog.dismiss();
    });
    const before = state.writes.length;
    await page.getByRole("button", { name: ui[other].delete, exact: true }).click();
    expect(state.writes).toHaveLength(before);
    page.once("dialog", (dialog) => dialog.accept());
    await page.getByRole("button", { name: ui[other].delete, exact: true }).click();
    await expect(page.getByText(ui[other]["skills.empty"], { exact: true })).toBeVisible();
  });

  test(`${locale}: MCP validation, preserved draft, save, edit and delete`, async ({ page }) => {
    const state = await fixture(page);
    await open(page, locale);
    await manager(page, locale);
    await page.getByRole("button", { name: text["mcp.title"], exact: true }).click();
    await expect(page.getByText(text["mcp.empty"], { exact: true })).toBeVisible();
    await page.getByRole("button", { name: text["mcp.add"], exact: true }).click();
    await page.getByRole("textbox", { name: text["mcp.name"] }).fill("synthetic-mcp");
    await page.getByRole("button", { name: text["mcp.add"], exact: true }).click();
    await expect(alert(page)).toHaveText(text["error.MCP_COMMAND_REQUIRED"]);
    await page.getByRole("textbox", { name: text["mcp.command"], exact: true }).fill("synthetic-command");
    await page.getByRole("textbox", { name: text["mcp.env"] }).fill("SYNTHETIC=value");
    await selector(page).selectOption(other);
    await expect(page.getByRole("textbox", { name: ui[other]["mcp.env"] })).toHaveValue("SYNTHETIC=value");
    expect(state.writes).toHaveLength(0);
    await page.getByRole("button", { name: ui[other]["mcp.add"], exact: true }).click();
    await expect(page.getByText("synthetic-mcp", { exact: true })).toBeVisible();
    expect(state.servers["synthetic-mcp"]).toMatchObject({ type: "local", command: "synthetic-command", env: { SYNTHETIC: "value" } });
    await page.getByRole("button", { name: ui[other]["skills.edit"], exact: true }).click();
    await page.getByRole("combobox", { name: ui[other]["mcp.type"] }).selectOption("http");
    await page.getByRole("textbox", { name: ui[other]["mcp.command"] }).fill("https://synthetic.example.test/mcp");
    await page.getByRole("spinbutton", { name: ui[other]["mcp.timeout"] }).fill("-1");
    await page.getByRole("button", { name: ui[other]["settings.save"], exact: true }).click();
    await expect(alert(page)).toHaveText(ui[other]["error.MCP_TIMEOUT_INVALID"]);
    await page.getByRole("spinbutton", { name: ui[other]["mcp.timeout"] }).fill("30");
    state.fail = "/mcp-servers";
    state.status = 403;
    await page.getByRole("button", { name: ui[other]["settings.save"], exact: true }).click();
    await expect(alert(page)).toHaveText(ui[other]["error.AUTH_REQUIRED"]);
    await expect(page.getByText("SENSITIVE_SYNTHETIC_DIAGNOSTIC")).toHaveCount(0);
    state.fail = "";
    await page.getByRole("button", { name: ui[other].cancel, exact: true }).click();
    expect(state.servers["synthetic-mcp"].type).toBe("local");
    page.once("dialog", (dialog) => dialog.dismiss());
    const before = state.writes.length;
    await page.getByRole("button", { name: ui[other].delete, exact: true }).click();
    expect(state.writes).toHaveLength(before);
    page.once("dialog", (dialog) => dialog.accept());
    await page.getByRole("button", { name: ui[other].delete, exact: true }).click();
    await expect(page.getByText(ui[other]["mcp.empty"], { exact: true })).toBeVisible();
  });

  test(`${locale}: load failures are visible, not empty success; OBO remains optional`, async ({ page }) => {
    const state = await fixture(page);
    await open(page, locale);
    await expect(page.getByRole("button", { name: text["auth.signIn"], exact: true })).toHaveCount(0);
    state.fail = "/settings";
    await page.getByRole("button", { name: text.settings, exact: true }).click();
    await expect(alert(page)).toContainText(text["error.SETTINGS_ERROR"]);
    await expect(page.getByRole("button", { name: text["settings.save"], exact: true })).toBeDisabled();
    await page.getByRole("button", { name: text.cancel, exact: true }).click();
    state.fail = "/mcp-servers";
    await manager(page, locale);
    await page.getByRole("button", { name: text["mcp.title"], exact: true }).click();
    await expect(alert(page)).toContainText(text["error.MCP_ERROR"]);
    await expect(page.getByText(text["mcp.empty"], { exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "APM", exact: true })).toHaveCount(0);
  });

  test(`${locale}: configured OBO control reports denied sign-in without provider details`, async ({ page }) => {
    const state = await fixture(page);
    state.obo = true;
    await page.addInitScript(() => { window.open = () => null; });
    await open(page, locale);
    await page.getByRole("button", { name: text["auth.signIn"], exact: true }).click();
    await expect(alert(page)).toContainText(text["error.AUTH_REQUIRED"]);
    await selector(page).selectOption(other);
    await expect(alert(page)).toContainText(ui[other]["error.AUTH_REQUIRED"]);
    expect(state.writes).toHaveLength(0);
  });

  test(`${locale}: consistency guidance and duration localize without changing model content`, async ({ page }) => {
    const state = await fixture(page);
    await open(page, locale);
    await manager(page, locale);
    await page.getByRole("combobox", { name: text.selectPersona }).last().selectOption("synthetic-custom");
    await page.getByRole("button", { name: text["skills.consistency"], exact: true }).click();
    await page.getByRole("button", { name: text["skills.runAnalysis"], exact: true }).click();
    await expect(page.getByText("Synthetic model summary", { exact: true })).toBeVisible();
    await expect(page.getByText(locale === "nl" ? /Analysetijd: 1,3/ : /1.3 sec.* analysis time/)).toBeVisible();
    const before = state.writes.length;
    await selector(page).selectOption(other);
    await expect(page.getByRole("combobox", { name: ui[other].selectPersona }).last()).toHaveValue("synthetic-custom");
    await expect(page.getByRole("heading", { name: ui[other]["skills.analysisTitle"], exact: true })).toBeVisible();
    await expect(page.getByText("Synthetic model summary", { exact: true })).toBeVisible();
    expect(state.writes).toHaveLength(before);
  });
}
