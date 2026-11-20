import { useEffect, useState } from 'react'
import './App.css'
import OpportunityWorkspace from './OppurtunityWorkspace.jsx'
import ResourceEstimationLookup from './ResourceEstimationLookup.jsx'

const ico = {
  props: {
    width: 18,
    height: 18,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.8,
    strokeLinecap: 'round',
    strokeLinejoin: 'round',
    'aria-hidden': true,
  },
}

function IconSun(p) {
  return (
    <svg {...ico.props} {...p}>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2.5v2M12 19.5v2M4.6 4.6l1.4 1.4M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 19.4 6 18M18 6l1.4-1.4" />
    </svg>
  )
}

function IconMoon(p) {
  return (
    <svg {...ico.props} {...p}>
      <path d="M20 14.5A8 8 0 0 1 9.5 4a8.2 8.2 0 1 0 10.5 10.5z" />
    </svg>
  )
}

function IconAuto(p) {
  return (
    <svg {...ico.props} {...p}>
      <rect x="3" y="4.5" width="18" height="12.5" rx="2.5" />
      <path d="M9 20.5h6M12 17v3.5" />
    </svg>
  )
}

function useBackendHealth() {
  const [state, setState] = useState('checking')

  useEffect(() => {
    let alive = true

    const check = () =>
      fetch('/api/v1/health')
        .then((r) => (r.ok ? 'ok' : 'down'))
        .catch(() => 'down')
        .then((s) => alive && setState(s))

    check()

    const timer = setInterval(check, 30000)

    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [])

  return state
}

const THEME_ORDER = ['system', 'light', 'dark']
const THEME_ICON = {
  system: IconAuto,
  light: IconSun,
  dark: IconMoon,
}

const HEALTH_LABEL = {
  ok: 'Connected',
  down: 'Offline',
  checking: 'Checking…',
}

function useTheme() {
  const [theme, setTheme] = useState(() => {
    try {
      return localStorage.getItem('glean-theme') || 'system'
    } catch {
      return 'system'
    }
  })

  useEffect(() => {
    if (theme === 'system') {
      delete document.documentElement.dataset.theme
    } else {
      document.documentElement.dataset.theme = theme
    }

    try {
      localStorage.setItem('glean-theme', theme)
    } catch {
      // Session-only theme.
    }
  }, [theme])

  const cycle = () => {
    setTheme(
      (current) =>
        THEME_ORDER[
          (THEME_ORDER.indexOf(current) + 1) % THEME_ORDER.length
        ],
    )
  }

  return [theme, cycle]
}

export default function App() {
  const health = useBackendHealth()
  const [theme, cycleTheme] = useTheme()
  const [activeTab, setActiveTab] = useState('opportunities')
  const ThemeIcon = THEME_ICON[theme] ?? IconAuto

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            G
          </span>

          <span className="brand-text">
            <strong>Glean Agent Studio</strong>
            <span>Salesforce → SOW intelligence → JOSS</span>
          </span>
        </div>

        <div className="topbar-actions">
          <span
            className={`health health-${health}`}
            title={`Backend ${HEALTH_LABEL[health].toLowerCase()}`}
          >
            <i className="health-dot" aria-hidden="true" />
            {HEALTH_LABEL[health]}
          </span>

          <button
            type="button"
            className="icon-btn"
            onClick={cycleTheme}
            aria-label={`Theme: ${theme}. Click to change.`}
            title={`Theme: ${theme}`}
          >
            <ThemeIcon />
          </button>
        </div>
      </header>

      <main className="view">
        <nav className="workspace-tabs" aria-label="Workspace views">
          <button
            type="button"
            className="workspace-tab"
            aria-selected={activeTab === 'opportunities'}
            onClick={() => setActiveTab('opportunities')}
          >
            Opportunities
          </button>

          <button
            type="button"
            className="workspace-tab"
            aria-selected={activeTab === 'resource-estimation'}
            onClick={() => setActiveTab('resource-estimation')}
          >
            Resource Estimation
          </button>
        </nav>

        {/* Both tabs stay mounted (hidden, not unmounted) so chat output,
            previews and drafts survive switching back and forth. */}
        <div className="workspace-panel" hidden={activeTab !== 'opportunities'}>
          <OpportunityWorkspace />
        </div>
        <div className="workspace-panel" hidden={activeTab !== 'resource-estimation'}>
          <ResourceEstimationLookup />
        </div>
      </main>
    </div>
  )
}
