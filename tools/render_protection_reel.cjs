// Build-time only: Node with playwright installed, and Python with Pillow.
// Usage: node tools/render_protection_reel.cjs <python> [chromium-executable] [live|intro]
const { execFileSync } = require("node:child_process");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { chromium } = require("playwright");

const root = path.resolve(__dirname, "..");
const source = path.join(root, "assets", "branding-source", "majestyguard_app_loading_screen.html");

async function main() {
  const [python, executablePath, mode = "live"] = process.argv.slice(2);
  if (!["live", "intro"].includes(mode)) throw new Error("Expected intro or live");
  const intro = mode === "intro";
  const target = path.join(root, "assets", intro ? "protection-intro.webp" : "protection-reel.webp");
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
  if (intro) {
    // Keep every chapter, but not the full-card white flash or simulated telemetry.
    replaceOnce("ctx.fillStyle = theme.flashBg;", 'ctx.fillStyle = "transparent";');
    replaceOnce("drawBackdrop(theme, t, cx, cy, minDim);", "ctx.clearRect(0, 0, w, h);");
    replaceOnce("const cx = w * 0.5 + sx;", "const cx = w * 0.5 + sx * .12;");
    replaceOnce("const cy = h * 0.46 + sy;", "const cy = h * 0.5 + sy * .12;");
    replaceOnce("minDim * 0.22) + \"px 'Syne'", "minDim * 0.062) + \"px 'Syne'");
    replaceOnce("cy - minDim * 0.16", "cy - minDim * 0.32");
    replaceOnce("cy + minDim * 0.18", "cy + minDim * 0.32");
    replaceOnce("theme.dark ? \"#050709\" : \"#101218\"", '"#182026"');
    html = html.replaceAll("color: theme.flashInk,", "color: theme.accent,");
    replaceOnce("              BOOT_STEPS[stepIdx],", '              "",');
    replaceOnce("              uiFade\n", "              0\n");
    replaceOnce('ctx.fillText(Math.round(progress01 * 100) + "%", px + pillW - 16, dotY + 0.5);', "");
  }
  replaceOnce("if (document.readyState === \"complete\") {", `
      window.renderLoaderFrame = (time) => {
        resize();
        ${intro ? `
        const theme = THEMES.obsidian;
        if (time >= .82) renderIntroReel(theme, time);
        if (time < .96) {
          const lockup = easeInOutCubic(prog(time, 0, .62));
          const release = 1 - easeInOutCubic(prog(time, .66, .96));
          const center = 210;
          const radius = 140 + 18 * lockup;
          ctx.save();
          ctx.translate(center, center);
          const lens = ctx.createRadialGradient(-26, -38, 8, 0, 0, radius);
          lens.addColorStop(0, "rgba(83,173,165,0.30)");
          lens.addColorStop(.62, "rgba(24,65,76,0.38)");
          lens.addColorStop(1, "rgba(11,25,31,0.86)");
          ctx.beginPath(); ctx.arc(0, 0, radius * .93, 0, Math.PI*2);
          ctx.globalAlpha = release; ctx.fillStyle = lens; ctx.fill();
          ctx.restore();
          drawAperture(theme, center, center, radius, .11 + .87 * lockup, -.34 * (1-lockup), release);
          // Opposed highlights make a single sweep across the bezel; no flash cut.
          ctx.save(); ctx.translate(center, center); ctx.lineCap = "round";
          for (const [offset, ink, length] of [[0, theme.accent, .91], [Math.PI, theme.accent2, .43]]) {
            const angle = -2.46 + 2.14 * lockup + offset;
            ctx.beginPath(); ctx.arc(0, 0, radius * 1.08, angle, angle + length);
            ctx.globalAlpha = release * (.58 + .42 * lockup);
            ctx.strokeStyle = ink; ctx.lineWidth = 3;
            ctx.shadowBlur = 16; ctx.shadowColor = ink; ctx.stroke();
          }
          ctx.shadowBlur = 0;
          ctx.beginPath(); ctx.arc(0, 0, 4 + lockup * 10, 0, Math.PI * 2);
          ctx.globalAlpha = release * (.78 - lockup * .58);
          ctx.fillStyle = theme.accent; ctx.fill();
          ctx.restore();
        }
        // Soften the edges of the wide ignition curtains and the final iris.
        ctx.save();
        ctx.globalCompositeOperation = "destination-in";
        const mask = ctx.createRadialGradient(210, 210, 161, 210, 210, 206);
        mask.addColorStop(0, "#fff"); mask.addColorStop(1, "transparent");
        ctx.fillStyle = mask; ctx.fillRect(0, 0, 420, 420); ctx.restore();
        if (time >= 6.24) {
          const mix = easeInOutCubic(prog(time, 6.24, INTRO_DUR - 1/30));
          const previous = document.createElement("canvas");
          previous.width = canvas.width; previous.height = canvas.height;
          previous.getContext("2d").drawImage(canvas, 0, 0);
          ctx.clearRect(0, 0, 420, 420);
          // Same geometry and phase as frame zero of the smaller looping export.
          state.w = 276; state.h = 276;
          ctx.save(); ctx.scale(420/276, 420/276);
          renderAppLoader(THEMES.obsidian, LOADER_DUR - (INTRO_DUR - time));
          ctx.restore(); ctx.save(); ctx.globalCompositeOperation = "destination-in";
          ctx.fillStyle = "rgba(0,0,0," + mix + ")"; ctx.fillRect(0, 0, 420, 420);
          ctx.globalCompositeOperation = "lighter"; ctx.globalAlpha = 1-mix;
          ctx.drawImage(previous, 0, 0, 420, 420); ctx.restore();
        }` : "renderAppLoader(THEMES.obsidian, time);"}
        return canvas.toDataURL("image/png").split(",")[1];
      };
      if (document.readyState === "complete") {`);

  const frames = await fs.mkdtemp(path.join(os.tmpdir(), "mg-loader-"));
  let browser;
  try {
    browser = await chromium.launch({ executablePath, args: ["--disable-gpu"] });
    const context = await browser.newContext({
      offline: true, viewport: { width: intro ? 420 : 276, height: intro ? 420 : 276 }, deviceScaleFactor: intro ? 1 : 1.5,
    });
    const page = await context.newPage();
    await page.evaluate(() => { window.requestAnimationFrame = () => 0; });
    await page.setContent(html, { waitUntil: "load" });
    for (let i = 0; i < (intro ? 204 : 72); i++) {
      const png = await page.evaluate((t) => window.renderLoaderFrame(t), i / 30);
      await fs.writeFile(path.join(frames, String(i).padStart(3, "0") + ".png"), Buffer.from(png, "base64"));
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
