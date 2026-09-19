import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, hasPermission } from '../services/api'
import { Card } from '../components/Badges'
import { EmptyState, Skeleton, StatusChip } from '../components/ui'
import type { ProductOut } from '../types'

/**
 * Product index. The scan-level repository (search, filters, checks, finalization) lives on the
 * Compliance Repository page; this page answers "what products have we seen, and how did they do
 * across every scan?" and links straight into each product's compliance history.
 */
export default function Products() {
  const [items, setItems] = useState<ProductOut[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const canOpenHistory = hasPermission('compliance.view')

  useEffect(() => {
    api
      .get<{ items: ProductOut[] }>('/products')
      .then((d) => setItems(d.items))
      .catch((e) => setError(e instanceof Error ? e.message : 'Load failed'))
      .finally(() => setLoading(false))
  }, [])

  const needle = search.trim().toLowerCase()
  const rows = needle
    ? items.filter((p) =>
        [p.name, p.brand, p.manufacturer, p.category].some((v) => (v || '').toLowerCase().includes(needle)),
      )
    : items

  return (
    <>
      <div className="page-title">Products</div>
      <div className="page-sub">
        Deduplicated products seen across inspections. Open a product for its complete compliance
        history — every scan's score, verdict and final decision are kept separately.
      </div>
      {error && <div className="alert error">{error}</div>}

      {loading ? (
        <Card><Skeleton lines={5} /></Card>
      ) : (
        <Card>
          {items.length > 0 && (
            <div className="filter-bar">
              <div className="field grow">
                <label htmlFor="prod-q">Search products</label>
                <input
                  id="prod-q"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Name, brand, manufacturer or category…"
                />
              </div>
            </div>
          )}
          <div style={{ overflowX: 'auto' }}>
            <table className="tbl">
              <thead>
                <tr>
                  <th>Product</th><th>Brand</th><th>Category</th><th>Manufacturer</th>
                  <th>Quantities seen</th><th>MRPs seen</th><th>Scans</th><th>Latest</th><th>Last seen</th><th></th>
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 && (
                  <tr>
                    <td colSpan={10}>
                      <EmptyState
                        headline={items.length === 0 ? 'No products recorded yet' : 'No products match that search'}
                        how={
                          items.length === 0
                            ? 'Products appear automatically after an inspection is processed: each scanned package is added to the repository.'
                            : 'Try a different name, brand, category or manufacturer.'
                        }
                        action={
                          items.length === 0 ? (
                            <Link className="btn sm" to="/inspections/new">Start an inspection</Link>
                          ) : (
                            <button className="btn sm secondary" onClick={() => setSearch('')}>Clear search</button>
                          )
                        }
                      />
                    </td>
                  </tr>
                )}
                {rows.map((p) => (
                  <tr key={p.id}>
                    <td>
                      {canOpenHistory ? (
                        <Link to={`/repository/products/${p.id}`}><b>{p.name || '—'}</b></Link>
                      ) : (
                        <b>{p.name || '—'}</b>
                      )}
                    </td>
                    <td>{p.brand || '—'}</td>
                    <td>{p.category || '—'}</td>
                    <td>{p.manufacturer || '—'}</td>
                    <td>{p.known_net_quantities || '—'}</td>
                    <td>{p.mrp_values || '—'}</td>
                    <td>{p.scan_count ?? p.inspections}</td>
                    <td>
                      {p.latest_score === null || p.latest_score === undefined ? (
                        <span className="muted">—</span>
                      ) : (
                        <span className="flex" style={{ gap: 6, alignItems: 'center' }}>
                          <b>{p.latest_score}%</b>
                          {p.latest_review_status && <StatusChip value={p.latest_review_status} />}
                        </span>
                      )}
                    </td>
                    <td>{p.last_seen.slice(0, 10)}</td>
                    <td>
                      {canOpenHistory ? <Link to={`/repository/products/${p.id}`}>History</Link> : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </>
  )
}
