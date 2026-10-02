import assert from "node:assert/strict";
import { readFile, stat } from "node:fs/promises";
import { resolve } from "node:path";
import { before, test } from "node:test";

let layout;
let referenced;
before(async () => {
  layout = await readFile(resolve("src/app/layout.tsx"), "utf8");
  referenced = [...layout.matchAll(/asset\("(\/[^"]+)"\)/g)].map((m) => m[1]);
});

async function pngSize(path) {
  const buf = await readFile(resolve("public", path));
  assert.equal(buf.toString("ascii", 1, 4), "PNG", `${path} is not a PNG`);
  return `${buf.readUInt32BE(16)}x${buf.readUInt32BE(20)}`;
}

function globToRegExp(glob) {
  const body = glob
    .replace(/[.+^$()|[\]\\]/g, "\\$&")
    .replace(/\*/g, "[^/]*")
    .replace(/\{([^}]+)\}/g, (_, alts) => `(?:${alts.split(",").join("|")})`);
  return new RegExp(`^${body}$`);
}

test("layout references the favicon, touch icon and manifest", () => {
  for (const path of [
    "/favicon.svg",
    "/favicon-32x32.png",
    "/favicon-16x16.png",
    "/apple-touch-icon.png",
    "/manifest.webmanifest",
  ]) {
    assert.ok(referenced.includes(path), `layout does not reference ${path}`);
  }
  assert.doesNotMatch(layout, /<link[^>]+rel="icon"/);
});

test("every icon and manifest file referenced by the layout exists", async () => {
  for (const path of referenced) {
    const info = await stat(resolve("public", `.${path}`));
    assert.ok(info.isFile() && info.size > 0, `public${path} is missing or empty`);
  }
});

test("SVG favicon is the compact mark, not the old generic glyph", async () => {
  const svg = await readFile(resolve("public/favicon.svg"), "utf8");
  assert.match(svg, /<svg[^>]+viewBox="[^"]+"/);
  assert.doesNotMatch(svg, /#7c3aed|#22d3ee/);
});

test("PNG icons have the sizes the metadata and manifest declare", async () => {
  assert.equal(await pngSize("favicon-16x16.png"), "16x16");
  assert.equal(await pngSize("favicon-32x32.png"), "32x32");
  assert.equal(await pngSize("apple-touch-icon.png"), "180x180");

  const manifest = JSON.parse(await readFile(resolve("public/manifest.webmanifest"), "utf8"));
  const sizes = manifest.icons.map((icon) => icon.sizes).sort();
  assert.deepEqual(sizes, ["192x192", "512x512"]);
  for (const icon of manifest.icons) {
    assert.ok(!icon.src.startsWith("/"), "manifest icons must be relative so basePath mounts work");
    assert.equal(await pngSize(icon.src), icon.sizes);
  }
});

test("manifest theme colour matches the layout viewport theme colour", async () => {
  const manifest = JSON.parse(await readFile(resolve("public/manifest.webmanifest"), "utf8"));
  const themeColor = layout.match(/themeColor:\s*"([^"]+)"/)?.[1];
  assert.ok(themeColor, "layout viewport has no themeColor");
  assert.equal(manifest.theme_color.toLowerCase(), themeColor.toLowerCase());
});

test("static web app navigation fallback does not intercept the icon files", async () => {
  const config = JSON.parse(await readFile(resolve("staticwebapp.config.json"), "utf8"));
  const excludes = config.navigationFallback.exclude.map(globToRegExp);
  for (const path of referenced) {
    assert.ok(
      excludes.some((re) => re.test(path)),
      `${path} would be rewritten to ${config.navigationFallback.rewrite}`,
    );
  }
  assert.equal(config.mimeTypes?.[".webmanifest"], "application/manifest+json");
});
