import { useEffect, useState } from 'react'
import { api } from '../services/api'
import { Card } from '../components/Badges'
import { EmptyState, Skeleton, StatusBadge } from '../components/ui'
import type { RuleOut } from '../types'

export default function RuleLibrary() {
  const [items, setItems] = useState<RuleOut[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api
      .get<{ items: RuleOut[] }>('/rules')
      .then((d) => setItems(d.items))
      .catch((e) => setError(e instanceof Error ? e.message : 'Load failed'))
      .finally(() => setLoading(false))
  }, [])

  return (
    <>
      <div className="page-title">Rule Library</div>
      <div className="page-sub">
        Versioned Legal Metrology (Packaged Commodities) Rules, 2011 definitions. Historical evaluations
        are pinned to the version in effect when they ran — later edits never rewrite history.
      </div>
      {error && <div className="alert error">{error}</div>}

      <div className="alert info">
        Legal source: Department of Consumer Affairs, Ministry of Consumer Affairs, Food &amp; Public
        Distribution — Legal Metrology Act 2009 and LM (Packaged Commodities) Rules 2011, as amended.
        Rule text here is an engineering summary; the Official Gazette text is authoritative.
      </div>

      {loading && <Card><Skeleton lines={4} /></Card>}
      {!loading && items.length === 0 && (
        <Card><EmptyState headline="No rules loaded" how="The rule library is seeded at first startup from the versioned rule definitions." /></Card>
      )}
      {items.map((r) => (
        <Card key={r.id} title={`${r.rule_id} · Rule ${r.rule_number} — ${r.title}`}>
          <div className="kv">
            <dt>Version</dt><dd>v{r.current_version} {r.effective_from ? `(effective ${r.effective_from})` : ''}</dd>
            <dt>Applicability</dt><dd><StatusBadge value={r.applicability} /></dd>
            <dt>Status</dt><dd><StatusBadge value={r.status} /></dd>
            <dt>Requirement</dt><dd>{r.requirement || r.description}</dd>
            {r.check_type && (
              <>
                <dt>Automated check</dt>
                <dd className="ident">{r.check_type}{r.method ? ` · ${r.method}` : ''}</dd>
              </>
            )}
            <dt>Source</dt><dd className="muted">{r.source_reference}</dd>
            {r.amendment && (<><dt>Amendment</dt><dd className="muted">{r.amendment}</dd></>)}
          </div>

          {/* Measurable requirements publish the table actually used by the check — the engine reads
              the same data, so what is shown here is what was applied. */}
          {r.font_size_tables && r.font_size_tables.length > 0 && (
            <div style={{ marginTop: 12 }}>
              <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>
                Minimum letter/numeral height in force (used by the measurement check)
              </div>
              {r.font_size_tables.map((t) => (
                <div key={t.id} style={{ marginBottom: 10, overflowX: 'auto' }}>
                  <div style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 4 }}>{t.id} — {t.title}</div>
                  <table className="measure-table">
                    <thead>
                      <tr><th>#</th><th>Area of principal display panel</th><th>Normal (mm)</th><th>Blown/formed/molded (mm)</th></tr>
                    </thead>
                    <tbody>
                      {t.rows.map((row) => (
                        <tr key={row.serial}>
                          <td>{row.serial}</td><td>{row.label}</td>
                          <td className="num">{row.normal_mm}</td><td className="num">{row.moulded_mm}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ))}
              {r.exempt_note && <p className="muted" style={{ fontSize: 12, lineHeight: 1.6 }}>{r.exempt_note}</p>}
            </div>
          )}
        </Card>
      ))}
    </>
  )
}
