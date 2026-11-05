type P = { className?: string };
const base = "inline-block shrink-0";

export const WalkIcon = ({ className = "h-4 w-4" }: P) => (
  <svg viewBox="0 0 24 24" fill="currentColor" className={`${base} ${className}`} aria-hidden>
    <circle cx="13" cy="4" r="2" />
    <path d="M10.5 8.5 8 21h2.2l1.6-7 2.2 2v5h2v-6.5l-2.2-2.1.6-3A6.5 6.5 0 0 0 19 12v-2a4.7 4.7 0 0 1-4-2.3l-1-1.6A2 2 0 0 0 12.3 5c-.3 0-.5 0-.8.1L6 7.4V12h2V8.7z" />
  </svg>
);

export const CarIcon = ({ className = "h-4 w-4" }: P) => (
  <svg viewBox="0 0 24 24" fill="currentColor" className={`${base} ${className}`} aria-hidden>
    <path d="M18.9 6a1.5 1.5 0 0 0-1.4-1h-11a1.5 1.5 0 0 0-1.4 1L3 12v8a1 1 0 0 0 1 1h1a1 1 0 0 0 1-1v-1h12v1a1 1 0 0 0 1 1h1a1 1 0 0 0 1-1v-8zM6.5 16a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3m11 0a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3M5 11l1.5-4.5h11L19 11z" />
  </svg>
);

export const BusIcon = ({ className = "h-4 w-4" }: P) => (
  <svg viewBox="0 0 24 24" fill="currentColor" className={`${base} ${className}`} aria-hidden>
    <path d="M4 16c0 .9.4 1.7 1 2.2V20a1 1 0 0 0 1 1h1a1 1 0 0 0 1-1v-1h8v1a1 1 0 0 0 1 1h1a1 1 0 0 0 1-1v-1.8c.6-.5 1-1.3 1-2.2V6c0-3.5-3.6-4-8-4S4 2.5 4 6zm3.5 1a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3m9 0a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3m1.5-6H6V6h12z" />
  </svg>
);

export const PinIcon = ({ className = "h-4 w-4" }: P) => (
  <svg viewBox="0 0 24 24" fill="currentColor" className={`${base} ${className}`} aria-hidden>
    <path d="M12 2a7 7 0 0 0-7 7c0 5.2 7 13 7 13s7-7.8 7-13a7 7 0 0 0-7-7m0 9.5a2.5 2.5 0 1 1 0-5 2.5 2.5 0 0 1 0 5" />
  </svg>
);

export const SwapIcon = ({ className = "h-4 w-4" }: P) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"
    className={`${base} ${className}`} aria-hidden>
    <path d="M7 4v16M3 8l4-4 4 4M17 20V4M13 16l4 4 4-4" />
  </svg>
);

export const ClockIcon = ({ className = "h-4 w-4" }: P) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"
    className={`${base} ${className}`} aria-hidden>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 7v5l3 2" />
  </svg>
);
