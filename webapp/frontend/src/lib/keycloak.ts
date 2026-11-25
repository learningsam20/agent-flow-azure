import Keycloak from "keycloak-js";

export type PublicConfig = {
  keycloakUrl: string;
  keycloakRealm: string;
  keycloakClientId: string;
  keycloakAudience: string;
  appInsightsConnectionString?: string | null;
  agents: string[];
};

let kc: Keycloak | null = null;
let publicConfig: PublicConfig | null = null;
/** Full init — login/logout always wait on this, never the UI timeout. */
let bootPromise: Promise<Keycloak> | null = null;

export async function loadPublicConfig(): Promise<PublicConfig> {
  if (publicConfig) return publicConfig;
  const res = await fetch("/api/config/public");
  if (!res.ok) throw new Error("Failed to load /api/config/public");
  publicConfig = (await res.json()) as PublicConfig;
  return publicConfig;
}

function withTimeout<T>(promise: Promise<T>, ms: number, label: string): Promise<T> {
  return new Promise((resolve, reject) => {
    const t = window.setTimeout(() => reject(new Error(`${label} timed out after ${ms}ms`)), ms);
    promise.then(
      (v) => {
        window.clearTimeout(t);
        resolve(v);
      },
      (e) => {
        window.clearTimeout(t);
        reject(e);
      }
    );
  });
}

function persistToken(instance: Keycloak) {
  if (instance.token) localStorage.setItem("afa_token", instance.token);
}

function wireTokenHandlers(instance: Keycloak) {
  persistToken(instance);
  instance.onAuthSuccess = () => persistToken(instance);
  instance.onAuthRefreshSuccess = () => persistToken(instance);
  instance.onAuthLogout = () => {
    localStorage.removeItem("afa_token");
  };
  // Refresh well before expiry (minValidity 5 min). Realm access TTL is 8h.
  window.setInterval(async () => {
    if (!instance.authenticated) return;
    try {
      const refreshed = await instance.updateToken(300);
      if (refreshed) persistToken(instance);
    } catch {
      /* ignore — next API call will retry / force login */
    }
  }, 20000);
}

/** Always completes Keycloak init (no UI timeout). Safe to call repeatedly. */
export function bootKeycloak(): Promise<Keycloak> {
  if (kc?.didInitialize) return Promise.resolve(kc);
  if (bootPromise) return bootPromise;

  bootPromise = (async () => {
    const cfg = await loadPublicConfig();
    const instance = new Keycloak({
      url: cfg.keycloakUrl,
      realm: cfg.keycloakRealm,
      clientId: cfg.keycloakClientId,
    });
    kc = instance;
    await instance.init({
      onLoad: "check-sso",
      pkceMethod: "S256",
      checkLoginIframe: false,
    });
    wireTokenHandlers(instance);
    return instance;
  })().catch((e) => {
    bootPromise = null;
    throw e;
  });

  return bootPromise;
}

/**
 * App bootstrap: prefer a quick SSO check, but never leave the UI stuck.
 * If SSO is slow, return the in-flight instance so the landing page can render;
 * Sign in still awaits full boot via login().
 */
export async function initKeycloak(): Promise<Keycloak> {
  if (kc?.didInitialize) return kc;
  const boot = bootKeycloak();
  try {
    return await withTimeout(boot, 3500, "Keycloak SSO check");
  } catch (e) {
    console.warn("Keycloak SSO check did not finish quickly; continuing signed out", e);
    if (kc) return kc;
    throw e;
  }
}

export function getKeycloak(): Keycloak | null {
  return kc;
}

/** True when the page was loaded as a Keycloak redirect callback. */
export function isAuthCallback(): boolean {
  const href = window.location.href;
  return /[?#&](code|state|session_state|iss)=/.test(href);
}

/** Prefer waiting for full SSO when returning from login or a prior session exists. */
export function shouldWaitForSession(): boolean {
  return isAuthCallback() || !!localStorage.getItem("afa_token");
}

export async function login(): Promise<void> {
  // Must wait for full init — calling login() before didInitialize is a no-op / race.
  const instance = await bootKeycloak();
  await instance.login({ redirectUri: window.location.origin + "/" });
}

export async function logout(): Promise<void> {
  const instance = await bootKeycloak();
  localStorage.removeItem("afa_token");
  await instance.logout({ redirectUri: window.location.origin + "/" });
}

export function getAccessToken(): string | null {
  return kc?.token || localStorage.getItem("afa_token");
}

/**
 * Ensure the access token is fresh before API calls.
 * Refreshes when under 5 minutes remain; clears stale localStorage on failure.
 */
export async function ensureFreshToken(minValiditySeconds = 300): Promise<string | null> {
  try {
    const instance = kc?.didInitialize ? kc : await bootKeycloak();
    if (!instance.authenticated && !instance.token) {
      return localStorage.getItem("afa_token");
    }
    try {
      const refreshed = await instance.updateToken(minValiditySeconds);
      if (refreshed || instance.token) persistToken(instance);
    } catch {
      localStorage.removeItem("afa_token");
      return null;
    }
    return instance.token || localStorage.getItem("afa_token");
  } catch {
    return localStorage.getItem("afa_token");
  }
}
