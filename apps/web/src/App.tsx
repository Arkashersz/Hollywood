import { useEffect, useState, type FormEvent } from 'react';
import type { Session } from '@supabase/supabase-js';
import { Clapperboard, LogIn, ShieldCheck } from 'lucide-react';
import { supabase } from './supabase';
import { Studio } from './Studio';

export default function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!supabase) return;
    supabase.auth.getSession().then(({ data }) => { setSession(data.session); setReady(true); });
    const { data } = supabase.auth.onAuthStateChange((_event, next) => setSession(next));
    return () => data.subscription.unsubscribe();
  }, []);

  if (!supabase) return <Setup />;
  if (!ready) return <div className="gate"><div className="gatecard"><p>Carregando…</p></div></div>;
  if (!session) return <Auth />;
  return <Studio db={supabase} session={session} />;
}

function Brand() {
  return (
    <div className="brand gatebrand">
      <div className="brandmark"><Clapperboard size={21} /></div>
      <div><b>TIKTOK STUDIO</b><small>CREATIVE WORKFLOW</small></div>
    </div>
  );
}

function Setup() {
  return (
    <div className="gate">
      <div className="gatecard">
        <Brand />
        <h1>Conecte o Supabase</h1>
        <p>O painel ainda não sabe a qual projeto Supabase se conectar.</p>
        <ol>
          <li>Copie <code>apps/web/.env.example</code> para <code>apps/web/.env</code>.</li>
          <li>Preencha <code>VITE_SUPABASE_URL</code> e <code>VITE_SUPABASE_PUBLISHABLE_KEY</code> (chave pública <code>sb_publishable_…</code>).</li>
          <li>Reinicie <code>npm run dev</code>.</li>
        </ol>
        <p className="hint"><ShieldCheck size={14} /> Nunca coloque aqui a chave secreta <code>sb_secret_…</code>: ela fica só no worker.</p>
      </div>
    </div>
  );
}

function Auth() {
  const [mode, setMode] = useState<'in' | 'up'>('in');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!supabase) return;
    setBusy(true);
    setMessage('');
    const result = mode === 'in'
      ? await supabase.auth.signInWithPassword({ email, password })
      : await supabase.auth.signUp({ email, password });
    setBusy(false);
    if (result.error) setMessage(result.error.message);
    else if (mode === 'up' && !result.data.session) setMessage('Conta criada. Confirme o e-mail recebido e depois entre.');
  }

  return (
    <div className="gate">
      <form className="gatecard" onSubmit={submit}>
        <Brand />
        <h1>{mode === 'in' ? 'Entrar' : 'Criar conta'}</h1>
        <label>E-mail<input type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} /></label>
        <label>Senha<input type="password" required minLength={8} autoComplete={mode === 'in' ? 'current-password' : 'new-password'} value={password} onChange={(e) => setPassword(e.target.value)} /></label>
        {message && <p className="gatemsg">{message}</p>}
        <button className="primary" disabled={busy}><LogIn size={16} /> {busy ? 'Aguarde…' : mode === 'in' ? 'Entrar' : 'Criar conta'}</button>
        <button type="button" className="linkbtn" onClick={() => { setMode(mode === 'in' ? 'up' : 'in'); setMessage(''); }}>
          {mode === 'in' ? 'Primeiro acesso? Criar conta' : 'Já tenho conta'}
        </button>
      </form>
    </div>
  );
}
