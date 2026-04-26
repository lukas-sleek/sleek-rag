"use client";
import * as React from "react";
import { createClient } from "@/lib/supabase/client";

const FIELD_INPUT =
  "bg-bg-input border border-border rounded-md text-text px-3.5 py-3 text-sm " +
  "[outline:none] transition-[border-color,background] duration-150 " +
  "focus:border-accent focus:bg-bg-elevated placeholder:text-text-tertiary";

export function LoginScreen() {
  const supabase = React.useMemo(() => createClient(), []);
  const [mode, setMode] = React.useState<"login" | "register">("login");
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [password2, setPassword2] = React.useState("");
  const [firstName, setFirstName] = React.useState("");
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [info, setInfo] = React.useState<string | null>(null);

  const isRegister = mode === "register";

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setInfo(null);
    if (!email || !password) return;
    if (isRegister && (!firstName || password !== password2)) {
      setError("Bitte Vornamen eingeben und Passwörter abgleichen.");
      return;
    }
    setLoading(true);
    try {
      if (isRegister) {
        const { data, error: err } = await supabase.auth.signUp({
          email,
          password,
          options: { data: { first_name: firstName.trim() } },
        });
        if (err) {
          setError(err.message);
          return;
        }
        if (!data.session) {
          setInfo("Konto erstellt. Bitte E-Mail bestätigen, dann einloggen.");
          setMode("login");
          return;
        }
      } else {
        const { error: err } = await supabase.auth.signInWithPassword({ email, password });
        if (err) {
          setError(err.message);
          return;
        }
      }
    } finally {
      setLoading(false);
    }
  };

  const switchMode = (next: "login" | "register") => {
    setMode(next);
    setError(null);
    setInfo(null);
  };

  return (
    <div className="grid grid-cols-[1.1fr_1fr] h-screen bg-bg">
      <div className="login-brand bg-bg-elevated p-14 flex flex-col justify-between border-r border-border">
        <div className="relative z-10 flex items-center gap-3.5">
          <div className="login-brand-eichenberger h-8 w-auto">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src="/eichenberger-logo.svg" alt="Eichenberger AG" />
          </div>
          <div className="w-px h-[22px] bg-border-strong" />
          <div className="font-display text-[22px] font-extrabold tracking-[-0.03em] text-text">sleek</div>
        </div>

        <div className="relative z-10 flex flex-col gap-7 max-w-[520px]">
          <div
            className="font-display text-[132px] font-extrabold tracking-[-0.04em] leading-[0.92] text-text text-left"
          >
            EAG <span className="text-accent">LLM</span>
          </div>
          <div className="text-[19px] leading-[1.45] text-text-secondary max-w-[440px]">Das EAG interne LLM</div>
        </div>

        <div className="relative z-10 flex justify-between text-xs text-text-tertiary font-mono">
          <span>v0.4.2 · internal preview</span>
          <span>© 2026 Sleek GmbH</span>
        </div>
      </div>

      <div className="flex items-center justify-center p-14 bg-bg">
        <form className="w-full max-w-[380px] flex flex-col gap-7" onSubmit={submit}>
          <div>
            <h1 className="font-display text-[28px] font-semibold tracking-[-0.02em] mb-1.5">
              {isRegister ? "Registrieren" : "Login"}
            </h1>
            <p className="m-0 text-text-secondary text-sm">
              {isRegister
                ? "Neues Konto für das EAG LLM erstellen"
                : "Mit einem bestehenden Benutzer anmelden"}
            </p>
          </div>

          {isRegister && (
            <div className="flex flex-col gap-2">
              <label htmlFor="first-name" className="text-xs font-medium text-text-secondary tracking-[0.01em]">Vorname</label>
              <input
                id="first-name"
                type="text"
                value={firstName}
                onChange={(e) => setFirstName(e.target.value)}
                placeholder="z. B. Anna"
                autoComplete="given-name"
                className={FIELD_INPUT}
              />
            </div>
          )}

          <div className="flex flex-col gap-2">
            <label htmlFor="email" className="text-xs font-medium text-text-secondary tracking-[0.01em]">Email</label>
            <input
              id="email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@eichenberger.com"
              autoComplete="email"
              className={FIELD_INPUT}
            />
          </div>

          <div className="flex flex-col gap-2">
            <label htmlFor="password" className="text-xs font-medium text-text-secondary tracking-[0.01em]">Passwort</label>
            <input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Passwort eingeben"
              autoComplete={isRegister ? "new-password" : "current-password"}
              className={FIELD_INPUT}
            />
          </div>

          {isRegister && (
            <div className="flex flex-col gap-2">
              <label htmlFor="password2" className="text-xs font-medium text-text-secondary tracking-[0.01em]">Passwort bestätigen</label>
              <input
                id="password2"
                type="password"
                value={password2}
                onChange={(e) => setPassword2(e.target.value)}
                placeholder="Passwort wiederholen"
                autoComplete="new-password"
                className={FIELD_INPUT}
              />
            </div>
          )}

          {error && (
            <div className="text-xs text-[#d63a3a] -mt-3">{error}</div>
          )}
          {info && (
            <div className="text-xs text-text-secondary -mt-3">{info}</div>
          )}

          <button className="btn-primary" type="submit" disabled={loading}>
            {loading
              ? isRegister
                ? "Konto wird erstellt…"
                : "Anmelden…"
              : isRegister
              ? "Registrieren"
              : "Login"}
          </button>

          <div className="text-xs text-text-tertiary text-center">
            {isRegister ? (
              <>
                Bereits registriert?{" "}
                <a
                  href="#"
                  onClick={(e) => {
                    e.preventDefault();
                    switchMode("login");
                  }}
                  className="text-text-secondary no-underline hover:text-text"
                >
                  Zum Login
                </a>
              </>
            ) : (
              <a
                href="#"
                onClick={(e) => {
                  e.preventDefault();
                  switchMode("register");
                }}
                className="text-text-secondary no-underline hover:text-text"
              >
                Jetzt registrieren
              </a>
            )}
          </div>
        </form>
      </div>
    </div>
  );
}
