/** Types and layout wrapper shared by all scene templates. */
import React from 'react';
import {useLayout} from '../motion_graphics/theme';
import type {Brand, Phrase, TransitionKind} from '../motion_graphics/overlays';
import type {BackgroundKind} from '../motion_graphics/backgrounds';

export type SceneData = {
  n: number;
  template: string;
  data: any;
  section: string;
  startFrame: number;
  durationFrames: number;
  background: BackgroundKind;
  transition: TransitionKind;
  status: string | null;
  source: string;
  /** scene-local frames at which list items are spoken (-1 = unknown) */
  cueFrames: number[];
  /** scene-local frame range in which the emphasis phrase is spoken */
  emphasis: {from: number; to: number} | null;
  emphasisText: string;
  phrases: Phrase[];
  seed: number;
  /** path (inside the public dir) of a licensed/operator-supplied image for broll scenes */
  media?: string;
  mediaCredit?: string;
  /** real GTA 6 footage behind the graphics (pre-cut clip or official screenshot) */
  footage?: {src: string; kind: 'video' | 'photo'; credit: string};
  /** left panel of a split screen (comparisons): the other game's footage */
  footageB?: {src: string; kind: 'video' | 'photo'; credit: string};
};

export type Timeline = {
  format: 'long' | 'short';
  fps: number;
  width: number;
  height: number;
  totalFrames: number;
  title: string;
  today: string;
  brand: Brand;
  burnCaptions: boolean;
  chapters: {title: string; startFrame: number}[];
  scenes: SceneData[];
};

export type SceneProps = {scene: SceneData; accent: string; today: string; brand?: Brand};

/** Places scene content inside the title-safe area, clear of the captions zone. */
export const Stage: React.FC<{children: React.ReactNode; align?: 'center' | 'top'}> = ({children, align = 'center'}) => {
  const {pad, safeBottom, safeTop} = useLayout();
  return (
    <div style={{position: 'absolute', left: pad, right: pad, top: safeTop, bottom: safeBottom, display: 'flex', flexDirection: 'column', justifyContent: align === 'center' ? 'center' : 'flex-start', alignItems: 'center'}}>
      {children}
    </div>
  );
};
