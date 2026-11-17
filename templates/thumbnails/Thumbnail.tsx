/**
 * Thumbnail design system (1280x720). Three reusable concepts, one dominant idea each:
 *
 *   stamp  - headline block + verification stamp + illustrated motif   (news with a clear verdict)
 *   number - one giant figure or date as the hero                      (stories driven by a number)
 *   split  - diagonal two-tone split with motif                        (analysis / "what it means")
 *
 * Original identity: ink background, neon signal colours, heavy italic condensed type,
 * a verification stamp instead of shock faces or arrows. No third-party artwork.
 */
import React from 'react';
import {AbsoluteFill, Img, staticFile} from 'remotion';
import {ensureFonts} from '../motion_graphics/fonts';
import {BrandMark} from '../motion_graphics/overlays';
import {C, F, STATUS, Status, hexA} from '../motion_graphics/theme';

ensureFonts();

export type ThumbProps = {
  concept: 'stamp' | 'number' | 'split';
  lines: string[];
  accent: string;
  status: Status | null;
  motif: 'sunset' | 'calendar' | 'chart' | 'document' | 'question' | 'controller';
  bigText: string;
  label: string;
  brand: string;
  color: 'coral' | 'cyan' | 'sun' | 'violet' | 'lime';
  calendar: {month: string; day: string} | null;
  /** optional real GTA 6 screenshot / trailer still (public path); replaces the drawn motif */
  photo?: string | null;
};

/** Full-bleed real GTA 6 picture, graded into the channel palette. `side` keeps one side darker for the text. */
const Photo: React.FC<{src: string; color: string; side: 'left' | 'right' | 'none'}> = ({src, color, side}) => (
  <AbsoluteFill>
    <Img src={src.startsWith('http') ? src : staticFile(src)} style={{width: '100%', height: '100%', objectFit: 'cover', filter: 'saturate(1.25) contrast(1.08)'}} />
    <AbsoluteFill style={{background: `linear-gradient(160deg, ${hexA(color, 0.18)} 0%, transparent 40%, ${hexA(C.violet, 0.22)} 100%)`, mixBlendMode: 'screen'}} />
    {side !== 'none' ? <AbsoluteFill style={{background: `linear-gradient(${side === 'left' ? 90 : 270}deg, ${hexA(C.ink, 0.92)} 0%, ${hexA(C.ink, 0.7)} 38%, transparent 70%)`}} /> : null}
    <AbsoluteFill style={{background: `linear-gradient(180deg, transparent 55%, ${hexA(C.ink, 0.7)} 100%)`}} />
  </AbsoluteFill>
);

const isHot = (word: string, accent: string) => accent.toUpperCase().split(/\s+/).filter(Boolean).includes(word.toUpperCase().replace(/[^A-Z0-9$%.]/g, ''));
/** Solid outline built from stacked shadows (text-stroke would eat into the letterforms). */
const outline = (color: string, r: number) => {
  const out: string[] = [];
  for (let a = 0; a < 16; a++) out.push(`${(Math.cos((a / 16) * Math.PI * 2) * r).toFixed(1)}px ${(Math.sin((a / 16) * Math.PI * 2) * r).toFixed(1)}px 0 ${color}`);
  out.push(`0 ${r * 2}px 0 ${color}`);
  return out.join(', ');
};

const W = 1280;
const H = 720;

