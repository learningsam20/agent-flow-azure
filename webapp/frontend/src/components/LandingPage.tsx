import { useState } from "react";

type LandingPageProps = {
  onSignIn: () => void | Promise<void>;
};

export function LandingPage({ onSignIn }: LandingPageProps) {
  const [signingIn, setSigningIn] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSignIn() {
    if (signingIn) return;
    setSigningIn(true);
    setError(null);
    try {
      await onSignIn();
    } catch (e) {
      setError((e as Error).message || "Sign in failed");
      setSigningIn(false);
    }
  }

  return (
    <div className="min-h-screen flex flex-col landing-root text-sand overflow-x-hidden">
      <header className="relative z-20 flex items-center justify-between px-5 md:px-10 py-5">
        <div className="flex items-center gap-2.5">
          <div className="w-9 h-9 rounded-lg bg-sand/10 border border-sand/15 grid place-items-center">
            <svg width="18" height="18" viewBox="0 0 32 32" fill="none" aria-hidden>
              <path
                d="M8 22V10l8 6 8-6v12"
                stroke="#f7f4ee"
                strokeWidth="2.4"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
              <circle cx="16" cy="16" r="2" fill="#7ec8b0" />
            </svg>
          </div>
          <span className="font-display text-lg tracking-tight">Agent Flow</span>
        </div>
      </header>

      <main className="relative z-10 flex-1 flex flex-col">
        <section className="relative min-h-[calc(100vh-4.5rem)] flex flex-col justify-end md:justify-center px-5 md:px-10 pb-16 md:pb-24 pt-8">
          <div className="landing-hero-copy relative z-10 max-w-xl">
            <h1 className="font-display text-[clamp(2.6rem,7vw,4.4rem)] leading-[0.95] tracking-tight text-sand landing-fade">
              Agent Flow
            </h1>
            <p className="mt-5 text-lg md:text-xl text-sand/75 max-w-md leading-relaxed landing-fade landing-delay-1">
              Orchestrate agents, tools, and human review on a live workflow canvas.
            </p>
            <div className="mt-8 flex flex-wrap items-center gap-3 landing-fade landing-delay-2">
              <button
                type="button"
                className="landing-cta"
                disabled={signingIn}
                onClick={() => void handleSignIn()}
              >
                {signingIn ? "Signing in…" : "Sign in"}
              </button>
              {error && <span className="text-sm text-ember/90">{error}</span>}
            </div>
          </div>

          <div className="landing-visual absolute inset-0 pointer-events-none" aria-hidden>
            <svg className="absolute inset-0 w-full h-full" viewBox="0 0 1200 800" preserveAspectRatio="xMidYMid slice">
              <defs>
                <linearGradient id="lg-edge" x1="0" y1="0" x2="1" y2="0">
                  <stop offset="0%" stopColor="#176a86" stopOpacity="0.15" />
                  <stop offset="50%" stopColor="#7ec8b0" stopOpacity="0.55" />
                  <stop offset="100%" stopColor="#176a86" stopOpacity="0.15" />
                </linearGradient>
                <filter id="soft">
                  <feGaussianBlur stdDeviation="1.2" />
                </filter>
              </defs>
              <path className="landing-edge" d="M180 220 C 320 180, 380 280, 520 260" stroke="url(#lg-edge)" strokeWidth="2" fill="none" />
              <path className="landing-edge landing-delay-1" d="M520 260 C 620 240, 680 320, 780 300" stroke="url(#lg-edge)" strokeWidth="2" fill="none" />
              <path className="landing-edge landing-delay-2" d="M520 260 C 560 360, 540 420, 620 480" stroke="url(#lg-edge)" strokeWidth="2" fill="none" />
              <path className="landing-edge landing-delay-3" d="M780 300 C 860 280, 920 340, 1020 320" stroke="url(#lg-edge)" strokeWidth="2" fill="none" />
              <path className="landing-edge landing-delay-2" d="M620 480 C 720 500, 800 440, 880 460" stroke="url(#lg-edge)" strokeWidth="1.5" fill="none" />

              <g className="landing-node">
                <rect x="120" y="185" width="120" height="70" rx="14" fill="#123047" stroke="#2a8aad" strokeWidth="1.5" />
                <text x="180" y="218" textAnchor="middle" fill="#a8d4e4" fontSize="12" fontFamily="Source Sans 3, sans-serif">Triage</text>
                <text x="180" y="236" textAnchor="middle" fill="#7ec8b0" fontSize="10" fontFamily="Source Sans 3, sans-serif">Running</text>
              </g>
              <g className="landing-node landing-delay-1">
                <rect x="460" y="225" width="120" height="70" rx="14" fill="#0f3d32" stroke="#2a6450" strokeWidth="1.5" />
                <text x="520" y="258" textAnchor="middle" fill="#b8e0d0" fontSize="12" fontFamily="Source Sans 3, sans-serif">Parallel</text>
                <text x="520" y="276" textAnchor="middle" fill="#7ec8b0" fontSize="10" fontFamily="Source Sans 3, sans-serif">Succeeded</text>
              </g>
              <g className="landing-node landing-delay-2">
                <rect x="720" y="265" width="120" height="70" rx="14" fill="#3d2414" stroke="#b84f1f" strokeWidth="2" />
                <circle cx="744" cy="300" r="4" fill="#b84f1f" className="landing-pulse" />
                <text x="790" y="298" textAnchor="middle" fill="#f0c4a8" fontSize="12" fontFamily="Source Sans 3, sans-serif">HITL</text>
                <text x="790" y="316" textAnchor="middle" fill="#e8a070" fontSize="10" fontFamily="Source Sans 3, sans-serif">Needs review</text>
              </g>
              <g className="landing-node landing-delay-3">
                <rect x="960" y="285" width="120" height="70" rx="14" fill="#123047" stroke="#176a86" strokeWidth="1.5" opacity="0.85" />
                <text x="1020" y="318" textAnchor="middle" fill="#a8d4e4" fontSize="12" fontFamily="Source Sans 3, sans-serif">Synthesize</text>
                <text x="1020" y="336" textAnchor="middle" fill="#6a9aab" fontSize="10" fontFamily="Source Sans 3, sans-serif">Pending</text>
              </g>
              <g className="landing-node landing-delay-2">
                <rect x="560" y="445" width="120" height="70" rx="14" fill="#123047" stroke="#176a86" strokeWidth="1.5" opacity="0.7" />
                <text x="620" y="478" textAnchor="middle" fill="#a8d4e4" fontSize="12" fontFamily="Source Sans 3, sans-serif">FinOps</text>
                <text x="620" y="496" textAnchor="middle" fill="#6a9aab" fontSize="10" fontFamily="Source Sans 3, sans-serif">Path B</text>
              </g>
            </svg>
            <div className="landing-visual-fade" />
          </div>
        </section>

        <section className="relative z-10 border-t border-sand/10 bg-[#0a1520]/90 px-5 md:px-10 py-14 md:py-20">
          <div className="max-w-3xl">
            <h2 className="font-display text-2xl md:text-3xl tracking-tight text-sand">
              Built for multi-agent work
            </h2>
            <p className="mt-4 text-sand/65 text-base md:text-lg leading-relaxed max-w-2xl">
              Run specialized agents with tools, branch through parallel paths, pause for
              human-in-the-loop approval, then resume with a full audit trail — all visible on one
              live workflow canvas.
            </p>
            <ul className="mt-8 grid gap-4 sm:grid-cols-3 text-sm text-sand/70">
              <li className="border-l-2 border-sea/50 pl-3">
                <div className="font-semibold text-sand/90">Agents + tools</div>
                <div className="mt-1 text-sand/55">Triage, security, FinOps, and synthesize with live tool chips.</div>
              </li>
              <li className="border-l-2 border-moss/50 pl-3">
                <div className="font-semibold text-sand/90">Parallel paths</div>
                <div className="mt-1 text-sand/55">Run Path A / Path B in parallel and join results cleanly.</div>
              </li>
              <li className="border-l-2 border-ember/50 pl-3">
                <div className="font-semibold text-sand/90">Human review</div>
                <div className="mt-1 text-sand/55">Role-scoped approve, reject, or re-route without leaving the graph.</div>
              </li>
            </ul>
          </div>
        </section>
      </main>
    </div>
  );
}
