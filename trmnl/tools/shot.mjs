// Screenshot rendered pages at 800x480 and run the layout gate.
//
//   node trmnl/tools/shot.mjs out/            (after render.py)
//
// Writes out/png/<name>.png and out/gate.json. Exits 1 if any page fails.
// Gate checks (a subset of the plugin-dev skill's check.py):
//   1. the framework runtime finished (TRMNL_PLUGINS_READY)
//   2. no view overflows its box; no element sticks out of its view
//   3. no Liquid error, "{{", "{%", "undefined" or "NaN" in the text
//   4. no text truncated (except .gt-clip: user text clipped on purpose)
//   5. no SVG path or icon without data; no broken image
import { createServer } from "node:http";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { extname, join, resolve } from "node:path";
import { createRequire } from "node:module";
import { execFileSync } from "node:child_process";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const out = resolve(process.argv[2] || "out");
const types = { ".html": "text/html", ".css": "text/css", ".js": "text/javascript",
  ".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf", ".svg": "image/svg+xml",
  ".png": "image/png" };

const server = createServer(async (req, res) => {
  try {
    const path = join(out, decodeURIComponent(new URL(req.url, "http://x").pathname));
    if (!path.startsWith(out)) throw new Error("outside");
    const body = await readFile(path);
    res.writeHead(200, { "content-type": types[extname(path)] || "application/octet-stream" });
    res.end(body);
  } catch {
    res.writeHead(404);
    res.end();
  }
}).listen(0, "127.0.0.1");
await new Promise((r) => server.once("listening", r));
const base = `http://127.0.0.1:${server.address().port}`;

const index = JSON.parse(await readFile(join(out, "index.json"), "utf8"));
await mkdir(join(out, "png"), { recursive: true });
const browser = await chromium.launch({
  args: ["--font-render-hinting=none", "--disable-font-subpixel-positioning"],
});
const context = await browser.newContext({ viewport: { width: 800, height: 480 }, deviceScaleFactor: 1 });
// The optional base map loads MapLibre from TRMNL's mirror and tiles from
// maps.trmnl.com. Fetch exactly those through curl (which uses the
// environment's proxy and CA bundle) and hand them to the page; everything
// else external is blocked, so renders stay offline and deterministic.
const PASS = [/^https:\/\/trmnl\.com\/js\/maplibre-gl\//, /^https:\/\/maps\.trmnl\.com\//];
const fetched = new Map();
await context.route(/^https?:\/\//, async (route) => {
  const url = route.request().url();
  if (url.startsWith(base)) return route.continue();
  if (!PASS.some((re) => re.test(url))) return route.abort();
  try {
    if (!fetched.has(url)) {
      const body = execFileSync("curl", ["-sS", "-f", "-m", "20", "--compressed", url], { maxBuffer: 64 << 20 });
      fetched.set(url, body);
    }
    const type = url.endsWith(".js") ? "text/javascript" : url.endsWith(".css") ? "text/css"
      : "application/x-protobuf";
    return route.fulfill({ status: 200, contentType: type, body: fetched.get(url),
      headers: { "access-control-allow-origin": "*" } });
  } catch {
    return route.fulfill({ status: 204, body: "" });
  }
});
const page = await context.newPage();
const results = [];
let failed = 0;

for (const { name } of index) {
  const problems = [];
  page.removeAllListeners("pageerror");
  page.on("pageerror", (e) => problems.push(`js error: ${e.message}`));
  await page.goto(`${base}/pages/${encodeURIComponent(name)}.html`);
  try {
    await page.waitForFunction(() => window.TRMNL_PLUGINS_READY === true, null, { timeout: 8000 });
  } catch {
    problems.push("framework runtime did not finish");
  }
  // Maps draw asynchronously: give their canvas time to settle.
  if (await page.$(".map")) await page.waitForTimeout(4000);
  await page.evaluate(() => document.fonts.ready);
  problems.push(...await page.evaluate(() => {
    const p = [];
    const text = document.body.innerText;
    for (const bad of ["Liquid error", "{{", "{%", "undefined", "NaN"]) {
      if (text.includes(bad)) p.push(`text contains ${bad}`);
    }
    for (const view of document.querySelectorAll(".view")) {
      const vr = view.getBoundingClientRect();
      const cls = view.className.replace("view view--", "");
      if (view.scrollHeight > view.clientHeight + 1 || view.scrollWidth > view.clientWidth + 1) {
        p.push(`${cls}: view overflows (${view.scrollWidth}x${view.scrollHeight} > ${view.clientWidth}x${view.clientHeight})`);
      }
      for (const el of view.querySelectorAll("*")) {
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) continue;
        if (r.left < vr.left - 1 || r.top < vr.top - 1 || r.right > vr.right + 1 || r.bottom > vr.bottom + 1) {
          const desc = el.className?.baseVal ?? el.className;
          p.push(`${cls}: <${el.tagName.toLowerCase()} class="${desc}"> outside view by ` +
            `${Math.round(Math.max(vr.left - r.left, vr.top - r.top, r.right - vr.right, r.bottom - vr.bottom))}px`);
          break;
        }
      }
      // .gt-clip marks user text (activity names) that is clipped on purpose
      // when it is too long even at the smaller size; anything else must fit.
      for (const el of view.querySelectorAll(".value, .label, .title")) {
        if (el.closest(".gt-clip")) continue;
        if (el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).overflow !== "visible") {
          p.push(`${cls}: truncated "${el.textContent.trim().slice(0, 40)}"`);
        }
      }
      for (const el of view.querySelectorAll(".gt-tile, .gt-stat, .gt-chip")) {
        for (const child of el.querySelectorAll(".value, .label")) {
          const a = el.getBoundingClientRect(), b = child.getBoundingClientRect();
          if (b.right > a.right + 1 || b.bottom > a.bottom + 1) {
            p.push(`${cls}: "${child.textContent.trim().slice(0, 30)}" spills out of its tile`);
          }
        }
      }
    }
    for (const path of document.querySelectorAll("path")) {
      if (!path.getAttribute("d")) p.push("empty path data");
    }
    for (const img of document.querySelectorAll("img")) {
      if (!img.complete || img.naturalWidth === 0) p.push("broken image");
    }
    return p;
  }));
  await page.screenshot({ path: join(out, "png", `${name}.png`) });
  const uniq = [...new Set(problems)];
  if (uniq.length) failed++;
  results.push({ name, problems: uniq });
  console.log(`${uniq.length ? "FAIL" : "ok  "} ${name}${uniq.length ? "\n     " + uniq.join("\n     ") : ""}`);
}

await browser.close();
server.close();
await writeFile(join(out, "gate.json"), JSON.stringify(results, null, 1));
console.log(failed ? `RESULT: FAIL (${failed} pages)` : "RESULT: PASS");
process.exit(failed ? 1 : 0);
