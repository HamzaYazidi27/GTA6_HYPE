/**
 * Script to Short: one narration take, cut to the real gameplay matched to each phrase.
 *
 *   shot - the take's footage clip (already cut at the exact frames of every beat), full screen.
 *          The style profile drives the motion: a slow push-in that restarts at every cut, a
 *          punch-in on the list number, a colour grade, a number badge for the current item, and
 *          (optionally) a white flash when a new item comes in. A beat without verified footage
 *          shows its words on the channel background: nothing unrelated is put in its place.
 *          data: {beats: [{from, to, number, item, numberAt, missing, label, role}], cuts: [frame],
 *                 style: {push, punch, punchFrames, saturation, contrast, brightness, badge, dim, flash}}
 */
import React from 'react';
import {AbsoluteFill, OffthreadVideo, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {Background} from '../motion_graphics/backgrounds';
import {clamp} from '../motion_graphics/primitives';
import {C, F, hexA, useLayout} from '../motion_graphics/theme';
import {SceneProps} from './shared';

type Beat = {from: number; to: number; number: number | null; item: number | null; numberAt: number | null; missing: boolean; label: string; role: string};
type Style = {push: number; punch: number; punchFrames: number; saturation: number; contrast: number; brightness: number; badge: boolean; dim: number; flash: boolean};

const url = (src: string) => (src.startsWith('http') ? src : staticFile(src));

/** The picture's scale at this frame: push-in since the last cut, times the punch on the list number. */
export const shotScale = (frame: number, fps: number, cuts: number[], beat: Beat | undefined, st: Style): number => {
  const lastCut = cuts.filter((c) => c <= frame).reduce((a, b) => Math.max(a, b), 0);
  let scale = 1 + st.push * ((frame - lastCut) / fps);
  if (beat && beat.numberAt !== null && st.punch > 1) {
    const k = frame - beat.numberAt;
    if (k >= 0) scale *= interpolate(k, [0, st.punchFrames, st.punchFrames + 6], [st.punch, st.punch, 1], clamp);
  }
  return scale;
};

const MissingCard: React.FC<{beat: Beat; accent: string; seed: number}> = ({beat, accent, seed}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const {u} = useLayout();
  const p = spring({frame: frame - beat.from, fps, config: {damping: 15, stiffness: 200}});
  const words = beat.label.replace(/^(?:number\s+)?\S+:\s*/i, '').toUpperCase();
  return (
    <AbsoluteFill>
      <Background kind="grid" accent={accent} seed={seed} />
      <AbsoluteFill style={{background: `radial-gradient(70% 55% at 50% 45%, ${hexA(C.ink, 0.35)} 0%, ${hexA(C.ink, 0.75)} 100%)`}} />
      <AbsoluteFill style={{justifyContent: 'center', alignItems: 'center', padding: `0 ${80 * u}px`}}>
        <div style={{fontFamily: F.display, fontWeight: 900, fontSize: 96 * u, lineHeight: 1.02, color: C.paper, textAlign: 'center', transform: `scale(${0.9 + 0.1 * p})`, opacity: Math.min(1, p * 1.5), textShadow: `0 ${6 * u}px 0 ${C.ink}`}}>{words}</div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

const NumberBadge: React.FC<{n: number; since: number; accent: string}> = ({n, since, accent}) => {
  const {fps} = useVideoConfig();
  const {u, H} = useLayout();
  const p = spring({frame: since, fps, config: {damping: 11, stiffness: 260, mass: 0.6}});
  return (
    <div style={{position: 'absolute', left: 0, right: 0, top: H * 0.13, display: 'flex', justifyContent: 'center', pointerEvents: 'none'}}>
      <div style={{fontFamily: F.display, fontWeight: 900, fontStyle: 'italic', fontSize: 210 * u, lineHeight: 0.9, color: accent, transform: `scale(${0.6 + 0.4 * p}) rotate(${-4 * (1 - p)}deg)`, opacity: Math.min(1, p * 2), textShadow: `0 ${8 * u}px 0 ${C.ink}, 0 0 ${50 * u}px ${hexA(C.ink, 0.8)}`}}>#{n}</div>
    </div>
  );
};

export const ShotScene: React.FC<SceneProps> = ({scene, accent}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const d = scene.data ?? {};
  const beats: Beat[] = d.beats ?? [];
  const st: Style = d.style ?? {push: 0.02, punch: 1, punchFrames: 4, saturation: 1, contrast: 1, brightness: 0, badge: true, dim: 0.06, flash: false};
  const beat = beats.find((b) => frame >= b.from && frame < b.to) ?? beats[beats.length - 1];
  const scale = shotScale(frame, fps, d.cuts ?? [0], beat, st);
  const filter = `saturate(${st.saturation}) contrast(${st.contrast}) brightness(${1 + st.brightness})`;
  const itemStart = beat && beat.item !== null ? beats.filter((b) => b.item === beat.item && b.numberAt !== null).map((b) => b.numberAt as number)[0] : undefined;
  const flash = st.flash && beat && beat.number !== null ? interpolate(frame - beat.from, [0, 1, 5], [0.0, 0.8, 0], clamp) : 0;
  return (
    <AbsoluteFill style={{background: C.ink, overflow: 'hidden'}}>
      {scene.footage?.src ? (
        <AbsoluteFill style={{transform: `scale(${scale})`, filter}}>
          <OffthreadVideo src={url(scene.footage.src)} muted style={{width: '100%', height: '100%', objectFit: 'cover'}} />
        </AbsoluteFill>
      ) : null}
      {/* readability: a light dim, and the bottom third darker where the captions sit */}
      <AbsoluteFill style={{background: hexA(C.ink, st.dim)}} />
      <AbsoluteFill style={{background: `linear-gradient(180deg, ${hexA(C.ink, 0.35)} 0%, transparent 18%, transparent 58%, ${hexA(C.ink, 0.55)} 100%)`}} />
      {beat && beat.missing ? <MissingCard beat={beat} accent={accent} seed={scene.seed} /> : null}
      {st.badge && beat && beat.item !== null && itemStart !== undefined && frame >= itemStart ? <NumberBadge n={beat.item} since={frame - itemStart} accent={accent} /> : null}
      {flash > 0 ? <AbsoluteFill style={{background: C.paper, opacity: flash}} /> : null}
    </AbsoluteFill>
  );
};
