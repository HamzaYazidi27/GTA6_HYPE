/**
 * Comparisons ("GTA 6 vs Red Dead 2") and top lists ("12 things to do on day 1").
 *
 *   compare - split screen: the other game's footage on the left (top in 9:16), GTA 6 on the right
 *             (bottom), each with its label and a "then / now" caption, the item number and title above.
 *             data: {n, total, title, left: {game, caption}, right: {game, caption, status}, panels: {left, right}}
 *             (panels are pixel rectangles [x, y, w, h]; the clips are cut at exactly those sizes)
 *   footage - full-screen footage of the game being talked about, with a small tag: game + current item.
 *             data: {game, side: 'other' | 'gta6', tag}
 *   item    - a numbered entry of a top list over GTA 6 footage: "#4 / 12  STEAL A CLASSIC CAR".
 *             data: {n, total, title, kicker}
 */
import React from 'react';
import {AbsoluteFill, Img, OffthreadVideo, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {Panel, Reveal, clamp, easeOut} from '../motion_graphics/primitives';
import {C, F, STATUS, Status, hexA, useLayout} from '../motion_graphics/theme';
import {SceneProps, Stage} from './shared';

const OTHER = C.cyan;
const MAIN = C.coral;

const url = (src: string) => (src.startsWith('http') ? src : staticFile(src));

/** One side of the split screen: the footage clip (cut at the panel's size), or the game's name when the library had none. */
const SidePanel: React.FC<{rect: number[]; src?: string; kind?: string; color: string; game: string; caption: string; status?: Status | null; from: 'left' | 'right'; delay: number; vertical: boolean}> = ({rect, src, kind, color, game, caption, status, from, delay, vertical}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const {u} = useLayout();
  const [x, y, w, h] = rect;
  const p = spring({frame: frame - delay, fps, config: {damping: 16, stiffness: 150}});
  const dx = (1 - p) * (from === 'left' ? -1 : 1) * (vertical ? 0 : 120 * u);
  const dy = (1 - p) * (vertical ? (from === 'left' ? -1 : 1) * 90 * u : 0);
  const capIn = interpolate(frame, [delay + 12, delay + 24], [0, 1], {...clamp, easing: easeOut});
  const st = status && status in STATUS ? STATUS[status] : null;
  // in 9:16 the VS badge sits on the right edge between the two panels: keep the top caption clear of it
  const capBox: React.CSSProperties = vertical
    ? {position: 'absolute', left: 18 * u, right: (from === 'left' ? 140 : 18) * u, bottom: 16 * u}
    : {position: 'absolute', left: 0, right: 0, top: h + 18 * u};
  return (
    <div style={{position: 'absolute', left: x, top: y, width: w, height: h, opacity: Math.min(1, p * 1.4), transform: `translate(${dx}px, ${dy}px)`}}>
      <div style={{position: 'absolute', inset: 0, borderRadius: 22 * u, overflow: 'hidden', background: C.ink2, border: `${3 * u}px solid ${hexA(color, 0.75)}`, boxShadow: `0 ${24 * u}px ${70 * u}px rgba(0,0,0,0.55), 0 0 ${50 * u}px ${hexA(color, 0.22)}`}}>
        {src ? (
          kind === 'photo' ? <Img src={url(src)} style={{width: '100%', height: '100%', objectFit: 'cover'}} /> : <OffthreadVideo src={url(src)} muted style={{width: '100%', height: '100%', objectFit: 'cover'}} />
        ) : (
          <AbsoluteFill style={{display: 'flex', alignItems: 'center', justifyContent: 'center', background: `radial-gradient(80% 80% at 50% 40%, ${hexA(color, 0.22)}, ${C.ink2})`}}>
            <div style={{fontFamily: F.display, fontWeight: 900, fontStyle: 'italic', fontSize: 110 * u, color: hexA(C.paper, 0.85), textTransform: 'uppercase', textAlign: 'center', lineHeight: 0.95, padding: `0 ${30 * u}px`}}>{game}</div>
          </AbsoluteFill>
        )}
        {vertical ? <AbsoluteFill style={{background: `linear-gradient(180deg, transparent 55%, ${hexA(C.ink, 0.82)} 100%)`}} /> : null}
      </div>
      <div style={{position: 'absolute', left: 18 * u, top: 16 * u, padding: `${6 * u}px ${16 * u}px ${4 * u}px`, borderRadius: 10 * u, background: color, color: C.ink, fontFamily: F.display, fontWeight: 900, fontSize: (vertical ? 40 : 36) * u, letterSpacing: '0.04em', lineHeight: 1.05, boxShadow: `0 ${6 * u}px ${18 * u}px rgba(0,0,0,0.45)`}}>{game}</div>
      {caption || st ? (
        <div style={{...capBox, display: 'flex', alignItems: 'center', gap: 14 * u, justifyContent: vertical ? 'flex-start' : 'center', opacity: capIn, transform: `translateY(${(1 - capIn) * 16 * u}px)`}}>
          {caption ? <div style={{fontFamily: F.display, fontWeight: 800, fontSize: (vertical ? 46 : 40) * u, lineHeight: 1.05, color: C.paper, textTransform: 'uppercase', textShadow: '0 2px 10px rgba(0,0,0,0.85)', textAlign: vertical ? 'left' : 'center'}}>{caption}</div> : null}
          {st ? (
            <div style={{flex: 'none', padding: `${5 * u}px ${12 * u}px`, borderRadius: 999, border: `${2 * u}px solid ${st.color}`, background: hexA(C.ink, 0.8), color: st.color, fontFamily: F.mono, fontWeight: 700, fontSize: 18 * u, letterSpacing: '0.14em'}}>{st.label}</div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
};

/** Same geometry as `compare_panels` in gta_factory/modules/topic/scenes.py (used when the data has none, e.g. in the Studio). */
const defaultPanels = (W: number, H: number, u: number): {left: number[]; right: number[]} => {
  if (H > W) {
    const [x, w, h, y] = [48 * u, W - 96 * u, 450 * u, 340 * u];
    return {left: [x, y, w, h], right: [x, y + h + 20 * u, w, h]};
  }
  const [w, h, y, x] = [(W - 180 * u) / 2, 560 * u, 232 * u, 70 * u];
  return {left: [x, y, w, h], right: [W - x - w, y, w, h]};
};

export const CompareScene: React.FC<SceneProps> = ({scene, accent}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const {u, W, H, vertical} = useLayout();
  const d = scene.data ?? {};
  const panels = d.panels ?? defaultPanels(W, H, u);
  const L: number[] = panels.left;
  const R: number[] = panels.right;
  const titleY = Math.min(L[1], R[1]) - (vertical ? 118 : 112) * u;
  const vs = spring({frame: frame - 14, fps, config: {damping: 10, stiffness: 220}});
  const vsX = vertical ? L[0] + L[2] - 74 * u : (L[0] + L[2] + R[0]) / 2;
  const vsY = vertical ? (L[1] + L[3] + R[1]) / 2 : L[1] + L[3] / 2;
  const fb = scene.footageB;
  return (
    <AbsoluteFill>
      <div style={{position: 'absolute', left: 0, right: 0, top: titleY, display: 'flex', alignItems: 'baseline', justifyContent: 'center', gap: 18 * u}}>
        {d.n ? (
          <Reveal delay={2} from="scale">
            <div style={{display: 'flex', alignItems: 'baseline', gap: 10 * u}}>
              <span style={{fontFamily: F.display, fontWeight: 900, fontStyle: 'italic', fontSize: (vertical ? 92 : 88) * u, lineHeight: 0.9, color: accent, textShadow: `0 0 ${30 * u}px ${hexA(accent, 0.6)}`}}>#{String(d.n).padStart(2, '0')}</span>
              {d.total ? <span style={{fontFamily: F.display, fontWeight: 700, fontSize: (vertical ? 40 : 38) * u, color: C.mute}}>/ {d.total}</span> : null}
            </div>
          </Reveal>
        ) : null}
        <Reveal delay={6} from="up" distance={30}>
          <div style={{fontFamily: F.display, fontWeight: 800, fontSize: (vertical ? 70 : 76) * u, lineHeight: 1, color: C.paper, textTransform: 'uppercase', textShadow: '0 4px 18px rgba(0,0,0,0.7)', whiteSpace: 'nowrap'}}>{d.title}</div>
        </Reveal>
      </div>
      <SidePanel rect={L} src={fb?.src} kind={fb?.kind} color={OTHER} game={d.left?.game ?? ''} caption={d.left?.caption ?? ''} from="left" delay={4} vertical={vertical} />
      <SidePanel rect={R} src={scene.footage?.src} kind={scene.footage?.kind} color={MAIN} game={d.right?.game ?? 'GTA 6'} caption={d.right?.caption ?? ''} status={d.right?.status ?? null} from="right" delay={9} vertical={vertical} />
      <div style={{position: 'absolute', left: vsX - 58 * u, top: vsY - 58 * u, width: 116 * u, height: 116 * u, borderRadius: '50%', background: C.ink, border: `${4 * u}px solid ${accent}`, display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: F.display, fontWeight: 900, fontStyle: 'italic', fontSize: 54 * u, color: accent, transform: `scale(${vs}) rotate(${(1 - vs) * 180}deg)`, boxShadow: `0 0 ${40 * u}px ${hexA(accent, 0.6)}`}}>VS</div>
    </AbsoluteFill>
  );
};

/** Which game is on screen, and which item the video is on. */
const GameTag: React.FC<{game: string; color: string; tag: string}> = ({game, color, tag}) => {
  const frame = useCurrentFrame();
  const {u, pad, vertical} = useLayout();
  const p = interpolate(frame, [4, 18], [0, 1], {...clamp, easing: easeOut});
  return (
    <div style={{position: 'absolute', left: pad * (vertical ? 1 : 0.55), top: (vertical ? 214 : 112) * u, display: 'flex', alignItems: 'center', gap: 14 * u, opacity: p, transform: `translateX(${(1 - p) * -30 * u}px)`}}>
      <div style={{padding: `${5 * u}px ${14 * u}px ${3 * u}px`, borderRadius: 9 * u, background: color, color: C.ink, fontFamily: F.display, fontWeight: 900, fontSize: (vertical ? 34 : 30) * u, letterSpacing: '0.04em', lineHeight: 1.05}}>{game}</div>
      {tag ? <div style={{fontFamily: F.mono, fontWeight: 700, fontSize: (vertical ? 24 : 21) * u, letterSpacing: '0.14em', color: C.paper, textShadow: '0 1px 8px rgba(0,0,0,0.9)'}}>{tag}</div> : null}
    </div>
  );
};

export const FootageScene: React.FC<SceneProps> = ({scene}) => {
  const {u, vertical} = useLayout();
  const d = scene.data ?? {};
  const color = d.side === 'other' ? OTHER : MAIN;
  return (
    <AbsoluteFill>
      {scene.footage ? null : (
        <Stage>
          <Reveal delay={4} from="scale">
            <div style={{fontFamily: F.display, fontWeight: 900, fontStyle: 'italic', fontSize: (vertical ? 150 : 170) * u, lineHeight: 0.92, color: hexA(C.paper, 0.9), textAlign: 'center', textTransform: 'uppercase', textShadow: `0 0 ${50 * u}px ${hexA(color, 0.5)}`}}>{d.game}</div>
          </Reveal>
        </Stage>
      )}
      <GameTag game={d.game ?? ''} color={color} tag={d.tag ?? ''} />
    </AbsoluteFill>
  );
};

export const ItemScene: React.FC<SceneProps> = ({scene, accent}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const {u, vertical} = useLayout();
  const d = scene.data ?? {};
  const slam = spring({frame: frame - 3, fps, config: {damping: 9, stiffness: 200}});
  return (
    <Stage align="center">
      <div style={{width: '100%', display: 'flex', flexDirection: 'column', alignItems: vertical ? 'center' : 'flex-start'}}>
        <div style={{display: 'flex', alignItems: 'baseline', gap: 16 * u, transform: `scale(${0.6 + 0.4 * slam})`, transformOrigin: vertical ? 'center' : 'left center', opacity: Math.min(1, slam * 2)}}>
          <span style={{fontFamily: F.display, fontWeight: 900, fontStyle: 'italic', fontSize: (vertical ? 260 : 240) * u, lineHeight: 0.85, color: accent, textShadow: `0 0 ${60 * u}px ${hexA(accent, 0.55)}, 0 ${8 * u}px 0 ${C.ink}`}}>#{d.n}</span>
          {d.total ? <span style={{fontFamily: F.display, fontWeight: 800, fontSize: (vertical ? 70 : 64) * u, color: C.paper, opacity: 0.75}}>/ {d.total}</span> : null}
        </div>
        <Panel accent={accent} delay={8} style={{marginTop: 22 * u, padding: `${22 * u}px ${40 * u}px ${24 * u}px ${48 * u}px`, maxWidth: (vertical ? 960 : 1500) * u}}>
          <div style={{fontFamily: F.display, fontWeight: 800, fontSize: (vertical ? 84 : 92) * u, lineHeight: 1, color: C.paper, textTransform: 'uppercase', textAlign: vertical ? 'center' : 'left'}}>{d.title}</div>
          {d.kicker ? <div style={{marginTop: 10 * u, fontFamily: F.mono, fontWeight: 700, fontSize: (vertical ? 30 : 28) * u, letterSpacing: '0.1em', color: C.sun, textTransform: 'uppercase', textAlign: vertical ? 'center' : 'left'}}>{d.kicker}</div> : null}
        </Panel>
      </div>
    </Stage>
  );
};
