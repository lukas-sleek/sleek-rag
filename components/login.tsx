"use client";
import * as React from "react";

export type LoginUser = { email: string };

export function LoginScreen({ onLogin }: { onLogin: (user: LoginUser) => void }) {
  const [mode, setMode] = React.useState<"login" | "register">("login");
  const [email, setEmail] = React.useState("alex@sleek.de");
  const [password, setPassword] = React.useState("••••••••");
  const [password2, setPassword2] = React.useState("");
  const [name, setName] = React.useState("");
  const [loading, setLoading] = React.useState(false);

  const isRegister = mode === "register";

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!email || !password) return;
    if (isRegister && (!name || password !== password2)) return;
    setLoading(true);
    setTimeout(() => onLogin({ email }), 650);
  };

  const switchMode = (next: "login" | "register") => {
    setMode(next);
    setLoading(false);
  };

  return (
    <div className="login-shell">
      <div className="login-brand">
        <div className="login-brand-top">
          <div className="login-brand-eichenberger">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src="/eichenberger-logo.svg" alt="Eichenberger AG" />
          </div>
          <div className="login-brand-divider" />
          <div className="login-brand-sleek">sleek</div>
        </div>

        <div className="login-brand-center">
          <div className="login-brand-mark" style={{ textAlign: "left" }}>
            EAG <span className="accent">LLM</span>
          </div>
          <div className="login-brand-tagline">Das EAG interne LLM</div>
        </div>

        <div className="login-brand-bottom">
          <span>v0.4.2 · internal preview</span>
          <span>© 2026 Sleek GmbH</span>
        </div>
      </div>

      <div className="login-form-wrap">
        <form className="login-form" onSubmit={submit}>
          <div>
            <h1>{isRegister ? "Registrieren" : "Login"}</h1>
            <p className="sub">
              {isRegister
                ? "Neues Konto für das EAG LLM erstellen"
                : "Mit einem bestehenden Benutzer anmelden"}
            </p>
          </div>

          {isRegister && (
            <div className="field">
              <label htmlFor="name">Name</label>
              <input
                id="name"
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Vor- und Nachname"
                autoComplete="name"
              />
            </div>
          )}

          <div className="field">
            <label htmlFor="email">Email</label>
            <input
              id="email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@eichenberger.com"
              autoComplete="email"
            />
          </div>

          <div className="field">
            <label htmlFor="password">Passwort</label>
            <input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Passwort eingeben"
              autoComplete={isRegister ? "new-password" : "current-password"}
            />
          </div>

          {isRegister && (
            <div className="field">
              <label htmlFor="password2">Passwort bestätigen</label>
              <input
                id="password2"
                type="password"
                value={password2}
                onChange={(e) => setPassword2(e.target.value)}
                placeholder="Passwort wiederholen"
                autoComplete="new-password"
              />
            </div>
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

          <div className="login-footer">
            {isRegister ? (
              <>
                Bereits registriert?{" "}
                <a
                  href="#"
                  onClick={(e) => {
                    e.preventDefault();
                    switchMode("login");
                  }}
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