const StaticStamp: React.FC<{status: Status; size: number; rotate?: number}> = ({status, size, rotate = -8}) => {
  const s = STATUS[status];
  return (
    <div style={{display: 'inline-flex', alignItems: 'center', gap: size * 0.26, padding: `${size * 0.18}px ${size * 0.46}px ${size * 0.14}px`, border: `${size * 0.1}px solid ${s.color}`, borderRadius: size * 0.22, color: s.color, background: hexA(C.ink, 0.86), boxShadow: `0 0 ${size * 0.8}px ${hexA(s.color, 0.5)}, 0 ${size * 0.2}px ${size * 0.5}px rgba(0,0,0,0.6)`, fontFamily: F.display, fontWeight: 900, fontSize: size, lineHeight: 1, letterSpacing: '0.05em', transform: `rotate(${rotate}deg)`, whiteSpace: 'nowrap'}}>
      <span style={{display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: size * 0.84, height: size * 0.84, borderRadius: '50%', background: s.color, color: C.ink, fontFamily: F.body, fontWeight: 800, fontSize: size * 0.58}}>{s.glyph}</span>
      {s.label}
    </div>
  );
};

const Headline: React.FC<{lines: string[]; accent: string; color: string; maxWidth: number; maxSize?: number}> = ({lines, accent, color, maxWidth, maxSize = 190}) => {
  const longest = Math.max(...lines.map((l) => l.length), 4);
  const size = Math.min(maxSize, (maxWidth / longest) * 2.12, (H * 0.62) / Math.max(1, lines.length) / 0.93);
  const hot = accent.toUpperCase().split(/\s+/).filter(Boolean);
  return (
    <div style={{fontFamily: F.display, fontWeight: 900, fontStyle: 'italic', fontSize: size, lineHeight: 0.93, letterSpacing: '-0.01em', color: C.paper, textTransform: 'uppercase', transform: 'rotate(-2.5deg)', transformOrigin: 'left center'}}>
      {lines.map((line, i) => (
        <div key={i} style={{whiteSpace: 'nowrap'}}>
          {line.split(/\s+/).map((w, k) => {
            const on = hot.includes(w.toUpperCase().replace(/[^A-Z0-9$%.]/g, ''));
            return (
              <span key={k} style={{color: on ? color : C.paper, textShadow: `0 ${size * 0.05}px 0 ${C.ink}, 0 0 ${size * 0.3}px rgba(0,0,0,0.75)`, marginRight: '0.22em', display: 'inline-block'}}>
                {w}
              </span>
            );
          })}
        </div>
      ))}
    </div>
  );
};

