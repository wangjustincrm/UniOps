import './lib/randomUUIDPolyfill'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { loadDisplayTimeZone } from '@uniops/shell'

// The plant's time zone (Portal → Admin → Company Settings) before the first
// render, so every time on screen is plant time (UI-DATE-002). Never blocks
// for long: on failure the zone last seen on this device is used.
void loadDisplayTimeZone(`${(import.meta.env.VITE_EPMS_API_URL as string | undefined) || 'http://localhost:8000'}/api/v1/config/public/branding`).finally(() =>
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
))
