import { useCallback, useEffect, useRef, useState } from 'react'
import TaskChat from './TaskChat'
import ContactsChat from './Contactschat'
import './Oppurtunities.css'
import ResourcesChat from './ResourcesChat'

const API = '/api/v1/opportunities'
const STEPS = ['Validating package', 'Writing project header', 'Writing QA profile', 'Confirming with JOSS']
const STATUS = {
  idle: ['', 'Not analyzed'], queued: ['warn', 'Queued'], analyzing: ['warn', 'Analyzing SOW…'],
  ready: ['ok', 'Ready'], failed: ['bad', 'Failed'],
}
const A2_LABEL = { pending: 'QA waiting', running: 'QA classifying…', ready: 'QA ready', failed: 'QA failed', skipped: 'QA skipped' }
const BADGE = { auto: 'ok', review: 'warn', blank: 'bad', default: '', system: '' }

async function http(method, path = '', body) {
  const res = await fetch(API + path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail ?? res.statusText))
  return data
}

const pct = (n) => (typeof n === 'number' ? `${Math.round(n * 100)}%` : '—')
const label = (k) => k.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase())
const money = (n) => (typeof n === 'number' ? `$${n.toLocaleString('en-US', { maximumFractionDigits: 0 })}` : null)

export default function OpportunityWorkspace() {
  const [meta, setMeta] = useState(null)
  const [selected, setSelected] = useState(null)
  const [detail, setDetail] = useState(null)
  const [error, setError] = useState('')
  const [now, setNow] = useState(Date.now())
  const [draft, setDraft] = useState({})
  const [resolve, setResolve] = useState([])
  const [saveState, setSaveState] = useState('')
  const [qaPick, setQaPick] = useState(null)
  const [qaReason, setQaReason] = useState('')
  const [sub, setSub] = useState(null)
  const saveTimer = useRef(null)

  // ---- polling the backend (the backend itself polls Salesforce every 30 s) ----
  const loadMeta = useCallback(
    () => http('GET').then((m) => { setMeta(m); setSelected((s) => s ?? m.opportunities[0]?.id ?? null) }).catch((e) => setError(e.message)),
    [],
  )
  useEffect(() => { loadMeta(); const t = setInterval(loadMeta, 4000); return () => clearInterval(t) }, [loadMeta])
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t) }, [])

  const loadDetail = useCallback(
    () => (selected ? http('GET', `/${selected}`).then(setDetail).catch((e) => setError(e.message)) : null),
    [selected],
  )
  useEffect(() => {
    setDetail(null); setDraft({}); setResolve([]); setSaveState(''); setQaPick(null); setQaReason(''); setSub(null)
    if (!selected) return undefined
    loadDetail()
    const t = setInterval(loadDetail, 3000)
    return () => clearInterval(t)
  }, [selected, loadDetail])

  // a fresh analysis replaces the server-side edits, so drop local ones too
  useEffect(() => { setDraft({}); setResolve([]); setQaPick(null) }, [detail?.analyzed_at])
  useEffect(() => { if (detail?.qa_choice) setQaPick((p) => p ?? detail.qa_choice) }, [detail?.qa_choice])

  // ---- autosave while the user types ----
  useEffect(() => {
    if (!selected || (!Object.keys(draft).length && !resolve.length)) return undefined
    setSaveState('saving')
    saveTimer.current = setTimeout(() => {
      http('PUT', `/${selected}/fields`, { fields: draft, resolve })
        .then((d) => { setDetail(d); setSaveState('saved') })
        .catch((e) => { setError(e.message); setSaveState('error') })
    }, 700)
    return () => clearTimeout(saveTimer.current)
  }, [draft, resolve, selected])

  // ---- submit animation (JOSS is simulated) ----
  useEffect(() => {
    if (!sub || sub.error || sub.step >= STEPS.length) return undefined
    const t = setTimeout(() => setSub((s) => s && { ...s, step: s.step + 1 }), 700)
    return () => clearTimeout(t)
  }, [sub])

  const act = (fn) => fn().catch((e) => setError(e.message))
  const setField = (k, v) => setDraft((d) => ({ ...d, [k]: v }))
  const resolveWith = (k, v) => { setField(k, v); setResolve((r) => [...new Set([...r, k])]) }
  const closeSub = () => { setSub(null); loadDetail(); loadMeta() }
  const startSubmit = () => {
    setError('')
    setSub({ step: 0, result: null, error: '' })
    http('POST', `/${selected}/submit`).then(
      (r) => setSub((s) => s && { ...s, result: r.submission }),
      (e) => setSub((s) => s && { ...s, error: e.message }),
    )
  }
  const saveQa = () => act(() => http('PUT', `/${selected}/qa`, { combination_id: qaPick, reason: qaReason }).then(setDetail))

  const nextIn = meta?.last_polled_at
    ? Math.max(0, Math.round(meta.poll_interval_s - (now - Date.parse(meta.last_polled_at)) / 1000)) : null
  const a1 = detail?.a1
  const a2 = detail?.a2
  const open = (detail?.unresolved_conflicts || []).filter((k) => !resolve.includes(k) && !(k in draft))
  const needsReason = a2?.preselect && qaPick && qaPick !== a2.selected_combination_id
  const canSubmit = detail?.status === 'ready' && !detail.submission && !open.length

  return (
    <div className="opp">
      <div className="opp-bar">
        <strong>Recent Salesforce opportunities</strong>
        <span className="opp-muted">
          {meta ? `source: ${meta.source} · polled every ${meta.poll_interval_s}s${nextIn !== null ? ` · next in ${nextIn}s` : ''}` : 'connecting…'}
        </span>
        <button className="opp-ghost" onClick={() => act(() => http('POST', '/poll').then(setMeta))}>Poll now</button>
      </div>
      {meta?.last_error && <div className="opp-banner bad">Salesforce poll failed: {meta.last_error}</div>}
      {error && <div className="opp-banner bad" onClick={() => setError('')}>{error} (click to dismiss)</div>}

      <div className="opp-layout">
        <aside className="opp-list">
          {meta?.opportunities.length === 0 && <p className="opp-muted">No opportunities returned yet.</p>}
          {meta?.opportunities.map((o) => (
            <button key={o.id} className={`opp-card ${o.id === selected ? 'on' : ''}`} onClick={() => setSelected(o.id)}>
              <b>{o.name}</b>
              <span className="opp-card-sub opp-muted">{[o.account, o.stage].filter(Boolean).join(' · ')}</span>
              <span className="opp-chips">
                <i className={`chip ${STATUS[o.status][0]}`}>{STATUS[o.status][1]}</i>
                {o.status === 'ready' && <i className={`chip ${o.a2_status === 'ready' ? 'ok' : o.a2_status === 'failed' ? 'bad' : 'warn'}`}>{A2_LABEL[o.a2_status]}</i>}
                {o.submitted && <i className="chip ok">Submitted ✓</i>}
                {o.sf_changed && <i className="chip warn">Changed in Salesforce</i>}
                {o.best_score != null && <i className="chip">QA {pct(o.best_score)}</i>}
                {money(o.amount) && <i className="chip">{money(o.amount)}</i>}
              </span>
            </button>
          ))}
        </aside>

        <main className="opp-main">
          {!detail && <p className="opp-muted">Select an opportunity.</p>}
          {detail && (
            <>
              <h2>{detail.name}</h2>
              <div className="opp-meta">
                <span className="opp-muted">{[detail.account, detail.stage].filter(Boolean).join(' · ')}</span>
                <code className="opp-oid" title="Salesforce Opportunity ID">{detail.id}</code>
                {money(detail.amount) && <i className="chip">{money(detail.amount)}</i>}
              </div>
              {detail.sf_changed && (
                <div className="opp-banner warn">This opportunity changed in Salesforce after it was analyzed.{' '}
                  <button className="opp-ghost" onClick={() => act(() => http('POST', `/${selected}/analyze`).then(setDetail))}>Re-analyze</button></div>
              )}
              {['queued', 'analyzing', 'idle'].includes(detail.status) && (
                <p><span className="opp-spin" /> {detail.status === 'idle' ? 'Not analyzed yet.' : 'Agent 1 is reading the SOW…'}{' '}
                  {detail.status === 'idle' && <button onClick={() => act(() => http('POST', `/${selected}/analyze`).then(setDetail))}>Analyze</button>}</p>
              )}
              {detail.status === 'failed' && (
                <div className="opp-banner bad">Agent 1 failed: {detail.a1_error}{' '}
                  <button className="opp-ghost" onClick={() => act(() => http('POST', `/${selected}/analyze`).then(setDetail))}>Retry</button></div>
              )}

              {a1 && (
                <section>
                  <h3>1. SOW details <span className="opp-muted">{saveState === 'saving' ? 'saving…' : saveState === 'saved' ? 'saved' : 'edit any field'}</span></h3>
                  {(a1.warnings || []).map((w, i) => <div key={i} className="opp-banner warn">{typeof w === 'string' ? w : JSON.stringify(w)}</div>)}
                  {(a1.errors || []).map((w, i) => <div key={i} className="opp-banner bad">{typeof w === 'string' ? w : JSON.stringify(w)}</div>)}
                  <div className="opp-table-wrap">
                    <table className="opp-table opp-table--fields">
                      <thead>
                        <tr>
                          <th>Field</th>
                          <th>Value (editable)</th>
                          <th>Review</th>
                          <th>Source &amp; evidence</th>
                        </tr>
                      </thead>
                      <tbody>
                        {Object.entries(a1.fields || {}).map(([k, f]) => {
                          const value = k in draft ? draft[k] : detail.final_fields[k]
                          const conflict = f.conflict && open.includes(k)
                          return (
                            <tr key={k}>
                              <td>{label(k)}</td>
                              <td>
                                {k === 'description'
                                  ? <textarea rows={2} value={value ?? ''} onChange={(e) => setField(k, e.target.value)} />
                                  : <input value={value ?? ''} onChange={(e) => setField(k, e.target.value)} placeholder={f.value == null ? 'Not found in SOW' : ''} />}
                                {conflict && (
                                  <div className="opp-conflict">SOW says <b>{f.conflict.sow_value}</b>, Salesforce says <b>{f.conflict.sf_value}</b>
                                    <button onClick={() => resolveWith(k, f.conflict.sow_value)}>Use SOW</button>
                                    <button onClick={() => resolveWith(k, f.conflict.sf_value)}>Use Salesforce</button></div>
                                )}
                              </td>
                              <td><i className={`chip ${BADGE[f.review_state] ?? ''}`}>{f.review_state || '—'}</i>
                                <div className="opp-bar-bg"><div style={{ width: pct(f.confidence) }} /></div></td>
                              <td className="opp-muted opp-small">{[f.source, f.evidence && `“${f.evidence}”`, f.review_reason].filter(Boolean).join(' · ')}</td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>
                </section>
              )}

              {a1 && (
                <section>
                  <h3>2. QA project type</h3>
                  {['pending', 'running'].includes(detail.a2_status) && <p><span className="opp-spin" /> Agent 2 is classifying…</p>}
                  {detail.a2_status === 'failed' && <div className="opp-banner bad">Agent 2 failed: {detail.a2_error}. The SOW details above are unaffected.</div>}
                  {detail.a2_status === 'ready' && a2 && (
                    <>
                      <div className={`opp-banner ${a2.preselect ? 'ok' : 'warn'}`}>
                        <span className="opp-score">{pct(a2.best_score)}</span>
                        {a2.preselect ? 'Best match is pre-selected. Accept it or choose another.' : 'No confident match. Please choose one.'}
                      </div>
                      {(a2.top_3 || []).map((t) => (
                        <label key={t.combination_id} className={`opp-option ${qaPick === t.combination_id ? 'on' : ''}`}>
                          <input type="radio" name="qa" checked={qaPick === t.combination_id} onChange={() => setQaPick(t.combination_id)} />
                          <span><b>#{t.rank} {t.combination_id}</b> {t.name}<br /><span className="opp-muted opp-small">{t.reason}</span></span>
                          <i className="chip">{pct(t.score)}</i>
                        </label>
                      ))}
                      {(a2.warnings || []).map((w, i) => <div key={i} className="opp-banner warn">{typeof w === 'string' ? w : JSON.stringify(w)}</div>)}
                      {needsReason && <input className="opp-wide" placeholder="Why change the suggestion? (required)" value={qaReason} onChange={(e) => setQaReason(e.target.value)} />}
                      <button disabled={!qaPick || qaPick === detail.qa_choice || (needsReason && !qaReason.trim())} onClick={saveQa}>Save QA choice</button>
                      {detail.qa_choice && <span className="opp-muted"> Chosen: {detail.qa_choice}{detail.qa_overridden ? ' (override)' : ''}</span>}
                    </>
                  )}
                </section>
              )}

              <section>
                <h3>3. Tasks <span className="opp-muted">create &amp; edit by chat (A3)</span></h3>
                <TaskChat projectId={selected} />
              </section>

              <section>
                <h3>4. Jade Contacts <span className="opp-muted">assign roles &amp; handovers by chat (A4)</span></h3>
                <ContactsChat projectId={selected} />
              </section>

              <section>
                <h3>5. Resource Management <span className="opp-muted">staff estimate lines by chat (A5)</span></h3>
                <ResourcesChat projectId={selected} />
              </section>

              {a1 && (
                <section>
                  <h3>6. Submit to JOSS <span className="opp-muted">final step — sends the package</span></h3>
                  {open.length > 0 && <div className="opp-banner warn">Resolve the conflict(s) first: {open.map(label).join(', ')}</div>}
                  {detail.submission
                    ? <div className="opp-banner ok">Submitted to JOSS as <b>{detail.submission.joss_id}</b> (simulated)</div>
                    : <button className="opp-submit" disabled={!canSubmit || !!sub} onClick={startSubmit}>Submit to JOSS</button>}
                </section>
              )}

            </>
          )}
        </main>
      </div>

      {sub && (
        <div className="opp-overlay">
          <div className="opp-modal">
            <h3>{sub.error ? 'Submit failed' : sub.result && sub.step >= STEPS.length ? 'Submitted to JOSS' : 'Submitting to JOSS…'}</h3>
            {STEPS.map((s, i) => (
              <div key={s} className={`opp-step ${i < sub.step ? 'done' : i === sub.step ? 'run' : ''}`}>
                <span className="opp-mark">{i < sub.step ? '✓' : i === sub.step && !sub.error ? <span className="opp-spin" /> : '○'}</span> {s}
              </div>
            ))}
            {sub.error && <div className="opp-banner bad">{sub.error}</div>}
            {!sub.error && sub.step >= STEPS.length && !sub.result && <p className="opp-muted">Waiting for JOSS…</p>}
            {sub.result && sub.step >= STEPS.length && (
              <div className="opp-done"><div className="opp-tick">✓</div><b>{sub.result.joss_id}</b>
                <p className="opp-muted opp-small">Demo only: JOSS is simulated. {Object.keys(sub.result.package.fields).length} fields submitted.</p></div>
            )}
            {(sub.error || (sub.result && sub.step >= STEPS.length)) && <button onClick={closeSub}>Close</button>}
          </div>
        </div>
      )}
    </div>
  )
}