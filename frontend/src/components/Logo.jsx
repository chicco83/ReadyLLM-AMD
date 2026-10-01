// Logo del marchio «ReadyLLM» — SVG vettoriale puro, autonomo, ridimensionabile a piacere
// Design: fondo scuro sfumato ad angoli arrotondati + arco di avanzamento circolare sfumato (pronto/in caricamento) + triangolo play al centro (deploy ed esecuzione con un clic) + punto di stato verde (online)
// size controlla la dimensione complessiva in pixel; l'id dei gradienti usa il prefisso rdl- per evitare conflitti con altri SVG della pagina

export default function Logo({ size = 36, className = '' }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 128 128"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      role="img"
      aria-label="ReadyLLM"
    >
      <defs>
        <linearGradient id="rdl-bg" x1="0" y1="0" x2="128" y2="128" gradientUnits="userSpaceOnUse">
          <stop stopColor="#0b1220" />
          <stop offset="1" stopColor="#16213e" />
        </linearGradient>
        <linearGradient id="rdl-ring" x1="0" y1="0" x2="128" y2="128" gradientUnits="userSpaceOnUse">
          <stop stopColor="#22d3ee" />
          <stop offset="0.5" stopColor="#3b82f6" />
          <stop offset="1" stopColor="#8b5cf6" />
        </linearGradient>
      </defs>

      {/* Fondo scuro sfumato ad angoli arrotondati */}
      <rect x="4" y="4" width="120" height="120" rx="28" fill="url(#rdl-bg)" />

      {/* Arco di avanzamento circolare sfumato: esprime «pronto / in caricamento» */}
      <circle
        cx="64"
        cy="64"
        r="34"
        fill="none"
        stroke="url(#rdl-ring)"
        strokeWidth="8"
        strokeLinecap="round"
        strokeDasharray="163 51"
        transform="rotate(-120 64 64)"
      />

      {/* Triangolo play al centro: deploy / esecuzione con un clic */}
      <path d="M56 50 L82 64 L56 78 Z" fill="#ffffff" />

      {/* Punto di stato verde: online / pronto */}
      <circle cx="88" cy="40" r="6.5" fill="#34d399" />
    </svg>
  )
}
