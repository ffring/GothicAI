import React from 'react';
import {OffthreadVideo, staticFile} from 'remotion';
import {FPS, H, W} from './theme';

export const STING_FRAMES = Math.round(2.2 * FPS);

export const BrandSting: React.FC<{volume: number}> = ({volume}) => (
  <OffthreadVideo src={staticFile('brand/sting.mp4')} volume={volume} style={{width: W, height: H}} />
);
