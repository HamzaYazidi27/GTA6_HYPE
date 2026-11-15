/** Scene templates, part A: typography, sources, quotes, fact-check cards, numbers. */
import React from 'react';
import {interpolate, useCurrentFrame} from 'remotion';
import {Counter, Kicker, Panel, Reveal, SlamWords, Stamp, Wipe, clamp, easeOut, useProgress, useSpring, withMarker} from '../motion_graphics/primitives';
import {C, F, STATUS, Status, hexA, useLayout} from '../motion_graphics/theme';
import {SceneProps, Stage} from './shared';

/** Bold kinetic typography: hooks and punch lines. */
export const KineticScene: React.FC<SceneProps> = ({scene, accent}) => {
  const {u, vertical, W} = useLayout();
  const frame = useCurrentFrame();
  const lines: string[] = scene.data.lines ?? [];
  const longest = Math.max(...lines.map((l) => l.length), 6);
  const size = Math.min((vertical ? 190 : 230) * u, ((W - (vertical ? 120 : 300) * u) / longest) * 2.05);
  const beat = scene.emphasis ? interpolate(frame, [scene.emphasis.from, scene.emphasis.from + 5, scene.emphasis.to + 8], [0, 1, 0], clamp) : 0;
  return (
    <Stage align="center">
      <div style={{display: 'flex', flexDirection: 'column', alignItems: vertical ? 'center' : 'flex-start', gap: 6 * u, transform: `scale(${1 + beat * 0.035}) rotate(-2deg)`}}>
        {scene.data.kicker ? <Kicker color={accent}>{scene.data.kicker}</Kicker> : null}
        {lines.map((line, i) => (
          <div key={i} style={{position: 'relative'}}>
            <SlamWords
              text={line}
              delay={3 + i * 6}
              accent={scene.data.accent}
              accentColor={accent}
              style={{fontFamily: F.display, fontWeight: 900, fontStyle: 'italic', fontSize: size, lineHeight: 0.94, color: C.paper, letterSpacing: '-0.01em', justifyContent: vertical ? 'center' : 'flex-start', textShadow: `0 ${10 * u}px ${40 * u}px rgba(0,0,0,0.6)`}}
            />
          </div>
        ))}
        <Wipe delay={10 + lines.length * 6} duration={12}>
          <div style={{height: 12 * u, width: Math.min(W * 0.5, size * 3.2), marginTop: 18 * u, background: `linear-gradient(90deg, ${accent}, ${hexA(accent, 0)})`, borderRadius: 6 * u}} />
        </Wipe>
      </div>
    </Stage>
  );
};

/** Episode title card. */
export const TitleScene: React.FC<SceneProps> = ({scene, accent}) => {
  const {u, vertical} = useLayout();
  const d = scene.data;
  return (
    <Stage align="center">
      <div style={{display: 'flex', flexDirection: 'column', alignItems: 'center', textAlign: 'center', gap: 26 * u, maxWidth: (vertical ? 940 : 1500) * u}}>
        <Kicker color={accent} delay={2}>
          {d.date || 'GTA 6 NEWS'}
        </Kicker>
        <SlamWords text={d.title} delay={6} stagger={2} accentColor={accent} style={{fontFamily: F.display, fontWeight: 800, fontSize: (vertical ? 120 : 132) * u, lineHeight: 1.0, color: C.paper, justifyContent: 'center', textTransform: 'uppercase'}} />
        <Wipe delay={18} duration={14} style={{width: '100%', display: 'flex', justifyContent: 'center'}}>
          <div style={{width: 320 * u, height: 8 * u, background: accent, borderRadius: 4 * u, boxShadow: `0 0 ${30 * u}px ${accent}`}} />
        </Wipe>
        {d.subtitle ? (
          <Reveal delay={24}>
            <div style={{fontFamily: F.body, fontWeight: 600, fontSize: (vertical ? 44 : 40) * u, color: C.mute, lineHeight: 1.3}}>{d.subtitle}</div>
          </Reveal>
        ) : null}
      </div>
    </Stage>
  );
};

