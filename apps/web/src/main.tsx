import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './style.css';
ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);

// Painel instalável (PC e celular). Só em produção, para não atrapalhar o `npm run dev`.
if ('serviceWorker' in navigator && import.meta.env.PROD && location.protocol !== 'file:') {
  window.addEventListener('load', () => { navigator.serviceWorker.register('/sw.js').catch(() => {}); });
}
