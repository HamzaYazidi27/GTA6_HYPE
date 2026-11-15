#!/usr/bin/env node
/**
 * Renderer bridge between the Python pipeline and Remotion.
 *
 * Usage:  node templates/render.mjs <job.json>
 *
 * job.json:
 * {
 *   "bundleDir": "templates/.bundle",        // cached webpack bundle (rebuilt when sources change)
 *   "rebuild": false,
 *   "concurrency": 4,
 *   "browserExecutable": null,                // optional; default: Remotion's own headless shell
 *   "tasks": [
 *     {"kind": "video", "composition": "Video"|"Short", "props": {...}, "frameRange": [0, 239],
 *      "out": "chunk_001.mp4", "crf": 14},
 *     {"kind": "still", "composition": "Thumbnail", "props": {...}, "frame": 0, "out": "thumb.png",
 *      "scale": 1}
 *   ]
 * }
 *
 * Progress is reported as JSON lines on stdout so the pipeline and dashboard can follow along:
 *   {"event":"bundle","progress":0.4} | {"event":"task_start","index":0} |
 *   {"event":"progress","index":0,"rendered":120,"total":240} | {"event":"task_done","index":0,"seconds":12.3}
 */
import {bundle} from '@remotion/bundler';
import {ensureBrowser, openBrowser, renderMedia, renderStill, selectComposition} from '@remotion/renderer';
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const emit = (obj) => process.stdout.write(JSON.stringify(obj) + '\n');

const sourceHash = () => {
  const h = crypto.createHash('sha1');
  const walk = (dir) => {
    for (const name of fs.readdirSync(dir).sort()) {
      if (name === '.bundle' || name === 'node_modules') continue;
      const p = path.join(dir, name);
      const st = fs.statSync(p);
      if (st.isDirectory()) walk(p);
      else if (/\.(tsx?|mjs|css|json|png|jpg|svg)$/.test(name)) {
        h.update(p);
        h.update(fs.readFileSync(p));
      }
    }
  };
  walk(here);
  return h.digest('hex');
};

const main = async () => {
  const jobPath = process.argv[2];
  if (!jobPath) {
    console.error('usage: node templates/render.mjs <job.json>');
    process.exit(2);
  }
  const job = JSON.parse(fs.readFileSync(jobPath, 'utf8'));
  const bundleDir = path.resolve(job.bundleDir ?? path.join(here, '.bundle'));
  const stamp = path.join(bundleDir, '.source-hash');
  const hash = sourceHash();
  const fresh = !job.rebuild && fs.existsSync(path.join(bundleDir, 'index.html')) && fs.existsSync(stamp) && fs.readFileSync(stamp, 'utf8') === hash;

  if (!fresh) {
    emit({event: 'bundle', progress: 0});
    // job media lives in <bundle>/public/job_media and must survive a rebuild
    const keep = path.join(bundleDir, 'public', 'job_media');
    const stash = fs.existsSync(keep) ? fs.mkdtempSync(path.join(os.tmpdir(), 'job_media_')) : null;
    if (stash) fs.cpSync(keep, stash, {recursive: true});
    await bundle({
      entryPoint: path.join(here, 'index.ts'),
      outDir: bundleDir,
      publicDir: path.join(here, 'public'),
      onProgress: (p) => emit({event: 'bundle', progress: p / 100}),
    });
    if (stash) fs.cpSync(stash, keep, {recursive: true});
    fs.writeFileSync(stamp, hash);
  }
  emit({event: 'bundle', progress: 1, cached: fresh});

  const browserExecutable = job.browserExecutable || process.env.GTA_FACTORY_BROWSER || null;
  if (!browserExecutable) {
    await ensureBrowser({onBrowserDownload: () => ({version: null, onProgress: ({percent}) => emit({event: 'browser_download', progress: percent})})});
  }
  const chromiumOptions = {gl: job.gl ?? (process.platform === 'darwin' ? 'angle' : 'swangle'), enableMultiProcessOnLinux: true};
  const browser = await openBrowser('chrome', {browserExecutable, chromiumOptions, chromeMode: 'headless-shell'});
  const concurrency = job.concurrency && job.concurrency > 0 ? job.concurrency : Math.max(1, os.cpus().length - 1);

  let failed = 0;
  for (let index = 0; index < job.tasks.length; index++) {
    const task = job.tasks[index];
    const t0 = Date.now();
    emit({event: 'task_start', index, kind: task.kind, out: task.out});
    try {
      const composition = await selectComposition({serveUrl: bundleDir, id: task.composition, inputProps: task.props, puppeteerInstance: browser, browserExecutable, chromiumOptions});
      fs.mkdirSync(path.dirname(path.resolve(task.out)), {recursive: true});
      if (task.kind === 'still') {
        await renderStill({composition, serveUrl: bundleDir, output: path.resolve(task.out), inputProps: task.props, frame: task.frame ?? 0, imageFormat: task.imageFormat ?? 'png', scale: task.scale ?? 1, puppeteerInstance: browser, browserExecutable, chromiumOptions});
      } else {
        const tmp = path.resolve(task.out) + '.part.mp4';
        await renderMedia({
          composition,
          serveUrl: bundleDir,
          codec: 'h264',
          outputLocation: tmp,
          inputProps: task.props,
          frameRange: task.frameRange ?? null,
          crf: task.crf ?? 14,
          pixelFormat: 'yuv420p',
          imageFormat: 'jpeg',
          jpegQuality: 92,
          muted: true,
          concurrency,
          puppeteerInstance: browser,
          browserExecutable,
          chromiumOptions,
          x264Preset: task.preset ?? 'veryfast',
          onProgress: ({renderedFrames, encodedFrames}) => emit({event: 'progress', index, rendered: renderedFrames, encoded: encodedFrames, total: task.frameRange ? task.frameRange[1] - task.frameRange[0] + 1 : composition.durationInFrames}),
        });
        fs.renameSync(tmp, path.resolve(task.out));
      }
      emit({event: 'task_done', index, out: task.out, seconds: (Date.now() - t0) / 1000});
    } catch (err) {
      failed++;
      emit({event: 'task_error', index, out: task.out, message: String(err && err.message ? err.message : err).slice(0, 1500)});
      if (job.stopOnError !== false) break;
    }
  }
  await browser.close({silent: true});
  emit({event: 'done', failed});
  process.exit(failed ? 1 : 0);
};

main().catch((err) => {
  emit({event: 'fatal', message: String(err && err.stack ? err.stack : err).slice(0, 3000)});
  process.exit(1);
});
