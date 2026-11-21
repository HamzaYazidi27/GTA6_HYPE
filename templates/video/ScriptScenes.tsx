/**
 * Script to Short: one narration take, cut to the real gameplay matched to each phrase.
 *
 *   shot - the take's footage clip (already cut at the exact frames of every beat), full screen.
 *          The style profile drives the motion: a slow camera move that restarts at every cut
 *          (push-in and pull-out alternate, so two shots in a row never move the same way), a
 *          punch-in with a short camera shake on the list number, a colour grade, a number badge
 *          for the current item, and a fast zoom-in with motion blur (or a white flash) when a new
 *          item comes in. Footage is chosen for every beat upstream; only when the library is
 *          empty does a beat show its words on the channel background.
 *          data: {beats: [{from, to, number, item, numberAt, missing, label, role}], cuts: [frame],
 *                 style: {push, punch, punchFrames, saturation, contrast, brightness, badge, dim, flash,
 *                         zoomIn, alternate, shake}}
 */
import React from 'react';
import {AbsoluteFill, OffthreadVideo, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {Background} from '../motion_graphics/backgrounds';
import {clamp} from '../motion_graphics/primitives';
import {C, F, hexA, useLayout} from '../motion_graphics/theme';
import {SceneProps} from './shared';

type Beat = {from: number; to: number; number: number | null; item: number | null; numberAt: number | null; missing: boolean; label: string; role: string};
type Style = {push: number; punch: number; punchFrames: number; saturation: number; contrast: number; brightness: number; badge: boolean; dim: number; flash: boolean; zoomIn?: boolean; alternate?: boolean; shake?: number};

const ZOOM_IN_FRAMES = 6; // the item entrance: from 1.22x and blurred to sharp in about 0.1-0.2 s

const url = (src: string) => (src.startsWith('http') ? src : staticFile(src));

/**
 * The picture's scale at this frame: the camera move since the last cut (a push-in, or with
 * ``alternate`` every other shot a pull-out that ends where a push-in would start), times the punch
 * on the list number and the zoom-in entrance of a new item.
 */
export const shotScale = (frame: number, fps: number, cuts: number[], beat: Beat | undefined, st: Style, end?: number): number => {
  const sorted = [...cuts].sort((a, b) => a - b);
  const idx = Math.max(0, sorted.filter((c) => c <= frame).length - 1);
  const lastCut = sorted.length ? sorted[idx] : 0;
  const nextCut = idx + 1 < sorted.length ? sorted[idx + 1] : end ?? frame + fps;
  const span = Math.max(1, nextCut - lastCut) / fps;
  const elapsed = (frame - lastCut) / fps;
  const pullOut = Boolean(st.alternate) && idx % 2 === 1;
  let scale = 1 + st.push * (pullOut ? Math.max(0, span - elapsed) : elapsed);
  if (beat && beat.numberAt !== null && st.punch > 1) {
    const k = frame - beat.numberAt;
    if (k >= 0) scale *= interpolate(k, [0, st.punchFrames, st.punchFrames + 6], [st.punch, st.punch, 1], clamp);
  }
  if (beat && st.zoomIn && beat.number !== null) scale *= interpolate(frame - beat.from, [0, ZOOM_IN_FRAMES], [1.22, 1], {...clamp, easing: (t) => 1 - Math.pow(1 - t, 3)});
  return scale;
};

/** A short, decaying camera shake when the list number lands: [x, y] in pixels. */
export const shotShake = (frame: number, beat: Beat | undefined, st: Style, u: number): [number, number] => {
  if (!beat || beat.numberAt === null || !st.shake) return [0, 0];
  const k = frame - beat.numberAt;
  const len = st.punchFrames + 8;
  if (k < 0 || k > len) return [0, 0];
  const a = st.shake * u * (1 - k / len);
  return [a * Math.sin(k * 2.7), a * Math.cos(k * 3.9)];
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
  const {u} = useLayout();
  const beat = beats.find((b) => frame >= b.from && frame < b.to) ?? beats[beats.length - 1];
  const end = beats.length ? beats[beats.length - 1].to : undefined;
  const scale = shotScale(frame, fps, d.cuts ?? [0], beat, st, end);
  const [sx, sy] = shotShake(frame, beat, st, u);
  const {width} = useVideoConfig();
  const covered = Math.max(scale, 1 + (2 * (Math.abs(sx) + Math.abs(sy))) / width); // a shake never shows the frame's edge
  const blur = st.zoomIn && beat && beat.number !== null ? interpolate(frame - beat.from, [0, ZOOM_IN_FRAMES], [14 * u, 0], clamp) : 0;
  const filter = `saturate(${st.saturation}) contrast(${st.contrast}) brightness(${1 + st.brightness})${blur > 0.05 ? ` blur(${blur.toFixed(2)}px)` : ''}`;
  const itemStart = beat && beat.item !== null ? beats.filter((b) => b.item === beat.item && b.numberAt !== null).map((b) => b.numberAt as number)[0] : undefined;
  const flash = st.flash && beat && beat.number !== null ? interpolate(frame - beat.from, [0, 1, 5], [0.0, 0.8, 0], clamp) : 0;
  return (
    <AbsoluteFill style={{background: C.ink, overflow: 'hidden'}}>
      {scene.footage?.src ? (
        <AbsoluteFill style={{transform: `translate(${sx.toFixed(2)}px, ${sy.toFixed(2)}px) scale(${covered})`, filter}}>
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