/** Original vector illustrations (drawn for this project). */
const Motif: React.FC<{kind: ThumbProps['motif']; color: string; size: number; calendar: ThumbProps['calendar']}> = ({kind, color, size, calendar}) => {
  const s = size;
  if (kind === 'calendar') {
    return (
      <div style={{width: s * 0.82, borderRadius: s * 0.08, overflow: 'hidden', background: C.paper, transform: 'rotate(6deg)', boxShadow: `0 ${s * 0.06}px ${s * 0.2}px rgba(0,0,0,0.6), 0 0 ${s * 0.3}px ${hexA(color, 0.45)}`}}>
        <div style={{background: color, color: C.ink, textAlign: 'center', fontFamily: F.display, fontWeight: 900, fontSize: s * 0.2, letterSpacing: '0.1em', padding: `${s * 0.02}px 0`}}>{calendar?.month ?? 'DATE'}</div>
        <div style={{textAlign: 'center', fontFamily: F.display, fontWeight: 900, fontSize: s * 0.6, lineHeight: 0.98, color: C.ink, paddingBottom: s * 0.03}}>{calendar?.day ?? '?'}</div>
      </div>
    );
  }
  return (
    <svg width={s} height={s} viewBox="0 0 400 400" style={{overflow: 'visible', filter: `drop-shadow(0 0 ${s * 0.08}px ${hexA(color, 0.55)})`}}>
      <defs>
        <linearGradient id="mg" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={C.sun} />
          <stop offset="0.6" stopColor={C.coral} />
          <stop offset="1" stopColor={C.violet} />
        </linearGradient>
        <clipPath id="stripes">
          {[0, 1, 2, 3, 4, 5, 6, 7].map((i) => (
            <rect key={i} x="0" y={40 + i * 42} width="400" height={36 - i * 3.2} />
          ))}
        </clipPath>
      </defs>
      {kind === 'sunset' && (
        <g>
          <circle cx="200" cy="200" r="170" fill="url(#mg)" clipPath="url(#stripes)" />
          <g fill={C.ink}>
            <rect x="30" y="300" width="50" height="100" />
            <rect x="86" y="262" width="42" height="140" />
            <rect x="134" y="318" width="60" height="90" />
            <rect x="240" y="280" width="46" height="130" />
            <rect x="292" y="310" width="70" height="100" />
            <path d="M205 400 C207 330 212 280 222 236 l10 2 C226 286 222 330 222 400 Z" />
            <path d="M226 238 C190 214 150 216 126 238 C160 226 196 232 226 244 Z" />
            <path d="M226 238 C262 208 304 212 326 236 C292 226 258 232 228 246 Z" />
            <path d="M226 236 C212 196 180 176 150 176 C186 186 208 208 222 242 Z" />
            <path d="M226 236 C242 194 274 176 304 178 C268 188 244 210 230 244 Z" />
          </g>
        </g>
      )}
      {kind === 'chart' && (
        <g>
          {[70, 120, 175, 250].map((h, i) => (
            <rect key={i} x={40 + i * 86} y={360 - h} width="62" height={h} rx="10" fill={i === 3 ? color : hexA(C.paper, 0.85)} />
          ))}
          <path d="M40 250 L130 205 L215 150 L330 62" fill="none" stroke={C.sun} strokeWidth="18" strokeLinecap="round" strokeLinejoin="round" />
          <path d="M276 52 L346 46 L338 116 Z" fill={C.sun} />
        </g>
      )}
      {kind === 'document' && (
        <g transform="rotate(7 200 200)">
          <rect x="70" y="24" width="260" height="350" rx="20" fill={C.paper} />
          <rect x="70" y="24" width="260" height="74" rx="20" fill={color} />
          <rect x="70" y="72" width="260" height="26" fill={color} />
          {[140, 180, 220, 260, 300].map((y, i) => (
            <rect key={i} x="104" y={y} width={i === 4 ? 110 : 192} height="16" rx="8" fill={i === 1 ? C.coral : '#B9C0DE'} />
          ))}
          <circle cx="282" cy="318" r="40" fill={C.ink} stroke={color} strokeWidth="10" />
          <path d="M262 318 l14 14 l26 -30" fill="none" stroke={color} strokeWidth="11" strokeLinecap="round" strokeLinejoin="round" />
        </g>
      )}
      {kind === 'question' && (
        <g>
          <circle cx="200" cy="200" r="176" fill={hexA(color, 0.14)} stroke={color} strokeWidth="14" strokeDasharray="34 20" />
          <text x="200" y="300" textAnchor="middle" fontFamily="Barlow Condensed" fontWeight="900" fontStyle="italic" fontSize="330" fill={color}>
            ?
          </text>
        </g>
      )}
      {kind === 'controller' && (
        <g>
          <path d="M96 120 h208 c46 0 70 34 78 92 l14 96 c6 44 -44 62 -70 28 l-40 -52 h-172 l-40 52 c-26 34 -76 16 -70 -28 l14 -96 c8 -58 32 -92 78 -92 Z" fill={C.paper} />
          <rect x="104" y="176" width="68" height="22" rx="8" fill={C.ink} />
          <rect x="127" y="153" width="22" height="68" rx="8" fill={C.ink} />
          <circle cx="262" cy="166" r="17" fill={color} />
          <circle cx="298" cy="198" r="17" fill={C.coral} />
          <circle cx="226" cy="198" r="17" fill={C.sun} />
          <circle cx="262" cy="230" r="17" fill={C.cyan} />
        </g>
      )}
    </svg>
  );
};

