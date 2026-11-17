/**
 * Original animated backgrounds. All of them are procedural (gradients + SVG),
 * so there is no third-party imagery and nothing to license.
 * They are built from cheap primitives (no CSS blur filters) to keep renders fast.
 */
import React, {useMemo} from 'react';
import {AbsoluteFill, Img, OffthreadVideo, interpolate, staticFile, useCurrentFrame} from 'remotion';
import {C, hexA, seeded, useLayout} from './theme';

export type BackgroundKind = 'grid' | 'skyline' | 'mesh' | 'rays' | 'waves' | 'dots';

const Base: React.FC<{accent: string; children?: React.ReactNode}> = ({accent, children}) => (
  <AbsoluteFill style={{background: `radial-gradient(120% 90% at 50% 0%, ${hexA(accent, 0.16)} 0%, ${C.ink2} 42%, ${C.ink} 100%)`}}>{children}</AbsoluteFill>
);

/** Perspective neon grid rolling toward the viewer under a horizon glow. */
const Grid: React.FC<{accent: string}> = ({accent}) => {
  const frame = useCurrentFrame();
  const {H, W, u} = useLayout();
  const cell = 120 * u;
  const shift = (frame * 2.2 * u) % cell;
  const horizon = H * 0.56;
  return (
    <Base accent={accent}>
      <div style={{position: 'absolute', left: 0, right: 0, top: horizon - 220 * u, height: 440 * u, background: `radial-gradient(60% 50% at 50% 50%, ${hexA(accent, 0.34)} 0%, ${hexA(accent, 0.08)} 45%, transparent 75%)`}} />
      <div style={{position: 'absolute', left: 0, right: 0, top: horizon, bottom: 0, perspective: `${520 * u}px`, overflow: 'hidden'}}>
        <div
          style={{
            position: 'absolute',
            left: -W * 0.6,
            right: -W * 0.6,
            top: 0,
            height: H * 1.6,
            transform: 'rotateX(74deg)',
            transformOrigin: 'top center',
            backgroundImage: `linear-gradient(${hexA(accent, 0.55)} ${2 * u}px, transparent ${2 * u}px), linear-gradient(90deg, ${hexA(accent, 0.45)} ${2 * u}px, transparent ${2 * u}px)`,
            backgroundSize: `${cell}px ${cell}px`,
            backgroundPosition: `0 ${shift}px`,
          }}
        />
        <div style={{position: 'absolute', inset: 0, background: `linear-gradient(180deg, ${C.ink} 0%, transparent 22%, transparent 70%, ${hexA(C.ink, 0.85)} 100%)`}} />
      </div>
      <div style={{position: 'absolute', left: 0, right: 0, top: horizon - 1.5 * u, height: 3 * u, background: `linear-gradient(90deg, transparent, ${accent}, transparent)`, opacity: 0.9}} />
    </Base>
  );
};

