import React from 'react';
import {AbsoluteFill, Freeze, Img, OffthreadVideo, interpolate, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {C, F, useLayout} from '../motion_graphics/theme';
import type {Phrase} from '../motion_graphics/overlays';
import type {SceneProps} from './shared';

type Panel = {beat: number; image: string; credit: string; label?: string};
type Beat = {
  from: number;
  to: number;
  role?: string;
  label?: string;
  detail?: boolean;
  verifiedDetail?: boolean;
  panels?: Panel[];
};

const clamped = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;
const normalize = (word: string) => word.toLowerCase().replace(/[^a-z0-9']/g, '');
const connectors = new Set(['a', 'an', 'the', 'as', 'at', 'for', 'in', 'of', 'on', 'to', 'and', 'or', 'but', 'than']);

const FocusCaption: React.FC<{
  words: Phrase['words'];
  frame: number;
  start: number;
  fps: number;
  opening: boolean;
  recap: boolean;
  emphasis: string;
}> = ({words, frame, start, fps, opening, recap, emphasis}) => {
  const {u} = useLayout();
  if (!words.length) return null;
  const size = Math.min(opening ? 142 : recap ? 82 : 112, 1400 / Math.max(...words.map(word => word.w.length), 1)) * u;
  const keyWords = emphasis.split(/\s+/).map(normalize).filter(w => w.length > 1);
  const entrance = interpolate(frame - start, [0, fps * 0.14], [0, 1], clamped);
  const active = words.reduce((last, word, index) =>
    word.s <= frame && !connectors.has(normalize(word.w)) ? index : last, -1);
  const tail = normalize(words[words.length - 1].w);
  const isChoice = words.some(word => normalize(word.w) === 'or') && words[words.length - 1].w.endsWith('?');
  return (
    <div style={{
      position: 'absolute', left: '6%', right: '11%',
      ...(recap ? {top: '9.2%'} : {bottom: opening ? '21%' : '22%'}),
      transform: `translateY(${(1 - entrance) * (opening ? 14 : 8) * u}px)`,
      fontFamily: F.display, fontWeight: opening ? 900 : 800, fontSize: size, lineHeight: 0.99,
      letterSpacing: opening ? '-0.028em' : '-0.012em', textTransform: 'uppercase',
      textWrap: 'balance', overflowWrap: 'anywhere',
      textShadow: `0 ${3*u}px ${8*u}px #06070ed9, 0 0 ${26*u}px #06070e78`,
    }}>
      {opening && <div style={{width: (48 + 48 * entrance) * u, height: 7 * u, background: C.coral, marginBottom: 20 * u}} />}
      {words.map((word, i) => {
        const token = normalize(word.w);
        const wordColor = i === active || (isChoice && token === tail) || keyWords.includes(token) ? '#ff849a' : '#fffaf1';
        return <React.Fragment key={i}><span style={{color: wordColor}}>{word.w}</span>{' '}</React.Fragment>;
      })}
    </div>
  );
};

export const FocusShot: React.FC<SceneProps & {captions: boolean}> = ({scene, brand, captions}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const {u} = useLayout();
  const beats: Beat[] = scene.data?.beats ?? [];
  const beat = beats.find(b => frame >= b.from && frame < b.to);
  const opening = scene.startFrame === 0 && (beat?.from ?? 0) === 0;
  const panels = beat?.panels ?? [];
  const since = frame - (beat?.from ?? 0);
  const length = Math.max(1, (beat?.to ?? scene.durationFrames) - (beat?.from ?? 0));
  const detail = beat?.detail && !panels.length;
  const holdAt = Math.min(Math.round(fps * 0.18), Math.floor(length / 4));
  const holding = detail && since >= holdAt && since < Math.min(length, holdAt + Math.round(fps * 0.36));
  const progress = Math.min(1, since / Math.max(1, fps * 0.5));
  const drift = Math.min(0.06, since / fps * 0.014);
  const lastCut = (scene.data?.cuts as number[] | undefined)?.filter(cut => cut <= frame).at(-1) ?? 0;
  // Settle once into the opening image or a numbered item. Detail shots keep their own hold and push.
  const settle = opening || beat?.role === 'item'
    ? interpolate(frame - lastCut, [0, fps * 0.28], [0.045, 0], clamped) : 0;
  const zoom = detail ? 1 + 0.13 * progress * progress * (3 - 2 * progress) : 1 + drift + settle;
  const phrases = scene.phrases ?? [];
  const phrase = phrases.find(p => frame >= p.s && frame < p.e);
  const openingWords = opening ? phrases.flatMap(p => p.words).filter(w => w.s < (beat?.to ?? scene.durationFrames)) : [];
  const fullOpening = openingWords.length > 0 && openingWords.length <= 9 && openingWords.map(w => w.w).join(' ').length <= 56;
  const words = fullOpening ? openingWords : (phrase ?? (opening && frame < phrases[0]?.s ? phrases[0] : undefined))?.words ?? [];
  const footage = scene.footage && <OffthreadVideo src={staticFile(scene.footage.src)} muted style={{width: '100%', height: '100%', objectFit: 'cover'}} />;

  return <AbsoluteFill style={{background: C.ink, color: '#fff8ee', overflow: 'hidden'}}>
    <AbsoluteFill style={{transform: `scale(${zoom})`}}>
      {holding ? <Freeze frame={(beat?.from ?? 0) + holdAt}>{footage}</Freeze> : footage}
    </AbsoluteFill>
    <AbsoluteFill style={{background: 'linear-gradient(0deg,rgba(6,7,14,.8),rgba(6,7,14,.62) 22%,rgba(6,7,14,.2) 43%,transparent 63%,rgba(6,7,14,.22))'}} />
    {panels.length > 0 && <>
      <div style={{position: 'absolute', inset: '0 0 78%', background: '#101018'}} />
      {panels.map((panel, i) => <div key={panel.beat} style={{
        position: 'absolute', top: i === 0 ? '22%' : '66%', width: '100%', height: i === 0 ? '17%' : '34%',
        overflow: 'hidden', borderBlock: `${4*u}px solid #101018`,
      }}>
        <Img src={panel.image} style={{width: '100%', height: '100%', objectFit: 'cover', transform: `scale(${1 + 0.025 * since / length})`}} />
        <div style={{position: 'absolute', inset: 0, background: 'linear-gradient(0deg,rgba(9,10,18,.84),transparent 65%)'}} />
        <div style={{position: 'absolute', left: '6%', right: '12%', bottom: (i === 0 ? 18 : 192)*u, fontFamily: F.body, fontSize: 21*u, lineHeight: 1.15}}>
          {panel.label && <b style={{display:'block', fontFamily: F.display, fontSize: 41*u, fontWeight: 800, textTransform:'uppercase'}}>{panel.label}</b>}
          {panel.credit}
        </div>
      </div>)}
    </>}
    {detail && holding && beat?.verifiedDetail && <div style={{position: 'absolute', left: '42%', top: '44%', width:'16%', height:'10%', border: `${3*u}px solid rgba(255,255,255,.8)`, borderRadius:'50%'}} />}
    {!panels.length && brand?.logo && <div style={{position:'absolute', left: 194*u, top: 123*u, font: `700 ${23*u}px ${F.body}`, letterSpacing: '0.055em', textShadow:'0 1px 8px #06070e'}}>{brand.name.toUpperCase()}</div>}
    {captions && <FocusCaption words={words} frame={frame} start={fullOpening ? 0 : phrase?.s ?? 0} fps={fps} opening={opening} recap={panels.length > 0} emphasis={scene.emphasisText ?? ''} />}
    <div style={{position:'absolute', left:'6%', right:'12%', bottom:(panels.length ? 112 : 168)*u, font:`700 ${21*u}px ${F.body}`, color:'#fff', textShadow:'0 1px 6px #000'}}>{scene.footage?.credit}</div>
  </AbsoluteFill>;
};
