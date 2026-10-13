import React from 'react';
import {AbsoluteFill, Easing, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import type {BarsCard as Bars, DotsCard as Dots, FormulaCard as Formula, StatsCard as Stats} from '../schema';
import {COLOR, FONT, OUTLINE, SAFE, W} from '../theme';
import {rise, shown} from './anim';

// cards: what has no picture in the world. Numbers are computed here from the plan's inputs, not typed in.

const LEFT = SAFE.left + 30;
const WIDTH = W - SAFE.left - SAFE.right - 60;

const text = (size: number, color: string = COLOR.text, weight = 800): React.CSSProperties => ({
  fontFamily: FONT.body,
  fontWeight: weight,
  fontSize: size,
  color,
  textShadow: OUTLINE,
  fontVariantNumeric: 'tabular-nums',
});

const Source: React.FC<{text: string}> = ({text: s}) => (
  <div style={{position: 'absolute', left: LEFT, top: 1440, width: WIDTH, ...text(28, COLOR.before, 600)}}>{s}</div>
);

const useT = () => useCurrentFrame() / useVideoConfig().fps;

const grow = (t: number, at: number, dur = 0.6) =>
  interpolate(t, [at, at + dur], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.cubic),
  });

// the damage formula: terms appear as they are said and stay; the worked example is computed from its inputs
// the same way the engine does it (damagecalculator.cpp: rounded at each step, protection not below 0,
// at least 5 at the end)
export const FormulaView: React.FC<{spec: Formula}> = ({spec}) => {
  const t = useT();
  const ex = spec.example;
  const num = (v: number, digits = 0) => v.toFixed(digits).replace('.', spec.decimal);
  const base = ex ? Math.round((ex.bow + ex.dex) * ex.draw) : 0;
  const rest = ex ? Math.max(0, base - ex.prot) : 0;
  // one column, in the order it is said: the title, a term per line, the legend, the worked example; the whole
  // of it sits above the captions
  return (
    <AbsoluteFill style={{background: COLOR.card}}>
      <div style={{position: 'absolute', left: LEFT, top: 280, width: WIDTH, display: 'flex', flexDirection: 'column'}}>
        <div style={{opacity: shown(t, 0), ...text(84), fontFamily: FONT.title, marginBottom: 6}}>{spec.title}</div>
        {spec.terms.map((term, i) => (
          <div
            key={i}
            style={{
              opacity: shown(t, term.at),
              transform: `translateY(${rise(t, term.at, 14)}px)`,
              ...text(58, term.color ?? COLOR.text),
              lineHeight: 1.22,
            }}
          >
            {term.text}
          </div>
        ))}
        {spec.legend && (
          <div style={{marginTop: 14, opacity: shown(t, spec.legend.at), ...text(30, COLOR.before, 600), lineHeight: 1.35}}>
            {spec.legend.text}
          </div>
        )}
        {ex && (
          <div style={{marginTop: 22, opacity: shown(t, ex.at)}}>
            <div style={text(46)}>
              ({ex.bow} + {ex.dex}) × {num(ex.draw, 1)} − {ex.prot} = {rest}
            </div>
            {ex.zones.map((z, i) => (
              <div key={i} style={{marginTop: 6, opacity: shown(t, z.at), ...text(46, i === 0 ? COLOR.now : COLOR.text)}}>
                × {num(z.k, z.k % 1 ? 1 : 0)} {z.name} = {Math.max(5, Math.round(rest * z.k))}
              </div>
            ))}
          </div>
        )}
      </div>
      <Source text={spec.source} />
    </AbsoluteFill>
  );
};

