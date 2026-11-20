import { useState } from 'react'
import './ResourceEstimationLookup.css'

const value = (input) =>
  input == null || input === '' ? '—' : String(input)

const number = (input) =>
  input == null || input === ''
    ? '—'
    : Number(input).toLocaleString()

const money = (input) =>
  input == null || input === ''
    ? '—'
    : `$${Number(input).toLocaleString(undefined, {
        maximumFractionDigits: 2,
      })}`

const KNOWN_LABELS = {
  resource_code: 'Resource Code',
  code: 'Code',
  count: 'Count',
  level: 'Level',
  tier: 'Tier',
  bill_rate_per_hour: 'Bill Rate',
  cost_rate_per_hour: 'Cost Rate',
  monthly_hours_per_resource: 'Monthly Hours',
  project_months: 'Project Months',
  associate_name: 'Associate',
  associate_id: 'Associate ID',
  line_number: '#',
  location_type: 'Location',
  hours_total: 'Total Hours',
  budget_amount_total: 'Total Budget',
  cost_amount_total: 'Total Cost',
  margin_percent: 'Margin',
  margin_amount: 'Margin Amount',
}

const humanize = (key) =>
  KNOWN_LABELS[key] ??
  key
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())

const MONEY_KEY = /rate|amount|cost|budget|price|salary|revenue/i
const NUM_KEY = /count|hours|months|number|qty|quantity|fte|days|weeks/i
const PCT_KEY = /percent|_pct\b|^pct/i
const NUMERIC = /^-?[\d,.]+$/

const formatCell = (key, cell) => {
  if (cell == null || cell === '') return '—'
  if (typeof cell === 'boolean') return cell ? 'Yes' : 'No'
  if (typeof cell === 'object') {
    try {
      return JSON.stringify(cell)
    } catch {
      return String(cell)
    }
  }
  if (typeof cell === 'number' || (typeof cell === 'string' && NUMERIC.test(cell))) {
    if (PCT_KEY.test(key)) return `${Number(cell)}%`
    if (MONEY_KEY.test(key)) return money(cell)
    if (NUM_KEY.test(key)) return number(cell)
  }
  return String(cell)
}

const PRIORITY_KEYS = ['line_number', 'resource_code', 'associate_name']

const dynamicColumns = (rows) => {
  const keys = []
  for (const row of rows) {
    if (!row || typeof row !== 'object') continue
    for (const key of Object.keys(row)) {
      if (!keys.includes(key)) keys.push(key)
    }
  }
  return keys.sort((a, b) => {
    const pa = PRIORITY_KEYS.indexOf(a)
    const pb = PRIORITY_KEYS.indexOf(b)
    if (pa === -1 && pb === -1) return 0
    return (pa === -1 ? 99 : pa) - (pb === -1 ? 99 : pb)
  })
}

async function fetchAllocation(opportunityId) {
  const response = await fetch(
    `/api/v1/opportunities/${encodeURIComponent(
      opportunityId,
    )}/resource-estimation`,
  )

  const body = await response.json().catch(() => ({}))

  if (!response.ok) {
    throw new Error(body.detail || 'Unable to fetch Opportunity details.')
  }

  return body
}

