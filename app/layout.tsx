import type { Metadata } from 'next';
import './globals.css';
export const metadata: Metadata = { title: 'Noctra — Research Terminal', description: 'Local BTCUSD and ETHUSD quantitative research, backtesting and simulation.' };
export default function Layout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