/** Source card: where the story comes from. */
export const HeadlineScene: React.FC<SceneProps> = ({scene, accent}) => {
  const {u, vertical} = useLayout();
  const d = scene.data;
  return (
    <Stage align="center">
      <Panel accent={accent} delay={2} style={{width: '100%', maxWidth: (vertical ? 960 : 1480) * u, padding: `${(vertical ? 60 : 64) * u}px ${(vertical ? 54 : 78) * u}px`}}>
        <div style={{display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 20 * u, flexWrap: 'wrap'}}>
          <Reveal delay={8} from="left">
            <div style={{display: 'flex', alignItems: 'center', gap: 18 * u}}>
              <div style={{padding: `${8 * u}px ${20 * u}px`, borderRadius: 10 * u, background: accent, color: C.ink, fontFamily: F.display, fontWeight: 900, fontSize: 44 * u, letterSpacing: '0.04em'}}>{String(d.publisher).toUpperCase()}</div>
              <div style={{fontFamily: F.mono, fontWeight: 500, fontSize: 24 * u, color: C.mute, letterSpacing: '0.12em'}}>SOURCE ARTICLE</div>
            </div>
          </Reveal>
          <Reveal delay={12} from="right">
            <div style={{fontFamily: F.mono, fontWeight: 500, fontSize: 26 * u, color: C.mute, letterSpacing: '0.08em'}}>{[d.date, d.byline].filter(Boolean).join('  ·  ')}</div>
          </Reveal>
        </div>
        <div style={{height: 2 * u, background: C.line, margin: `${30 * u}px 0 ${34 * u}px`}} />
        <Reveal delay={14}>
          <div style={{fontFamily: F.display, fontWeight: 800, fontSize: (vertical ? 84 : 92) * u, lineHeight: 1.06, color: C.paper}}>{withMarker(d.headline, d.highlight, accent, 30)}</div>
        </Reveal>
      </Panel>
    </Stage>
  );
};

/** Verified quotation. */
export const QuoteScene: React.FC<SceneProps> = ({scene, accent}) => {
  const {u, vertical} = useLayout();
  const d = scene.data;
  const len = String(d.quote).length;
  const size = (len > 170 ? 54 : len > 110 ? 64 : 76) * (vertical ? 1.05 : 1) * u;
  const mark = useSpring(2, {damping: 11});
  return (
    <Stage align="center">
      <div style={{position: 'relative', width: '100%', maxWidth: (vertical ? 940 : 1440) * u}}>
        <div style={{position: 'absolute', left: -20 * u, top: -150 * u, fontFamily: F.display, fontWeight: 900, fontSize: 420 * u, lineHeight: 1, color: hexA(accent, 0.28), transform: `scale(${mark})`, transformOrigin: 'left top'}}>{'“'}</div>
        <Reveal delay={6}>
          <div style={{position: 'relative', fontFamily: F.body, fontWeight: 700, fontSize: size, lineHeight: 1.24, color: C.paper}}>{d.quote}</div>
        </Reveal>
        <Reveal delay={18} from="left">
          <div style={{display: 'flex', alignItems: 'center', gap: 20 * u, marginTop: 44 * u}}>
            <div style={{width: 70 * u, height: 6 * u, background: accent, borderRadius: 3 * u}} />
            <div>
              <div style={{fontFamily: F.display, fontWeight: 800, fontSize: 50 * u, color: accent, lineHeight: 1.05}}>{d.speaker}</div>
              {d.publisher ? <div style={{fontFamily: F.mono, fontWeight: 500, fontSize: 24 * u, color: C.mute, letterSpacing: '0.12em', marginTop: 6 * u}}>VIA {String(d.publisher).toUpperCase()}</div> : null}
            </div>
          </div>
        </Reveal>
      </div>
    </Stage>
  );
};

