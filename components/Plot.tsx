'use client';
import { useEffect, useRef, useState } from 'react';
import type { Data, Layout, PlotMouseEvent } from 'plotly.js';
const DEFAULT_LAYOUT: Partial<Layout> = {};

export default function Plot({ data, layout = DEFAULT_LAYOUT, onClick }: { data: Data[]; layout?: Partial<Layout>; onClick?: (event: PlotMouseEvent) => void }) {
  const host = useRef<HTMLDivElement>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let disposed = false;
    let observer: ResizeObserver | undefined;
    let cleanup: (() => void) | undefined;
    async function draw() {
      try {
        const Plotly = await import('plotly.js-dist-min');
        if (disposed || !host.current) return;
        // Each effect owns its own plot node. A late completion from an old
        // render must never purge a newer plot mounted into the same host.
        const element = document.createElement('div');
        element.style.cssText = 'width:100%;height:100%;min-height:0';
        host.current.replaceChildren(element);
        const plot = await Plotly.react(element, data, {
          paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
          font: { color: '#949494', size: 11, family: 'Arial' },
          margin: { l: 48, r: 18, t: 15, b: 32 }, autosize: true, showlegend: false,
          xaxis: { gridcolor: '#191919', zerolinecolor: '#333', showgrid: false, automargin: true },
          yaxis: { gridcolor: '#191919', zerolinecolor: '#333', automargin: true },
          hoverlabel: { bgcolor: '#181818', bordercolor: '#444', font: { color: '#fff' } },
          ...layout,
        }, { responsive: true, displaylogo: false, displayModeBar: 'hover', modeBarButtonsToRemove: ['sendChartToCloud'] });
        if (disposed) { Plotly.purge(element); element.remove(); return; }
        if (onClick) plot.on('plotly_click', onClick);
        observer = new ResizeObserver(() => {
          if (!disposed && element.isConnected && element.clientWidth > 0 && element.clientHeight > 0) {
            void Promise.resolve(Plotly.Plots.resize(element)).catch(() => {
              // A panel can be hidden or unmounted while Plotly is resizing.
              // Its next visible ResizeObserver notification retries sizing.
            });
          }
        });
        observer.observe(element);
        cleanup = () => { Plotly.purge(element); element.remove(); };
      } catch (e) { if (!disposed) setError(e instanceof Error ? e.message : 'Chart rendering failed'); }
    }
    void draw();
    return () => { disposed = true; observer?.disconnect(); cleanup?.(); };
  }, [data, layout, onClick]);
  if (error) return <div className="chart-error">Chart unavailable: {error}. Use the data tables below.</div>;
  return <div ref={host} className="plot" aria-label="Interactive research chart" />;
}
