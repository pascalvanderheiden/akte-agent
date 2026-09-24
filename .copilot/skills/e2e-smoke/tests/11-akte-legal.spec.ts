/**
 * #24: actual packaged draft bytes through the existing chat/download UI.
 * Deterministic SDK/transport fixtures, NOT live model or register evaluations.
 */
import { test, expect, type Page } from "@playwright/test";
import { readFileSync, unlinkSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { FRONTEND_URL } from "./helpers";
import { loadCatalog, runPython, root } from "./akte-fixtures";
import type { Locale } from "../../../../src/frontend/src/types";

const { en, nl }: typeof import("../../../../src/frontend/src/lib/i18n") =
  createRequire(import.meta.url)("../../../../src/frontend/src/lib/i18n.ts");
const ui = { en, nl };
const origin = new URL(FRONTEND_URL).origin;
const mount = new URL(FRONTEND_URL).pathname.replace(/\/$/, "");
const generated: string[] = [];
type Kind = "research" | "deed" | "index" | "translation" | "comparison" | "delivery";
type Input = { dossier: string; locale: Locale; kind: Kind; jurisdiction: string };
type Artifact = { path: string; kind: Kind; content: string };

function makeArtifact(input: Input): Artifact {
  const output: { artifact: { path: string }; content: string } = runPython(`
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path("../../use-cases/akte-agent/skills/working-artifacts/scripts").resolve()))
from legal_record import build_record
from artifact import write_artifact
data = json.load(sys.stdin)
artifact = write_artifact(build_record(data))
print(json.dumps({"artifact": artifact, "content": Path(artifact["path"]).read_text()}))
`, input);
  generated.push(output.artifact.path);
  return { path: output.artifact.path, kind: input.kind, content: output.content };
}

test.afterAll(() => { for (const file of generated) unlinkSync(file); });

async function setup(page: Page, locale: Locale) {
  const catalog = loadCatalog();
  const input: Input = JSON.parse(readFileSync(path.join(root,
    "use-cases/akte-agent/skills/legal-preparation/references/synthetic-legal.json"), "utf8"))[locale];
  const state = {
    calls: [] as { conversationId: string; locale: Locale; useCase: string; message: string }[],
    artifacts: [] as Artifact[], failDownload: false, failGeneration: false,
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
        return route.fulfill({ status: 201, json: { id: "synthetic-legal", title: body.title, useCase: body.useCase, status: "active", createdAt: "2026-01-15T12:00:00Z", updatedAt: "2026-01-15T12:00:00Z" } });
      }
      return route.fulfill({ json: { conversations: [] } });
    }
    if (pathname.endsWith("/messages")) return route.fulfill({ json: [] });
    if (pathname === "/api/conversations/synthetic-legal" && request.method() === "PATCH") return route.fulfill({ json: {} });
    if (pathname === "/api/agent/chat") {
      const body = request.postDataJSON();
      state.calls.push(body);
      const responseLocale: Locale = body.message.includes("in English") ? "en" : body.locale;
      let content: string;
      if (state.failGeneration) {
        // Exercise real renderer rejection; the model's explanation remains a fixture.
        const result = runPython(`
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path("../../use-cases/akte-agent/skills/working-artifacts/scripts").resolve()))
from legal_record import build_record
try:
    build_record(json.load(sys.stdin))
except ValueError as error:
    print(json.dumps({"error": str(error)}))
`, { ...input, jurisdiction: "" });
        expect(result.error).toBeTruthy();
        content = responseLocale === "en" ? "Generation failed; no new download exists." : "Aanmaken mislukt; geen nieuwe download beschikbaar.";
      } else if (body.message.startsWith("INTAKE")) {
        content = responseLocale === "en"
          ? "SYNTHETIC-AKTE-LEGAL intake: Netherlands estate; civil status contradictory, human review pending."
          : "SYNTHETIC-AKTE-LEGAL intake: Nederlandse nalatenschap; burgerlijke staat tegenstrijdig, menselijke beoordeling open.";
      } else {
        const kinds: Kind[] = body.message.startsWith("DRAFT")
          ? ["deed", "index"]
          : body.message.includes("in English") ? ["translation"]
            : body.message.startsWith("RECORDS") ? ["comparison", "delivery"] : ["research"];
        const artifacts = kinds.map((kind) => makeArtifact({ ...input, kind, locale: responseLocale }));
        state.artifacts.push(...artifacts);
        content = artifacts.map((artifact) => `${artifact.content}\n\n${artifact.path}`).join("\n\n");
      }
      return route.fulfill({ contentType: "text/event-stream",
        body: [{ type: "content", content }, { type: "done" }].map((event) => `data: ${JSON.stringify(event)}\n\n`).join("") });
    }
    if (pathname.startsWith("/api/files/download/")) {
      if (state.failDownload) return route.fulfill({ status: 404, json: { code: "DOWNLOAD_ERROR" } });
      const file = url.searchParams.get("path")!;
      expect(state.artifacts.map((artifact) => artifact.path)).toContain(file);
      return route.fulfill({ contentType: "text/markdown", body: readFileSync(file) });
    }
    throw new Error(`Unexpected API request: ${request.method()} ${pathname}`);
  });
  await page.addInitScript((value) => localStorage.setItem("kratos.locale", value), locale);
  await page.goto(`${FRONTEND_URL}/`);
  await expect(page.getByRole("heading", { name: "Akte Agent", exact: true })).toBeVisible();
  await expect(page.getByRole("combobox", { name: ui[locale].selectPersona })).toHaveCount(0);
  return { state, input };
}

