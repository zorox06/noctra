'use client';
import {useEffect,useRef,useState} from 'react';
import {CandlestickSeries,ColorType,createChart,createSeriesMarkers,HistogramSeries,LineSeries,LineStyle,type UTCTimestamp} from 'lightweight-charts';
import type {Candle,Run,Trade} from '@/lib/types';

export type ChartFocus={time?:number;fit?:boolean;token:number};
function makeChart(host:HTMLDivElement){
  const chart=createChart(host,{
    autoSize:true,layout:{background:{type:ColorType.Solid,color:'#070707'},textColor:'#898989',fontFamily:'Arial',fontSize:11,attributionLogo:true},
    grid:{vertLines:{color:'#151515',style:LineStyle.Dotted},horzLines:{color:'#1b1b1b',style:LineStyle.Dotted}},
    rightPriceScale:{borderColor:'#202020',scaleMargins:{top:.14,bottom:.21}},
    timeScale:{borderColor:'#202020',timeVisible:true,secondsVisible:false},
    crosshair:{vertLine:{color:'#777',labelBackgroundColor:'#333'},horzLine:{color:'#777',labelBackgroundColor:'#333'}},
  });
  const series=chart.addSeries(CandlestickSeries,{upColor:'#2ebd85',downColor:'#f6465d',wickUpColor:'#2ebd85',wickDownColor:'#f6465d',borderVisible:false});
  const volume=chart.addSeries(HistogramSeries,{priceFormat:{type:'volume'},priceScaleId:'volume',lastValueVisible:false,priceLineVisible:false});
  volume.priceScale().applyOptions({scaleMargins:{top:.85,bottom:0},visible:false});
  const fast=chart.addSeries(LineSeries,{color:'#ddd',lineWidth:1,lastValueVisible:false,priceLineVisible:false});
  const slow=chart.addSeries(LineSeries,{color:'#777',lineWidth:1,lineStyle:LineStyle.Dashed,lastValueVisible:false,priceLineVisible:false});
  const markers=createSeriesMarkers(series,[]);
  return {chart,series,volume,fast,slow,markers};
}

