/** Animation primitives shared by every scene. */
import React from 'react';
import {Easing, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {C, F, STATUS, Status, hexA, useLayout} from './theme';

export const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;
export const easeOut = Easing.bezier(0.16, 1, 0.3, 1);
export const easeInOut = Easing.bezier(0.65, 0, 0.35, 1);

/** 0 -> 1 spring that starts at `delay` frames. */
export const useSpring = (delay = 0, config: {damping?: number; stiffness?: number; mass?: number} = {}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  return spring({frame: frame - delay, fps, config: {damping: 16, stiffness: 140, mass: 0.7, ...config}});
};

/** Linear-eased 0 -> 1 progress between two frames. */
export const useProgress = (from: number, duration: number, easing = easeOut) => {
  const frame = useCurrentFrame();
  return interpolate(frame, [from, from + Math.max(1, duration)], [0, 1], {...clamp, easing});
};

type RevealProps = {
  delay?: number;
  from?: 'up' | 'down' | 'left' | 'right' | 'scale' | 'none';
  distance?: number;
  children: React.ReactNode;
  style?: React.CSSProperties;
};

/** Standard entrance: fade + slide (or scale) on a spring. */
export const Reveal: React.FC<RevealProps> = ({delay = 0, from = 'up', distance = 60, children, style}) => {
  const p = useSpring(delay);
  const {u} = useLayout();
  const d = distance * u * (1 - p);
  const t =
    from === 'up' ? `translateY(${d}px)` : from === 'down' ? `translateY(${-d}px)` : from === 'left' ? `translateX(${-d}px)` : from === 'right' ? `translateX(${d}px)` : from === 'scale' ? `scale(${0.82 + 0.18 * p})` : 'none';
  return <div style={{opacity: Math.min(1, p * 1.6), transform: t, ...style}}>{children}</div>;
};

/** Clip-path wipe: the element is revealed left-to-right like a marker stroke. */
export const Wipe: React.FC<{delay?: number; duration?: number; children: React.ReactNode; style?: React.CSSProperties}> = ({delay = 0, duration = 14, children, style}) => {
  const p = useProgress(delay, duration);
  return <div style={{clipPath: `inset(0 ${100 - p * 100}% 0 0)`, ...style}}>{children}</div>;
};

/** Word-by-word slam used by kinetic headlines. */
export const SlamWords: React.FC<{text: string; delay?: number; stagger?: number; accent?: string; accentColor?: string; style?: React.CSSProperties}> = ({text, delay = 0, stagger = 3, accent = '', accentColor = C.coral, style}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const accentWords = accent.toUpperCase().split(/\s+/).filter(Boolean);
  const words = text.split(/\s+/).filter(Boolean);
  return (
    <div style={{display: 'flex', flexWrap: 'wrap', gap: '0 0.26em', ...style}}>
      {words.map((w, i) => {
        const p = spring({frame: frame - delay - i * stagger, fps, config: {damping: 12, stiffness: 190, mass: 0.6}});
        const hot = accentWords.includes(w.toUpperCase().replace(/[^A-Z0-9$%.]/g, ''));
        return (
          <span key={i} style={{display: 'inline-block', overflow: 'hidden', paddingBottom: '0.06em'}}>
            <span
              style={{
                display: 'inline-block',
                transform: `translateY(${(1 - p) * 110}%) skewX(${(1 - p) * -14}deg)`,
                color: hot ? accentColor : undefined,
                textShadow: hot ? `0 0 ${0.25}em ${hexA(accentColor, 0.45)}` : undefined,
              }}
            >
              {w}
            </span>
          </span>
        );
      })}
    </div>
  );
};

/** Small monospace label with a leading tick mark. */
export const Kicker: React.FC<{children: React.ReactNode; color?: string; delay?: number; size?: number}> = ({children, color = C.cyan, delay = 0, size = 26}) => {
  const {u} = useLayout();
  const p = useSpring(delay);
  return (
    <div style={{display: 'flex', alignItems: 'center', gap: 14 * u, opacity: p, transform: `translateX(${(1 - p) * -24 * u}px)`}}>
      <div style={{width: 34 * u * p, height: 4 * u, background: color, boxShadow: `0 0 ${14 * u}px ${color}`}} />
      <div style={{fontFamily: F.mono, fontWeight: 700, fontSize: size * u, letterSpacing: '0.16em', color, textTransform: 'uppercase'}}>{children}</div>
    </div>
  );
};

/** Glassy card used by source cards, quotes and lists. */
export const Panel: React.FC<{children: React.ReactNode; accent?: string; style?: React.CSSProperties; delay?: number; glow?: boolean}> = ({children, accent = C.cyan, style, delay = 0, glow = true}) => {
  const {u} = useLayout();
  const p = useSpring(delay, {damping: 18});
  return (
    <div
      style={{
        position: 'relative',
        background: `linear-gradient(155deg, ${hexA(C.panel2, 0.96)} 0%, ${hexA(C.panel, 0.96)} 55%, ${hexA(C.ink2, 0.98)} 100%)`,
        border: `${2 * u}px solid ${hexA(accent, 0.32)}`,
        borderRadius: 26 * u,
        boxShadow: glow ? `0 ${30 * u}px ${90 * u}px rgba(0,0,0,0.55), 0 0 ${70 * u}px ${hexA(accent, 0.13)}, inset 0 ${1.5 * u}px 0 rgba(255,255,255,0.09)` : undefined,
        opacity: Math.min(1, p * 1.5),
        transform: `translateY(${(1 - p) * 70 * u}px) scale(${0.96 + 0.04 * p})`,
        ...style,
      }}
    >
      <div style={{position: 'absolute', left: 0, top: 26 * u, bottom: 26 * u, width: 7 * u, borderRadius: 4 * u, background: accent, boxShadow: `0 0 ${22 * u}px ${accent}`, transform: `scaleY(${p})`}} />
      {children}
    </div>
  );
};

/** The channel's signature: a verification stamp that slams onto the frame. */
export const Stamp: React.FC<{status: Status; delay?: number; size?: number; rotate?: number}> = ({status, delay = 0, size = 64, rotate = -7}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const {u} = useLayout();
  const s = STATUS[status] ?? STATUS.reported;
  const p = spring({frame: frame - delay, fps, config: {damping: 9, stiffness: 240, mass: 0.7}});
  const ring = interpolate(frame - delay, [0, 16], [0, 1], clamp);
  const fs = size * u;
  return (
    <div style={{position: 'relative', display: 'inline-block', transform: `rotate(${rotate}deg) scale(${interpolate(p, [0, 1], [2.6, 1])})`, opacity: Math.min(1, p * 2.2)}}>
      <div style={{position: 'absolute', inset: -fs * 0.5, borderRadius: fs * 0.5, border: `${3 * u}px solid ${hexA(s.color, 0.55 * (1 - ring))}`, transform: `scale(${1 + ring * 0.5})`}} />
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: fs * 0.28,
          padding: `${fs * 0.2}px ${fs * 0.5}px ${fs * 0.16}px`,
          border: `${Math.max(3, fs * 0.09)}px solid ${s.color}`,
          borderRadius: fs * 0.22,
          color: s.color,
          background: hexA(s.color, 0.09),
          boxShadow: `0 0 ${fs * 0.7}px ${hexA(s.color, 0.35)}, inset 0 0 ${fs * 0.5}px ${hexA(s.color, 0.12)}`,
          fontFamily: F.display,
          fontWeight: 900,
          fontSize: fs,
          lineHeight: 1,
          letterSpacing: '0.06em',
          whiteSpace: 'nowrap',
        }}
      >
        <span style={{display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: fs * 0.82, height: fs * 0.82, borderRadius: '50%', background: s.color, color: C.ink, fontFamily: F.body, fontWeight: 800, fontSize: fs * 0.56}}>{s.glyph}</span>
        {s.label}
      </div>
    </div>
  );
};

