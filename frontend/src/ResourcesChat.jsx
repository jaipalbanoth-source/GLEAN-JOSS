import { useEffect, useRef, useState } from 'react'

const api = (projectId) => `/api/v1/projects/${projectId}/resources`

const value = (v) => (v == null || v === '' ? '—' : String(v))
const money = (v) =>
  typeof v === 'number'
    ? `$${v.toLocaleString(undefined, { maximumFractionDigits: 2 })}`
    : value(v)

async function http(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })

  const data = await res.json().catch(() => ({}))

  if (!res.ok) {
    const detail = data.detail
    const text =
      typeof detail === 'string'
        ? detail
        : detail?.hits
          ? `${detail.message}: ${detail.hits.map((h) => `${h.rule} ${h.message}`).join(' | ')}`
          : JSON.stringify(detail || res.statusText)

    const error = new Error(text)
    error.status = res.status
    throw error
  }

  return data
}

export default function ResourcesChat({ projectId }) {
  const [ctx, setCtx] = useState(null)
  const [messages, setMessages] = useState([])
  const [last, setLast] = useState(null)
  const [input, setInput] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const logRef = useRef(null)

  const load = () =>
    http('GET', api(projectId))
      .then(setCtx)
      .catch((e) => setError(e.message))

  useEffect(() => {
    setCtx(null)
    setMessages([])
    setLast(null)
    setError('')
    setNotice('')
    if (projectId) load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId])

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight })
  }, [messages, busy])

  const send = (suggestion) => {
    const message = (suggestion || input).trim()
    if (!message || busy) return

    const history = messages
    setInput('')
    setBusy(true)
    setError('')
    setNotice('')
    setMessages((m) => [...m, { role: 'user', content: message }])

    http('POST', `${api(projectId)}/chat`, {
      message,
      chat_history: history,
    })
      .then((result) => {
        setLast(result)
        setMessages((m) => [
          ...m,
          { role: 'assistant', content: result.reply || '' },
        ])
      })
      .catch((e) => setError(e.message))
      .finally(() => setBusy(false))
  }

  const apply = () => {
    if (!last?.changes?.length || busy) return

    const requestId = crypto.randomUUID
      ? crypto.randomUUID()
      : `resource-${Date.now()}`

    setBusy(true)
    setError('')

    http('POST', `${api(projectId)}/apply`, {
      changes: last.changes,
      request_id: requestId,
      expected_revision: ctx?.revision,
    })
      .then((result) => {
        setCtx((old) => ({
          ...old,
          revision: result.revision,
          resources: result.resources,
        }))
        setNotice(`Applied ${result.applied.length} change(s) · ${result.salesforce}`)
        setLast(null)
      })
      .catch((e) => {
        setError(e.message)

        if (e.status === 409) {
          setLast(null)
          load()
        }
      })
      .finally(() => setBusy(false))
  }

  const changes = last?.status === 'ready' ? last.changes || [] : []
  const active = (ctx?.resources || []).filter((r) => r.active)
  const summary = last?.budget_summary
  const unassigned = last?.unassigned_line_ids || []

  return (
    <div className="task-chat">
      {active.length > 0 && (
        <table className="opp-table">
          <thead>
            <tr>
              <th>Role</th>
              <th>Associate</th>
              <th>Hours</th>
              <th>Rate</th>
              <th>Allocation</th>
              <th>Budget</th>
              <th>Dates</th>
            </tr>
          </thead>
          <tbody>
            {active.map((row) => (
              <tr key={row.id}>
                <td>{value(row.role)}</td>
                <td>{value(row.associate_name)}</td>
                <td>{value(row.hours)}</td>
                <td>{money(row.bill_rate)}</td>
                <td>{value(row.allocation_pct)}%</td>
                <td>{money(row.budget)}</td>
                <td>{value(row.start)} → {value(row.end)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="task-log" ref={logRef}>
        {!messages.length && !busy && (
          <p className="opp-muted">
            Add a named resource line — for example: “Add Priya Nair as a
            Salesforce Consultant, 160 hours at 85 per hour, 50% from 1 Feb
            to 28 Feb”.
          </p>
        )}

        {messages.map((message, index) => (
          <div
            key={index}
            className={`task-msg ${message.role}`}
          >
            {message.content}
          </div>
        ))}

        {busy && (
          <div className="task-msg assistant opp-muted">
            A5 is thinking…
          </div>
        )}
      </div>

      {error && (
        <div
          className="opp-banner bad"
          onClick={() => setError('')}
        >
          {error} (click to dismiss)
        </div>
      )}

      {notice && <div className="opp-banner ok">{notice}</div>}

      {unassigned.length > 0 && (
        <div className="opp-banner warn">
          <b>RM-01:</b> Unassigned resource lines: {unassigned.join(', ')}.
        </div>
      )}

      {last && (
        <div className="task-a3">
          {(last.missing || []).length > 0 && (
            <div className="opp-banner info">
              <b>I still need:</b>
              <ol className="task-missing">
                {last.missing.map((item) => (
                  <li key={item.n}>{item.question}</li>
                ))}
              </ol>
            </div>
          )}

          {(last.rule_hits || []).map((hit, index) => (
            <div
              key={index}
              className={`opp-banner ${hit.level === 'BLOCK' ? 'bad' : 'warn'}`}
            >
              <b>{hit.rule}</b>
              {hit.line_id ? ` · ${hit.line_id}` : ''}: {hit.message}
            </div>
          ))}

          {(last.candidates || []).length > 0 && (
            <div className="opp-banner warn">
              <b>Which associate?</b>
              <div className="task-cands">
                {last.candidates.map((candidate) => (
                  <button
                    key={candidate.associate_id}
                    className="opp-ghost"
                    onClick={() =>
                      send(`Use ${candidate.name} (${candidate.email})`)
                    }
                  >
                    {candidate.name} · {candidate.email}
                  </button>
                ))}
              </div>
            </div>
          )}

          {changes.map((change, index) => (
            <div
              key={index}
              className={`task-card ${change.op}`}
            >
              <div className="task-card-h">
                <i
                  className={`chip ${
                    change.op === 'add'
                      ? 'ok'
                      : change.op === 'deactivate'
                        ? 'bad'
                        : 'warn'
                  }`}
                >
                  {change.op}
                </i>

                <b>{change.line?.role || change.line_id}</b>
              </div>

              {change.op !== 'deactivate' && (
                <table className="task-diff">
                  <tbody>
                    {[
                      'associate_name',
                      'level',
                      'technology',
                      'hours',
                      'bill_rate',
                      'allocation_pct',
                      'start',
                      'end',
                      'budget',
                    ]
                      .filter((key) => change.line?.[key] != null)
                      .map((key) => (
                        <tr key={key}>
                          <td>{key.replace(/_/g, ' ')}</td>
                          <td>
                            {key === 'bill_rate' || key === 'budget'
                              ? money(change.line[key])
                              : value(change.line[key])}
                            {key === 'allocation_pct' ? '%' : ''}
                          </td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              )}

              {change.op === 'deactivate' && (
                <p className="opp-muted opp-small">
                  This resource line will be set inactive.
                </p>
              )}
            </div>
          ))}

          {summary && (
            <p className={`opp-banner ${summary.margin_flag ? 'warn' : 'info'}`}>
              <b>Budget:</b> {money(summary.recomputed_total)} vs estimate{' '}
              {money(summary.estimate_total)} · variance {summary.variance_pct}%
              · tolerance {summary.tolerance_pct}%
            </p>
          )}

          {last.status === 'ready' && changes.length > 0 && (
            <button
              className="opp-submit"
              disabled={busy}
              onClick={apply}
            >
              {changes.every((change) => change.op === 'add')
                ? 'Create'
                : 'Update'}
            </button>
          )}
        </div>
      )}

      <form
        className="task-form"
        onSubmit={(event) => {
          event.preventDefault()
          send()
        }}
      >
        <input
          value={input}
          onChange={(event) => setInput(event.target.value)}
          disabled={busy}
          placeholder="Add Priya Nair as a Salesforce Consultant, 160 hours…"
        />

        <button
          type="submit"
          disabled={busy || !input.trim()}
        >
          Send
        </button>
      </form>

      {ctx && (
        <p className="opp-muted opp-small">
          revision {ctx.revision} · {active.length} active resource line(s)
          {' · '}estimate {money(ctx.project?.estimate_budget_total)}
        </p>
      )}
    </div>
  )
}