// bars by groups, growing as each group is named; the value written over each bar
export const BarsView: React.FC<{spec: Bars}> = ({spec}) => {
  const t = useT();
  const top = 560;
  const bottom = 1060;
  const groupW = WIDTH / spec.groups.length;
  const barW = Math.min(110, (groupW - 60) / spec.series.length);
  return (
    <AbsoluteFill style={{background: COLOR.card}}>
      <div style={{position: 'absolute', left: LEFT, top: 300, width: WIDTH, ...text(60), lineHeight: 1.15}}>{spec.title}</div>
      <div style={{position: 'absolute', left: LEFT, top: 470, display: 'flex', gap: 36}}>
        {spec.series.map((s) => (
          <span key={s.name} style={{display: 'flex', alignItems: 'center', gap: 12, ...text(36, s.color)}}>
            <span style={{width: 34, height: 34, borderRadius: 6, background: s.color}} />
            {s.name}
          </span>
        ))}
      </div>
      <div style={{position: 'absolute', left: LEFT, top: bottom, width: WIDTH, height: 3, background: COLOR.grid}} />
      {spec.groups.map((g, gi) => {
        const x0 = LEFT + gi * groupW + (groupW - barW * spec.series.length - 16 * (spec.series.length - 1)) / 2;
        const k = grow(t, g.at);
        return (
          <React.Fragment key={g.label}>
            {g.values.map((v, si) => {
              const h = ((bottom - top) * v * k) / spec.max;
              const x = x0 + si * (barW + 16);
              return (
                <React.Fragment key={si}>
                  <div
                    style={{
                      position: 'absolute',
                      left: x,
                      top: bottom - h,
                      width: barW,
                      height: h,
                      background: spec.series[si].color,
                      borderRadius: '8px 8px 0 0',
                    }}
                  />
                  <div
                    style={{
                      position: 'absolute',
                      left: x + barW / 2,
                      top: bottom - h - 56,
                      transform: 'translateX(-50%)',
                      opacity: k,
                      whiteSpace: 'nowrap',
                      ...text(36, spec.series[si].color),
                    }}
                  >
                    {spec.format.replace('{v}', String(Number(v.toFixed(2))).replace('.', spec.decimal))}
                  </div>
                </React.Fragment>
              );
            })}
            <div
              style={{
                position: 'absolute',
                left: LEFT + gi * groupW + groupW / 2,
                top: bottom + 18,
                transform: 'translateX(-50%)',
                opacity: shown(t, g.at),
                ...text(44),
              }}
            >
              {g.label}
            </div>
          </React.Fragment>
        );
      })}
      <Source text={spec.source} />
    </AbsoluteFill>
  );
};

// marks filling one by one: the hits of all the shots
export const DotsView: React.FC<{spec: Dots}> = ({spec}) => {
  const t = useT();
  const cols = 6;
  const size = 96;
  const gap = (WIDTH - cols * size) / (cols - 1);
  const filled = Math.min(spec.hits, Math.floor(interpolate(t, [spec.at, spec.at + spec.dur], [0, spec.hits + 0.999], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  })));
  return (
    <AbsoluteFill style={{background: COLOR.card}}>
      <div style={{position: 'absolute', left: LEFT, top: 300, width: WIDTH, ...text(60), lineHeight: 1.15}}>{spec.title}</div>
      {Array.from({length: spec.total}, (_, i) => {
        const r = Math.floor(i / cols);
        const c = i % cols;
        const on = i < filled;
        return (
          <div
            key={i}
            style={{
              position: 'absolute',
              left: LEFT + c * (size + gap),
              top: 470 + r * (size + 22),
              width: size,
              height: size,
              borderRadius: size / 2,
              border: `5px solid ${on ? COLOR.now : COLOR.faint}`,
              background: on ? COLOR.now : 'transparent',
              opacity: shown(t, 0.1 + i * 0.01),
            }}
          />
        );
      })}
      <div style={{position: 'absolute', left: LEFT, top: 1080, width: WIDTH, textAlign: 'center', ...text(72, COLOR.now)}}>
        {spec.caption.replace('{n}', String(filled))}
      </div>
      <Source text={spec.source} />
    </AbsoluteFill>
  );
};

// the character's numbers, then they grey out and a question stands over them
export const StatsView: React.FC<{spec: Stats}> = ({spec}) => {
  const t = useT();
  const grey = interpolate(t, [spec.fadeAt, spec.fadeAt + 0.5], [1, 0.3], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  return (
    <AbsoluteFill style={{background: COLOR.card}}>
      <div
        style={{
          position: 'absolute',
          left: LEFT,
          top: 380,
          width: WIDTH,
          padding: '34px 40px',
          borderRadius: 18,
          border: `2px solid ${COLOR.grid}`,
          opacity: grey,
          filter: `grayscale(${1 - grey})`,
        }}
      >
        {spec.rows.map((row, i) => (
          <div
            key={i}
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              padding: '14px 0',
              opacity: shown(t, 0.2 + i * 0.25),
              ...text(64, row.color ?? COLOR.text),
            }}
          >
            <span>{row.name}</span>
            <span>{row.value}</span>
          </div>
        ))}
      </div>
      <div
        style={{
          position: 'absolute',
          left: LEFT,
          top: 860,
          width: WIDTH,
          textAlign: 'center',
          opacity: shown(t, spec.question.at),
          transform: `translateY(${rise(t, spec.question.at, 20)}px)`,
          ...text(72, COLOR.now),
          fontFamily: FONT.title,
        }}
      >
        {spec.question.text}
      </div>
    </AbsoluteFill>
  );
};
