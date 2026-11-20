import { useEffect, useRef, useState } from 'react'

const API = (projectId) => `/api/v1/projects/${projectId}/contacts`

function errorText(detail, fallback) {
  if (typeof detail === 'string') return detail
  if (detail && Array.isArray(detail.hits)) {
    return `${detail.message || 'A rule was broken'}: ` +
      detail.hits.map((h) => `${h.rule} ${h.contact ? `(${h.contact}) ` : ''}${h.message}`).join(' | ')
  }
  return detail ? JSON.stringify(detail) : fallback
}

async function http(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    const err = new Error(errorText(data.detail, res.statusText))
    err.status = res.status
    throw err
  }
  return data
}

const val = (v) => (v === true ? 'Yes' : v === false ? 'No' : v == null || v === '' ? '—' : String(v))
const pretty = (k) => k.replace(/_/g, ' ')
const newRequestId = () => (crypto.randomUUID ? crypto.randomUUID() : `req-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`)

export default function ContactsChat({ projectId }) {
  const [ctx, setCtx] = useState(null)
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [last, setLast] = useState(null)
  const [requestId, setRequestId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [applied, setApplied] = useState('')
  const [gaps, setGaps] = useState([])
  const logRef = useRef(null)

  const load = () =>
    http('GET', API(projectId)).then((c) => { setCtx(c); return c }).catch((e) => setError(e.message))

  useEffect(() => {
    setCtx(null); setMessages([]); setLast(null); setError(''); setApplied(''); setGaps([]); setRequestId('')
    if (!projectId) return undefined
    load()
    return undefined
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId])

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight })
  }, [messages, busy])

  const send = (text) => {
    const message = (text ?? input).trim()
    if (!message || busy) return
    setError(''); setApplied('')
    const history = messages
    setInput('')
    setMessages((m) => [...m, { role: 'user', content: message }])
    setBusy(true)
    http('POST', `${API(projectId)}/chat`, { message, chat_history: history })
      .then((r) => {
        setLast(r)
        setRequestId(newRequestId()) // one id per proposal; reused if Create/Update is retried
        setGaps(r.uncovered_pm_ranges || [])
        setMessages((m) => [...m, { role: 'assistant', content: r.reply || '' }])
      })
      .catch((e) => setError(e.message))
      .finally(() => setBusy(false))
  }

  const apply = () => {
    if (!last?.changes?.length || busy) return
    setBusy(true); setError('')
    http('POST', `${API(projectId)}/apply`, {
      changes: last.changes,
      request_id: requestId,
      expected_revision: ctx?.revision,
    })
      .then((r) => {
        setCtx((c) => ({ ...c, revision: r.revision, contacts: r.contacts }))
        setApplied(`Applied ${r.results.length} change(s) · ${r.salesforce}`)
        setGaps(r.uncovered_pm_ranges || [])
        setLast(null)
      })
      .catch((e) => {
        setError(e.message)
        if (e.status === 409) { setLast(null); load() } // contacts changed: reload, ask again
      })
      .finally(() => setBusy(false))
  }

  const byId = Object.fromEntries((ctx?.contacts || []).map((c) => [c.id, c]))
  const holders = (ctx?.contacts || []).filter((c) => c.active)
  const changes = last?.status === 'ready' ? last.changes || [] : []
  const isHandover = changes.some((c) => c.contact?.source === 'handover')

  return (
    <div className="task-chat">
      {holders.length > 0 && (
        <table className="task-diff"><tbody>
          <tr><td><b>Role</b></td><td><b>Associate</b></td><td><b>From → To</b></td></tr>
          {holders.map((c) => (
            <tr key={c.id}><td>{c.role}</td><td>{c.associate_name}</td><td>{val(c.start)} → {val(c.end)}</td></tr>
          ))}
        </tbody></table>
      )}

      <div className="task-log" ref={logRef}>
        {messages.length === 0 && !busy && (
          <p className="opp-muted">Assign roles in plain language — e.g. “Make Priya Nair the Tech Lead from 1 Feb” or “Replace Sara Khan with Rahul Patil as Project Manager from 1 Feb”.</p>
        )}
        {messages.map((m, i) => <div key={i} className={`task-msg ${m.role}`}>{m.content}</div>)}
        {busy && <div className="task-msg assistant opp-muted">A4 is thinking…</div>}
      </div>

      {error && <div className="opp-banner bad" onClick={() => setError('')}>{error} (click to dismiss)</div>}
      {applied && <div className="opp-banner ok">{applied}</div>}
      {gaps.length > 0 && (
        <div className="opp-banner warn">
          <b>JC-05:</b> no Project Manager on {gaps.map((g) => `${g.from} → ${g.to}`).join(', ')}. This will block submit.
        </div>
      )}

      {last && (
        <div className="task-a3">
          {(last.missing || []).length > 0 && (
            <div className="opp-banner info">
              <b>I still need:</b>
              <ol className="task-missing">
                {last.missing.map((q, i) => <li key={i}>{q.question}</li>)}
              </ol>
            </div>
          )}

          {(last.rule_hits || []).filter((r) => r.rule !== 'JC-05').map((r, i) => (
            <div key={i} className={`opp-banner ${r.level === 'BLOCK' ? 'bad' : r.level === 'AUTO' ? 'info' : 'warn'}`}>
              <b>{r.rule}</b>{r.contact_id && byId[r.contact_id] ? ` · ${byId[r.contact_id].associate_name}` : ''}: {r.message}
            </div>
          ))}

          {(last.candidates || []).length > 0 && (
            <div className="opp-banner warn">
              <b>Which one did you mean?</b>
              <div className="task-cands">
                {last.candidates.map((c) => (
                  <button key={c.associate_id} className="opp-ghost" onClick={() => send(`Use ${c.name} (${c.email})`)}>
                    {c.name} · {c.email}{c.location ? ` · ${c.location}` : ''}
                  </button>
                ))}
              </div>
            </div>
          )}

          {isHandover && changes.length > 0 && (
            <p className="opp-muted opp-small">Handover: these changes are saved together or not at all.</p>
          )}

          {changes.map((ch, i) => {
            const cur = ch.contact_id ? byId[ch.contact_id] : null
            const c = ch.contact || {}
            return (
              <div key={i} className={`task-card ${ch.op}`}>
                <div className="task-card-h">
                  <i className={`chip ${ch.op === 'add' ? 'ok' : ch.op === 'deactivate' ? 'bad' : 'warn'}`}>{ch.op}</i>
                  <b>{ch.op === 'add' ? `${c.role} — ${c.associate_name || c.associate_id}` : `${cur?.role || ''} — ${cur?.associate_name || ch.contact_id}`}</b>
                </div>
                {ch.op === 'add' && (
                  <table className="task-diff"><tbody>
                    {['role', 'associate_name', 'start', 'end', 'source'].map((k) => (
                      <tr key={k}><td>{pretty(k)}</td><td>{val(c[k])}</td></tr>
                    ))}
                  </tbody></table>
                )}
                {ch.op === 'update' && (
                  <table className="task-diff"><tbody>
                    {['start', 'end', 'source'].filter((k) => c[k] != null && c[k] !== '' && c[k] !== cur?.[k]).map((k) => (
                      <tr key={k}>
                        <td>{pretty(k)}</td>
                        <td><s>{val(k === 'end' && ch.previous?.end ? ch.previous.end : cur?.[k])}</s> → <b>{val(c[k])}</b></td>
                      </tr>
                    ))}
                  </tbody></table>
                )}
                {ch.op === 'deactivate' && <p className="opp-muted opp-small">Role holder will be set inactive — contacts are never deleted.</p>}
                {ch.reason && <p className="opp-muted opp-small">{ch.reason}</p>}
              </div>
            )
          })}

          {last.timesheet_approver && (
            <p className="opp-muted opp-small">
              Timesheet approver: {last.timesheet_approver.name} from {last.timesheet_approver.from}
            </p>
          )}

          {last.status === 'ready' && changes.length > 0 && (
            <button className="opp-submit" onClick={apply} disabled={busy}>
              {changes.every((c) => c.op === 'add') ? 'Create' : 'Update'}
            </button>
          )}
          {last.status === 'ready' && changes.length === 0 && <p className="opp-muted">Nothing to change.</p>}
        </div>
      )}

      <form className="task-form" onSubmit={(e) => { e.preventDefault(); send() }}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Make Priya Nair the Tech Lead from 1 Feb…"
          disabled={busy}
        />
        <button type="submit" disabled={busy || !input.trim()}>Send</button>
      </form>
      {ctx && (
        <p className="opp-muted opp-small">
          revision {ctx.revision} · {holders.length} active role holder(s) · project {ctx.project?.start_date} → {ctx.project?.finish_date}
        </p>
      )}
    </div>
  )
}
