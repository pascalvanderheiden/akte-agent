import { test, expect } from "@playwright/test";
import { FRONTEND_URL } from "./helpers";

test.describe("UI smoke (browser)", () => {
  test("sidebar branding adapts without duplicate accessible names or links", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto(FRONTEND_URL, { waitUntil: "domcontentloaded" });
    const sidebar = page.locator("aside[aria-label]");
    await expect(sidebar.getByRole("img", { name: "Akte Agent" })).toBeVisible();
    await expect(sidebar.locator("a, button").filter({ has: sidebar.getByRole("img", { name: "Akte Agent" }) })).toHaveCount(0);

    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole("button", { name: /open sidebar|zijbalk openen/i }).click();
    await expect(sidebar.getByText("Akte Agent", { exact: true })).toBeVisible();
    const mark = sidebar.locator('img[alt=""]:visible');
    await expect(mark).toBeVisible();
    expect(await mark.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true);
    await expect(sidebar.getByRole("img", { name: "Akte Agent" })).toHaveCount(0);
  });

  test("home page renders without console errors", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (err) => errors.push(`pageerror: ${err.message}`));
    page.on("console", (msg) => {
      if (msg.type() === "error") errors.push(`console.error: ${msg.text()}`);
    });

    await page.goto(FRONTEND_URL, { waitUntil: "domcontentloaded" });
    await expect(page).toHaveTitle(/.+/);

    await page.waitForLoadState("networkidle", { timeout: 30_000 }).catch(() => undefined);

    const bodyText = (await page.locator("body").innerText()).toLowerCase();
    expect(
      bodyText.length,
      "body should have visible text after hydration",
    ).toBeGreaterThan(50);

    const cspNoise = errors.filter(
      (e) => !/csp|content security policy|favicon|hydration/i.test(e),
    );
    expect(cspNoise, `unexpected JS errors: ${cspNoise.join(" | ")}`).toEqual([]);
  });
});
