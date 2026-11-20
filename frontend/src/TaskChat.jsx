import { useEffect, useRef, useState } from 'react'

const API = (projectId) => `/api/v1/projects/${projectId}/tasks`

async function http(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail ?? res.statusText))
  return data
}

const val = (v) => (v === true ? 'Yes' : v === false ? 'No' : v == null || v === '' ? '—' : String(v))
const pretty = (k) => k.replace(/_/g, ' ')

export default function TaskChat({ projectId }) {
  const [ctx, setCtx] = useState(null)
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [last, setLast] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [applied, setApplied] = useState('')
  const logRef = useRef(null)

  useEffect(() => {
    setCtx(null); setMessages([]); setLast(null); setError(''); setApplied('')
    if (!projectId) return undefined
    http('GET', API(projectId)).then(setCtx).catch((e) => setError(e.message))
    return undefined
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
        setMessages((m) => [...m, { role: 'assistant', content: r.reply || '' }])
      })
      .catch((e) => setError(e.message))
      .finally(() => setBusy(false))
  }

  const apply = () => {
    if (!last?.changes?.length || busy) return
    setBusy(true); setError('')
    http('POST', `${API(projectId)}/apply`, { changes: last.changes })
      .then((r) => {
        setCtx((c) => ({ ...c, revision: r.revision, tasks: r.tasks }))
        setApplied(`Applied ${r.applied.length} change(s) · ${r.salesforce}`)
        setLast(null)
      })
      .catch((e) => setError(e.message))
      .finally(() => setBusy(false))
  }

  const changes = last?.status === 'ready' ? last.changes || [] : []

  return (
    <div className="task-chat">
      <div className="task-log" ref={logRef}>
        {messages.length === 0 && !busy && (
          <p className="opp-muted">Describe a task in plain language — e.g. “Create Data Migration for Rahul, 50%, starting 1 Nov”.</p>
        )}
        {messages.map((m, i) => <div key={i} className={`task-msg ${m.role}`}>{m.content}</div>)}
        {busy && <div className="task-msg assistant opp-muted">A3 is thinking…</div>}
      </div>

      {error && <div className="opp-banner bad" onClick={() => setError('')}>{error} (click to dismiss)</div>}
      {applied && <div className="opp-banner ok">{applied}</div>}

      {last && (
        <div className="task-a3">
          {(last.missing || []).length > 0 && (
            <div className="opp-banner info">
              <b>I still need:</b>
              <ol className="task-missing">
                {last.missing.map((q, i) => (
                  <li key={i}>{q.question}{q.default != null ? ` — default: ${val(q.default)}` : ''}</li>
                ))}
              </ol>
            </div>
          )}

          {(last.rule_hits || []).map((r, i) => (
            <div key={i} className={`opp-banner ${r.level === 'BLOCK' ? 'bad' : 'warn'}`}>
              <b>{r.rule}</b> · {r.task}: {r.message}
            </div>
          ))}

          {(last.candidates || []).length > 0 && (
            <div className="opp-banner warn">
              <b>Which one did you mean?</b>
              <div className="task-cands">
                {last.candidates.map((c) => (
                  <button key={c.associate_id} className="opp-ghost" onClick={() => send(`Use ${c.name}`)}>
                    {c.name} · {c.email}
                  </button>
                ))}
              </div>
            </div>
          )}

          {changes.map((ch, i) => (
            <div key={i} className={`task-card ${ch.op}`}>
              <div className="task-card-h">
                <i className={`chip ${ch.op === 'add' ? 'ok' : ch.op === 'deactivate' ? 'bad' : 'warn'}`}>{ch.op}</i>
                <b>{ch.op === 'add' ? ch.task?.name : ch.task_name}</b>
              </div>
              {ch.op === 'add' && ch.task && (
                <table className="task-diff"><tbody>
                  {['assignee_name', 'billable', 'utilization', 'start', 'end'].map((k) => (
                    <tr key={k}><td>{pretty(k)}</td><td>{val(ch.task[k])}</td></tr>
                  ))}
                </tbody></table>
              )}
              {ch.op === 'update' && (
                <table className="task-diff"><tbody>
                  {Object.entries(ch.changes || {}).map(([k, d]) => (
                    <tr key={k}>
                      <td>{pretty(k)}</td>
                      <td><s>{val(d?.from)}</s> → <b>{val(d?.to)}</b></td>
                    </tr>
                  ))}
                </tbody></table>
              )}
              {ch.op === 'deactivate' && <p className="opp-muted opp-small">Task will be set inactive — tasks are never deleted.</p>}
            </div>
          ))}

          {(last.assignee_load || []).map((a) => (
            <div key={a.associate_id} className="task-load">
              <span className="opp-small">
                {a.associate_id} · peak {a.peak_utilization_pct}% of {a.allocation_pct}% allocation · {a.from} → {a.to}
              </span>
              <div className="opp-bar-bg"><div style={{ width: `${Math.min(100, a.peak_utilization_pct)}%` }} /></div>
            </div>
          ))}

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
          placeholder="Create Data Migration for Rahul, 50%, starting 1 Nov…"
          disabled={busy}
        />
        <button type="submit" disabled={busy || !input.trim()}>Send</button>
      </form>
      {ctx && (
        <p className="opp-muted opp-small">
          revision {ctx.revision} · {ctx.tasks.length} task(s) · project {ctx.project?.start_date} → {ctx.project?.finish_date} · billable default {ctx.project?.default_billable ? 'Yes' : 'No'}
        </p>
      )}
    </div>
  )
}