/** Fact-check card with the verification stamp. */
export const ClaimScene: React.FC<SceneProps> = ({scene}) => {
  const {u, vertical} = useLayout();
  const d = scene.data;
  const status = (d.status in STATUS ? d.status : 'reported') as Status;
  const color = STATUS[status].color;
  const level = d.confidence === 'high' ? 3 : d.confidence === 'low' ? 1 : 2;
  const meter = useProgress(30, 18);
  return (
    <Stage align="center">
      <Panel accent={color} delay={2} style={{width: '100%', maxWidth: (vertical ? 960 : 1500) * u, padding: `${(vertical ? 60 : 66) * u}px ${(vertical ? 54 : 80) * u}px ${(vertical ? 64 : 60) * u}px`}}>
        <div style={{display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 30 * u}}>
          <Kicker color={color} delay={6}>
            Fact check
          </Kicker>
          {d.source ? (
            <Reveal delay={10} from="right">
              <div style={{fontFamily: F.mono, fontWeight: 500, fontSize: 24 * u, color: C.mute, letterSpacing: '0.1em'}}>SOURCE {'·'} {String(d.source).toUpperCase()}</div>
            </Reveal>
          ) : null}
        </div>
        <Reveal delay={10}>
          <div style={{fontFamily: F.display, fontWeight: 800, fontSize: (String(d.claim).length > 90 ? 76 : 90) * (vertical ? 0.95 : 1) * u, lineHeight: 1.08, color: C.paper}}>{d.claim}</div>
        </Reveal>
        <div style={{display: 'flex', flexDirection: vertical ? 'column' : 'row', alignItems: vertical ? 'flex-start' : 'center', justifyContent: 'space-between', gap: 30 * u, marginTop: 48 * u}}>
          <div style={{paddingLeft: 26 * u}}>
            <Stamp status={status} delay={22} size={vertical ? 72 : 78} />
          </div>
          <Reveal delay={30} from="right">
            <div style={{display: 'flex', alignItems: 'center', gap: 16 * u}}>
              <div style={{fontFamily: F.mono, fontWeight: 500, fontSize: 24 * u, color: C.mute, letterSpacing: '0.12em'}}>CONFIDENCE</div>
              <div style={{display: 'flex', gap: 8 * u}}>
                {[1, 2, 3].map((i) => (
                  <div key={i} style={{width: 58 * u, height: 16 * u, borderRadius: 8 * u, background: i <= level ? color : 'rgba(255,255,255,0.12)', opacity: i <= level ? Math.min(1, meter * 3 - (i - 1)) : 1, boxShadow: i <= level ? `0 0 ${16 * u}px ${hexA(color, 0.6)}` : 'none'}} />
                ))}
              </div>
              <div style={{fontFamily: F.display, fontWeight: 800, fontSize: 36 * u, color}}>{String(d.confidence).toUpperCase()}</div>
            </div>
          </Reveal>
        </div>
        {d.note ? (
          <Reveal delay={38}>
            <div style={{marginTop: 30 * u, fontFamily: F.body, fontWeight: 600, fontSize: 32 * u, color: C.mute}}>{d.note}</div>
          </Reveal>
        ) : null}
      </Panel>
    </Stage>
  );
};

/** One big number. */
export const StatScene: React.FC<SceneProps> = ({scene, accent}) => {
  const {u, vertical} = useLayout();
  const frame = useCurrentFrame();
  const d = scene.data;
  const start = Math.max(6, (scene.cueFrames?.[0] ?? 10) - 14);
  const glow = interpolate(frame, [start + 30, start + 40, start + 60], [0, 1, 0.35], clamp);
  const text = `${d.prefix ?? ''}${Number(d.value).toLocaleString('en-US', {maximumFractionDigits: d.decimals})}${d.suffix ?? ''}`;
  const size = Math.min(vertical ? 330 : 400, (vertical ? 1900 : 2500) / Math.max(4, text.length)) * u;
  return (
    <Stage align="center">
      <div style={{display: 'flex', flexDirection: 'column', alignItems: 'center', textAlign: 'center', gap: 8 * u}}>
        <Kicker color={accent} delay={2}>
          {d.source ? `Figure · ${d.source}` : 'By the numbers'}
        </Kicker>
        <Reveal delay={start} from="scale">
          <div style={{fontFamily: F.display, fontWeight: 900, fontStyle: 'italic', fontSize: size, lineHeight: 0.95, color: C.paper, textShadow: `0 0 ${90 * u * glow}px ${hexA(accent, 0.75)}, 0 ${12 * u}px ${50 * u}px rgba(0,0,0,0.5)`, letterSpacing: '-0.02em'}}>
            <Counter value={Number(d.value)} delay={start} duration={32} decimals={d.decimals ?? 0} prefix={d.prefix} />
            <span style={{color: accent, fontSize: '0.62em', marginLeft: '0.06em'}}>{d.suffix}</span>
          </div>
        </Reveal>
        <Reveal delay={start + 14}>
          <div style={{fontFamily: F.display, fontWeight: 800, fontSize: (vertical ? 76 : 80) * u, color: accent, textTransform: 'uppercase', letterSpacing: '0.02em', lineHeight: 1.05}}>{d.label}</div>
        </Reveal>
        {d.context ? (
          <Reveal delay={start + 24}>
            <div style={{fontFamily: F.body, fontWeight: 600, fontSize: (vertical ? 42 : 40) * u, color: C.mute, maxWidth: 1200 * u, lineHeight: 1.3, marginTop: 10 * u}}>{d.context}</div>
          </Reveal>
        ) : null}
        {d.compare ? (
          <Reveal delay={start + 34}>
            <div style={{marginTop: 26 * u, display: 'flex', alignItems: 'center', gap: 18 * u, padding: `${14 * u}px ${30 * u}px`, borderRadius: 999, border: `${2 * u}px solid ${C.line}`, background: hexA(C.ink, 0.6)}}>
              <span style={{fontFamily: F.mono, fontWeight: 500, fontSize: 26 * u, color: C.mute, letterSpacing: '0.1em'}}>{String(d.compare.label).toUpperCase()}</span>
              <span style={{fontFamily: F.display, fontWeight: 800, fontSize: 48 * u, color: C.paper}}>
                {d.compare.prefix}
                {Number(d.compare.value).toLocaleString('en-US')}
                {d.compare.suffix}
              </span>
            </div>
          </Reveal>
        ) : null}
      </div>
    </Stage>
  );
};

