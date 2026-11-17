/**
 * "Details you missed": the footage plays up to the detail's frame, freezes (white flash), the camera
 * zooms onto the detail, a neon ring draws itself around it and a card names it ("#3 / 5 GAS STATION SIGN"),
 * with the timestamp in the original footage so viewers can check it.
 *
 * data: {n, total, label, text, source, box: [x1, y1, x2, y2] (fractions of the output frame), zoom, freeze (s)}
 * The footage clip comes pre-cut (run-in + frozen frame, cropped around the detail) in `scene.footage`.
 */
import React from 'react';
import {AbsoluteFill, Img, OffthreadVideo, interpolate, staticFile, useCurrentFrame} from 'remotion';
import {Panel, clamp, easeInOut, easeOut} from '../motion_graphics/primitives';
import {C, F, hexA, useLayout} from '../motion_graphics/theme';
import {SceneProps} from './shared';

export const DetailScene: React.FC<SceneProps> = ({scene, accent}) => {
  const frame = useCurrentFrame();
  const {W, H, u, vertical, fps, safeTop, pad} = useLayout();
  const d = scene.data ?? {};
  const box: number[] = Array.isArray(d.box) && d.box.length === 4 ? d.box : [0.42, 0.42, 0.58, 0.58];
  const Z: number = typeof d.zoom === 'number' ? d.zoom : 2;
  const freeze = Math.round((d.freeze ?? 0) * fps);
  const zoomStart = freeze + 5;
  const zoomLen = 16;
  const p = interpolate(frame, [zoomStart, zoomStart + zoomLen], [0, 1], {...clamp, easing: easeInOut});

  // camera: the detail's centre travels to the focus point (under the card, above the captions) while scaling up
  const bx = ((box[0] + box[2]) / 2) * W;
  const by = ((box[1] + box[3]) / 2) * H;
  const tx = W / 2;
  const ty = H * (vertical ? 0.49 : 0.61);
  const s = 1 + (Z - 1) * p;
  const fx = W / 2 + (bx - W / 2) * p;
  const fy = H / 2 + (by - H / 2) * p;
  const gx = W / 2 + (tx - W / 2) * p;
  const gy = H / 2 + (ty - H / 2) * p;
  const dx = Math.min(0, Math.max(W - W * s, gx - fx * s)); // never show past the picture's edges
  const dy = Math.min(0, Math.max(H - H * s, gy - fy * s));

  // the ring follows the detail on screen
  const x1 = box[0] * W * s + dx;
  const y1 = box[1] * H * s + dy;
  const x2 = box[2] * W * s + dx;
  const y2 = box[3] * H * s + dy;
  const cx = (x1 + x2) / 2;
  const cy = (y1 + y2) / 2;
  const rw = Math.max(70 * u, ((x2 - x1) / 2) * 1.3 + 22 * u);
  const rh = Math.max(56 * u, ((y2 - y1) / 2) * 1.3 + 22 * u);
  const ringP = interpolate(frame, [zoomStart + zoomLen - 6, zoomStart + zoomLen + 12], [0, 1], {...clamp, easing: easeOut});
  const pulse = 1 + 0.025 * Math.sin(Math.max(0, frame - zoomStart - zoomLen) / 4.5);
  const perimeter = Math.PI * (3 * (rw + rh) - Math.sqrt((3 * rw + rh) * (rw + 3 * rh)));
  const flash = interpolate(frame, [freeze, freeze + 2, freeze + 9], [0, 0.6, 0], clamp);
  const spot = interpolate(frame, [zoomStart, zoomStart + zoomLen], [0, 0.6], clamp);
  const cardIn = zoomStart + zoomLen - 4;

  const src = scene.footage?.src;
  const url = src ? (src.startsWith('http') ? src : staticFile(src)) : null;
  const n = d.n ?? 1;
  const total = d.total ?? 1;
  const label: string = d.label ?? '';
  const text: string = d.text ?? '';

  return (
    <AbsoluteFill style={{background: C.ink, overflow: 'hidden'}}>
      <AbsoluteFill style={{transformOrigin: '0 0', transform: `translate(${dx}px, ${dy}px) scale(${s})`}}>
        {url ? (
          scene.footage?.kind === 'photo' ? (
            <Img src={url} style={{width: '100%', height: '100%', objectFit: 'cover'}} />
          ) : (
            <OffthreadVideo src={url} muted style={{width: '100%', height: '100%', objectFit: 'cover'}} />
          )
        ) : null}
      </AbsoluteFill>
      {/* spotlight: everything but the detail darkens */}
      <AbsoluteFill style={{background: `radial-gradient(ellipse ${rw * 2.4}px ${rh * 2.4}px at ${cx}px ${cy}px, transparent 42%, ${hexA(C.ink, spot)} 100%)`}} />
      <AbsoluteFill style={{background: `linear-gradient(180deg, ${hexA(C.ink, 0.55 * p)} 0%, transparent 30%, transparent 70%, ${hexA(C.ink, 0.5 * p)} 100%)`}} />
      <AbsoluteFill style={{background: '#FFFFFF', opacity: flash}} />
      <svg width={W} height={H} style={{position: 'absolute', left: 0, top: 0, overflow: 'visible'}}>
        <ellipse
          cx={cx}
          cy={cy}
          rx={rw * pulse}
          ry={rh * pulse}
          fill="none"
          stroke={hexA(accent, 0.35)}
          strokeWidth={22 * u}
          strokeDasharray={perimeter}
          strokeDashoffset={perimeter * (1 - ringP)}
          transform={`rotate(-6 ${cx} ${cy})`}
        />
        <ellipse
          cx={cx}
          cy={cy}
          rx={rw * pulse}
          ry={rh * pulse}
          fill="none"
          stroke={accent}
          strokeWidth={7 * u}
          strokeLinecap="round"
          strokeDasharray={perimeter}
          strokeDashoffset={perimeter * (1 - ringP)}
          transform={`rotate(-6 ${cx} ${cy})`}
        />
      </svg>
      <div style={{position: 'absolute', left: pad, right: vertical ? pad : undefined, top: safeTop - (vertical ? 20 : 10) * u, maxWidth: vertical ? undefined : 1020 * u}}>
        <Panel accent={accent} delay={cardIn} style={{padding: `${22 * u}px ${34 * u}px ${24 * u}px ${46 * u}px`}}>
          <div style={{display: 'flex', alignItems: 'baseline', gap: 16 * u}}>
            <div style={{fontFamily: F.display, fontWeight: 900, fontStyle: 'italic', fontSize: (vertical ? 96 : 84) * u, lineHeight: 0.9, color: accent, textShadow: `0 0 ${30 * u}px ${hexA(accent, 0.6)}`}}>#{n}</div>
            <div style={{fontFamily: F.display, fontWeight: 700, fontSize: (vertical ? 44 : 38) * u, color: C.mute}}>/ {total}</div>
            {d.source ? <div style={{marginLeft: 'auto', paddingLeft: 24 * u, fontFamily: F.mono, fontWeight: 700, fontSize: (vertical ? 24 : 21) * u, letterSpacing: '0.14em', color: C.mute, whiteSpace: 'nowrap'}}>{d.source}</div> : null}
          </div>
          <div style={{fontFamily: F.display, fontWeight: 800, fontSize: (vertical ? 70 : 62) * u, lineHeight: 1.0, color: C.paper, textTransform: 'uppercase', marginTop: 6 * u}}>{label}</div>
          {text ? <div style={{fontFamily: F.mono, fontWeight: 700, fontSize: (vertical ? 32 : 28) * u, color: C.sun, marginTop: 10 * u}}>« {text} »</div> : null}
        </Panel>
      </div>
    </AbsoluteFill>
  );
};