/** Layered city silhouette with a striped sun: generic coastal-city dusk, drawn from scratch. */
const Skyline: React.FC<{accent: string; seed: number}> = ({accent, seed}) => {
  const frame = useCurrentFrame();
  const {W, H, u} = useLayout();
  const layers = useMemo(() => {
    const rnd = seeded(seed + 11);
    return [0, 1, 2].map((layer) => {
      const blocks: {x: number; w: number; h: number; lit: number[]}[] = [];
      let x = -200;
      while (x < 2400) {
        const w = 60 + rnd() * 130;
        const h = (layer === 0 ? 140 : layer === 1 ? 220 : 330) * (0.45 + rnd() * 0.9);
        const lit = Array.from({length: 10}, () => rnd());
        blocks.push({x, w, h, lit});
        x += w + rnd() * 26;
      }
      return blocks;
    });
  }, [seed]);
  const sunY = H * 0.5;
  const sunR = Math.min(W, H) * 0.3;
  const baseY = H * 0.86;
  return (
    <Base accent={accent}>
      <div style={{position: 'absolute', inset: 0, background: `linear-gradient(180deg, ${C.ink} 0%, ${hexA(C.violet, 0.22)} 45%, ${hexA(accent, 0.28)} 72%, ${C.ink2} 100%)`}} />
      <svg width={W} height={H} style={{position: 'absolute', inset: 0}}>
        <defs>
          <linearGradient id={`sun${seed}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor={C.sun} />
            <stop offset="0.55" stopColor={C.coral} />
            <stop offset="1" stopColor={C.violet} />
          </linearGradient>
          <clipPath id={`sunclip${seed}`}>
            {Array.from({length: 9}, (_, i) => {
              const gap = 4 + i * 3.2;
              const top = sunY - sunR + (i / 9) * sunR * 2 + ((frame * 0.25) % (sunR / 4.5));
              return <rect key={i} x={0} y={top} width={W} height={Math.max(2, (sunR * 2) / 9 - gap * u)} />;
            })}
          </clipPath>
        </defs>
        <circle cx={W * 0.72} cy={sunY} r={sunR * 1.25} fill={hexA(C.coral, 0.1)} />
        <circle cx={W * 0.72} cy={sunY} r={sunR} fill={`url(#sun${seed})`} clipPath={`url(#sunclip${seed})`} opacity={0.9} />
        {layers.map((blocks, layer) => {
          const drift = (frame * (0.25 + layer * 0.22) * u) % 400;
          const fill = layer === 0 ? '#151a3a' : layer === 1 ? '#0d1128' : '#070914';
          return (
            <g key={layer} transform={`translate(${-drift}, 0) scale(${u})`}>
              {blocks.map((b, i) => (
                <g key={i}>
                  <rect x={b.x} y={baseY / u - b.h} width={b.w} height={b.h + 400} fill={fill} />
                  {layer > 0 &&
                    b.lit.map((l, k) => {
                      const on = l > 0.55 && Math.sin(frame / 22 + l * 40 + k) > -0.6;
                      return on ? <rect key={k} x={b.x + 10 + (k % 3) * (b.w / 3.6)} y={baseY / u - b.h + 16 + Math.floor(k / 3) * 34} width={b.w / 7} height={9} fill={k % 4 === 0 ? C.sun : accent} opacity={0.5} /> : null;
                    })}
                </g>
              ))}
            </g>
          );
        })}
      </svg>
      <div style={{position: 'absolute', left: 0, right: 0, bottom: 0, height: H * 0.2, background: `linear-gradient(180deg, transparent, ${C.ink})`}} />
    </Base>
  );
};

/** Slow colour fields: calm backdrop for analysis scenes. */
const Mesh: React.FC<{accent: string; seed: number}> = ({accent, seed}) => {
  const frame = useCurrentFrame();
  const t = frame / 90 + seed;
  const blob = (color: string, i: number, size: number) => {
    const x = 50 + Math.sin(t * 0.7 + i * 2.1) * 34;
    const y = 50 + Math.cos(t * 0.55 + i * 1.3) * 30;
    return `radial-gradient(${size}% ${size}% at ${x}% ${y}%, ${hexA(color, 0.42)} 0%, ${hexA(color, 0.12)} 45%, transparent 72%)`;
  };
  return (
    <AbsoluteFill style={{background: C.ink}}>
      <AbsoluteFill style={{backgroundImage: [blob(accent, 0, 46), blob(C.violet, 1, 52), blob(C.cyan, 2, 38), blob(C.coral, 3, 30)].join(',')}} />
      <AbsoluteFill style={{background: `radial-gradient(90% 80% at 50% 50%, transparent 30%, ${hexA(C.ink, 0.86)} 100%)`}} />
    </AbsoluteFill>
  );
};

/** Rotating sunburst for hooks and calls to action. */
const Rays: React.FC<{accent: string}> = ({accent}) => {
  const frame = useCurrentFrame();
  const {W, H} = useLayout();
  const size = Math.hypot(W, H) * 1.3;
  return (
    <Base accent={accent}>
      <div
        style={{
          position: 'absolute',
          left: W / 2 - size / 2,
          top: H * 0.52 - size / 2,
          width: size,
          height: size,
          transform: `rotate(${frame * 0.12}deg)`,
          background: `repeating-conic-gradient(from 0deg, ${hexA(accent, 0.13)} 0deg 5deg, transparent 5deg 15deg)`,
          maskImage: 'radial-gradient(circle, black 0%, black 20%, transparent 62%)',
          WebkitMaskImage: 'radial-gradient(circle, black 0%, black 20%, transparent 62%)',
        }}
      />
      <AbsoluteFill style={{background: `radial-gradient(55% 45% at 50% 52%, ${hexA(accent, 0.22)} 0%, transparent 70%)`}} />
    </Base>
  );
};

/** Flowing contour lines: an abstract coastline / signal motif. */
const Waves: React.FC<{accent: string; seed: number}> = ({accent, seed}) => {
  const frame = useCurrentFrame();
  const {W, H, u} = useLayout();
  const lines = 13;
  return (
    <Base accent={accent}>
      <svg width={W} height={H} style={{position: 'absolute', inset: 0}}>
        {Array.from({length: lines}, (_, i) => {
          const baseY = H * (0.3 + (i / lines) * 0.75);
          const amp = (26 + i * 5) * u;
          const pts: string[] = [];
          for (let x = -40; x <= W + 40; x += 40) {
            const y = baseY + Math.sin(x / (210 * u) + frame / 34 + i * 0.6 + seed) * amp + Math.sin(x / (90 * u) - frame / 50 + i) * amp * 0.28;
            pts.push(`${x},${y.toFixed(1)}`);
          }
          return <polyline key={i} points={pts.join(' ')} fill="none" stroke={i % 4 === 0 ? accent : C.cyan} strokeOpacity={0.1 + (i / lines) * 0.3} strokeWidth={(1.5 + (i % 3)) * u} />;
        })}
      </svg>
      <AbsoluteFill style={{background: `linear-gradient(180deg, ${hexA(C.ink, 0.7)} 0%, transparent 35%, transparent 75%, ${hexA(C.ink, 0.9)} 100%)`}} />
    </Base>
  );
};

/** Halftone dot matrix with a sweeping light: the "evidence desk" backdrop. */
const Dots: React.FC<{accent: string}> = ({accent}) => {
  const frame = useCurrentFrame();
  const {u} = useLayout();
  const sweep = interpolate((frame % 240) / 240, [0, 1], [-30, 130]);
  const step = 34 * u;
  return (
    <Base accent={accent}>
      <AbsoluteFill style={{backgroundImage: `radial-gradient(${hexA(C.paper, 0.16)} ${2.2 * u}px, transparent ${2.6 * u}px)`, backgroundSize: `${step}px ${step}px`}} />
      <AbsoluteFill
        style={{
          backgroundImage: `radial-gradient(${hexA(accent, 0.9)} ${2.6 * u}px, transparent ${3 * u}px)`,
          backgroundSize: `${step}px ${step}px`,
          maskImage: `linear-gradient(105deg, transparent ${sweep - 18}%, black ${sweep}%, transparent ${sweep + 18}%)`,
          WebkitMaskImage: `linear-gradient(105deg, transparent ${sweep - 18}%, black ${sweep}%, transparent ${sweep + 18}%)`,
        }}
      />
      <AbsoluteFill style={{background: `radial-gradient(85% 75% at 50% 45%, transparent 25%, ${hexA(C.ink, 0.92)} 100%)`}} />
    </Base>
  );
};

export const Background: React.FC<{kind: BackgroundKind; accent: string; seed?: number}> = ({kind, accent, seed = 1}) => {
  switch (kind) {
    case 'skyline':
      return <Skyline accent={accent} seed={seed} />;
    case 'mesh':
      return <Mesh accent={accent} seed={seed} />;
    case 'rays':
      return <Rays accent={accent} />;
    case 'waves':
      return <Waves accent={accent} seed={seed} />;
    case 'dots':
      return <Dots accent={accent} />;
    default:
      return <Grid accent={accent} />;
  }
};

/** Licensed stock clip or operator-supplied still, darkened so text stays readable. */
export const MediaBackdrop: React.FC<{src: string; durationInFrames: number}> = ({src, durationInFrames}) => {
  const frame = useCurrentFrame();
  const scale = interpolate(frame, [0, durationInFrames], [1.06, 1.16]);
  return (
    <AbsoluteFill style={{background: C.ink, overflow: 'hidden'}}>
      <Img src={src.startsWith('http') ? src : staticFile(src)} style={{width: '100%', height: '100%', objectFit: 'cover', transform: `scale(${scale})`}} />
      <AbsoluteFill style={{background: `linear-gradient(180deg, ${hexA(C.ink, 0.55)} 0%, ${hexA(C.ink, 0.2)} 40%, ${hexA(C.ink, 0.88)} 100%)`}} />
    </AbsoluteFill>
  );
};

/**
 * Real GTA 6 footage behind the graphics: a pre-cut trailer clip (played muted, frame-accurate)
 * or an official screenshot with a slow Ken Burns move. `dim` (0-1) is how much the picture is
 * pushed back so the overlay stays readable: light for big type, stronger for dense panels.
 */
export const FootageBackdrop: React.FC<{src: string; kind: 'video' | 'photo'; durationInFrames: number; seed: number; accent: string; dim: number}> = ({src, kind, durationInFrames, seed, accent, dim}) => {
  const frame = useCurrentFrame();
  const t = frame / Math.max(1, durationInFrames);
  const url = src.startsWith('http') ? src : staticFile(src);
  // photos drift and zoom in a seed-dependent direction; video gets a barely visible push-in
  const dir = seed % 2 === 0 ? 1 : -1;
  const scale = kind === 'photo' ? interpolate(t, [0, 1], [1.08, 1.2]) : interpolate(t, [0, 1], [1.02, 1.06]);
  const x = kind === 'photo' ? interpolate(t, [0, 1], [-1.5 * dir, 1.5 * dir]) : 0;
  return (
    <AbsoluteFill style={{background: C.ink, overflow: 'hidden'}}>
      <AbsoluteFill style={{transform: `scale(${scale}) translateX(${x}%)`}}>
        {kind === 'video' ? (
          <OffthreadVideo src={url} muted style={{width: '100%', height: '100%', objectFit: 'cover'}} />
        ) : (
          <Img src={url} style={{width: '100%', height: '100%', objectFit: 'cover'}} />
        )}
      </AbsoluteFill>
      {/* channel grade: a touch of the section colour in the shadows, then the readability scrim */}
      <AbsoluteFill style={{background: `linear-gradient(160deg, ${hexA(accent, 0.16)} 0%, transparent 45%, ${hexA(C.violet, 0.14)} 100%)`, mixBlendMode: 'screen'}} />
      <AbsoluteFill style={{background: hexA(C.ink, dim)}} />
      <AbsoluteFill style={{background: `linear-gradient(180deg, ${hexA(C.ink, 0.5)} 0%, transparent 22%, transparent 62%, ${hexA(C.ink, 0.78)} 100%)`}} />
    </AbsoluteFill>
  );
};

/** Film grain + scanlines + vignette. One static noise tile, re-positioned every frame. */
export const Atmosphere: React.FC = () => {
  const frame = useCurrentFrame();
  const {u} = useLayout();
  const ox = (frame * 37) % 256;
  const oy = (frame * 61) % 256;
  return (
    <>
      <AbsoluteFill style={{backgroundImage: `url(${staticFile('grain.png')})`, backgroundSize: '256px 256px', backgroundPosition: `${ox}px ${oy}px`, opacity: 0.07, mixBlendMode: 'overlay'}} />
      <AbsoluteFill style={{backgroundImage: `repeating-linear-gradient(0deg, rgba(0,0,0,0.16) 0px, rgba(0,0,0,0.16) ${1 * u}px, transparent ${1 * u}px, transparent ${4 * u}px)`, opacity: 0.35}} />
      <AbsoluteFill style={{background: 'radial-gradient(120% 100% at 50% 50%, transparent 55%, rgba(0,0,0,0.55) 100%)'}} />
    </>
  );
};
