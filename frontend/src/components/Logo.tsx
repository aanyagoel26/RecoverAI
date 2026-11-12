export function Logo({ compact = false }: { compact?: boolean }) {
  return (
    <span className="inline-flex items-center gap-2.5">
      <svg viewBox="0 0 32 32" className="h-8 w-8 shrink-0" aria-hidden>
        <rect width="32" height="32" rx="8" fill="#111D37" stroke="#26375C" />
        <path d="M9 22V10h7.2a4.4 4.4 0 0 1 0 8.8H13l5.5 4.2" fill="none" stroke="#3B7BFF" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx="23.5" cy="10" r="2.6" fill="#34D399" />
      </svg>
      {!compact && (
        <span className="leading-none">
          <span className="block text-[15px] font-extrabold tracking-[0.14em] text-white">RECOVER<span className="text-volt-400">AI</span></span>
          <span className="block pt-1 text-[10px] font-medium tracking-wide text-ink-400">Revenue recovery agent</span>
        </span>
      )}
    </span>
  );
}