const Chip: React.FC<{children: React.ReactNode; color: string}> = ({children, color}) => (
  <div style={{display: 'inline-block', padding: '6px 20px 4px', borderRadius: 10, background: color, color: C.ink, fontFamily: F.display, fontWeight: 900, fontSize: 46, letterSpacing: '0.04em', lineHeight: 1.05}}>{children}</div>
);

const Frame: React.FC<{children: React.ReactNode; color: string; brand: string}> = ({children, color, brand}) => (
  <AbsoluteFill style={{background: C.ink, overflow: 'hidden'}}>
    {children}
    <AbsoluteFill style={{backgroundImage: `url(${staticFile('grain.png')})`, backgroundSize: '256px 256px', opacity: 0.06, mixBlendMode: 'overlay'}} />
    <div style={{position: 'absolute', left: 0, right: 0, bottom: 0, height: 12, background: `linear-gradient(90deg, ${color}, ${C.sun})`}} />
    <div style={{position: 'absolute', right: 28, top: 24, display: 'flex', alignItems: 'center', gap: 10, opacity: 0.95}}>
      <BrandMark size={44} color={color} />
      <div style={{fontFamily: F.mono, fontWeight: 700, fontSize: 17, letterSpacing: '0.2em', color: C.paper}}>{brand.toUpperCase()}</div>
    </div>
  </AbsoluteFill>
);

