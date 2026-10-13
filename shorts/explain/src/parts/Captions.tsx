import React from 'react';
import {AbsoluteFill, useCurrentFrame, useVideoConfig} from 'remotion';
import type {Word} from '../schema';
import {COLOR, FONT, SAFE, W} from '../theme';

const MAX_WORDS = 4;
// a page does not end on these: they belong to the word after them
const LEANS = new Set(
  ('в во из на и а но не до к ко с со по за о об от у же ли что как ' +
    'a an the of to in on at by for from and or but not').split(' '),
);
const leans = (w: Word) => LEANS.has(w.text.toLowerCase().replace(/[^\p{L}]/gu, ''));
const HOLD = 0.35; // a page stays this long after its last word unless the next one starts

// pages of 2-4 words: a sentence is never split across pages with another one, a page ends after
// a comma, colon or dash when it has two words already
export const pages = (lines: Word[][]) => {
  const out: Word[][] = [];
  for (const line of lines) {
    let page: Word[] = [];
    for (const w of line) {
      page.push(w);
      const breaks = /[,:;—–-]$/.test(w.text) || w.text === '—';
      if (page.length >= MAX_WORDS || (breaks && page.length >= 2)) {
        // a full page ending on a preposition hands it to the next one
        const carry = !breaks && page.length > 1 && leans(page[page.length - 1]) ? page.splice(-1) : [];
        out.push(page);
        page = carry;
      }
    }
    if (page.length > 0) {
      // a lone last word joins the page before it, if that one is of this sentence and short enough
      const prev = out[out.length - 1];
      if (page.length === 1 && prev && line.includes(prev[0]) && prev.length < MAX_WORDS) {
        prev.push(...page);
      } else {
        out.push(page);
      }
    }
  }
  return out;
};

// the voice's words at the lower middle, the word being said in the accent colour
export const Captions: React.FC<{lines: Word[][]}> = ({lines}) => {
  const t = useCurrentFrame() / useVideoConfig().fps;
  const all = pages(lines);
  const i = all.findIndex((p, k) => {
    const next = all[k + 1];
    const end = Math.min(p[p.length - 1].end + HOLD, next ? next[0].start : Infinity);
    return t >= p[0].start && t < end;
  });
  if (i < 0) {
    return null;
  }
  return (
    <AbsoluteFill>
      <div
        style={{
          position: 'absolute',
          top: 1170,
          left: SAFE.left,
          width: W - SAFE.left - SAFE.right,
          textAlign: 'center',
          fontFamily: FONT.body,
          fontWeight: 900,
          fontSize: 76,
          lineHeight: 1.12,
          textTransform: 'uppercase',
          WebkitTextStroke: '14px #000',
          paintOrder: 'stroke fill',
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {all[i].map((w, k) => (
          <span key={k} style={{color: t >= w.start && t < w.end + 0.05 ? COLOR.now : COLOR.text}}>
            {(k > 0 ? ' ' : '') + w.text}
          </span>
        ))}
      </div>
    </AbsoluteFill>
  );
};
