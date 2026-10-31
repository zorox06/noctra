export function BrandMark({ className = '' }: { className?: string }) {
  return <svg className={className} viewBox="0 0 48 48" fill="none" aria-hidden="true" focusable="false"><path d="M8 38V10h7l18 19V10h7v28h-7L15 19v19H8Z" fill="currentColor"/></svg>;
}

export function BrandWordmark() {
  return <svg viewBox="0 0 240 32" fill="none" aria-hidden="true" focusable="false"><path d="M4 30V2L28 30V2 M49 2H61Q69 2 69 10V22Q69 30 61 30H49Q41 30 41 22V10Q41 2 49 2Z M109 6Q106 2 101 2H93Q85 2 85 10V22Q85 30 93 30H101Q106 30 109 26 M124 2H152 M138 2V30 M168 30V2H184Q192 2 192 10Q192 18 184 18H168 M181 18L194 30 M208 30L222 2L236 30 M213 21H231" stroke="currentColor" strokeWidth="3.2" strokeLinecap="square" strokeLinejoin="miter"/></svg>;
}
