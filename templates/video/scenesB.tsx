/** Scene templates, part B: timelines, lists, comparisons, countdown, source board, analysis, bumpers. */
import React from 'react';
import {interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {MediaBackdrop} from '../motion_graphics/backgrounds';
import {BrandMark} from '../motion_graphics/overlays';
import {Kicker, Panel, Reveal, SlamWords, Wipe, clamp, cueFrame, easeOut, useProgress, useSpring} from '../motion_graphics/primitives';
import {C, F, hexA, useLayout} from '../motion_graphics/theme';
import {SceneProps, Stage} from './shared';

const Heading: React.FC<{title?: string; kicker: string; accent: string}> = ({title, kicker, accent}) => {
  const {u, vertical} = useLayout();
  return (
    <div style={{width: '100%', marginBottom: 36 * u}}>
      <Kicker color={accent} delay={2}>
        {kicker}
      </Kicker>
      {title ? (
        <Reveal delay={5}>
          <div style={{fontFamily: F.display, fontWeight: 800, fontSize: (vertical ? 86 : 90) * u, color: C.paper, lineHeight: 1.04, marginTop: 12 * u, textTransform: 'uppercase'}}>{title}</div>
        </Reveal>
      ) : null}
    </div>
  );
};

/** Dated events on an animated line. */
export const TimelineScene: React.FC<SceneProps> = ({scene, accent}) => {
  const {u, vertical} = useLayout();
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const events: {date: string; label: string; state: string}[] = scene.data.events ?? [];
  const n = events.length;
  const at = (i: number) => cueFrame(scene.cueFrames, i, n, scene.durationFrames, 14);
  const lineP = interpolate(frame, [10, Math.max(30, at(n - 1) + 10)], [0, 1], {...clamp, easing: easeOut});
  const colorOf = (s: string) => (s === 'future' ? C.sun : s === 'now' ? accent : C.cyan);
  if (vertical) {
    return (
      <Stage>
        <div style={{width: '100%', maxWidth: 940 * u}}>
          <Heading title={scene.data.title} kicker="Timeline" accent={accent} />
          <div style={{position: 'relative', paddingLeft: 70 * u}}>
            <div style={{position: 'absolute', left: 22 * u, top: 10 * u, bottom: 10 * u, width: 6 * u, background: 'rgba(255,255,255,0.1)', borderRadius: 3 * u}}>
              <div style={{width: '100%', height: `${lineP * 100}%`, background: `linear-gradient(180deg, ${C.cyan}, ${accent})`, borderRadius: 3 * u}} />
            </div>
            {events.map((ev, i) => {
              const p = spring({frame: frame - at(i), fps, config: {damping: 14, stiffness: 170}});
              const col = colorOf(ev.state);
              return (
                <div key={i} style={{position: 'relative', marginBottom: 44 * u, opacity: Math.min(1, p * 2), transform: `translateX(${(1 - p) * 60 * u}px)`}}>
                  <div style={{position: 'absolute', left: -62 * u, top: 12 * u, width: 34 * u, height: 34 * u, borderRadius: '50%', background: ev.state === 'future' ? C.ink : col, border: `${6 * u}px solid ${col}`, boxShadow: `0 0 ${24 * u}px ${hexA(col, 0.7)}`, transform: `scale(${p})`}} />
                  <div style={{fontFamily: F.mono, fontWeight: 700, fontSize: 34 * u, color: col, letterSpacing: '0.08em'}}>{ev.date.toUpperCase()}</div>
                  <div style={{fontFamily: F.display, fontWeight: 800, fontSize: 62 * u, color: C.paper, lineHeight: 1.08}}>{ev.label}</div>
                </div>
              );
            })}
          </div>
        </div>
      </Stage>
    );
  }
  return (
    <Stage>
      <div style={{width: '100%', maxWidth: 1620 * u}}>
        <Heading title={scene.data.title} kicker="Timeline" accent={accent} />
        <div style={{position: 'relative', height: 420 * u, marginTop: 30 * u}}>
          <div style={{position: 'absolute', left: 0, right: 0, top: 96 * u, height: 8 * u, borderRadius: 4 * u, background: 'rgba(255,255,255,0.1)'}}>
            <div style={{height: '100%', width: `${lineP * 100}%`, borderRadius: 4 * u, background: `linear-gradient(90deg, ${C.cyan}, ${accent})`, boxShadow: `0 0 ${24 * u}px ${hexA(accent, 0.6)}`}} />
          </div>
          {events.map((ev, i) => {
            const p = spring({frame: frame - at(i), fps, config: {damping: 13, stiffness: 180}});
            const col = colorOf(ev.state);
            const x = n === 1 ? 50 : 6 + (i / (n - 1)) * 88;
            return (
              <div key={i} style={{position: 'absolute', left: `${x}%`, top: 0, width: 0}}>
                <div style={{position: 'absolute', left: -180 * u, width: 360 * u, top: 6 * u, textAlign: 'center', fontFamily: F.mono, fontWeight: 700, fontSize: 30 * u, color: col, letterSpacing: '0.08em', opacity: p}}>{ev.date.toUpperCase()}</div>
                <div style={{position: 'absolute', left: -24 * u, top: 76 * u, width: 48 * u, height: 48 * u, borderRadius: '50%', background: ev.state === 'future' ? C.ink : col, border: `${8 * u}px solid ${col}`, boxShadow: `0 0 ${30 * u}px ${hexA(col, 0.75)}`, transform: `scale(${p})`}} />
                <div style={{position: 'absolute', left: -3 * u, top: 130 * u, width: 6 * u, height: 40 * u * p, background: hexA(col, 0.6)}} />
                <div style={{position: 'absolute', left: -170 * u, width: 340 * u, top: 184 * u, textAlign: 'center', fontFamily: F.display, fontWeight: 800, fontSize: 46 * u, lineHeight: 1.08, color: C.paper, opacity: Math.min(1, p * 1.5), transform: `translateY(${(1 - p) * 30 * u}px)`}}>
                  {ev.label}
                  {ev.state === 'now' ? <div style={{marginTop: 10 * u, fontFamily: F.mono, fontSize: 22 * u, color: col, letterSpacing: '0.16em'}}>{'●'} NOW</div> : null}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </Stage>
  );
};

/** Points revealed in sync with the voice. */
export const BulletsScene: React.FC<SceneProps> = ({scene, accent}) => {
  const {u, vertical} = useLayout();
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const items: string[] = scene.data.items ?? [];
  const style = scene.data.style ?? 'check';
  const glyph = (i: number) => (style === 'number' ? String(i + 1).padStart(2, '0') : style === 'question' ? '?' : style === 'cross' ? '✕' : '✓');
  return (
    <Stage>
      <div style={{width: '100%', maxWidth: (vertical ? 960 : 1500) * u}}>
        <Heading title={scene.data.title} kicker={style === 'question' ? 'Open questions' : 'Key points'} accent={accent} />
        <div style={{display: 'flex', flexDirection: 'column', gap: (vertical ? 26 : 20) * u}}>
          {items.map((it, i) => {
            const at = cueFrame(scene.cueFrames, i, items.length, scene.durationFrames, 12);
            const p = spring({frame: frame - at, fps, config: {damping: 15, stiffness: 170}});
            const fresh = interpolate(frame, [at, at + 6, at + 30], [0, 1, 0], clamp);
            return (
              <div
                key={i}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 28 * u,
                  padding: `${(vertical ? 26 : 22) * u}px ${32 * u}px`,
                  borderRadius: 20 * u,
                  background: `linear-gradient(90deg, ${hexA(accent, 0.1 + fresh * 0.22)}, ${hexA(C.panel, 0.72)})`,
                  border: `${2 * u}px solid ${hexA(accent, 0.2 + fresh * 0.6)}`,
                  opacity: Math.min(1, p * 2),
                  transform: `translateX(${(1 - p) * -90 * u}px)`,
                }}
              >
                <div style={{flex: 'none', width: 74 * u, height: 74 * u, borderRadius: 18 * u, background: accent, color: C.ink, display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: F.display, fontWeight: 900, fontSize: (style === 'number' ? 40 : 48) * u, transform: `scale(${0.5 + 0.5 * p}) rotate(${(1 - p) * -40}deg)`}}>{glyph(i)}</div>
                <div style={{fontFamily: F.body, fontWeight: 700, fontSize: (vertical ? 46 : 46) * u, color: C.paper, lineHeight: 1.18}}>{it}</div>
              </div>
            );
          })}
        </div>
      </div>
    </Stage>
  );
};

/** Two-sided comparison. */
export const VersusScene: React.FC<SceneProps> = ({scene, accent}) => {
  const {u, vertical} = useLayout();
  const d = scene.data;
  const half = Math.round(scene.durationFrames * 0.4);
  const Side: React.FC<{side: any; color: string; delay: number; from: 'left' | 'right'}> = ({side, color, delay, from}) => (
    <Reveal delay={delay} from={from} distance={120} style={{flex: 1, minWidth: 0}}>
      <div style={{height: '100%', borderRadius: 24 * u, padding: `${40 * u}px ${44 * u}px`, background: `linear-gradient(160deg, ${hexA(color, 0.2)}, ${hexA(C.panel, 0.9)})`, border: `${2.5 * u}px solid ${hexA(color, 0.55)}`, boxShadow: `0 0 ${60 * u}px ${hexA(color, 0.14)}`}}>
        <div style={{fontFamily: F.mono, fontWeight: 700, fontSize: 28 * u, letterSpacing: '0.14em', color}}>{String(side.title).toUpperCase()}</div>
        {side.value ? <div style={{fontFamily: F.display, fontWeight: 900, fontStyle: 'italic', fontSize: 130 * u, lineHeight: 1, color: C.paper, margin: `${10 * u}px 0 ${14 * u}px`}}>{side.value}</div> : <div style={{height: 20 * u}} />}
        {(side.points ?? []).map((pt: string, i: number) => (
          <Reveal key={i} delay={delay + 10 + i * 7} from="up" distance={24}>
            <div style={{display: 'flex', gap: 16 * u, alignItems: 'flex-start', marginTop: 16 * u}}>
              <div style={{flex: 'none', width: 14 * u, height: 14 * u, marginTop: 17 * u, borderRadius: 4 * u, background: color}} />
              <div style={{fontFamily: F.body, fontWeight: 600, fontSize: 40 * u, lineHeight: 1.22, color: C.paper}}>{pt}</div>
            </div>
          </Reveal>
        ))}
      </div>
    </Reveal>
  );
  const vs = useSpring(Math.min(half, 22), {damping: 10, stiffness: 220});
  return (
    <Stage>
      <div style={{width: '100%', maxWidth: (vertical ? 960 : 1600) * u}}>
        <Heading title={d.title} kicker="Side by side" accent={accent} />
        <div style={{position: 'relative', display: 'flex', flexDirection: vertical ? 'column' : 'row', gap: (vertical ? 70 : 90) * u, alignItems: 'stretch'}}>
          <Side side={d.left} color={C.cyan} delay={8} from="left" />
          <div style={{position: 'absolute', left: '50%', top: '50%', width: 110 * u, height: 110 * u, marginLeft: -55 * u, marginTop: -55 * u, borderRadius: '50%', background: C.ink, border: `${4 * u}px solid ${accent}`, display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: F.display, fontWeight: 900, fontStyle: 'italic', fontSize: 50 * u, color: accent, transform: `scale(${vs}) rotate(${(1 - vs) * 180}deg)`, zIndex: 2, boxShadow: `0 0 ${40 * u}px ${hexA(accent, 0.6)}`}}>VS</div>
          <Side side={d.right} color={C.coral} delay={Math.min(half, 22)} from="right" />
        </div>
        {d.verdict ? (
          <Reveal delay={Math.min(half, 22) + 34}>
            <div style={{marginTop: 34 * u, textAlign: 'center', fontFamily: F.display, fontWeight: 800, fontSize: 52 * u, color: C.sun}}>{d.verdict}</div>
          </Reveal>
        ) : null}
      </div>
    </Stage>
  );
};

/** Countdown to a confirmed date, computed from the render's "today". */
export const CountdownScene: React.FC<SceneProps> = ({scene, accent, today}) => {
  const {u, vertical} = useLayout();
  const frame = useCurrentFrame();
  const d = scene.data;
  const target = new Date(d.target_date + 'T00:00:00Z');
  const now = new Date(today + 'T00:00:00Z');
  const days = Math.max(0, Math.round((target.getTime() - now.getTime()) / 86400000));
  const p = interpolate(frame, [8, 40], [0, 1], {...clamp, easing: easeOut});
  const shown = Math.round(days + (1 - p) * Math.min(30, Math.max(6, days)));
  const month = target.toLocaleString('en-US', {month: 'short', timeZone: 'UTC'}).toUpperCase();
  const flip = useSpring(6, {damping: 12});
  return (
    <Stage>
      <div style={{display: 'flex', flexDirection: vertical ? 'column' : 'row', alignItems: 'center', gap: (vertical ? 60 : 110) * u}}>
        <div style={{width: 380 * u, borderRadius: 34 * u, overflow: 'hidden', background: C.paper, boxShadow: `0 ${30 * u}px ${90 * u}px rgba(0,0,0,0.6), 0 0 ${80 * u}px ${hexA(accent, 0.3)}`, transform: `perspective(${1200 * u}px) rotateX(${(1 - flip) * -80}deg) rotate(-4deg)`, transformOrigin: 'top center'}}>
          <div style={{background: accent, color: C.ink, textAlign: 'center', fontFamily: F.display, fontWeight: 900, fontSize: 74 * u, letterSpacing: '0.12em', padding: `${12 * u}px 0`}}>{month}</div>
          <div style={{textAlign: 'center', fontFamily: F.display, fontWeight: 900, fontSize: 250 * u, lineHeight: 1, color: C.ink, padding: `${6 * u}px 0 ${4 * u}px`}}>{target.getUTCDate()}</div>
          <div style={{textAlign: 'center', fontFamily: F.mono, fontWeight: 700, fontSize: 34 * u, color: C.ink2, letterSpacing: '0.2em', paddingBottom: 22 * u}}>{target.getUTCFullYear()}</div>
        </div>
        <div style={{textAlign: vertical ? 'center' : 'left'}}>
          <Kicker color={accent} delay={10}>
            Countdown
          </Kicker>
          <div style={{display: 'flex', alignItems: 'baseline', gap: 24 * u, justifyContent: vertical ? 'center' : 'flex-start'}}>
            <div style={{fontFamily: F.display, fontWeight: 900, fontStyle: 'italic', fontSize: 340 * u, lineHeight: 0.95, color: C.paper, fontVariantNumeric: 'tabular-nums', textShadow: `0 0 ${70 * u}px ${hexA(accent, 0.5)}`}}>{shown}</div>
            <div style={{fontFamily: F.display, fontWeight: 900, fontSize: 110 * u, color: accent}}>{days === 1 ? 'DAY' : 'DAYS'}</div>
          </div>
          <Reveal delay={26}>
            <div style={{fontFamily: F.display, fontWeight: 800, fontSize: 66 * u, color: C.paper, textTransform: 'uppercase', lineHeight: 1.05}}>{d.label}</div>
            {d.sublabel ? <div style={{fontFamily: F.body, fontWeight: 600, fontSize: 36 * u, color: C.mute, marginTop: 10 * u}}>{d.sublabel}</div> : null}
          </Reveal>
        </div>
      </div>
    </Stage>
  );
};

const STANCE: Record<string, {label: string; color: string; glyph: string}> = {
  confirms: {label: 'CONFIRMS', color: C.lime, glyph: '✓'},
  reports: {label: 'REPORTS', color: C.cyan, glyph: '◆'},
  disputes: {label: 'DISPUTES', color: C.coral, glyph: '✕'},
  silent: {label: 'NO COMMENT', color: C.mute, glyph: '–'},
};

/** Source board: who says what. */
export const SourcesScene: React.FC<SceneProps> = ({scene, accent}) => {
  const {u, vertical} = useLayout();
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const rows: {publisher: string; stance: string; note: string}[] = scene.data.sources ?? [];
  return (
    <Stage>
      <div style={{width: '100%', maxWidth: (vertical ? 960 : 1500) * u}}>
        <Heading title={scene.data.title} kicker="Source check" accent={accent} />
        <div style={{display: 'flex', flexDirection: 'column', gap: 18 * u}}>
          {rows.map((r, i) => {
            const at = cueFrame(scene.cueFrames, i, rows.length, scene.durationFrames, 12);
            const p = spring({frame: frame - at, fps, config: {damping: 15, stiffness: 170}});
            const st = STANCE[r.stance] ?? STANCE.reports;
            return (
              <div key={i} style={{display: 'flex', alignItems: 'center', gap: 26 * u, padding: `${22 * u}px ${30 * u}px`, borderRadius: 18 * u, background: hexA(C.panel, 0.82), border: `${2 * u}px solid ${hexA(st.color, 0.35)}`, opacity: Math.min(1, p * 2), transform: `translateY(${(1 - p) * 40 * u}px)`}}>
                <div style={{flex: 'none', width: (vertical ? 300 : 360) * u, fontFamily: F.display, fontWeight: 900, fontSize: 52 * u, color: C.paper, lineHeight: 1}}>{r.publisher.toUpperCase()}</div>
                {!vertical && <div style={{flex: 1, fontFamily: F.body, fontWeight: 600, fontSize: 34 * u, color: C.mute, lineHeight: 1.2}}>{r.note}</div>}
                <div style={{flex: 'none', marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 12 * u, padding: `${9 * u}px ${22 * u}px`, borderRadius: 999, background: hexA(st.color, 0.14), border: `${2 * u}px solid ${st.color}`, color: st.color, fontFamily: F.mono, fontWeight: 700, fontSize: 26 * u, letterSpacing: '0.1em', transform: `scale(${0.6 + 0.4 * p})`}}>
                  <span style={{fontFamily: F.body, fontWeight: 800}}>{st.glyph}</span>
                  {st.label}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </Stage>
  );
};

/** Cause -> consequences diagram. */
export const TakeawayScene: React.FC<SceneProps> = ({scene, accent}) => {
  const {u, vertical} = useLayout();
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const effects: string[] = scene.data.effects ?? [];
  return (
    <Stage>
      <div style={{width: '100%', maxWidth: (vertical ? 960 : 1560) * u}}>
        <Heading title={scene.data.title} kicker="What it means" accent={accent} />
        <div style={{display: 'flex', flexDirection: vertical ? 'column' : 'row', alignItems: 'center', gap: (vertical ? 30 : 50) * u}}>
          <Panel accent={accent} delay={8} style={{flex: 'none', width: vertical ? '100%' : 520 * u, padding: `${44 * u}px ${46 * u}px`}}>
            <div style={{fontFamily: F.mono, fontWeight: 700, fontSize: 24 * u, color: accent, letterSpacing: '0.16em'}}>THE NEWS</div>
            <div style={{fontFamily: F.display, fontWeight: 800, fontSize: 62 * u, lineHeight: 1.08, color: C.paper, marginTop: 10 * u}}>{scene.data.cause}</div>
          </Panel>
          <div style={{flex: 1, width: vertical ? '100%' : undefined, display: 'flex', flexDirection: 'column', gap: 20 * u}}>
            {effects.map((e, i) => {
              const at = cueFrame(scene.cueFrames, i, effects.length, scene.durationFrames, 22);
              const p = spring({frame: frame - at, fps, config: {damping: 15, stiffness: 170}});
              return (
                <div key={i} style={{display: 'flex', alignItems: 'center', gap: 22 * u, opacity: Math.min(1, p * 2)}}>
                  <div style={{flex: 'none', width: (vertical ? 60 : 110) * u * p, height: 6 * u, background: `linear-gradient(90deg, ${hexA(accent, 0.2)}, ${accent})`, position: 'relative'}}>
                    <div style={{position: 'absolute', right: -2 * u, top: -9 * u, width: 0, height: 0, borderLeft: `${18 * u}px solid ${accent}`, borderTop: `${12 * u}px solid transparent`, borderBottom: `${12 * u}px solid transparent`}} />
                  </div>
                  <div style={{flex: 1, marginLeft: 14 * u, padding: `${24 * u}px ${30 * u}px`, borderRadius: 18 * u, background: hexA(C.panel, 0.85), border: `${2 * u}px solid ${hexA(accent, 0.35)}`, fontFamily: F.body, fontWeight: 700, fontSize: 42 * u, lineHeight: 1.2, color: C.paper, transform: `translateX(${(1 - p) * 60 * u}px)`}}>{e}</div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </Stage>
  );
};

/** Section bumper. */
export const ChapterScene: React.FC<SceneProps> = ({scene, accent}) => {
  const {u, W} = useLayout();
  const bar = useProgress(0, 14);
  return (
    <Stage>
      <div style={{display: 'flex', alignItems: 'center', gap: 44 * u}}>
        <Reveal delay={2} from="scale">
          <div style={{fontFamily: F.display, fontWeight: 900, fontStyle: 'italic', fontSize: 330 * u, lineHeight: 0.9, color: 'transparent', WebkitTextStroke: `${5 * u}px ${accent}`}}>{String(scene.data.index ?? 1).padStart(2, '0')}</div>
        </Reveal>
        <div>
          <div style={{height: 10 * u, width: Math.min(W * 0.4, 520 * u) * bar, background: accent, borderRadius: 5 * u, marginBottom: 20 * u, boxShadow: `0 0 ${30 * u}px ${accent}`}} />
          <SlamWords text={scene.data.title} delay={6} accentColor={accent} style={{fontFamily: F.display, fontWeight: 900, fontSize: 150 * u, lineHeight: 0.98, color: C.paper}} />
          {scene.data.kicker ? (
            <Reveal delay={16}>
              <div style={{fontFamily: F.mono, fontWeight: 500, fontSize: 30 * u, color: C.mute, letterSpacing: '0.14em', marginTop: 16 * u}}>{String(scene.data.kicker).toUpperCase()}</div>
            </Reveal>
          ) : null}
        </div>
      </div>
    </Stage>
  );
};

/** Closing question. Leaves the right side calm for YouTube end-screen elements. */
export const OutroScene: React.FC<SceneProps> = ({scene, accent}) => {
  const {u, vertical} = useLayout();
  const frame = useCurrentFrame();
  const bob = Math.sin(frame / 9) * 8 * u;
  return (
    <Stage>
      <div style={{display: 'flex', flexDirection: 'column', alignItems: 'center', textAlign: 'center', gap: 34 * u, maxWidth: (vertical ? 940 : 1400) * u}}>
        <Reveal delay={2} from="scale">
          <BrandMark size={120 * u} color={accent} />
        </Reveal>
        <Kicker color={accent} delay={6}>
          Your call
        </Kicker>
        <Reveal delay={10}>
          <div style={{fontFamily: F.display, fontWeight: 800, fontSize: (vertical ? 96 : 104) * u, lineHeight: 1.06, color: C.paper}}>{scene.data.question}</div>
        </Reveal>
        <Reveal delay={24}>
          <div style={{display: 'flex', alignItems: 'center', gap: 18 * u, padding: `${18 * u}px ${40 * u}px`, borderRadius: 999, background: accent, color: C.ink, fontFamily: F.display, fontWeight: 900, fontSize: 50 * u, letterSpacing: '0.04em', transform: `translateY(${bob}px)`, boxShadow: `0 0 ${50 * u}px ${hexA(accent, 0.55)}`}}>
            {String(scene.data.cta).toUpperCase()} <span style={{fontFamily: F.body}}>{'↓'}</span>
          </div>
        </Reveal>
      </div>
    </Stage>
  );
};

/** Licensed stock / operator-supplied still with a caption. */
export const BrollScene: React.FC<SceneProps> = ({scene, accent}) => {
  const {u} = useLayout();
  return (
    <>
      {scene.media ? <MediaBackdrop src={scene.media} durationInFrames={scene.durationFrames} /> : null}
      <Stage>
        <div style={{marginTop: 'auto', width: '100%'}}>
          {scene.data.kicker ? <Kicker color={accent}>{scene.data.kicker}</Kicker> : null}
          {scene.data.caption ? (
            <Wipe delay={8} duration={16}>
              <div style={{display: 'inline-block', marginTop: 14 * u, padding: `${14 * u}px ${30 * u}px`, background: hexA(C.ink, 0.82), borderLeft: `${8 * u}px solid ${accent}`, fontFamily: F.display, fontWeight: 800, fontSize: 70 * u, color: C.paper, lineHeight: 1.08}}>{scene.data.caption}</div>
            </Wipe>
          ) : null}
          {scene.mediaCredit ? <div style={{marginTop: 14 * u, fontFamily: F.mono, fontSize: 20 * u, color: C.mute, letterSpacing: '0.1em'}}>{scene.mediaCredit}</div> : null}
        </div>
      </Stage>
    </>
  );
};
