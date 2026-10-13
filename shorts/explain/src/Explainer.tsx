import React from 'react';
import {
  AbsoluteFill,
  Audio,
  Freeze,
  Img,
  OffthreadVideo,
  Sequence,
  interpolate,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';
import './fonts';
import {Captions} from './parts/Captions';
import {DiceChart} from './parts/DiceChart';
import {Tags} from './parts/Tags';
import {Hook, Texts} from './parts/Texts';
import type {Beat, Plan} from './schema';
import {COLOR, FONT, H, OUTLINE, W} from './theme';

// the voice speaks in these spans (seconds of the short): the game ducks under them
type Spans = [number, number][];

const ducked = (t: number, spans: Spans, duck: number) => {
  let k = 1;
  for (const [a, b] of spans) {
    // ~150 ms ramps either side of a line
    const v = interpolate(t, [a - 0.15, a, b, b + 0.15], [1, duck, duck, 1], {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
    });
    k = Math.min(k, v);
  }
  return k;
};

// a picture filling the frame: footage (frozen from `freeze` on) or a still, with an optional slow zoom
const Picture: React.FC<{
  src: string;
  from: number;
  beat: Beat;
  width: number;
  height: number;
  volume: (f: number) => number;
}> = ({src, from, beat, width, height, volume}) => {
  const {fps} = useVideoConfig();
  const t = useCurrentFrame() / fps;
  const z = beat.zoom;
  const s = z
    ? interpolate(t, [z.at, z.at + z.dur], [1, z.scale], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'})
    : 1;
  const style: React.CSSProperties = {
    width,
    height,
    objectFit: 'cover',
    transform: `scale(${s})`,
    transformOrigin: z ? `${z.x}px ${z.y}px` : 'center',
  };
  if (src.endsWith('.png') || src.endsWith('.jpg')) {
    return <Img src={staticFile(src)} style={style} />;
  }
  const video = <OffthreadVideo src={staticFile(src)} startFrom={Math.round(from * fps)} volume={volume} style={style} />;
  if (beat.freeze !== undefined && t >= beat.freeze) {
    return <Freeze frame={Math.round(beat.freeze * fps)}>{video}</Freeze>;
  }
  return video;
};

const HalfLabel: React.FC<{text: string; top: number; color: string}> = ({text, top, color}) => (
  <div
    style={{
      position: 'absolute',
      top,
      left: 40,
      fontFamily: FONT.body,
      fontWeight: 800,
      fontSize: 40,
      color,
      background: COLOR.shade,
      padding: '6px 16px',
      borderRadius: 10,
      textShadow: OUTLINE,
    }}
  >
    {text}
  </div>
);

const BeatView: React.FC<{beat: Beat; start: number; spans: Spans; duck: number}> = ({beat, start, spans, duck}) => {
  const {fps} = useVideoConfig();
  const volume = (f: number) => beat.game * ducked(start + f / fps, spans, duck);
  let body: React.ReactNode = null;
  if (beat.kind === 'card' && beat.chart) {
    body = <DiceChart spec={beat.chart} />;
  } else if (beat.kind === 'split' && beat.src && beat.srcBottom) {
    const half = H / 2;
    body = (
      <>
        <div style={{position: 'absolute', top: 0, width: W, height: half, overflow: 'hidden'}}>
          <Picture src={beat.src} from={beat.from} beat={beat} width={W} height={half} volume={() => 0} />
        </div>
        <div style={{position: 'absolute', top: half, width: W, height: half, overflow: 'hidden'}}>
          <Picture src={beat.srcBottom} from={beat.fromBottom} beat={beat} width={W} height={half} volume={volume} />
        </div>
        <div style={{position: 'absolute', top: half - 2, width: W, height: 4, background: '#000'}} />
        {beat.labels && (
          <>
            <HalfLabel text={beat.labels[0]} top={170} color={beat.labelColors?.[0] ?? COLOR.before} />
            <HalfLabel text={beat.labels[1]} top={half + 40} color={beat.labelColors?.[1] ?? COLOR.now} />
          </>
        )}
      </>
    );
  } else if (beat.src) {
    body = <Picture src={beat.src} from={beat.from} beat={beat} width={W} height={H} volume={volume} />;
  }
  return (
    <AbsoluteFill style={{background: '#000', overflow: 'hidden'}}>
      {body}
      <Tags tags={beat.tags} />
      <Texts texts={beat.texts} />
    </AbsoluteFill>
  );
};

export const Explainer: React.FC<Plan> = (plan) => {
  const {fps} = useVideoConfig();
  const spans: Spans = plan.lines.filter((l) => l.length > 0).map((l) => [l[0].start, l[l.length - 1].end]);
  let t = 0;
  return (
    <AbsoluteFill style={{background: '#000'}}>
      {plan.beats.map((beat) => {
        const from = Math.round(t * fps);
        const start = t;
        t += beat.dur;
        return (
          <Sequence key={beat.id} from={from} durationInFrames={Math.round(t * fps) - from} name={beat.id}>
            <BeatView beat={beat} start={start} spans={spans} duck={plan.duck} />
          </Sequence>
        );
      })}
      {plan.voice.map((v) => (
        <Sequence key={v.file} from={Math.round(v.start * fps)} name={v.file}>
          <Audio src={staticFile(v.file)} />
        </Sequence>
      ))}
      <Captions lines={plan.lines} />
      <Hook text={plan.hook.text} until={plan.hook.until} />
    </AbsoluteFill>
  );
};