export default function MarketChart(props:{
  candles:Candle[];run:Run|null;selectedTrade:number|null;onTradeSelect:(id:number)=>void;replayIndex?:number;
  datasetId:string;focus?:ChartFocus;canLoadOlder:boolean;canLoadNewer:boolean;
  onLoadOlder:()=>Promise<void>;onLoadNewer:()=>Promise<void>;onVisibleRange:(from:number,to:number)=>void;
}){
  const host=useRef<HTMLDivElement>(null),model=useRef<ReturnType<typeof makeChart>|null>(null);
  const latest=useRef(props);latest.current=props;
  const previous=useRef<{first:number;last:number;count:number;dataset:string}|null>(null);
  const changing=useRef(false),fetching=useRef(false),interacted=useRef(false),appliedFocus=useRef(-1);
  const dragging=useRef(false);
  const [flushVersion,setFlushVersion]=useState(0);
  useEffect(()=>{
    if(!host.current)return;
    const value=makeChart(host.current);model.current=value;
    const finishDrag=()=>{if(dragging.current){dragging.current=false;setFlushVersion(v=>v+1);}};
    window.addEventListener('pointerup',finishDrag,true);
    window.addEventListener('pointercancel',finishDrag,true);
    window.addEventListener('blur',finishDrag);
    value.chart.subscribeClick(event=>{
      if(typeof event.time!=='number')return;
      const trade=latest.current.run?.trades.find(t=>t.entry_time===event.time||t.exit_time===event.time);
      if(trade)latest.current.onTradeSelect(trade.id);
    });
    value.chart.timeScale().subscribeVisibleLogicalRangeChange(range=>{
      const visible=value.chart.timeScale().getVisibleRange();
      if(visible&&typeof visible.from==='number'&&typeof visible.to==='number')latest.current.onVisibleRange(visible.from,visible.to);
      const current=latest.current;
      if(!range||changing.current||fetching.current||!interacted.current||current.replayIndex!=null)return;
      const older=range.from<80&&current.canLoadOlder;
      const newer=range.to>current.candles.length-80&&current.canLoadNewer;
      if(older||newer){interacted.current=false;fetching.current=true;void (older?current.onLoadOlder():current.onLoadNewer()).finally(()=>{fetching.current=false;});}
    });
    return()=>{window.removeEventListener('pointerup',finishDrag,true);window.removeEventListener('pointercancel',finishDrag,true);window.removeEventListener('blur',finishDrag);value.chart.remove();model.current=null;previous.current=null;};
  },[]);

  useEffect(()=>{
    // The chart's drag anchor uses the old data length. Apply a fetched page
    // after pointer-up so that a continuing drag cannot overwrite our restored range.
    const value=model.current;if(!value||!props.candles.length||dragging.current)return;
    changing.current=true;
    const data=props.replayIndex==null?props.candles:props.candles.slice(0,props.replayIndex+1);
    const oldRange=value.chart.timeScale().getVisibleLogicalRange(),old=previous.current;
    let overlap=old?.dataset===props.datasetId?data.findIndex(r=>r.time===old.first):NaN;
    if(old&&overlap===-1){const end=data.findIndex(r=>r.time===old.last);overlap=end<0?NaN:end-(old.count-1);}
    value.series.setData(data.map(r=>({...r,time:r.time as UTCTimestamp})));
    value.volume.setData(data.map(r=>({time:r.time as UTCTimestamp,value:r.volume,color:r.close>=r.open?'#2ebd8538':'#f6465d38'})));
    const run=props.run;
    const indices=new Map((run?.indicator_times??run?.equity.map(p=>p.time)??[]).map((time,i)=>[time,i]));
    for(const key of ['fast','slow'] as const)value[key].setData(run?.indicators?data.flatMap(r=>{const i=indices.get(r.time),n=i==null?undefined:run.indicators[key][i];return n==null?[]:[{time:r.time as UTCTimestamp,value:n}];}):[]);
    const marks=(run?.trades??[]).flatMap((trade:Trade)=>[
      {time:trade.entry_time as UTCTimestamp,position:trade.side===1?'belowBar' as const:'aboveBar' as const,color:'#eee',shape:trade.side===1?'arrowUp' as const:'arrowDown' as const,text:`#${trade.id}`},
      {time:trade.exit_time as UTCTimestamp,position:trade.side===1?'aboveBar' as const:'belowBar' as const,color:'#888',shape:'circle' as const,text:''},
    ]).filter(m=>m.time>=data[0].time&&m.time<=data[data.length-1].time).sort((a,b)=>a.time-b.time);
    value.markers.setMarkers(marks);
    if(props.focus&&props.focus.token!==appliedFocus.current){
      appliedFocus.current=props.focus.token;
      if(props.focus.fit)value.chart.timeScale().fitContent();
      else{const found=data.findIndex(r=>r.time>=(props.focus?.time??data[data.length-1].time)),index=found<0?data.length-1:found;
        const from=Math.max(0,Math.min(index-90,data.length-180));value.chart.timeScale().setVisibleLogicalRange({from,to:from+180});}
      interacted.current=false;
    }else if(oldRange&&Number.isFinite(overlap)){
      // Shift indices when older bars are prepended, preserving the viewport.
      value.chart.timeScale().setVisibleLogicalRange({from:oldRange.from+overlap,to:oldRange.to+overlap});
    }else{value.chart.timeScale().setVisibleLogicalRange({from:Math.max(0,data.length-180),to:data.length+5});interacted.current=false;}
    previous.current={first:data[0].time,last:data[data.length-1].time,count:data.length,dataset:props.datasetId};changing.current=false;
  },[props.candles,props.run,props.replayIndex,props.datasetId,props.focus,flushVersion]);

  useEffect(()=>{
    const trade=props.run?.trades.find(t=>t.id===props.selectedTrade);
    if(!trade||!model.current||!props.candles.length)return;
    if(trade.entry_time<props.candles[0].time||trade.entry_time>props.candles[props.candles.length-1].time)return;
    const interval=props.candles.length>1?props.candles[1].time-props.candles[0].time:3600;
    changing.current=true;model.current.chart.timeScale().setVisibleRange({from:(trade.entry_time-20*interval) as UTCTimestamp,to:(trade.exit_time+20*interval) as UTCTimestamp});changing.current=false;
  },[props.selectedTrade,props.run]);
  return <div ref={host} className="market-canvas" onPointerDown={()=>{interacted.current=true;dragging.current=true;}} onWheel={()=>{interacted.current=true;}} aria-label="Interactive Delta candlestick chart. Drag right to browse older prices; history loads automatically."/>;
}
