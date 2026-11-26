import { useEffect, useState } from 'react'
import flagsmith from '@flagsmith/flagsmith'
import './App.css'

// Experiment wiring. These names must match the Flagsmith dashboard (checked by `preflight check`).
const EXPERIMENT_FLAG = 'show_demo_button'   // multivariate flag: control | variant_b
const CONVERSION_EVENT = 'demo_button_clicked' // metric event name, case-sensitive
const REPO_URL = 'https://github.com/roybishal362/preflight-flagsmith'

const ENV_KEY = import.meta.env.VITE_FLAGSMITH_ENV_KEY as string
// Self-hosted Flagsmith: set BOTH, or events silently go to Flagsmith Cloud (Preflight rule PF006).
const API_URL = (import.meta.env.VITE_FLAGSMITH_API_URL as string | undefined) || undefined
// If only the API URL is given, derive the events URL from it (…/api/v1/ -> …/api/) rather than fall back to Cloud.
const EVENTS_URL =
  (import.meta.env.VITE_FLAGSMITH_EVENTS_URL as string | undefined) ||
  (API_URL ? API_URL.replace(/v1\/?$/, '') : undefined)

const LABELS: Record<string, string> = {
  control: 'Get the demo',
  variant_b: 'Try the live demo now',
}

function visitorId(): string {
  const k = 'preflight_visitor_id'
  let id = localStorage.getItem(k)
  if (!id) {
    id = crypto.randomUUID()
    localStorage.setItem(k, id)
  }
  return id
}

const FAILURES = [
  ['Events never enabled', 'No exposures, no conversions.'],
  ['Flag read before identify()', 'No exposures recorded.'],
  ['Event name case mismatch', 'Conversions silently ignored.'],
  ['Events never flushed', 'Data lost when the process exits.'],
  ['Variant without a key', 'Half your traffic records nothing.'],
]

export default function App() {
  const [variant, setVariant] = useState<string>('control')
  const [ready, setReady] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      await flagsmith.init({
        environmentID: ENV_KEY,
        enableEvents: true,
        defaultFlags: {},
        // The JS SDK reads the events URL ONLY from eventProcessorConfig (verified in 12.6 source);
        // a top-level `eventsApiUrl` is silently ignored and events go to Flagsmith Cloud.
        ...(API_URL ? { api: API_URL, eventProcessorConfig: { eventsApiUrl: EVENTS_URL } } : {}),
      })
      await flagsmith.identify(visitorId())
      const flag = flagsmith.getExperimentFlag(EXPERIMENT_FLAG)
      if (!cancelled) {
        const value = flag?.enabled && flag?.value ? String(flag.value) : 'control'
        setVariant(value in LABELS ? value : 'control')
        setReady(true)
      }
    })().catch(() => {
      if (!cancelled) setReady(true) // Flagsmith unreachable: show control
    })
    return () => {
      cancelled = true
    }
  }, [])

  const onClick = async () => {
    setBusy(true)
    try {
      flagsmith.trackEvent(CONVERSION_EVENT)
      await flagsmith.flushEvents() // send before we navigate away
    } finally {
      window.location.href = REPO_URL
    }
  }

  return (
    <main className="page">
      <p className="eyebrow">Toggle Wars · Flagsmith Experimentation Hackathon</p>
      <h1>Preflight</h1>
      <p className="lead">
        Your Flagsmith A/B test can look perfect in the dashboard and still record <strong>nothing</strong>.
        Preflight checks your code and your Flagsmith setup before launch, then refuses to call a winner too early.
      </p>

      <button className="cta" onClick={onClick} disabled={!ready || busy}>
        {ready ? LABELS[variant] : 'Loading…'}
      </button>
      <p className="note">Opens the open-source repo on GitHub.</p>

      <section>
        <h2>The silent failures it catches</h2>
        <ul className="failures">
          {FAILURES.map(([what, effect]) => (
            <li key={what}>
              <span>{what}</span>
              <em>{effect}</em>
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h2>You are in an experiment right now</h2>
        <p>
          This page runs a real Flagsmith experiment on the button above. Visitors are split 50/50 between two labels,
          and a click counts as a conversion. No personal data is collected: only a random ID stored in your browser.
        </p>
        <pre className="cli">{`$ preflight lint ./web --event demo_button_clicked
✓ No problems found. This experiment can record data.

$ preflight verdict 692
Not enough data yet. Do not call a winner.`}</pre>
      </section>
    </main>
  )
}
