/**
 * Main composition. Used for long-form (16:9) and Shorts (9:16): every scene
 * adapts through `useLayout()`, so one storyboard renders in both formats.
 */
import React from 'react';
import {AbsoluteFill, Sequence, useCurrentFrame} from 'remotion';
import {Atmosphere, Background, FootageBackdrop} from '../motion_graphics/backgrounds';
import {ensureFonts} from '../motion_graphics/fonts';
import {BrandBug, Captions, FootageCredit, ProgressRail, SceneOut, SourceStrip, StatusTag, TransitionIn} from '../motion_graphics/overlays';
import {CameraDrift} from '../motion_graphics/primitives';
import {C, STATUS, Status, accentFor, hexA} from '../motion_graphics/theme';
import {BarsScene, ClaimScene, HeadlineScene, KineticScene, QuoteScene, StatScene, TitleScene} from './scenesA';
import {BrollScene, BulletsScene, ChapterScene, CountdownScene, OutroScene, SourcesScene, TakeawayScene, TimelineScene, VersusScene} from './scenesB';
import type {SceneData, SceneProps, Timeline} from './shared';

ensureFonts();

export const SCENES: Record<string, React.FC<SceneProps>> = {
  kinetic: KineticScene,
  title: TitleScene,
  headline: HeadlineScene,
  quote: QuoteScene,
  claim: ClaimScene,
  stat: StatScene,
  bars: BarsScene,
  timeline: TimelineScene,
  bullets: BulletsScene,
  versus: VersusScene,
  countdown: CountdownScene,
  sources: SourcesScene,
  takeaway: TakeawayScene,
  chapter: ChapterScene,
  outro: OutroScene,
  broll: BrollScene,
};

/** How much real footage is pushed back behind each template (big type reads on bright footage, dense panels need more). */
const FOOTAGE_DIM: Record<string, number> = {kinetic: 0.28, title: 0.38, chapter: 0.3, outro: 0.45, broll: 0.2, quote: 0.55, claim: 0.5, headline: 0.52, stat: 0.5, bullets: 0.55, countdown: 0.5, takeaway: 0.6, timeline: 0.62, versus: 0.62, sources: 0.62, bars: 0.64};

const SceneFrame: React.FC<{scene: SceneData; timeline: Timeline}> = ({scene, timeline}) => {
  const Comp = SCENES[scene.template] ?? KineticScene;
  // the claim card colours itself by verdict; everything else follows its section colour
  const accent = scene.template === 'claim' ? accentFor(scene.section, scene.data?.status) : accentFor(scene.section, null);
  const status = scene.status && scene.status in STATUS ? (scene.status as Status) : null;
  const showStatus = status && scene.template !== 'claim' && scene.template !== 'title' && scene.template !== 'chapter' && scene.template !== 'outro';
  return (
    <AbsoluteFill style={{background: C.ink}}>
      {scene.footage ? (
        <>
          <FootageBackdrop src={scene.footage.src} kind={scene.footage.kind} durationInFrames={scene.durationFrames} seed={scene.seed} accent={accent} dim={FOOTAGE_DIM[scene.template] ?? 0.5} />
          {/* focus pool behind the type, so the picture stays bright at the edges */}
          <AbsoluteFill style={{background: `radial-gradient(70% 58% at 50% 48%, ${hexA(C.ink, 0.38)} 0%, ${hexA(C.ink, 0.12)} 60%, transparent 100%)`}} />
        </>
      ) : (
        <>
          <CameraDrift durationInFrames={scene.durationFrames} seed={scene.seed} amount={0.05}>
            <Background kind={scene.background} accent={accent} seed={scene.seed} />
          </CameraDrift>
          {/* soft scrim: keeps type readable over the brighter backgrounds (sun, rays) */}
          <AbsoluteFill style={{background: `radial-gradient(75% 60% at 50% 46%, ${hexA(C.ink, scene.background === 'skyline' ? 0.72 : 0.42)} 0%, ${hexA(C.ink, 0.2)} 60%, transparent 100%)`}} />
        </>
      )}
      <CameraDrift durationInFrames={scene.durationFrames} seed={scene.seed + 1} amount={0.022}>
        <Comp scene={scene} accent={accent} today={timeline.today} />
      </CameraDrift>
      <Atmosphere />
      <BrandBug name={timeline.brand.name} />
      {showStatus ? <StatusTag status={status} /> : null}
      <SourceStrip source={scene.source} />
      {scene.footage ? <FootageCredit credit={scene.footage.credit} /> : null}
      {timeline.burnCaptions ? <Captions phrases={scene.phrases} accent={accent} emphasis={scene.emphasisText} /> : null}
      <SceneOut durationInFrames={scene.durationFrames} />
      <TransitionIn kind={scene.transition} accent={accent} />
    </AbsoluteFill>
  );
};

export const Video: React.FC<{timeline: Timeline}> = ({timeline}) => {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill style={{background: C.ink}}>
      {timeline.scenes.map((scene) => (
        <Sequence key={scene.n} from={scene.startFrame} durationInFrames={scene.durationFrames} name={`${scene.n} ${scene.template}`}>
          <SceneFrame scene={scene} timeline={timeline} />
        </Sequence>
      ))}
      <ProgressRail frame={frame} total={timeline.totalFrames} chapters={timeline.chapters} />
    </AbsoluteFill>
  );
};