async function send(page: Page, locale: Locale, text: string) {
  await page.getByRole("textbox", { name: ui[locale].ask }).fill(text);
  await page.getByRole("button", { name: ui[locale].sendMessage, exact: true }).click();
}

for (const locale of ["en", "nl"] as const) {
  for (const entry of ["direct", "after intake"] as const) {
    test(`${locale}: stages 2/3 ${entry}, deed/index bytes and honest pending actions`, async ({ page }) => {
      const { state, input } = await setup(page, locale);
      if (entry === "after intake") {
        await send(page, locale, `INTAKE ${input.dossier}`);
        await expect(page.getByText(/SYNTHETIC-AKTE-LEGAL intake:/)).toBeVisible();
      }
      await send(page, locale, `RESEARCH ${JSON.stringify(input)}`);
      await expect(page.getByRole("heading", { name: locale === "en" ? "Register-check plan" : "Registercontroleplan", exact: true })).toBeVisible();
      for (const register of ["BRP", "Handelsregister", "CTR"]) {
        await expect(page.getByRole("heading", { name: register, exact: true })).toBeVisible();
      }
      await expect(page.getByText(locale === "en" ? /NOT PERFORMED by the assistant/ : /NIET UITGEVOERD door de assistent/)).toBeVisible();
      await send(page, locale, "DRAFT approved name only; unknown date, amount and provisions");
      await expect(page.getByRole("heading", { name: locale === "en" ? "Draft deed" : "Conceptakte", exact: true })).toBeVisible();
      await expect(page.getByRole("heading", { name: locale === "en" ? "Dossier index and correspondence log" : "Dossierindex en correspondentielog", exact: true })).toBeVisible();
      for (const artifact of state.artifacts.filter((item) => item.kind === "deed" || item.kind === "index")) {
        const pending = page.waitForEvent("download");
        await page.getByRole("link", { name: new RegExp(path.basename(artifact.path)) }).click();
        const download = await pending;
        const chunks: Buffer[] = [];
        for await (const chunk of (await download.createReadStream())!) chunks.push(Buffer.from(chunk));
        const bytes = Buffer.concat(chunks);
        expect(bytes).toEqual(readFileSync(artifact.path));
        const text = bytes.toString("utf8");
        expect(text).toContain("Dossier: SYNTHETIC-AKTE-LEGAL");
        expect(text).toContain(locale === "en" ? "Status: DRAFT" : "Status: CONCEPT");
        expect(text).toContain(locale === "en" ? "Temporary working download" : "Tijdelijke werkdownload");
        expect(text).toContain("SYNTHETIC template A");
        if (artifact.kind === "deed") {
          expect(text).toContain(locale === "en" ? "[UNKNOWN: amount]" : "[ONBEKEND: bedrag]");
          expect(text).toContain(locale === "en" ? "NOT SIGNED / NOT EXECUTED" : "NIET ONDERTEKEND / NIET GEPASSEERD");
        } else {
          expect(text).toContain(locale === "en" ? "MISSING" : "ONTBREEKT");
          const log = text.split(locale === "en" ? "## Chronological correspondence" : "## Chronologische correspondentie")[1];
          expect(log.indexOf("email-a")).toBeLessThan(log.indexOf("letter-b"));
        }
      }
      await send(page, locale, "RECORDS compare A/B and prepare unsent delivery");
      await expect(page.getByRole("heading", { name: locale === "en" ? "DRAFT - NOT SENT" : "CONCEPT - NIET VERZONDEN", exact: true })).toBeVisible();
      await expect(page.getByRole("listitem").filter({ hasText: /EUR 100 \/ EUR 120/ })).toBeVisible();
      state.failDownload = true;
      await page.getByRole("link", { name: new RegExp(path.basename(state.artifacts[0].path)) }).click();
      await expect(page.getByRole("alert").filter({ hasText: ui[locale]["error.DOWNLOAD_ERROR"] })).toBeVisible();
      await page.getByRole("combobox", { name: /^(Language|Taal)$/ }).selectOption("nl");
      await send(page, "nl", "Translate supplied T in English");
      await expect(page.getByRole("heading", { name: "NONCERTIFIED working translation", exact: true })).toBeVisible();
      expect(state.calls.at(-1)?.locale).toBe("nl");
      expect(state.calls.at(-1)?.message).toContain("in English");
      expect(new Set(state.calls.map((call) => call.conversationId)).size).toBe(1);
      expect(state.calls.every((call) => call.useCase === "akte-agent")).toBe(true);
      const count = state.artifacts.length;
      state.failGeneration = true;
      await send(page, "nl", "Maak nog een download");
      await expect(page.getByText("Aanmaken mislukt; geen nieuwe download beschikbaar.")).toBeVisible();
      expect(state.artifacts.length).toBe(count);
    });
  }
}
