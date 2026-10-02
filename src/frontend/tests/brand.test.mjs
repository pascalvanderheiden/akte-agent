import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { register } from "node:module";
import { before, test } from "node:test";

register("./support/ts-loader.mjs", import.meta.url);

const ASSETS = {
  full: "public/images/akte-agent-logo.svg",
  mark: "public/images/akte-agent-mark.svg",
};
const NAVY = "#1b2a4e";
const PLATE = "#ffffff";

let createElement, renderToStaticMarkup, BrandLogo, BRAND_LOGO_ASSETS, LocaleProvider, nl;
before(async () => {
  ({ createElement } = await import("react"));
  ({ renderToStaticMarkup } = await import("react-dom/server"));
  ({ BrandLogo, BRAND_LOGO_ASSETS } = await import("../src/components/BrandLogo.tsx"));
  ({ LocaleProvider } = await import("../src/components/LocaleProvider.tsx"));
  ({ nl } = await import("../src/lib/i18n.ts"));
});

function render(props) {
  return renderToStaticMarkup(createElement(LocaleProvider, null, createElement(BrandLogo, props)));
}

function luminance(hex) {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

test("logo assets are lightweight artwork-only SVGs with a viewBox", async () => {
  for (const [variant, path] of Object.entries(ASSETS)) {
    const svg = await readFile(path, "utf8");
    assert.match(svg.trim(), /^<svg\s[^>]*xmlns="http:\/\/www\.w3\.org\/2000\/svg"/, `${variant} is not an SVG`);
    assert.match(svg.trim(), /<\/svg>$/, `${variant} SVG is not closed`);
    const viewBox = svg.match(/viewBox="0 0 (\d+(?:\.\d+)?) (\d+(?:\.\d+)?)"/);
    assert.ok(viewBox, `${variant} SVG has no viewBox`);
    assert.equal(BRAND_LOGO_ASSETS[variant].src, path.replace(/^public/, ""));
    assert.equal(BRAND_LOGO_ASSETS[variant].aspectRatio, Number(viewBox[1]) / Number(viewBox[2]));
    assert.ok(svg.length < 8_000, `${variant} SVG is not lightweight`);
    assert.doesNotMatch(svg, /<script|<image|<foreignObject|https?:\/\/(?!www\.w3\.org\/2000\/svg)/i);
  }
});

test("navy artwork meets 3:1 on every light surface and on the dark-mode plate", async () => {
  const css = await readFile("src/app/globals.css", "utf8");
  const lightBlocks = [...css.matchAll(/(?:^|\n)(:root|\[data-theme="[^"]+"\])\s*\{([^}]*)\}/g)];
  assert.ok(lightBlocks.length > 1);
  for (const [, selector, body] of lightBlocks) {
    for (const token of ["--surface", "--surface-2"]) {
      const value = body.match(new RegExp(`${token}:\\s*(#[0-9a-f]{6})`, "i"))?.[1];
      assert.ok(value, `${selector} ${token}`);
      assert.ok(contrast(NAVY, value) >= 3, `${selector} ${token} ${value}`);
    }
  }
  assert.ok(contrast(NAVY, PLATE) >= 3);
  assert.ok(contrast(NAVY, "#0c0f16") < 3, "dark surfaces need the backing plate");
});

test("each variant exposes the accessible name exactly once", () => {
  for (const variant of ["full", "mark"]) {
    const html = render({ variant });
    assert.equal(html.match(/alt="Akte Agent"/g)?.length, 1, html);
    assert.equal(html.match(/Akte Agent/g)?.length, 1, html);
    assert.doesNotMatch(html, /aria-hidden/);
    assert.match(html, new RegExp(`src="${BRAND_LOGO_ASSETS[variant].src}"`));
  }
  assert.equal(nl["app.name"], "Akte Agent");
});

test("logo constrains height only and keeps its aspect ratio", () => {
  const html = render({ variant: "full", height: 48 });
  assert.match(html, /height:48px/);
  assert.match(html, /width:auto/);
  assert.match(html, /aspect-ratio:3\.75/);
  assert.doesNotMatch(html, /\swidth="|\sheight="/);
});

test("decorative usage is hidden from assistive technology", () => {
  for (const variant of ["full", "mark"]) {
    const html = render({ variant, decorative: true });
    assert.match(html, /alt=""/);
    assert.match(html, /aria-hidden="true"/);
    assert.doesNotMatch(html, /Akte Agent/);
  }
});
