/**
 * Gates the web app behind a Kobevoice Cloud sign-in. Until the user is
 * authenticated, a login/register screen is shown; afterwards the wrapped app
 * renders and every API request carries the user's bearer token.
 */
import React, { useEffect, useState } from 'react';
import { CLOUD_URL } from './config';
import { fetchMe, hasToken, initAuth, login, logout, register, type CloudUser } from './auth';

type Status = 'loading' | 'anon' | 'authed';

export function CloudAuthGate({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<Status>('loading');
  const [user, setUser] = useState<CloudUser | null>(null);

  useEffect(() => {
    initAuth();
    if (!hasToken()) {
      setStatus('anon');
      return;
    }
    fetchMe()
      .then((u) => {
        setUser(u);
        setStatus('authed');
      })
      .catch(() => {
        logout();
        setStatus('anon');
      });
  }, []);

  if (status === 'loading') {
    return (
      <div style={styles.center}>
        <div style={{ color: '#9aa0ad' }}>Loading Kobevoice…</div>
      </div>
    );
  }

  if (status === 'anon') {
    return (
      <AuthScreen
        onAuthed={async () => {
          setUser(await fetchMe());
          setStatus('authed');
        }}
      />
    );
  }

  return (
    <>
      <CloudBar user={user} onSignOut={() => { logout(); setUser(null); setStatus('anon'); }} />
      {children}
    </>
  );
}

function AuthScreen({ onAuthed }: { onAuthed: () => void }) {
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setError('');
    setBusy(true);
    try {
      if (mode === 'register') await register(email, password, name);
      else await login(email, password);
      onAuthed();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Something went wrong');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={styles.center}>
      <div style={styles.card}>
        <div style={styles.brand}>
          <span style={styles.dot} /> Kobevoice <span style={{ color: '#9aa0ad', fontWeight: 500 }}>Cloud</span>
        </div>
        <h2 style={{ margin: '6px 0 2px', fontSize: 18 }}>
          {mode === 'login' ? 'Sign in' : 'Create your account'}
        </h2>
        <p style={{ color: '#9aa0ad', fontSize: 13, marginTop: 0 }}>
          {mode === 'login' ? 'Welcome back.' : 'Free plan included — no card required.'}
        </p>
        {mode === 'register' && (
          <input style={styles.input} placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} />
        )}
        <input style={styles.input} type="email" placeholder="you@example.com" value={email} onChange={(e) => setEmail(e.target.value)} />
        <input style={styles.input} type="password" placeholder="Password" value={password} onChange={(e) => setPassword(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && submit()} />
        {error && <div style={{ color: '#f2545b', fontSize: 13, minHeight: 18 }}>{error}</div>}
        <button style={styles.button} onClick={submit} disabled={busy}>
          {busy ? 'Please wait…' : mode === 'login' ? 'Sign in' : 'Create account'}
        </button>
        <div style={{ color: '#9aa0ad', fontSize: 13, marginTop: 12 }}>
          {mode === 'login' ? "No account?" : 'Already have one?'}{' '}
          <a href="#" style={{ color: '#7c8bff' }} onClick={(e) => { e.preventDefault(); setError(''); setMode(mode === 'login' ? 'register' : 'login'); }}>
            {mode === 'login' ? 'Create one' : 'Sign in'}
          </a>
        </div>
        <div style={{ color: '#5b6070', fontSize: 11, marginTop: 16 }}>Cloud: {CLOUD_URL}</div>
      </div>
    </div>
  );
}

function CloudBar({ user, onSignOut }: { user: CloudUser | null; onSignOut: () => void }) {
  return (
    <div style={styles.bar}>
      <span style={{ color: '#9aa0ad', fontSize: 13 }}>
        {user?.email}
        {user?.is_admin ? ' · admin' : ''}
      </span>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
        <a href={CLOUD_URL} target="_blank" rel="noreferrer" style={{ color: '#7c8bff', fontSize: 13 }}>
          Account &amp; billing
        </a>
        <button style={styles.signout} onClick={onSignOut}>Sign out</button>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  center: { display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '100vh', background: '#0b0c10', color: '#e7e9ee', fontFamily: 'system-ui, sans-serif' },
  card: { width: 360, background: '#15171f', border: '1px solid #2a2e3a', borderRadius: 12, padding: 24 },
  brand: { display: 'flex', alignItems: 'center', gap: 8, fontWeight: 700, fontSize: 17 },
  dot: { width: 12, height: 12, borderRadius: '50%', background: 'linear-gradient(135deg,#5b6ef7,#7c8bff)', display: 'inline-block' },
  input: { width: '100%', padding: '11px 12px', margin: '8px 0', background: '#1d2029', border: '1px solid #2a2e3a', borderRadius: 9, color: '#e7e9ee', fontSize: 14, boxSizing: 'border-box' },
  button: { width: '100%', marginTop: 8, padding: '11px 16px', border: 'none', borderRadius: 9, fontSize: 14, fontWeight: 600, color: 'white', cursor: 'pointer', background: 'linear-gradient(135deg,#5b6ef7,#7c8bff)' },
  bar: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '6px 16px', background: '#0f1015', borderBottom: '1px solid #2a2e3a' },
  signout: { background: 'transparent', border: '1px solid #2a2e3a', color: '#e7e9ee', borderRadius: 8, padding: '5px 10px', fontSize: 12, cursor: 'pointer' },
};