export const Thumbnail: React.FC<ThumbProps> = (p) => {
  const color = (C as Record<string, string>)[p.color] ?? C.coral;
  const status = p.status && p.status in STATUS ? p.status : null;
  if (p.concept === 'number') {
    const big = p.bigText || p.lines[0] || 'GTA 6';
    const size = Math.min(430, 1560 / Math.max(3, big.length));
    return (
      <Frame color={color} brand={p.brand}>
        {p.photo ? (
          <Photo src={p.photo} color={color} side="left" />
        ) : (
          <>
            <AbsoluteFill style={{background: `radial-gradient(70% 80% at 30% 55%, ${hexA(color, 0.42)} 0%, ${hexA(C.violet, 0.16)} 45%, ${C.ink} 80%)`}} />
            <div style={{position: 'absolute', left: -200, right: -200, top: 430, bottom: -200, perspective: 420}}>
              <div style={{position: 'absolute', inset: 0, transform: 'rotateX(72deg)', transformOrigin: 'top center', backgroundImage: `linear-gradient(${hexA(color, 0.6)} 2px, transparent 2px), linear-gradient(90deg, ${hexA(color, 0.5)} 2px, transparent 2px)`, backgroundSize: '90px 90px'}} />
            </div>
          </>
        )}
        <div style={{position: 'absolute', left: 56, top: 40}}>
          <Chip color={C.paper}>GTA 6</Chip>
        </div>
        <div style={{position: 'absolute', left: 50, top: 108, fontFamily: F.display, fontWeight: 900, fontStyle: 'italic', fontSize: size, lineHeight: 0.92, color: C.paper, letterSpacing: '-0.02em', transform: 'rotate(-3deg)', textShadow: `0 10px 0 ${C.ink}, 0 0 90px ${hexA(color, 0.9)}`, whiteSpace: 'nowrap'}}>{big}</div>
        <div style={{position: 'absolute', left: 60, bottom: 54, right: 380}}>
          <div style={{display: 'inline-block', background: color, color: C.ink, fontFamily: F.display, fontWeight: 900, fontStyle: 'italic', fontSize: Math.min(104, 1500 / Math.max(8, (p.label || p.lines.join(' ')).length)), lineHeight: 1, padding: '10px 26px 6px', transform: 'rotate(-3deg)', textTransform: 'uppercase', whiteSpace: 'nowrap'}}>{p.label || p.lines.join(' ')}</div>
        </div>
        {status ? (
          <div style={{position: 'absolute', right: 60, bottom: 92}}>
            <StaticStamp status={status} size={58} rotate={7} />
          </div>
        ) : null}
        {p.photo ? null : (
          <div style={{position: 'absolute', right: 70, top: 120}}>
            <Motif kind={p.motif} color={color} size={300} calendar={p.calendar} />
          </div>
        )}
      </Frame>
    );
  }
  if (p.concept === 'split') {
    return (
      <Frame color={color} brand={p.brand}>
        <AbsoluteFill style={{background: `linear-gradient(135deg, ${C.ink2} 0%, ${C.ink} 100%)`}} />
        {p.photo ? (
          <div style={{position: 'absolute', left: 560, top: 0, right: 0, bottom: 0}}>
            <Photo src={p.photo} color={color} side="none" />
          </div>
        ) : null}
        <div style={{position: 'absolute', left: -120, top: -80, width: 900, height: 900, background: `linear-gradient(160deg, ${color} 0%, ${hexA(color, 0.82)} 100%)`, transform: 'skewX(-14deg)'}} />
        <div style={{position: 'absolute', left: 700, top: -80, width: 18, height: 900, background: C.sun, transform: 'skewX(-14deg)'}} />
        <div style={{position: 'absolute', left: 54, top: 44}}>
          <Chip color={C.ink}>
            <span style={{color: C.paper}}>GTA 6</span>
          </Chip>
        </div>
        <div style={{position: 'absolute', left: 54, top: 150, width: 640}}>
          <div style={{fontFamily: F.display, fontWeight: 900, fontStyle: 'italic', fontSize: Math.min(176, (640 / Math.max(...p.lines.map((l) => l.length), 4)) * 2.1, 420 / p.lines.length), lineHeight: 0.93, color: C.ink, textTransform: 'uppercase', transform: 'rotate(-2.5deg)', transformOrigin: 'left center'}}>
            {p.lines.map((l, i) => (
              <div key={i} style={{whiteSpace: 'nowrap'}}>
                {l.split(/\s+/).map((w, k) => (
                  <span key={k} style={{display: 'inline-block', marginRight: '0.22em', color: isHot(w, p.accent) ? C.paper : C.ink, textShadow: isHot(w, p.accent) ? outline(C.ink, 7) : 'none'}}>
                    {w}
                  </span>
                ))}
              </div>
            ))}
          </div>
        </div>
        {p.photo ? null : (
          <div style={{position: 'absolute', right: 70, top: 150}}>
            <Motif kind={p.motif} color={color} size={400} calendar={p.calendar} />
          </div>
        )}
        {status ? (
          <div style={{position: 'absolute', right: 90, bottom: 62}}>
            <StaticStamp status={status} size={62} rotate={-6} />
          </div>
        ) : null}
      </Frame>
    );
  }
  // stamp concept
  return (
    <Frame color={color} brand={p.brand}>
      {p.photo ? (
        <Photo src={p.photo} color={color} side="left" />
      ) : (
        <>
          <AbsoluteFill style={{background: `linear-gradient(180deg, ${C.ink} 0%, ${hexA(C.violet, 0.3)} 55%, ${hexA(color, 0.42)} 100%)`}} />
          <div style={{position: 'absolute', right: -40, top: 90}}>
            <Motif kind={p.motif} color={color} size={560} calendar={p.calendar} />
          </div>
          <AbsoluteFill style={{background: `linear-gradient(90deg, ${hexA(C.ink, 0.96)} 0%, ${hexA(C.ink, 0.85)} 42%, transparent 72%)`}} />
        </>
      )}
      <div style={{position: 'absolute', left: 56, top: 40}}>
        <Chip color={color}>GTA 6</Chip>
      </div>
      <div style={{position: 'absolute', left: 56, top: 140, width: 860}}>
        <Headline lines={p.lines} accent={p.accent} color={color} maxWidth={820} />
      </div>
      {status ? (
        <div style={{position: 'absolute', left: 76, bottom: 58}}>
          <StaticStamp status={status} size={70} />
        </div>
      ) : null}
    </Frame>
  );
};