/** Number that counts up with easing; keeps digits tabular so the layout does not jitter. */
export const Counter: React.FC<{value: number; delay?: number; duration?: number; decimals?: number; prefix?: string; suffix?: string; style?: React.CSSProperties}> = ({value, delay = 0, duration = 34, decimals = 0, prefix = '', suffix = '', style}) => {
  const p = useProgress(delay, duration, Easing.bezier(0.12, 0.9, 0.25, 1));
  const v = value * p;
  const txt = v.toLocaleString('en-US', {minimumFractionDigits: decimals, maximumFractionDigits: decimals});
  return (
    <span style={{fontVariantNumeric: 'tabular-nums', ...style}}>
      {prefix}
      {txt}
      {suffix}
    </span>
  );
};

/** Highlighter stroke behind a phrase. */
export const Marker: React.FC<{children: React.ReactNode; color?: string; delay?: number}> = ({children, color = C.sun, delay = 0}) => {
  const p = useProgress(delay, 16);
  return (
    <span style={{position: 'relative', display: 'inline', whiteSpace: 'nowrap'}}>
      <span style={{position: 'absolute', left: '-0.12em', right: '-0.12em', bottom: '0.04em', height: '0.42em', background: hexA(color, 0.55), transform: `scaleX(${p})`, transformOrigin: 'left center', borderRadius: '0.08em'}} />
      <span style={{position: 'relative'}}>{children}</span>
    </span>
  );
};

/** Render `text` with `phrase` wrapped in a Marker (case-insensitive, first match). */
export const withMarker = (text: string, phrase: string | undefined, color: string, delay: number): React.ReactNode => {
  if (!phrase) return text;
  const i = text.toLowerCase().indexOf(phrase.toLowerCase());
  if (i < 0) return text;
  return (
    <>
      {text.slice(0, i)}
      <Marker color={color} delay={delay}>
        {text.slice(i, i + phrase.length)}
      </Marker>
      {text.slice(i + phrase.length)}
    </>
  );
};

/** Slow continuous push-in so no scene is ever a frozen frame. */
export const CameraDrift: React.FC<{children: React.ReactNode; durationInFrames: number; amount?: number; seed?: number}> = ({children, durationInFrames, amount = 0.045, seed = 0}) => {
  const frame = useCurrentFrame();
  const t = frame / Math.max(1, durationInFrames);
  const dir = seed % 2 === 0 ? 1 : -1;
  const scale = 1 + amount * t;
  const x = dir * 14 * t;
  const y = -8 * t;
  return <div style={{position: 'absolute', inset: 0, transform: `scale(${scale}) translate(${x}px, ${y}px)`, transformOrigin: seed % 3 === 0 ? '40% 55%' : '60% 45%'}}>{children}</div>;
};

/** Frame at which list item `i` should enter: narration cue when known, otherwise an even spread. */
export const cueFrame = (cues: number[] | undefined, i: number, count: number, durationInFrames: number, start = 12): number => {
  const c = cues?.[i];
  if (typeof c === 'number' && c >= 0) return Math.max(start, c - 3);
  const usable = Math.max(20, durationInFrames * 0.72 - start);
  return Math.round(start + (usable * i) / Math.max(1, count));
};
