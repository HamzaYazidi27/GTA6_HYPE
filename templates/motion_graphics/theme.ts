/**
 * NEON DISPATCH design system - the single source of truth for colour, type and
 * spacing used by every scene, the Shorts layout and the thumbnails.
 *
 * Identity in one line: an ink-dark newsroom with neon signal colours, where
 * every claim wears its verification status as a stamp.
 */
import {useVideoConfig} from 'remotion';

export const C = {
  ink: '#06070E',
  ink2: '#0C0F1F',
  panel: '#12162E',
  panel2: '#1A2040',
  line: 'rgba(255,255,255,0.10)',
  paper: '#F6F8FF',
  mute: '#9BA6CC',
  coral: '#FF4D6D',
  cyan: '#2DE2E6',
  sun: '#FFD23F',
  violet: '#8A6BFF',
  lime: '#2EE59D',
  amber: '#FFAA2B',
};

export type Status = 'confirmed' | 'reported' | 'rumor' | 'speculation';

export const STATUS: Record<Status, {label: string; color: string; glyph: string}> = {
  confirmed: {label: 'CONFIRMED', color: C.lime, glyph: '✓'},
  reported: {label: 'REPORTED', color: C.cyan, glyph: '◆'},
  rumor: {label: 'RUMOR', color: C.amber, glyph: '?'},
  speculation: {label: 'SPECULATION', color: C.violet, glyph: '≈'},
};

export const F = {
  display: '"Barlow Condensed", "Arial Narrow", sans-serif',
  body: 'Inter, "Helvetica Neue", Arial, sans-serif',
  mono: '"JetBrains Mono", "SF Mono", Menlo, monospace',
};

/** Section accent: each part of the video has its own colour so the edit visibly moves forward. */
export const SECTION_ACCENT: Record<string, string> = {
  hook: C.coral,
  context: C.cyan,
  story: C.sun,
  evidence: C.lime,
  analysis: C.violet,
  community: C.cyan,
  conclusion: C.sun,
  payoff: C.violet,
  cta: C.coral,
};

export const accentFor = (section?: string, status?: string | null): string => {
  if (status && status in STATUS) return STATUS[status as Status].color;
  return SECTION_ACCENT[section ?? ''] ?? C.coral;
};

/**
 * Layout helper. `u` is the unit every size is multiplied by, so the same
 * component renders correctly at 1920x1080, 1080x1920, 4K or a 1280x720 still.
 */
export const useLayout = () => {
  const {width, height, fps} = useVideoConfig();
  const vertical = height > width;
  const u = Math.min(width, height) / 1080;
  return {
    W: width,
    H: height,
    fps,
    vertical,
    u,
    pad: (vertical ? 64 : 110) * u,
    /** area left free for captions + rail at the bottom */
    safeBottom: (vertical ? 520 : 190) * u,
    safeTop: (vertical ? 210 : 120) * u,
  };
};

export const seeded = (seed: number) => {
  let s = (seed * 9301 + 49297) % 233280;
  return () => {
    s = (s * 9301 + 49297) % 233280;
    return s / 233280;
  };
};

export const hexA = (hex: string, a: number) => {
  const h = hex.replace('#', '');
  const n = parseInt(h.length === 3 ? h.split('').map((c) => c + c).join('') : h, 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
};
