import React from 'react';
import {AbsoluteFill, OffthreadVideo, staticFile, useCurrentFrame} from 'remotion';
import {F, useLayout} from '../motion_graphics/theme';
import type {SceneProps} from './shared';

type Card = {from: number; to: number; headline: string; item?: number; gameLabel?: string; value?: string; unit?: string};
type Credit = {from: number; label: string};

export const ReplayShot: React.FC<SceneProps & {captions: boolean}> = ({scene, brand, captions}) => {
  const frame = useCurrentFrame();
  const {u} = useLayout();
  const cards: Card[] = scene.data.beats;
  const card = cards.find(c => frame >= c.from && frame < c.to) ?? cards[0];
  const credits: Credit[] = scene.data.credits;
  const credit = credits.filter(c => c.from <= frame).at(-1)?.label;
  const phrase = scene.phrases.find(p => frame >= p.s && frame < p.e);
  const accent = '#ff7492';
  const label = card?.headline ?? '';
  const hasRank = card?.item != null;
  const pos = (top: number, left = 62): React.CSSProperties => ({position:'absolute', top:top*u, left:left*u, right:66*u});
  const headingSize = Math.min(126, (hasRank ? 1500 : 1800) / Math.max(12, label.length));
  return <AbsoluteFill style={{background:'#111217', color:'#fbf6e9', fontFamily:F.body, overflow:'hidden'}}>
    <div style={{...pos(108), display:'flex', justifyContent:'space-between', alignItems:'center', fontSize:28*u, fontWeight:700}}>
      <span>{(brand?.name ?? 'Ghost Plates').toUpperCase()}</span><span style={{color:'#bbbdbd'}}>{card?.gameLabel ?? scene.data.sectionLabel}{credit?.startsWith('@') ? ' / TIKTOK' : ''}</span>
    </div>
    <div style={{...pos(180), height:3*u, background:'#48494d'}} />
    <div style={{...pos(197), height:178*u, display:'flex', alignItems:'center', gap:24*u, fontFamily:F.display, fontWeight:900, lineHeight:.95, textTransform:'uppercase'}}>
      {hasRank && <span style={{fontSize:130*u, color:accent}}>#{card.item}</span>}
      <span style={{fontSize:headingSize*u, overflow:'hidden', display:'-webkit-box', WebkitLineClamp:2, WebkitBoxOrient:'vertical', overflowWrap:'anywhere'}}>{label}</span>
    </div>
    <div style={{position:'absolute', top:'20.3125%', width:'100%', height:'50%', overflow:'hidden'}}>
      {scene.footage && <OffthreadVideo muted src={staticFile(scene.footage.src)} style={{width:'100%',height:'100%',objectFit:'cover'}} />}
    </div>
    <div style={{position:'absolute', top:'70.3125%', width:'100%', height:6*u, background:accent}} />
    {card?.value && <div style={{...pos(1384), display:'flex', alignItems:'baseline', gap:19*u}}>
      <b style={{fontFamily:F.display, fontWeight:900, fontSize:Math.min(146, 1150/card.value.length)*u, lineHeight:1}}>{card.value}</b>
      <b style={{fontSize:31*u,color:accent}}>{card.unit}</b>
    </div>}
    <div style={{...pos(card?.value ? 1550 : 1400), fontSize:34*u, fontWeight:700, color:'#c6c7c7', whiteSpace:'nowrap', textOverflow:'ellipsis', overflow:'hidden'}}>{credit}</div>
    {captions && phrase && <div style={{...pos(card?.value ? 1630 : 1490), right:92*u, fontSize:(card?.value ? 47 : 65)*u, fontWeight:700, lineHeight:1.12}}>{phrase.words.map(w => w.w).join(' ')}</div>}
  </AbsoluteFill>;
};
