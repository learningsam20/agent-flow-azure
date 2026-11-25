import { useEffect, useRef, useState } from "react";
import type { MeResponse } from "../lib/api";
import type { ThemeMode } from "../lib/theme";

function avatarInitials(me: MeResponse): string {
  const name = (me.name || "").trim();
  const parts = name
    .replace(/[^a-zA-Z\s.-]/g, " ")
    .trim()
    .split(/[\s.]+/)
    .filter((p) => /[a-zA-Z]/.test(p));
  if (parts.length >= 2) {
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }
  if (parts.length === 1 && parts[0].length >= 2) {
    return parts[0].slice(0, 2).toUpperCase();
  }
  if (parts.length === 1) return parts[0][0].toUpperCase();
  const local = (me.email || "").split("@")[0] || "";
  const letters = local.replace(/[^a-zA-Z]/g, "");
  if (letters.length >= 2) return letters.slice(0, 2).toUpperCase();
  if (letters.length === 1) return letters.toUpperCase();
  return "U";
}

export function TopBar({
  me,
  theme,
  onToggleTheme,
  onLogout,
  onOpenLearning,
  onOpenPermissions,
  onOpenAdmin,
  onSyncAzure,
  syncing,
  syncedAt,
}: {
  me: MeResponse;
  theme: ThemeMode;
  onToggleTheme: () => void;
  onLogout: () => void;
  onOpenLearning: () => void;
  onOpenPermissions: () => void;
  onOpenAdmin: () => void;
  onSyncAzure?: () => void;
  syncing?: boolean;
  syncedAt?: string | null;
}) {
  const [navOpen, setNavOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const navRef = useRef<HTMLDivElement>(null);
  const profileRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (!navRef.current?.contains(e.target as Node)) setNavOpen(false);
      if (!profileRef.current?.contains(e.target as Node)) setProfileOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const initials = avatarInitials(me);

  return (
    <header className="topbar sticky top-0 z-40 px-4 py-2.5 flex items-center gap-3 justify-between">
      <div className="flex items-center gap-2.5 min-w-0">
        <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-sea to-moss grid place-items-center shrink-0">
          <svg width="16" height="16" viewBox="0 0 32 32" fill="none" aria-hidden>
            <path
              d="M8 22V10l8 6 8-6v12"
              stroke="#f7f4ee"
              strokeWidth="2.4"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            <circle cx="16" cy="16" r="2" fill="#f7f4ee" />
          </svg>
        </div>
        <div className="min-w-0">
          <div className="font-display text-base md:text-lg tracking-tight leading-none text-[var(--topbar-fg)]">
            Agent Flow
          </div>
          <div className="text-[10px] text-[var(--topbar-muted)] mt-0.5 truncate">Multi-agent workflows</div>
        </div>
      </div>

      <div className="flex items-center gap-1.5">
        {onSyncAzure && (
          <button
            type="button"
            className="topbar-icon-btn"
            aria-label="Sync agents and workflows from Azure"
            title={
              syncedAt
                ? `Sync from Azure (last: ${new Date(syncedAt).toLocaleString()})`
                : "Sync agents & workflows from Azure"
            }
            disabled={syncing}
            onClick={() => onSyncAzure()}
          >
            <svg
              width="18"
              height="18"
              viewBox="0 0 24 24"
              fill="none"
              aria-hidden
              className={syncing ? "animate-spin" : undefined}
            >
              <path
                d="M4 12a8 8 0 0 1 14.2-5M20 12a8 8 0 0 1-14.2 5"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
              />
              <path d="M18 4v4h-4M6 20v-4h4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
        )}
        <div className="relative" ref={navRef}>
          <button
            type="button"
            className="topbar-icon-btn"
            aria-label="Open menu"
            aria-expanded={navOpen}
            onClick={() => {
              setNavOpen((v) => !v);
              setProfileOpen(false);
            }}
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden>
              <path d="M4 7h16M4 12h16M4 17h16" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
          </button>
          {navOpen && (
            <div className="dropdown-menu absolute right-0 mt-1.5 w-52 z-50 fade-in">
              {onSyncAzure && (
                <button
                  type="button"
                  className="dropdown-item"
                  disabled={syncing}
                  onClick={() => {
                    setNavOpen(false);
                    onSyncAzure();
                  }}
                >
                  {syncing ? "Syncing Azure…" : "Sync from Azure"}
                </button>
              )}
              <button
                type="button"
                className="dropdown-item"
                onClick={() => {
                  setNavOpen(false);
                  onOpenLearning();
                }}
              >
                Learning
              </button>
              <button
                type="button"
                className="dropdown-item"
                onClick={() => {
                  setNavOpen(false);
                  onOpenPermissions();
                }}
              >
                Permissions
              </button>
              {me.isAdmin && (
                <button
                  type="button"
                  className="dropdown-item"
                  onClick={() => {
                    setNavOpen(false);
                    onOpenAdmin();
                  }}
                >
                  Users & roles
                </button>
              )}
              <div className="dropdown-sep" />
              <button
                type="button"
                className="dropdown-item"
                onClick={() => {
                  setNavOpen(false);
                  onToggleTheme();
                }}
              >
                {theme === "dark" ? "Light theme" : "Dark theme"}
              </button>
            </div>
          )}
        </div>

        <div className="relative" ref={profileRef}>
          <button
            type="button"
            className="topbar-avatar"
            aria-label="Account menu"
            aria-expanded={profileOpen}
            onClick={() => {
              setProfileOpen((v) => !v);
              setNavOpen(false);
            }}
          >
            {initials}
          </button>
          {profileOpen && (
            <div className="dropdown-menu absolute right-0 mt-1.5 w-64 z-50 fade-in">
              <div className="px-3 py-2.5 border-b border-[var(--line)]">
                <div className="text-sm font-semibold truncate text-[var(--ink)]">
                  {me.name || me.email || "Signed in"}
                </div>
                {me.email && me.name && (
                  <div className="text-[11px] text-[var(--muted)] truncate mt-0.5">{me.email}</div>
                )}
                <div className="text-[11px] text-[var(--sea)] mt-1.5 leading-snug">
                  {(me.appRoles || []).join(" · ") || "No app roles"}
                </div>
              </div>
              <button
                type="button"
                className="dropdown-item"
                onClick={() => {
                  setProfileOpen(false);
                  onToggleTheme();
                }}
              >
                {theme === "dark" ? "Switch to light" : "Switch to dark"}
              </button>
              <button
                type="button"
                className="dropdown-item text-ember"
                onClick={() => {
                  setProfileOpen(false);
                  onLogout();
                }}
              >
                Sign out
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
