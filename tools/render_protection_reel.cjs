// Build-time only: Node with playwright installed, and Python with Pillow.
// Usage: node tools/render_protection_reel.cjs <python> [chromium-executable]
const { execFileSync } = require("node:child_process");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { chromium } = require("playwright");

const root = path.resolve(__dirname, "..");
const source = path.join(root, "assets", "branding-source", "majestyguard_app_loading_screen.html");
const target = path.join(root, "assets", "protection-reel.webp");

async function main() {
  const [python, executablePath] = process.argv.slice(2);
  if (!python) throw new Error("Pass a Python executable with Pillow installed");

  // The card supplies its own background and live state; export just the optics.
  let html = (await fs.readFile(source, "utf8")).replace(/<link\b[^>]*>/gi, "");
  const start = html.indexOf("      function renderAppLoader(");
  const end = html.indexOf("      function renderImpulses(", start);
  if (start < 0 || end < 0) throw new Error("Unrecognized loader source");
  let reel = html.slice(start, end);
  const adapt = (from, to) => {
    if (reel.split(from).length !== 2) throw new Error("Unrecognized reel: " + from);
    reel = reel.replace(from, to);
  };
  adapt("clamp(minDim / 410, 0.72, 1.38)", "minDim / 245");
  adapt("h * 0.44", "h * 0.5");
  adapt("drawBackdrop(theme, t, cx, cy, minDim);", "ctx.clearRect(0, 0, w, h);");
  adapt("minDim * 0.25", "minDim * 0.431");
  adapt("u * Math.PI * 0.65", "Math.sin(u * Math.PI * 2) * 0.18");
  adapt("0.72 + 0.14 * breath", "0.78 + 0.13 * breath");
  adapt("theme.dark ? 0.34 : 0.22", "0.70");
  adapt("minDim * 0.20", "minDim * 0.305");
  adapt("0.0, u, 0.46", "0.0, 0.5 - 0.5 * Math.cos(u * Math.PI * 2), 0.54");
  adapt("breath > 0.92 ? (breath - 0.92) * 45 : 0", "0");
  adapt("(u * 1.4) % 1.2", "(u * 1.2)");
  adapt("drawDynamicIslandAndRuler(theme, cx, cy + 124 * baseScale, 1, u, BOOT_STEPS[stepIdx], 1);", "");
  html = html.slice(0, start) + reel + html.slice(end);
  function replaceOnce(from, to) {
    if (html.split(from).length !== 2) throw new Error("Unrecognized loader source: " + from);
    html = html.replace(from, to);
  }
  replaceOnce("if (document.readyState === \"complete\") {", `
      window.renderLoaderFrame = (time) => {
        resize();
        renderAppLoader(THEMES.obsidian, time);
        return canvas.toDataURL("image/png").split(",")[1];
      };
      if (document.readyState === "complete") {`);

  const frames = await fs.mkdtemp(path.join(os.tmpdir(), "mg-loader-"));
  let browser;
  try {
    browser = await chromium.launch({ executablePath, args: ["--disable-gpu"] });
    const context = await browser.newContext({
      offline: true, viewport: { width: 276, height: 276 }, deviceScaleFactor: 1.5,
    });
    const page = await context.newPage();
    await page.evaluate(() => { window.requestAnimationFrame = () => 0; });
    await page.setContent(html, { waitUntil: "load" });
    for (let i = 0; i < 72; i++) {
      const png = await page.evaluate((t) => window.renderLoaderFrame(t), i / 30);
      await fs.writeFile(path.join(frames, String(i).padStart(2, "0") + ".png"), Buffer.from(png, "base64"));
    }
    await browser.close();
    browser = undefined;
    execFileSync(python, ["-c", [
      "import sys",
      "from pathlib import Path",
      "from PIL import Image",
      "frames = [Image.open(f).convert('RGBA') for f in sorted(Path(sys.argv[1]).glob('*.png'))]",
      "durations = [round((i+1)*1000/30)-round(i*1000/30) for i in range(len(frames))]",
      "target = Path(sys.argv[2])",
      "temp = target.with_suffix('.tmp.webp')",
      "try:",
      "    frames[0].save(temp, save_all=True, append_images=frames[1:], duration=durations, loop=0, quality=82, method=3)",
      "    temp.replace(target)",
      "finally:",
      "    temp.unlink(missing_ok=True)",
    ].join("\n"), frames, target], { stdio: "inherit" });
  } finally {
    if (browser) await browser.close();
    await fs.rm(frames, { recursive: true, force: true });
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