function Table({ headers, children, empty }) {
  const hasRows = Array.isArray(children) && children.length > 0

  return (
    <div className="resource-lookup__table-wrap">
      <table>
        <thead>
          <tr>
            {headers.map((header) => (
              <th key={header}>{header}</th>
            ))}
          </tr>
        </thead>

        <tbody>
          {hasRows ? (
            children
          ) : (
            <tr>
              <td
                colSpan={headers.length}
                className="resource-lookup__empty"
              >
                {empty}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

export default function ResourceEstimationLookup() {
  const [opportunityId, setOpportunityId] = useState('')
  const [data, setData] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const submit = async (event) => {
    event.preventDefault()

    const id = opportunityId.trim()

    if (!id || busy) return

    setBusy(true)
    setError('')
    setData(null)

    try {
      const result = await fetchAllocation(id)
      setData(result)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const groups = (data?.estimation?.groups || []).filter(
    (row) => row && typeof row === 'object',
  )
  const lines = (data?.resource_allocation?.lines || []).filter(
    (row) => row && typeof row === 'object',
  )
  const summary = data?.estimation || {}
  const opportunity = data?.opportunity || {}

  const KNOWN_SUMMARY = new Set([
    'resource_count',
    'total_hours',
    'total_budget_amount',
    'total_cost_amount',
    'total_margin_amount',
    'margin_percent',
    'blended_bill_rate_per_hour',
    'groups',
  ])
  const extraSummary = Object.entries(summary).filter(
    ([key, cell]) =>
      !KNOWN_SUMMARY.has(key) &&
      (typeof cell !== 'object' || cell === null),
  )
  const groupCols = dynamicColumns(groups)
  const lineCols = dynamicColumns(lines)

  return (
    <section className="resource-lookup">
      <header className="resource-lookup__header">
        <div>
          <p className="resource-lookup__eyebrow">
            Salesforce Opportunity
          </p>
          <h2>Project Estimation & Resource Allocation</h2>
        </div>

        <p>
          Enter an Opportunity ID to load its saved estimation and
          resource allocation lines.
        </p>
      </header>

      <form className="resource-lookup__form" onSubmit={submit}>
        <label htmlFor="opportunity-id">Opportunity ID</label>

        <input
          id="opportunity-id"
          value={opportunityId}
          onChange={(event) => setOpportunityId(event.target.value)}
          placeholder="006xxxxxxxxxxxxxxx"
          disabled={busy}
          autoComplete="off"
        />

        <button
          type="submit"
          disabled={!opportunityId.trim() || busy}
        >
          {busy ? 'Fetching…' : 'Fetch details'}
        </button>
      </form>

      {error && (
        <div className="resource-lookup__alert resource-lookup__alert--error">
          {error}
        </div>
      )}

      {(data?.warnings || []).map((warning) => (
        <div
          key={warning}
          className="resource-lookup__alert resource-lookup__alert--warning"
        >
          {warning}
        </div>
      ))}

      {Object.entries(data?.fields || {})
        .filter(([, field]) => !field.ok && field.error)
        .map(([name, field]) => (
          <div
            key={name}
            className="resource-lookup__alert resource-lookup__alert--error"
          >
            <b>{name}</b> — {field.error}
            {field.raw_preview && (
              <details>
                <summary>Raw value stored in Salesforce</summary>
                <pre>{field.raw_preview}</pre>
              </details>
            )}
          </div>
        ))}

      {data && (
        <>
          <section className="resource-lookup__section">
            <h3>{value(opportunity.name)}</h3>

            <div className="resource-lookup__facts">
              <div>
                <span>Account</span>
                <b>{value(opportunity.account_name)}</b>
              </div>

              <div>
                <span>Stage</span>
                <b>{value(opportunity.stage)}</b>
              </div>

              <div>
                <span>PO Number</span>
                <b>{value(opportunity.po_number)}</b>
              </div>

              <div>
                <span>Version</span>
                <b>{value(opportunity.project_version)}</b>
              </div>

              <div>
                <span>Project Dates</span>
                <b>
                  {value(opportunity.start_date)} →{' '}
                  {value(opportunity.end_date)}
                </b>
              </div>

              <div>
                <span>Opportunity Budget</span>
                <b>{money(opportunity.budget_amount)}</b>
              </div>
            </div>
          </section>

          <section className="resource-lookup__section">
            <h3>Estimation Summary</h3>

            <div className="resource-lookup__metrics">
              <div>
                <span>Resource Count</span>
                <b>{number(summary.resource_count)}</b>
              </div>

              <div>
                <span>Total Hours</span>
                <b>{number(summary.total_hours)}</b>
              </div>

              <div>
                <span>Budget</span>
                <b>{money(summary.total_budget_amount)}</b>
              </div>

              <div>
                <span>Cost</span>
                <b>{money(summary.total_cost_amount)}</b>
              </div>

              <div>
                <span>Margin</span>
                <b>
                  {money(summary.total_margin_amount)} (
                  {value(summary.margin_percent)}%)
                </b>
              </div>

              <div>
                <span>Blended Bill Rate</span>
                <b>{money(summary.blended_bill_rate_per_hour)}</b>
              </div>

              {extraSummary.map(([key, cell]) => (
                <div key={key}>
                  <span>{humanize(key)}</span>
                  <b>{formatCell(key, cell)}</b>
                </div>
              ))}
            </div>

            <Table
              empty="No estimation data saved on this Opportunity yet."
              headers={
                groupCols.length > 0
                  ? groupCols.map(humanize)
                  : [
                      'Resource Code',
                      'Count',
                      'Bill Rate',
                      'Monthly Hours',
                      'Cost Rate',
                      'Project Months',
                    ]
              }
            >
              {groups.map((group, index) => (
                <tr key={`${group.resource_code ?? 'group'}-${index}`}>
                  {groupCols.map((key) => (
                    <td key={key}>{formatCell(key, group[key])}</td>
                  ))}
                </tr>
              ))}
            </Table>
          </section>

          <section className="resource-lookup__section">
            <h3>
              Allocated Resources <small>{lines.length} line(s)</small>
            </h3>

            <Table
              empty="No resource allocation lines saved on this Opportunity yet."
              headers={
                lineCols.length > 0
                  ? lineCols.map(humanize)
                  : [
                      '#',
                      'Associate',
                      'Code',
                      'Level',
                      'Tier',
                      'Location',
                      'Hours',
                      'Bill Rate',
                      'Budget',
                      'Margin',
                    ]
              }
            >
              {lines.map((line, index) => (
                <tr key={line.line_number ?? index}>
                  {lineCols.map((key) => (
                    <td key={key}>{formatCell(key, line[key])}</td>
                  ))}
                </tr>
              ))}
            </Table>
          </section>
        </>
      )}
    </section>
  )
}
