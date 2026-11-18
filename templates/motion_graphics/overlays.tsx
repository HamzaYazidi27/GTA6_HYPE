/** Persistent overlays: channel watermark, status tag, source strip, chapter rail, captions, transitions. */
import React from 'react';
import {AbsoluteFill, Img, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {clamp, easeInOut, easeOut} from './primitives';
import {C, F, STATUS, Status, hexA, useLayout} from './theme';

export type Brand = {name: string; tagline: string; logo?: string; watermarkOpacity?: number};

/** Fallback mark, used only when no channel logo is configured (channel.logo: ""): three signal bars in a rounded tile. */
export const BrandMark: React.FC<{size: number; color?: string}> = ({size, color = C.coral}) => (
  <svg width={size} height={size} viewBox="0 0 64 64">
    <rect x="2" y="2" width="60" height="60" rx="14" fill={C.ink} stroke={color} strokeWidth="4" />
    <rect x="15" y="34" width="8" height="16" rx="2" fill={C.cyan} />
    <rect x="28" y="24" width="8" height="26" rx="2" fill={C.sun} />
    <rect x="41" y="14" width="8" height="36" rx="2" fill={color} />
  </svg>
);

/** The channel logo (round, from channel.logo), or the fallback mark. */
export const ChannelLogo: React.FC<{logo?: string; size: number; color?: string; shadow?: boolean}> = ({logo, size, color, shadow = true}) =>
  logo ? (
    <Img
      src={logo.startsWith('http') ? logo : staticFile(logo)}
      style={{width: size, height: size, borderRadius: '50%', objectFit: 'cover', display: 'block', filter: shadow ? `drop-shadow(0 ${size * 0.05}px ${size * 0.14}px rgba(0,0,0,0.55))` : undefined}}
    />
  ) : (
    <BrandMark size={size} color={color} />
  );

/**
 * The channel watermark, in the top-left corner for the whole video (one fade-in at the start, no
 * re-animation at each scene, like a TV channel bug). Hidden during the end screen, which shows the logo big.
 */
export const Watermark: React.FC<{brand: Brand; hideFrom?: number; hideTo?: number}> = ({brand, hideFrom, hideTo}) => {
  const {u, pad, vertical} = useLayout();
  const frame = useCurrentFrame();
  let p = interpolate(frame, [6, 24], [0, 1], {...clamp, easing: easeOut});
  if (hideFrom !== undefined && hideTo !== undefined) {
    p *= 1 - interpolate(frame, [hideFrom - 8, hideFrom, hideTo, hideTo + 8], [0, 1, 1, 0], clamp);
  }
  if (p <= 0.001) return null;
  const opacity = (brand.watermarkOpacity ?? 0.92) * p;
  const left = pad * (vertical ? 1 : 0.55);
  const top = (vertical ? 92 : 36) * u;
  if (brand.logo) {
    const size = (vertical ? 116 : 84) * u; // ends at the title-safe line (safeTop)
    return (
      <div style={{position: 'absolute', left, top, opacity, transform: `scale(${0.9 + 0.1 * p})`, transformOrigin: 'top left'}}>
        <ChannelLogo logo={brand.logo} size={size} />
      </div>
    );
  }
  const size = (vertical ? 54 : 46) * u;
  return (
    <div style={{position: 'absolute', left, top: top + 6 * u, display: 'flex', alignItems: 'center', gap: 14 * u, opacity}}>
      <BrandMark size={size} />
      <div style={{fontFamily: F.mono, fontWeight: 700, fontSize: (vertical ? 26 : 22) * u, letterSpacing: '0.2em', color: C.paper}}>{brand.name.toUpperCase()}</div>
    </div>
  );
};

/** Top-right pill that tells viewers how solid the current information is. */
export const StatusTag: React.FC<{status: Status}> = ({status}) => {
  const {u, pad, vertical} = useLayout();
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const s = STATUS[status];
  const p = spring({frame: frame - 6, fps, config: {damping: 15, stiffness: 160}});
  const pulse = 0.6 + 0.4 * Math.sin(frame / 7);
  return (
    <div
      style={{
        position: 'absolute',
        right: pad * (vertical ? 1 : 0.55),
        top: (vertical ? 100 : 50) * u,
        display: 'flex',
        alignItems: 'center',
        gap: 12 * u,
        padding: `${9 * u}px ${20 * u}px`,
        borderRadius: 999,
        border: `${2 * u}px solid ${hexA(s.color, 0.7)}`,
        background: hexA(C.ink, 0.72),
        fontFamily: F.mono,
        fontWeight: 700,
        fontSize: (vertical ? 24 : 20) * u,
        letterSpacing: '0.16em',
        color: s.color,
        opacity: p,
        transform: `translateX(${(1 - p) * 40 * u}px)`,
      }}
    >
      <span style={{width: 12 * u, height: 12 * u, borderRadius: '50%', background: s.color, boxShadow: `0 0 ${16 * u * pulse}px ${s.color}`}} />
      {s.label}
    </div>
  );
};

export const SourceStrip: React.FC<{source: string}> = ({source}) => {
  const {u, pad, vertical} = useLayout();
  const frame = useCurrentFrame();
  const p = interpolate(frame, [10, 24], [0, 1], {...clamp, easing: easeOut});
  if (!source || vertical) return null;
  return (
    <div style={{position: 'absolute', left: pad * 0.55, bottom: 34 * u, fontFamily: F.mono, fontWeight: 500, fontSize: 19 * u, letterSpacing: '0.12em', color: C.mute, opacity: p * 0.9}}>
      SOURCE <span style={{color: C.paper}}>{'·'} {source.toUpperCase()}</span>
    </div>
  );
};

/** Small credit for the real footage on screen (bottom-right in 16:9, beside the watermark in 9:16). */
export const FootageCredit: React.FC<{credit: string; besideLogo?: boolean}> = ({credit, besideLogo = false}) => {
  const {u, pad, vertical} = useLayout();
  const frame = useCurrentFrame();
  const p = interpolate(frame, [6, 20], [0, 1], {...clamp, easing: easeOut});
  if (!credit) return null;
  const pos: React.CSSProperties = vertical ? (besideLogo ? {left: pad + 132 * u, top: 138 * u} : {left: pad, top: 168 * u}) : {right: pad * 0.55, bottom: 34 * u};
  return (
    <div style={{position: 'absolute', ...pos, fontFamily: F.mono, fontWeight: 500, fontSize: (vertical ? 20 : 17) * u, letterSpacing: '0.14em', color: C.paper, opacity: p * 0.7, textShadow: '0 1px 6px rgba(0,0,0,0.8)'}}>
      {credit.toUpperCase()}
    </div>
  );
};

/** Thin progress rail with chapter ticks: shows the video is going somewhere. */
export const ProgressRail: React.FC<{frame: number; total: number; chapters: {title: string; startFrame: number}[]}> = ({frame, total, chapters}) => {
  const {u, W, vertical} = useLayout();
  const p = Math.min(1, frame / Math.max(1, total));
  const current = [...chapters].reverse().find((c) => frame >= c.startFrame);
  return (
    <>
      <div style={{position: 'absolute', left: 0, right: 0, bottom: 0, height: 7 * u, background: 'rgba(255,255,255,0.09)'}}>
        <div style={{position: 'absolute', left: 0, top: 0, bottom: 0, width: `${p * 100}%`, background: `linear-gradient(90deg, ${C.coral}, ${C.sun})`, boxShadow: `0 0 ${14 * u}px ${C.coral}`}} />
        {chapters.slice(1).map((c, i) => (
          <div key={i} style={{position: 'absolute', left: (c.startFrame / total) * W, top: -3 * u, width: 3 * u, height: 13 * u, background: C.ink}} />
        ))}
      </div>
      {current && !vertical && (
        // above the footage credit (bottom-right, 34u), below the captions box (78u)
        <div style={{position: 'absolute', right: 60 * u, bottom: 56 * u, fontFamily: F.mono, fontWeight: 500, fontSize: 19 * u, letterSpacing: '0.12em', color: C.mute}}>
          {String(chapters.indexOf(current) + 1).padStart(2, '0')} / {String(chapters.length).padStart(2, '0')} <span style={{color: C.paper}}>{'·'} {current.title.toUpperCase()}</span>
        </div>
      )}
    </>
  );
};

export type Phrase = {s: number; e: number; words: {w: string; s: number; e: number}[]};

/** Burned-in captions: short phrases, the spoken word lights up. Timings are scene-local frames. */
export const Captions: React.FC<{phrases: Phrase[]; accent: string; emphasis?: string}> = ({phrases, accent, emphasis}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const {u, vertical, H} = useLayout();
  const phrase = phrases.find((p) => frame >= p.s && frame < p.e);
  if (!phrase) return null;
  const pop = spring({frame: frame - phrase.s, fps, config: {damping: 14, stiffness: 240, mass: 0.5}});
  const emph = (emphasis ?? '').toLowerCase().split(/\s+/).filter(Boolean);
  const size = (vertical ? 76 : 46) * u;
  return (
    <div style={{position: 'absolute', left: 0, right: 0, top: vertical ? H * 0.69 : undefined, bottom: vertical ? undefined : 78 * u, display: 'flex', justifyContent: 'center', padding: `0 ${(vertical ? 60 : 200) * u}px`}}>
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          justifyContent: 'center',
          // the column gap must stay wider than the active word's scale-up, or long words touch their neighbours
          gap: `${size * 0.08}px ${size * (vertical ? 0.36 : 0.3)}px`,
          padding: vertical ? 0 : `${12 * u}px ${28 * u}px ${14 * u}px`,
          borderRadius: 18 * u,
          background: vertical ? 'transparent' : hexA(C.ink, 0.78),
          border: vertical ? 'none' : `${1.5 * u}px solid ${C.line}`,
          transform: `scale(${0.94 + 0.06 * pop})`,
          opacity: Math.min(1, pop * 2),
          fontFamily: vertical ? F.display : F.body,
          fontWeight: vertical ? 900 : 700,
          fontSize: size,
          lineHeight: 1.12,
          textTransform: vertical ? 'uppercase' : 'none',
        }}
      >
        {phrase.words.map((w, i) => {
          const active = frame >= w.s && frame < w.e + 2;
          const spoken = frame >= w.s;
          const key = w.w.toLowerCase().replace(/[^a-z0-9$%']/g, '');
          const isEmph = emph.includes(key);
          const color = active ? (vertical ? accent : C.sun) : isEmph && spoken ? accent : spoken ? C.paper : hexA(C.paper, vertical ? 0.55 : 0.5);
          return (
            <span
              key={i}
              style={{
                color,
                transform: active ? `translateY(${-3 * u}px) scale(${vertical ? 1.06 : 1.03})` : 'none',
                transformOrigin: 'center bottom',
                display: 'inline-block',
                textShadow: vertical ? `0 ${5 * u}px 0 ${C.ink}, 0 0 ${26 * u}px rgba(0,0,0,0.9), ${3 * u}px ${3 * u}px 0 ${C.ink}, ${-3 * u}px ${-3 * u}px 0 ${C.ink}, ${3 * u}px ${-3 * u}px 0 ${C.ink}, ${-3 * u}px ${3 * u}px 0 ${C.ink}` : 'none',
              }}
            >
              {w.w}
            </span>
          );
        })}
      </div>
    </div>
  );
};

export type TransitionKind = 'cut' | 'wipe' | 'slice' | 'glitch' | 'push' | 'flash';
const IN_FRAMES = 9;

/**
 * Transition overlay. Each scene carries its own entrance cover, so scene chunks
 * can be rendered independently and still join seamlessly.
 */
export const TransitionIn: React.FC<{kind: TransitionKind; accent: string}> = ({kind, accent}) => {
  const frame = useCurrentFrame();
  const {W, H, u} = useLayout();
  if (kind === 'cut' || frame > IN_FRAMES + 4) return null;
  const p = interpolate(frame, [0, IN_FRAMES], [0, 1], {...clamp, easing: easeInOut});
  if (kind === 'flash') {
    return <AbsoluteFill style={{background: C.paper, opacity: interpolate(frame, [0, 6], [0.85, 0], clamp)}} />;
  }
  if (kind === 'wipe') {
    return (
      <AbsoluteFill>
        <div style={{position: 'absolute', top: 0, bottom: 0, left: `${p * 100}%`, width: '100%', background: C.ink}} />
        <div style={{position: 'absolute', top: 0, bottom: 0, left: `${p * 100}%`, width: 14 * u, marginLeft: -7 * u, background: accent, boxShadow: `0 0 ${40 * u}px ${accent}`}} />
      </AbsoluteFill>
    );
  }
  if (kind === 'slice') {
    const bars = 6;
    return (
      <AbsoluteFill>
        {Array.from({length: bars}, (_, i) => {
          const local = interpolate(frame, [i * 0.9, IN_FRAMES - (bars - i) * 0.3 + 2], [0, 1], {...clamp, easing: easeInOut});
          const dir = i % 2 === 0 ? 1 : -1;
          return <div key={i} style={{position: 'absolute', left: 0, width: W, top: (H / bars) * i - 1, height: H / bars + 2, background: i % 3 === 0 ? accent : C.ink, transform: `translateX(${dir * local * 102}%)`}} />;
        })}
      </AbsoluteFill>
    );
  }
  if (kind === 'push') {
    return (
      <AbsoluteFill>
        <div style={{position: 'absolute', left: 0, right: 0, top: 0, height: '100%', background: `linear-gradient(180deg, ${accent} 0%, ${C.ink} 12%)`, transform: `translateY(${-p * 102}%)`}} />
      </AbsoluteFill>
    );
  }
  // glitch: RGB-split strips that settle quickly
  const strips = 9;
  return (
    <AbsoluteFill style={{mixBlendMode: 'screen'}}>
      {Array.from({length: strips}, (_, i) => {
        const jitter = Math.sin(frame * 12.9898 + i * 78.233) * 43758.5453;
        const r = jitter - Math.floor(jitter);
        const on = r > 0.35 && frame < IN_FRAMES;
        if (!on) return null;
        return <div key={i} style={{position: 'absolute', left: 0, right: 0, top: `${(i / strips) * 100}%`, height: `${100 / strips}%`, background: i % 2 === 0 ? hexA(C.cyan, 0.5) : hexA(accent, 0.5), transform: `translateX(${(r - 0.5) * 220 * u * (1 - p)}px)`, opacity: 0.75 * (1 - p)}} />;
      })}
      <AbsoluteFill style={{background: C.paper, opacity: interpolate(frame, [0, 3], [0.5, 0], clamp)}} />
    </AbsoluteFill>
  );
};

/** Two-frame dip at the very end of a scene so hard cuts feel intentional. */
export const SceneOut: React.FC<{durationInFrames: number}> = ({durationInFrames}) => {
  const frame = useCurrentFrame();
  const o = interpolate(frame, [durationInFrames - 4, durationInFrames - 1], [0, 0.35], clamp);
  return <AbsoluteFill style={{background: C.ink, opacity: o, pointerEvents: 'none'}} />;
};
