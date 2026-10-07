import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { register } from "node:module";
import { before, test } from "node:test";

register("./support/ts-loader.mjs", import.meta.url);

const ASSETS = {
  full: "public/images/akte-agent-logo.png",
  mark: "public/images/akte-agent-mark.png",
};

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

test("logo assets exist as lightweight PNGs with matching intrinsic aspect ratios", async () => {
  for (const [variant, path] of Object.entries(ASSETS)) {
    const png = await readFile(path);
    assert.equal(png.toString("ascii", 1, 4), "PNG", `${variant} is not a PNG`);
    const width = png.readUInt32BE(16);
    const height = png.readUInt32BE(20);
    assert.equal(BRAND_LOGO_ASSETS[variant].src, path.replace(/^public/, ""));
    assert.equal(BRAND_LOGO_ASSETS[variant].aspectRatio, width / height);
    assert.ok(png.length < 1_000_000, `${variant} PNG is not lightweight`);
  }
});

test("transparent logo retains its dark-mode and forced-colours backing plate", () => {
  const html = render({ variant: "full" });
  assert.match(html, /dark:bg-white/);
  assert.match(html, /forced-colors:bg-white/);
  assert.match(html, /forced-color-adjust-none/);
});

test("each variant exposes the accessible name exactly once", () => {
  for (const variant of ["full", "mark"]) {
    const html = render({ variant });
    assert.equal(html.match(new RegExp(`alt="${nl["app.name"]}"`, "g"))?.length, 1, html);
    assert.equal(html.match(new RegExp(nl["app.name"], "g"))?.length, 1, html);
    assert.doesNotMatch(html, /aria-hidden/);
    assert.match(html, new RegExp(`src="${BRAND_LOGO_ASSETS[variant].src}"`));
  }
  assert.equal(nl["app.name"], "Akte Agent");
});

test("logo constrains height only and keeps its aspect ratio", () => {
  const html = render({ variant: "full", height: 48 });
  assert.match(html, /height:48px/);
  assert.match(html, /width:auto/);
  assert.match(html, /aspect-ratio:1/);
  assert.doesNotMatch(html, /\swidth="|\sheight="/);
});

test("logo asset URLs include the configured base path", () => {
  const previous = process.env.NEXT_PUBLIC_BASE_PATH;
  process.env.NEXT_PUBLIC_BASE_PATH = "/tenant/";
  try {
    assert.match(render({ variant: "full" }), /src="\/tenant\/images\/akte-agent-logo\.png"/);
  } finally {
    if (previous === undefined) delete process.env.NEXT_PUBLIC_BASE_PATH;
    else process.env.NEXT_PUBLIC_BASE_PATH = previous;
  }
});

test("decorative usage is hidden from assistive technology", () => {
  for (const variant of ["full", "mark"]) {
    const html = render({ variant, decorative: true });
    assert.match(html, /alt=""/);
    assert.match(html, /aria-hidden="true"/);
    assert.doesNotMatch(html, /Akte Agent/);
  }
});
