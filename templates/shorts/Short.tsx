/**
 * YouTube Shorts composition (1080x1920). The storyboard and scene templates are
 * shared with long-form; `useLayout()` switches every scene to its vertical layout
 * (stacked content, larger type, centred word-pop captions clear of the Shorts UI).
 */
import React from 'react';
import {Video} from '../video/Video';
import type {Timeline} from '../video/shared';

export const Short: React.FC<{timeline: Timeline}> = ({timeline}) => <Video timeline={timeline} />;
