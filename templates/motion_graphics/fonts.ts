/**
 * Self-hosted fonts (SIL Open Font License) bundled from @fontsource, so renders
 * never depend on the network and look identical on every machine.
 */
import '@fontsource/barlow-condensed/600.css';
import '@fontsource/barlow-condensed/700.css';
import '@fontsource/barlow-condensed/800.css';
import '@fontsource/barlow-condensed/800-italic.css';
import '@fontsource/barlow-condensed/900.css';
import '@fontsource/barlow-condensed/900-italic.css';
import '@fontsource/inter/400.css';
import '@fontsource/inter/600.css';
import '@fontsource/inter/700.css';
import '@fontsource/inter/800.css';
import '@fontsource/jetbrains-mono/500.css';
import '@fontsource/jetbrains-mono/700.css';
import {continueRender, delayRender} from 'remotion';

const FACES = [
  '600 20px "Barlow Condensed"',
  '700 20px "Barlow Condensed"',
  '800 20px "Barlow Condensed"',
  'italic 800 20px "Barlow Condensed"',
  '900 20px "Barlow Condensed"',
  'italic 900 20px "Barlow Condensed"',
  '400 20px Inter',
  '600 20px Inter',
  '700 20px Inter',
  '800 20px Inter',
  '500 20px "JetBrains Mono"',
  '700 20px "JetBrains Mono"',
];

let started = false;

/** Block rendering until every face is decoded (no fallback-font frames). */
export const ensureFonts = () => {
  if (started || typeof document === 'undefined') return;
  started = true;
  const handle = delayRender('Loading fonts');
  Promise.all(FACES.map((f) => document.fonts.load(f)))
    .then(() => document.fonts.ready)
    .then(() => continueRender(handle))
    .catch(() => continueRender(handle));
};