/** Animated horizontal bars. */
export const BarsScene: React.FC<SceneProps> = ({scene, accent}) => {
  const {u, vertical} = useLayout();
  const frame = useCurrentFrame();
  const d = scene.data;
  const items: {label: string; value: number; display?: string; highlight?: boolean}[] = d.items ?? [];
  const max = Math.max(...items.map((i) => i.value), 1);
  return (
    <Stage align="center">
      <div style={{width: '100%', maxWidth: (vertical ? 960 : 1520) * u}}>
        <Kicker color={accent} delay={2}>
          {d.source ? `Data · ${d.source}` : 'Comparison'}
        </Kicker>
        <Reveal delay={5}>
          <div style={{fontFamily: F.display, fontWeight: 800, fontSize: (vertical ? 84 : 88) * u, color: C.paper, lineHeight: 1.05, margin: `${14 * u}px 0 ${40 * u}px`, textTransform: 'uppercase'}}>{d.title}</div>
        </Reveal>
        <div style={{display: 'flex', flexDirection: 'column', gap: (vertical ? 34 : 28) * u}}>
          {items.map((it, i) => {
            const at = scene.cueFrames?.[i] != null && scene.cueFrames[i] >= 0 ? Math.max(10, scene.cueFrames[i] - 4) : 14 + i * 12;
            const p = interpolate(frame, [at, at + 24], [0, 1], {...clamp, easing: easeOut});
            const col = it.highlight ? accent : C.cyan;
            return (
              <div key={i} style={{opacity: Math.min(1, p * 3)}}>
                <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 10 * u}}>
                  <div style={{fontFamily: F.body, fontWeight: 700, fontSize: (vertical ? 42 : 38) * u, color: it.highlight ? C.paper : C.mute}}>{it.label}</div>
                  <div style={{fontFamily: F.display, fontWeight: 900, fontSize: (vertical ? 62 : 58) * u, color: col, fontVariantNumeric: 'tabular-nums'}}>{it.display || `${(it.value * p).toLocaleString('en-US', {maximumFractionDigits: 1})}${d.unit ? ' ' + d.unit : ''}`}</div>
                </div>
                <div style={{height: (vertical ? 40 : 36) * u, borderRadius: 10 * u, background: 'rgba(255,255,255,0.07)', overflow: 'hidden'}}>
                  <div style={{height: '100%', width: `${Math.max(2, (it.value / max) * 100 * p)}%`, borderRadius: 10 * u, background: `linear-gradient(90deg, ${hexA(col, 0.55)}, ${col})`, boxShadow: `0 0 ${30 * u}px ${hexA(col, 0.5)}`}} />
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </Stage>
  );
};
