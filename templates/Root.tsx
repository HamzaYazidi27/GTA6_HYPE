import React from 'react';
import {Composition} from 'remotion';
import {Short} from './shorts/Short';
import {Thumbnail, ThumbProps} from './thumbnails/Thumbnail';
import {Video} from './video/Video';
import type {Timeline} from './video/shared';
import {SAMPLE_TIMELINE} from './video/sample';

const SAMPLE_THUMB: ThumbProps = {concept: 'stamp', lines: ['SMALL-TIME', 'CRIMINALS'], accent: 'CRIMINALS', status: 'reported', motif: 'sunset', bigText: '', label: '', brand: 'NEON DISPATCH', color: 'coral', calendar: null};

const meta = ({props}: {props: {timeline: Timeline}}) => ({
  durationInFrames: Math.max(1, props.timeline.totalFrames),
  fps: props.timeline.fps,
  width: props.timeline.width,
  height: props.timeline.height,
});

export const Root: React.FC = () => (
  <>
    <Composition id="Video" component={Video} durationInFrames={SAMPLE_TIMELINE.totalFrames} fps={30} width={1920} height={1080} defaultProps={{timeline: SAMPLE_TIMELINE}} calculateMetadata={meta} />
    <Composition id="Short" component={Short} durationInFrames={SAMPLE_TIMELINE.totalFrames} fps={30} width={1080} height={1920} defaultProps={{timeline: {...SAMPLE_TIMELINE, format: 'short' as const, width: 1080, height: 1920}}} calculateMetadata={meta} />
    <Composition id="Thumbnail" component={Thumbnail} durationInFrames={1} fps={30} width={1280} height={720} defaultProps={SAMPLE_THUMB} />
  </>
);
