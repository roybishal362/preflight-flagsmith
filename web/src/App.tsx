import { useEffect, useState } from 'react'
import flagsmith from '@flagsmith/flagsmith'
import './App.css'

// Experiment wiring. These names must match what you create in the Flagsmith dashboard.
const EXPERIMENT_FLAG = 'show_demo_button'   // multivariate flag, variants must have keys
const CONVERSION_EVENT = 'demo_button_clicked' // metric event name, case-sensitive

const ENV_KEY = import.meta.env.VITE_FLAGSMITH_ENV_KEY as string

function visitorId(): string {
  const k = 'preflight_visitor_id'
  let id = localStorage.getItem(k)
  if (!id) {
    id = crypto.randomUUID()
    localStorage.setItem(k, id)
  }
  return id
}

export default function App() {
  const [variant, setVariant] = useState<string>('control')
  const [ready, setReady] = useState(false)
  const [clicked, setClicked] = useState(false)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      await flagsmith.init({
        environmentID: ENV_KEY,
        enableEvents: true,
        defaultFlags: {},
      })
      await flagsmith.identify(visitorId())
      const flag = flagsmith.getExperimentFlag(EXPERIMENT_FLAG)
      if (!cancelled) {
        setVariant(flag?.enabled && flag?.value ? String(flag.value) : 'control')
        setReady(true)
      }
    })().catch(() => {
      if (!cancelled) setReady(true) // fall back to control if Flagsmith is unreachable
    })
    return () => {
      cancelled = true
    }
  }, [])

  const onClick = async () => {
    setClicked(true)
    flagsmith.trackEvent(CONVERSION_EVENT)
    await flagsmith.flushEvents()
  }

  if (!ready) return <main><p>Loading…</p></main>

  return (
    <main>
      <h1>Preflight</h1>
      <p>Checks your Flagsmith A/B test before launch, so it never silently records nothing.</p>
      <button onClick={onClick} disabled={clicked}>
        {variant === 'control' ? 'Get the demo' : variant === 'variant_b' ? 'Try the live demo now' : 'Get the demo'}
      </button>
      {clicked && <p>Thanks. Your click was recorded.</p>}
    </main>
  )
}
