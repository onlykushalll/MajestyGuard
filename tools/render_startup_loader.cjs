// Build-time only: Node with playwright installed, and Python with Pillow.
// Usage: node tools/render_startup_loader.cjs <python> [chromium-executable]
const { execFileSync } = require("node:child_process");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { chromium } = require("playwright");

const root = path.resolve(__dirname, "..");
const source = path.join(root, "assets", "branding-source", "majestyguard_app_loading_screen.html");
const target = path.join(root, "assets", "startup-loader.webp");

async function main() {
  const [python, executablePath] = process.argv.slice(2);
  if (!python) throw new Error("Pass a Python executable with Pillow installed");

  // Keep the supplied renderer, but export only the loader and leave its status
  // label blank: Qt supplies real status and never displays demo percentages.
  let html = (await fs.readFile(source, "utf8")).replace(/<link\b[^>]*>/gi, "");
  function replaceOnce(from, to) {
    if (html.split(from).length !== 2) throw new Error("Unrecognized loader source: " + from);
    html = html.replace(from, to);
  }
  replaceOnce("1, u, BOOT_STEPS[stepIdx], 1", '1, u, "", 1');
  replaceOnce('ctx.fillText(Math.round(progress01 * 100) + "%", px + pillW - 16, dotY + 0.5);', "");
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
      offline: true, viewport: { width: 384, height: 324 }, deviceScaleFactor: 2,
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
      "frames = [Image.open(f).convert('RGB') for f in sorted(Path(sys.argv[1]).glob('*.png'))]",
      "durations = [round((i+1)*1000/30)-round(i*1000/30) for i in range(len(frames))]",
      "target = Path(sys.argv[2])",
      "temp = target.with_suffix('.tmp.webp')",
      "try:",
      "    frames[0].save(temp, save_all=True, append_images=frames[1:], duration=durations, loop=0, quality=86, method=6)",
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
